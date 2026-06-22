"""AstraCore SDK client — embeddable async client with full feature parity."""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select

from astracore.infrastructure.db.models import SkillRow
from astracore.infrastructure.db.session import get_session, init_db
from astracore.infrastructure.memory.hybrid import HybridMemoryAdapter
from astracore.infrastructure.memory.store import SQLMemoryStore
from astracore.infrastructure.memory.vector import MemoryVectorAdapter
from astracore.infrastructure.retrieval.chroma import ChromaRetrieverAdapter
from astracore.infrastructure.workflow.native import NativeWorkflowOrchestrator
from astracore.modules.agent.domain import AgentTask
from astracore.modules.agent.ports.workflow import WorkflowState
from astracore.modules.chat.domain.chat_context import ChatContext
from astracore.modules.chat.domain.chat_options import ChatOptions
from astracore.modules.chat.pipeline import ChatPipeline
from astracore.modules.memory.application.engine import MemoryEngine
from astracore.modules.memory.domain import (
    ConversationProjectBinding,
    MemoryScope,
    MemoryStatus,
    MemoryType,
    Project,
    StructuredMemory,
)
from astracore.modules.rag.application.pipeline import RAGPipeline
from astracore.modules.skills.seeds import seed_builtin_skills
from astracore.modules.tools.ports.tool import MutableToolAdapter, ToolParameter
from astracore.sdk.config import AstraCoreConfig
from astracore.shared.observability.hooks import HookRegistry
from astracore.shared.observability.logger import get_logger
from astracore.shared.policy.engine import PolicyConfig as _EnginePolicyConfig
from astracore.shared.policy.engine import PolicyEngine
from astracore.shared.ports.llm import StreamEvent, StreamEventType

logger = get_logger(__name__)


@dataclass
class ChatResult:
    """Result of a non-streaming chat call."""

    content: str
    session_id: UUID
    model_profile: str
    model: str


class Conversation:
    """Multi-turn conversation facade.

    Maintains session state across turns and stores per-conversation defaults
    so callers do not need to pass ``session_id`` or repeated options on every call.

    Create via :meth:`AstraCoreClient.conversation` — do not instantiate directly::

        async with AstraCoreClient() as client:
            async with client.conversation(options=ChatOptions(use_tools=True)) as conv:
                result = await conv.send("你好")
                async for chunk in conv.stream("继续"):
                    ...

    Per-turn overrides use ``dataclasses.replace`` field names::

        result = await conv.send("你好", temperature=0.2)
    """

    def __init__(
        self,
        client: AstraCoreClient,
        *,
        session_id: UUID | None = None,
        project_id: str | None = None,
        project_locked: bool = False,
        project_source: str = "sdk",
        options: ChatOptions | None = None,
    ) -> None:
        self._client = client
        self._session_id = session_id or uuid4()
        self._project_id = project_id
        self._project_locked = project_locked
        self._project_source = project_source
        self._project_bound = False
        self._defaults = options or ChatOptions()

    @property
    def session_id(self) -> UUID:
        """Stable session identifier for this conversation."""
        return self._session_id

    @property
    def default_options(self) -> ChatOptions:
        """Per-conversation default options."""
        return self._defaults

    async def bind_project(
        self,
        project_id: str,
        *,
        locked: bool = False,
        source: str = "sdk",
    ) -> ConversationProjectBinding:
        """Bind this conversation to a project for project-scoped memory."""
        binding = await self._client.projects.bind_conversation(
            conversation_id=self._session_id,
            project_id=project_id,
            locked=locked,
            source=source,
        )
        self._project_id = project_id
        self._project_locked = locked
        self._project_source = source
        self._project_bound = True
        return binding

    async def _ensure_project_binding(self) -> None:
        if self._project_id is None or self._project_bound:
            return
        await self.bind_project(
            self._project_id,
            locked=self._project_locked,
            source=self._project_source,
        )

    def _effective_options(self, **overrides: Any) -> ChatOptions:
        """Merge per-conversation defaults with per-turn field overrides."""
        if not overrides:
            return self._defaults
        return dataclasses.replace(self._defaults, **overrides)

    async def send(self, message: str, **overrides: Any) -> ChatResult:
        """Send a message and return the complete response.

        Keyword arguments are ``ChatOptions`` field names that override the
        conversation defaults for this turn only.
        """
        await self._ensure_project_binding()
        return await self._client.chat(
            message,
            session_id=self._session_id,
            options=self._effective_options(**overrides),
        )

    async def stream(self, message: str, **overrides: Any) -> AsyncIterator[str]:
        """Stream text chunks from a chat response.

        Yields only text content — tool calls, thinking blocks, and skill-match events
        are filtered out. Use :meth:`stream_events` when raw event access is needed.
        """
        await self._ensure_project_binding()
        async for event in self._client.chat_stream(
            message,
            session_id=self._session_id,
            options=self._effective_options(**overrides),
        ):
            if event.event_type == StreamEventType.TEXT_DELTA and event.content:
                yield event.content

    async def stream_events(self, message: str, **overrides: Any) -> AsyncIterator[StreamEvent]:
        """Stream all raw :class:`StreamEvent` objects for this turn."""
        await self._ensure_project_binding()
        async for event in self._client.chat_stream(
            message,
            session_id=self._session_id,
            options=self._effective_options(**overrides),
        ):
            yield event

    async def clear(self) -> None:
        """Delete all memory for this conversation."""
        await self._client.clear_session(self._session_id)

    async def __aenter__(self) -> Conversation:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.clear()


class AstraCoreClient:
    """Embeddable AstraCore async client with full feature parity to the HTTP service.

    Must be used as an async context manager::

        async with AstraCoreClient() as client:
            async with client.conversation() as conv:
                result = await conv.send("你好")

    Config is loaded from ``config/config.yaml`` by default (same source as the HTTP service).
    All heavy resources (ChromaDB, Redis, MCP) are initialized inside the context manager —
    constructing the client itself is always fast and never raises.
    """

    def __init__(
        self,
        config: AstraCoreConfig | None = None,
        hooks: HookRegistry | None = None,
    ) -> None:
        self.config = config or AstraCoreConfig()
        self._hooks = hooks
        self._initialized = False
        self._mcp_adapter: Any = None

        # Lightweight: just stores the DB URL, no network connection until queries run
        cfg = self.config
        self._vector_adapter = MemoryVectorAdapter(
            persist_directory=cfg.storage.vector.persist_directory,
            embedding_model=cfg.storage.vector.embedding_model,
        )
        self._memory_engine = MemoryEngine(
            SQLMemoryStore(cfg.storage.db_url),
            vector_adapter=self._vector_adapter,
        )
        self.memory = MemoryClient(self._memory_engine)
        self.projects = ProjectClient(self._memory_engine)

        # Heavy fields — declared for type checkers, assigned in _start()
        self._memory: HybridMemoryAdapter
        self._rag_pipeline: RAGPipeline
        self._user_adapter: MutableToolAdapter
        self._tool_adapter: MutableToolAdapter
        self._pipeline: ChatPipeline

    # ------------------------------------------------------------------
    # Async context manager lifecycle
    # ------------------------------------------------------------------

    async def __aenter__(self) -> AstraCoreClient:
        await self._start()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self._stop()

    async def _start(self) -> None:
        cfg = self.config

        # Heavy resource initialization — kept here so __init__ never raises
        self._memory = HybridMemoryAdapter(
            redis_url=cfg.storage.redis_url,
            db_url=cfg.storage.db_url,
        )
        self._rag_pipeline = RAGPipeline(
            retriever=ChromaRetrieverAdapter(
                collection_name=cfg.storage.vector.collection_name,
                persist_directory=cfg.storage.vector.persist_directory,
            )
        )

        from astracore.infrastructure.tools.composite import CompositeToolAdapter  # noqa: PLC0415
        from astracore.infrastructure.tools.native import NativeToolAdapter  # noqa: PLC0415
        from astracore.modules.tools.builtin import build_tool_adapter  # noqa: PLC0415

        builtin_adapter = build_tool_adapter(db_url=cfg.storage.db_url)
        self._user_adapter = NativeToolAdapter()
        self._tool_adapter = CompositeToolAdapter([builtin_adapter, self._user_adapter])

        self._pipeline = self._build_pipeline()

        # Async initialization
        await init_db(cfg.storage.db_url)
        try:
            await seed_builtin_skills(cfg.storage.db_url, extra_skill_dirs=cfg.skills.extra_dirs)
        except Exception:
            logger.warning("内置 Skill 种子写入失败，继续启动")

        if cfg.mcp.servers:
            try:
                from astracore.infrastructure.tools.mcp import (  # noqa: PLC0415
                    MCPToolAdapter,
                    build_server_configs,
                )

                mcp_configs = build_server_configs(cfg.mcp.servers)
                self._mcp_adapter = MCPToolAdapter(mcp_configs)
                await asyncio.wait_for(self._mcp_adapter.start(), timeout=30)
                self._tool_adapter = CompositeToolAdapter(
                    [builtin_adapter, self._user_adapter, self._mcp_adapter]
                )
                logger.info("MCP tool adapter started with %d server(s)", len(mcp_configs))
                self._pipeline = self._build_pipeline()
            except Exception:
                logger.warning("MCP 适配器启动失败，回退到内置工具")
                self._mcp_adapter = None

        self._initialized = True

    async def _stop(self) -> None:
        if self._mcp_adapter is not None:
            try:
                await self._mcp_adapter.stop()
            except Exception:
                logger.warning("MCP 适配器停止时出错")

    def _build_pipeline(self) -> ChatPipeline:
        cfg = self.config
        return ChatPipeline(
            config=cfg,
            memory=self._memory,
            rag_pipeline=self._rag_pipeline,
            policy=PolicyEngine(
                config=_EnginePolicyConfig(
                    retry=cfg.policy.retry,
                    timeout=cfg.policy.timeout,
                    compaction=cfg.policy.compaction,
                )
            ),
            tool_adapter=self._tool_adapter,
            memory_engine=self._memory_engine,
            vector_adapter=self._vector_adapter,
            hooks=self._hooks,
        )

    def _require_initialized(self) -> None:
        if not self._initialized:
            raise RuntimeError(
                "AstraCoreClient must be used as an async context manager:\n"
                "    async with AstraCoreClient() as client:\n"
                "        ..."
            )

    # ------------------------------------------------------------------
    # Conversation facade
    # ------------------------------------------------------------------

    def conversation(
        self,
        *,
        session_id: UUID | None = None,
        project_id: str | None = None,
        project_locked: bool = False,
        project_source: str = "sdk",
        model_profile: str | None = None,
        temperature: float | None = None,
        use_tools: bool = False,
        thinking_mode: str | None = None,
        thinking_budget: int = 8000,
        reasoning_effort: str | None = None,
        verbosity: str | None = None,
        enable_rag: bool = False,
        enable_web: bool = False,
    ) -> Conversation:
        """Create a :class:`Conversation` for multi-turn chat.

        ``options`` sets per-conversation defaults; individual fields can be
        overridden per-turn via keyword arguments to :meth:`Conversation.send`,
        :meth:`Conversation.stream`, or :meth:`Conversation.stream_events`.

        Pass ``session_id`` to resume an existing session; omit to start a new one.

        Supports ``async with`` for automatic session cleanup::

            async with client.conversation(use_tools=True) as conv:
                result = await conv.send("hello")
        """
        self._require_initialized()
        return Conversation(
            self,
            session_id=session_id,
            project_id=project_id,
            project_locked=project_locked,
            project_source=project_source,
            options=ChatOptions(
                model_profile=model_profile,
                temperature=temperature,
                use_tools=use_tools,
                thinking_mode=thinking_mode,
                thinking_budget=thinking_budget,
                reasoning_effort=reasoning_effort,
                verbosity=verbosity,
                enable_rag=enable_rag,
                enable_web=enable_web,
            ),
        )

    # ------------------------------------------------------------------
    # Low-level API (single-turn, session_id managed by caller)
    # ------------------------------------------------------------------

    async def chat_stream(
        self,
        message: str,
        *,
        session_id: UUID | None = None,
        options: ChatOptions | None = None,
        model_profile: str | None = None,
        temperature: float | None = None,
        use_tools: bool = False,
        thinking_mode: str | None = None,
        thinking_budget: int = 8000,
        reasoning_effort: str | None = None,
        verbosity: str | None = None,
        enable_rag: bool = False,
        enable_web: bool = False,
    ) -> AsyncIterator[StreamEvent]:
        """Stream a single-turn chat response as raw :class:`StreamEvent` objects.

        For multi-turn conversations prefer :meth:`conversation` which manages
        ``session_id`` automatically.
        """
        self._require_initialized()
        _session_id = session_id or uuid4()
        ctx = await self._pipeline.prepare(
            message=message,
            session_id=_session_id,
            options=options
            or ChatOptions(
                model_profile=model_profile,
                temperature=temperature,
                use_tools=use_tools,
                thinking_mode=thinking_mode,
                thinking_budget=thinking_budget,
                reasoning_effort=reasoning_effort,
                verbosity=verbosity,
                enable_rag=enable_rag,
                enable_web=enable_web,
            ),
        )
        accumulated = ""
        async for event in self._pipeline.stream(ctx):
            if event.event_type == StreamEventType.TEXT_DELTA and event.content:
                accumulated += event.content
            yield event
        await self._extract_memories_safe(ctx, accumulated)

    async def chat(
        self,
        message: str,
        *,
        session_id: UUID | None = None,
        options: ChatOptions | None = None,
        model_profile: str | None = None,
        temperature: float | None = None,
        use_tools: bool = False,
        thinking_mode: str | None = None,
        thinking_budget: int = 8000,
        reasoning_effort: str | None = None,
        verbosity: str | None = None,
        enable_rag: bool = False,
        enable_web: bool = False,
    ) -> ChatResult:
        """Send a single-turn message and return the complete response.

        For multi-turn conversations prefer :meth:`conversation` which manages
        ``session_id`` automatically.
        """
        self._require_initialized()
        _session_id = session_id or uuid4()
        ctx = await self._pipeline.prepare(
            message=message,
            session_id=_session_id,
            options=options
            or ChatOptions(
                model_profile=model_profile,
                temperature=temperature,
                use_tools=use_tools,
                thinking_mode=thinking_mode,
                thinking_budget=thinking_budget,
                reasoning_effort=reasoning_effort,
                verbosity=verbosity,
                enable_rag=enable_rag,
                enable_web=enable_web,
            ),
        )
        content = await self._pipeline.execute(ctx)
        await self._extract_memories_safe(ctx, content)
        return ChatResult(
            content=content,
            session_id=_session_id,
            model_profile=ctx.profile.id,
            model=ctx.profile.model,
        )

    # ------------------------------------------------------------------
    # Knowledge base
    # ------------------------------------------------------------------

    async def index_document(
        self,
        document_id: str,
        text: str,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Index a document for RAG retrieval."""
        self._require_initialized()
        return await self._rag_pipeline.index_document(
            document_id=document_id,
            text=text,
            metadata=metadata,
        )

    async def retrieve(self, query: str, top_k: int = 5) -> list[Any]:
        """Retrieve relevant chunks from the knowledge base."""
        self._require_initialized()
        return await self._rag_pipeline.retrieve_with_citations(query=query, top_k=top_k)

    # ------------------------------------------------------------------
    # Skills
    # ------------------------------------------------------------------

    async def list_skills(self) -> list[dict[str, Any]]:
        """Return all skills sorted by sort_order."""
        self._require_initialized()
        async with get_session(self.config.storage.db_url) as db:
            result = await db.execute(select(SkillRow).order_by(SkillRow.sort_order))
            rows = result.scalars().all()
        return [
            {
                "id": row.id,
                "name": row.name,
                "display_name": row.display_name or "",
                "description": row.description,
                "category": row.category,
                "order": row.sort_order,
                "is_builtin": row.is_builtin,
            }
            for row in rows
        ]

    # ------------------------------------------------------------------
    # Tools
    # ------------------------------------------------------------------

    def register_tool(
        self,
        name: str,
        func: Any,
        description: str,
        parameters: list[ToolParameter],
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Register a custom tool available during tool-loop calls."""
        self._require_initialized()
        self._tool_adapter.register_tool(
            name=name,
            func=func,
            description=description,
            parameters=parameters,
            metadata=metadata,
        )

    # ------------------------------------------------------------------
    # Workflow
    # ------------------------------------------------------------------

    @property
    def workflow(self) -> WorkflowClient:
        """Return the :class:`WorkflowClient` for DAG workflow execution."""
        return WorkflowClient(self)

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    async def clear_session(self, session_id: UUID) -> None:
        """Delete all memory for a session, including structured memories."""
        self._require_initialized()
        await self._memory.delete_session_memory(session_id)
        await self._memory_engine.delete_conversation_memories(session_id)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _extract_memories_safe(self, ctx: ChatContext, assistant_content: str) -> None:
        """Extract and store structured memories from a completed chat turn.

        Silently skips if the response is empty. Shields against cancellation so
        in-flight extraction is not interrupted when the caller exits early.
        """
        if not assistant_content.strip():
            return
        try:
            await asyncio.shield(
                self._memory_engine.extract_and_store(
                    session_id=ctx.session_id,
                    user_message=ctx.message,
                    assistant_content=assistant_content,
                    source_run_id=str(uuid4()),
                    llm_adapter=self._pipeline.get_llm_adapter(ctx.profile),
                    model=ctx.profile.model,
                    session_only=True,
                )
            )
        except asyncio.CancelledError:
            logger.warning("结构化记忆提取被取消，session_id=%s", ctx.session_id)
        except Exception:
            logger.warning("结构化记忆提取失败，session_id=%s", ctx.session_id)


# ------------------------------------------------------------------
# WorkflowClient
# ------------------------------------------------------------------


class WorkflowClient:
    """SDK facade for DAG workflow execution.

    Usage::

        async with AstraCoreClient() as client:
            from astracore.modules.agent.domain import AgentTask, AgentRole
            from uuid import uuid4

            t1 = AgentTask(role=AgentRole.EXECUTOR, description="Step 1: research X")
            t2 = AgentTask(role=AgentRole.EXECUTOR, description="Step 2: summarise X",
                           depends_on=[t1.task_id])
            state = await client.workflow.run("my-pipeline", [t1, t2])
            print(state.result)
    """

    def __init__(self, client: AstraCoreClient) -> None:
        self._client = client

    async def run(
        self,
        name: str,
        tasks: list[AgentTask],
        *,
        session_id: UUID | None = None,
        use_tools: bool = False,
        model_profile: str | None = None,
        temperature: float | None = None,
        enable_rag: bool = False,
    ) -> WorkflowState:
        """Execute a DAG workflow.

        Each task is run via the chat pipeline sharing a single session so that
        context from earlier tasks is visible to later ones through conversation
        memory. Pass ``session_id`` to resume a previous workflow session.

        Per-task overrides can be stored in ``task.metadata``:
        - ``"use_tools"`` (bool)
        - ``"model_profile"`` (str)
        - ``"temperature"`` (float)
        """
        workflow_session_id = session_id or uuid4()
        orchestrator = NativeWorkflowOrchestrator()
        pipeline = self._client._pipeline

        async def executor(task: AgentTask, task_results: dict[str, str]) -> str:
            message = task.description
            if task_results:
                lines = [f"[Task {tid}]\n{result}" for tid, result in task_results.items()]
                message = message + "\n\n---\n[已完成任务的结果]\n" + "\n\n".join(lines)

            ctx = await pipeline.prepare(
                message=message,
                session_id=workflow_session_id,
                options=ChatOptions(
                    use_tools=bool(task.metadata.get("use_tools", use_tools)),
                    model_profile=task.metadata.get("model_profile") or model_profile,
                    temperature=task.metadata.get("temperature") or temperature,
                    enable_rag=bool(task.metadata.get("enable_rag", enable_rag)),
                ),
            )
            return await pipeline.execute(ctx)

        wf = await orchestrator.create_workflow(name=name, tasks=tasks)
        return await orchestrator.execute_workflow(wf.workflow_id, executor=executor)


# ------------------------------------------------------------------
# Enum coercion helpers (shared by MemoryClient)
# ------------------------------------------------------------------


def _coerce_scope(value: MemoryScope | str) -> MemoryScope:
    return value if isinstance(value, MemoryScope) else MemoryScope(value)


def _coerce_type(value: MemoryType | str) -> MemoryType:
    return value if isinstance(value, MemoryType) else MemoryType(value)


def _coerce_status(value: MemoryStatus | str) -> MemoryStatus:
    return value if isinstance(value, MemoryStatus) else MemoryStatus(value)


class MemoryClient:
    """SDK facade for structured memory CRUD."""

    def __init__(self, engine: MemoryEngine) -> None:
        self._engine = engine

    async def list(
        self,
        *,
        scope: MemoryScope | str | None = None,
        memory_type: MemoryType | str | None = None,
        session_id: UUID | None = None,
        project_id: str | None = None,
        query: str | None = None,
        status: MemoryStatus | str = MemoryStatus.ACTIVE,
        limit: int = 100,
    ) -> list[StructuredMemory]:
        """List structured memories."""
        return await self._engine.list_memories(
            scope=_coerce_scope(scope) if scope is not None else None,
            memory_type=_coerce_type(memory_type) if memory_type is not None else None,
            session_id=session_id,
            project_id=project_id,
            query=query,
            status=_coerce_status(status),
            limit=limit,
        )

    async def get(self, memory_id: str) -> StructuredMemory | None:
        """Get one structured memory by id."""
        return await self._engine.get_memory(memory_id)

    async def create(
        self,
        *,
        scope: MemoryScope | str,
        memory_type: MemoryType | str,
        content: str,
        subject: str = "",
        summary: str = "",
        session_id: UUID | None = None,
        conversation_id: UUID | None = None,
        project_id: str | None = None,
        source_run_id: str | None = None,
        importance: int = 3,
        confidence: float = 1.0,
        locked: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> StructuredMemory:
        """Create a structured memory."""
        return await self._engine.create_memory(
            scope=_coerce_scope(scope),
            memory_type=_coerce_type(memory_type),
            content=content,
            subject=subject,
            summary=summary,
            session_id=session_id,
            conversation_id=conversation_id,
            project_id=project_id,
            source_run_id=source_run_id,
            importance=importance,
            confidence=confidence,
            locked=locked,
            metadata=metadata,
        )

    async def update(
        self,
        memory_id: str,
        *,
        scope: MemoryScope | str | None = None,
        memory_type: MemoryType | str | None = None,
        content: str | None = None,
        subject: str | None = None,
        summary: str | None = None,
        session_id: UUID | None = None,
        conversation_id: UUID | None = None,
        project_id: str | None = None,
        source_run_id: str | None = None,
        importance: int | None = None,
        confidence: float | None = None,
        status: MemoryStatus | str | None = None,
        locked: bool | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> StructuredMemory:
        """Update a structured memory by id.

        Only fields explicitly provided (non-None) are changed; omitted fields
        retain their current values.
        """
        memory = await self._engine.get_memory(memory_id)
        if memory is None:
            raise ValueError(f"Memory not found: {memory_id}")

        if scope is not None:
            memory.scope = _coerce_scope(scope)
        if memory_type is not None:
            memory.type = _coerce_type(memory_type)
        if status is not None:
            memory.status = _coerce_status(status)
        if content is not None:
            memory.content = content
        if subject is not None:
            memory.subject = subject
        if summary is not None:
            memory.summary = summary
        if session_id is not None:
            memory.session_id = session_id
        if conversation_id is not None:
            memory.conversation_id = conversation_id
        if project_id is not None:
            memory.project_id = project_id
        if source_run_id is not None:
            memory.source_run_id = source_run_id
        if importance is not None:
            memory.importance = importance
        if confidence is not None:
            memory.confidence = confidence
        if locked is not None:
            memory.locked = locked
        if metadata is not None:
            memory.metadata = metadata

        return await self._engine.update_memory(memory)

    async def delete(self, memory_id: str) -> None:
        """Delete one structured memory."""
        await self._engine.delete_memory(memory_id)

    async def delete_session(self, session_id: UUID) -> int:
        """Delete session-scoped structured memories for a session."""
        return await self._engine.delete_session_memories(session_id)

    async def delete_conversation(self, conversation_id: UUID) -> int:
        """Delete all structured memories linked to a conversation.

        This is a superset of :meth:`delete_session` — it removes session-scoped
        memories **and** any other memories that carry this ``conversation_id``.
        """
        return await self._engine.delete_conversation_memories(conversation_id)


class ProjectClient:
    """SDK facade for project memory boundaries and conversation bindings."""

    def __init__(self, engine: MemoryEngine) -> None:
        self._engine = engine

    async def list_all(self) -> list[Project]:
        """List all projects."""
        return await self._engine.list_projects()

    async def get(self, project_id: str) -> Project | None:
        """Get one project by id."""
        return await self._engine.get_project(project_id)

    async def create(
        self,
        *,
        name: str,
        root_paths: list[str] | None = None,
        description: str = "",
    ) -> Project:
        """Create a project boundary for project-scoped memory."""
        return await self._engine.create_project(
            name=name,
            root_paths=root_paths,
            description=description,
        )

    async def bind_conversation(
        self,
        *,
        conversation_id: UUID,
        project_id: str,
        locked: bool = False,
        source: str = "sdk",
    ) -> ConversationProjectBinding:
        """Bind a conversation/session id to a project."""
        return await self._engine.bind_conversation(
            conversation_id=conversation_id,
            project_id=project_id,
            locked=locked,
            source=source,
        )

    async def delete(self, project_id: str) -> bool:
        """Delete a project along with its memories and conversation bindings.

        Returns True if the project existed and was deleted, False if not found.
        Deletion cascades at the database level — all project-scoped memories and
        any conversation bindings for this project are removed automatically.
        """
        return await self._engine.delete_project(project_id)

    async def get_conversation_binding(
        self,
        conversation_id: UUID,
    ) -> ConversationProjectBinding | None:
        """Return the project binding for a conversation/session id."""
        return await self._engine.get_conversation_binding(conversation_id)
