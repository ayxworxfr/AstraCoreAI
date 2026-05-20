"""AstraCore SDK client — embeddable async client with full feature parity."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import select

from astracore.adapters.db.models import SkillRow
from astracore.adapters.db.session import get_session, init_db
from astracore.adapters.memory.hybrid import HybridMemoryAdapter
from astracore.adapters.memory.store import SQLMemoryStore
from astracore.adapters.retrieval.chroma import ChromaRetrieverAdapter
from astracore.core.application.memory_engine import MemoryEngine
from astracore.core.application.rag import RAGPipeline
from astracore.core.domain.chat_context import ChatContext
from astracore.core.domain.memory import (
    ConversationProjectBinding,
    MemoryScope,
    MemoryStatus,
    MemoryType,
    Project,
    StructuredMemory,
)
from astracore.core.ports.llm import StreamEvent, StreamEventType
from astracore.core.ports.tool import ToolAdapter, ToolParameter
from astracore.runtime.observability.logger import get_logger
from astracore.runtime.policy.engine import PolicyEngine
from astracore.sdk.config import AstraCoreConfig
from astracore.service.chat_pipeline import ChatPipeline
from astracore.service.seeds import seed_builtin_skills
from astracore.service.skill_router import SkillRouter

logger = get_logger(__name__)


@dataclass
class ChatResult:
    """Result of a non-streaming chat call."""

    content: str
    session_id: UUID
    model_profile: str
    model: str
    anchor_skill: str | None = None
    routed_skills: tuple[str, ...] = ()


class Conversation:
    """Multi-turn conversation facade.

    Maintains session state across turns and stores per-conversation defaults
    so callers do not need to pass ``session_id`` or repeated options on every call.

    Create via :meth:`AstraCoreClient.conversation` — do not instantiate directly::

        async with AstraCoreClient() as client:
            conv = client.conversation(use_tools=True)
            result = await conv.send("你好")
            async for chunk in conv.stream("继续"):
                ...
    """

    def __init__(
        self,
        client: AstraCoreClient,
        *,
        skill_id: UUID | None = None,
        use_tools: bool = False,
        enable_rag: bool = False,
        enable_web: bool = False,
        enable_thinking: bool = False,
        thinking_budget: int = 8000,
        model_profile: str | None = None,
        temperature: float | None = None,
        disable_skill: bool = False,
        session_id: UUID | None = None,
        project_id: str | None = None,
        project_locked: bool = False,
        project_source: str = "sdk",
    ) -> None:
        self._client = client
        self._session_id = session_id or uuid4()
        self._project_id = project_id
        self._project_locked = project_locked
        self._project_source = project_source
        self._project_bound = False
        self._defaults: dict[str, Any] = {
            "skill_id": skill_id,
            "use_tools": use_tools,
            "enable_rag": enable_rag,
            "enable_web": enable_web,
            "enable_thinking": enable_thinking,
            "thinking_budget": thinking_budget,
            "model_profile": model_profile,
            "temperature": temperature,
            "disable_skill": disable_skill,
        }

    @property
    def session_id(self) -> UUID:
        """Stable session identifier for this conversation."""
        return self._session_id

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

    async def send(self, message: str, **overrides: Any) -> ChatResult:
        """Send a message and return the complete response.

        Keyword overrides temporarily replace the conversation defaults for this turn only.
        """
        await self._ensure_project_binding()
        return await self._client.chat(
            message,
            session_id=self._session_id,
            **{**self._defaults, **overrides},
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
            **{**self._defaults, **overrides},
        ):
            if event.event_type == StreamEventType.TEXT_DELTA and event.content:
                yield event.content

    async def stream_events(self, message: str, **overrides: Any) -> AsyncIterator[StreamEvent]:
        """Stream all raw :class:`StreamEvent` objects for this turn.

        Use this when you need access to tool-call, thinking, or skill-match events.
        """
        await self._ensure_project_binding()
        async for event in self._client.chat_stream(
            message,
            session_id=self._session_id,
            **{**self._defaults, **overrides},
        ):
            yield event

    async def clear(self) -> None:
        """Delete all memory for this conversation."""
        await self._client.clear_session(self._session_id)


class AstraCoreClient:
    """Embeddable AstraCore async client with full feature parity to the HTTP service.

    Must be used as an async context manager::

        async with AstraCoreClient() as client:
            conv = client.conversation()
            result = await conv.send("你好")

    Config is loaded from ``config/config.yaml`` by default (same source as the HTTP service).
    MCP tool adapters require async setup and are only available inside the context manager.
    """

    def __init__(self, config: AstraCoreConfig | None = None) -> None:
        self.config = config or AstraCoreConfig()
        cfg = self.config

        memory = HybridMemoryAdapter(
            redis_url=cfg.memory.redis_url,
            db_url=cfg.memory.db_url,
        )
        rag_pipeline = RAGPipeline(
            retriever=ChromaRetrieverAdapter(
                collection_name=cfg.retrieval.collection_name,
                persist_directory=cfg.retrieval.persist_directory,
            )
        )
        self._memory = memory
        self._memory_engine = MemoryEngine(SQLMemoryStore(cfg.memory.db_url))
        self.memory = MemoryClient(self._memory_engine)
        self.projects = ProjectClient(self._memory_engine)
        self._rag_pipeline = rag_pipeline
        self._tool_adapter: ToolAdapter = self._new_native_adapter()
        self._mcp_adapter: Any = None
        self._skill_router: SkillRouter | None = (
            SkillRouter(config=cfg, db_url=cfg.memory.db_url)
            if cfg.skill_routing.mode != "off"
            else None
        )
        self._pipeline = self._build_pipeline()

    def _new_native_adapter(self) -> ToolAdapter:
        from astracore.service.builtin_tools import build_tool_adapter  # noqa: PLC0415

        return build_tool_adapter()

    def _build_pipeline(self) -> ChatPipeline:
        cfg = self.config
        return ChatPipeline(
            config=cfg,
            memory=self._memory,
            rag_pipeline=self._rag_pipeline,
            policy=PolicyEngine(),
            tool_adapter=self._tool_adapter,
            skill_router=self._skill_router,
            memory_engine=self._memory_engine,
        )

    # ------------------------------------------------------------------
    # Async context manager lifecycle
    # ------------------------------------------------------------------

    async def __aenter__(self) -> AstraCoreClient:
        await self._start()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self._stop()

    async def _start(self) -> None:
        await init_db(self.config.memory.db_url)
        try:
            await seed_builtin_skills(
                self.config.memory.db_url, extra_skill_dirs=self.config.skills.extra_dirs
            )
        except Exception:
            logger.warning("内置 Skill 种子写入失败，继续启动")

        if self._skill_router is not None:
            try:
                await self._skill_router.precompute()
            except Exception:
                logger.warning("SkillRouter precompute 失败，继续启动")

        if self.config.mcp.servers:
            try:
                from astracore.adapters.tools.composite import CompositeToolAdapter  # noqa: PLC0415
                from astracore.adapters.tools.mcp import (  # noqa: PLC0415
                    MCPToolAdapter,
                    build_server_configs,
                )

                mcp_configs = build_server_configs(self.config.mcp.servers)
                self._mcp_adapter = MCPToolAdapter(mcp_configs)
                await asyncio.wait_for(self._mcp_adapter.start(), timeout=30)
                self._tool_adapter = CompositeToolAdapter(
                    [self._new_native_adapter(), self._mcp_adapter]
                )
                logger.info("MCP tool adapter started with %d server(s)", len(mcp_configs))
                self._pipeline = self._build_pipeline()
            except Exception:
                logger.warning("MCP 适配器启动失败，回退到内置工具")
                self._mcp_adapter = None

    async def _stop(self) -> None:
        if self._mcp_adapter is not None:
            try:
                await self._mcp_adapter.stop()
            except Exception:
                logger.warning("MCP 适配器停止时出错")

    # ------------------------------------------------------------------
    # Conversation facade
    # ------------------------------------------------------------------

    def conversation(
        self,
        *,
        skill_id: UUID | None = None,
        use_tools: bool = False,
        enable_rag: bool = False,
        enable_web: bool = False,
        enable_thinking: bool = False,
        thinking_budget: int = 8000,
        model_profile: str | None = None,
        temperature: float | None = None,
        disable_skill: bool = False,
        session_id: UUID | None = None,
        project_id: str | None = None,
        project_locked: bool = False,
        project_source: str = "sdk",
    ) -> Conversation:
        """Create a :class:`Conversation` for multi-turn chat.

        All parameters become per-conversation defaults and can be overridden
        per-turn via keyword arguments to :meth:`Conversation.send` or
        :meth:`Conversation.stream`.

        Pass ``session_id`` to resume an existing session; omit to start a new one.
        """
        return Conversation(
            self,
            skill_id=skill_id,
            use_tools=use_tools,
            enable_rag=enable_rag,
            enable_web=enable_web,
            enable_thinking=enable_thinking,
            thinking_budget=thinking_budget,
            model_profile=model_profile,
            temperature=temperature,
            disable_skill=disable_skill,
            session_id=session_id,
            project_id=project_id,
            project_locked=project_locked,
            project_source=project_source,
        )

    # ------------------------------------------------------------------
    # Low-level API (single-turn, session_id managed by caller)
    # ------------------------------------------------------------------

    async def chat_stream(
        self,
        message: str,
        *,
        session_id: UUID | None = None,
        model_profile: str | None = None,
        temperature: float | None = None,
        use_tools: bool = False,
        enable_thinking: bool = False,
        thinking_budget: int = 8000,
        enable_rag: bool = False,
        enable_web: bool = False,
        skill_id: UUID | None = None,
        disable_skill: bool = False,
    ) -> AsyncIterator[StreamEvent]:
        """Stream a single-turn chat response as raw :class:`StreamEvent` objects.

        For multi-turn conversations prefer :meth:`conversation` which manages
        ``session_id`` automatically.
        """
        _session_id = session_id or uuid4()
        ctx = await self._pipeline.prepare(
            message=message,
            session_id=_session_id,
            model_profile=model_profile,
            temperature=temperature,
            use_tools=use_tools,
            enable_thinking=enable_thinking,
            thinking_budget=thinking_budget,
            enable_rag=enable_rag,
            enable_web=enable_web,
            skill_id=skill_id,
            disable_skill=disable_skill,
        )
        if ctx.anchor_skill or ctx.routed_skills:
            yield StreamEvent(
                event_type=StreamEventType.SKILL_MATCH,
                metadata={"anchor": ctx.anchor_skill, "routed": list(ctx.routed_skills)},
            )
        accumulated = ""
        async for event in self._pipeline.stream(ctx):
            if event.event_type == StreamEventType.TEXT_DELTA and event.content:
                accumulated += event.content
            yield event
        # Stream completed naturally — extract memories from this turn.
        await self._extract_memories_safe(ctx, accumulated)

    async def chat(
        self,
        message: str,
        *,
        session_id: UUID | None = None,
        model_profile: str | None = None,
        temperature: float | None = None,
        use_tools: bool = False,
        enable_thinking: bool = False,
        thinking_budget: int = 8000,
        enable_rag: bool = False,
        enable_web: bool = False,
        skill_id: UUID | None = None,
        disable_skill: bool = False,
    ) -> ChatResult:
        """Send a single-turn message and return the complete response.

        For multi-turn conversations prefer :meth:`conversation` which manages
        ``session_id`` automatically.
        """
        _session_id = session_id or uuid4()
        ctx = await self._pipeline.prepare(
            message=message,
            session_id=_session_id,
            model_profile=model_profile,
            temperature=temperature,
            use_tools=use_tools,
            enable_thinking=enable_thinking,
            thinking_budget=thinking_budget,
            enable_rag=enable_rag,
            enable_web=enable_web,
            skill_id=skill_id,
            disable_skill=disable_skill,
        )
        content = await self._pipeline.execute(ctx)
        await self._extract_memories_safe(ctx, content)
        return ChatResult(
            content=content,
            session_id=_session_id,
            model_profile=ctx.profile.id,
            model=ctx.profile.model,
            anchor_skill=ctx.anchor_skill,
            routed_skills=ctx.routed_skills,
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
        return await self._rag_pipeline.index_document(
            document_id=document_id,
            text=text,
            metadata=metadata,
        )

    async def retrieve(self, query: str, top_k: int = 5) -> list[Any]:
        """Retrieve relevant chunks from the knowledge base."""
        return await self._rag_pipeline.retrieve_with_citations(query=query, top_k=top_k)

    # ------------------------------------------------------------------
    # Skills
    # ------------------------------------------------------------------

    async def list_skills(self) -> list[dict[str, Any]]:
        """Return all skills sorted by sort_order."""
        async with get_session(self.config.memory.db_url) as db:
            result = await db.execute(select(SkillRow).order_by(SkillRow.sort_order))
            rows = result.scalars().all()
        return [
            {
                "id": row.id,
                "name": row.name,
                "description": row.description,
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
    ) -> None:
        """Register a custom tool available during tool-loop calls."""
        register = getattr(self._tool_adapter, "register_tool", None)
        if not callable(register):
            raise TypeError("Current tool adapter does not support dynamic registration")
        cast(Any, register)(
            name=name,
            func=func,
            description=description,
            parameters=parameters,
        )

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    async def clear_session(self, session_id: UUID) -> None:
        """Delete all memory for a session, including structured memories."""
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
                    llm_adapter=self._pipeline._get_llm_adapter(ctx.profile),
                    model=ctx.profile.model,
                )
            )
        except asyncio.CancelledError:
            logger.warning("结构化记忆提取被取消，session_id=%s", ctx.session_id)
        except Exception:
            logger.warning("结构化记忆提取失败，session_id=%s", ctx.session_id)


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

    async def list(self) -> list[Project]:
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
