"""AstraCore SDK client — embeddable async client with full feature parity."""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select

from astracore.adapters.db.models import SkillRow
from astracore.adapters.db.session import get_session, init_db
from astracore.adapters.memory.hybrid import HybridMemoryAdapter
from astracore.adapters.retrieval.chroma import ChromaRetrieverAdapter
from astracore.core.application.rag import RAGPipeline
from astracore.core.ports.llm import StreamEvent, StreamEventType
from astracore.core.ports.tool import ToolAdapter, ToolParameter
from astracore.runtime.observability.logger import get_logger
from astracore.runtime.policy.engine import PolicyEngine
from astracore.sdk.config import AstraCoreConfig
from astracore.service.chat_orchestrator import ChatOrchestrator
from astracore.service.seeds import seed_builtin_skills

logger = get_logger(__name__)


@dataclass
class ChatResult:
    """Result of a non-streaming chat call."""

    content: str
    session_id: UUID
    model_profile: str
    model: str


class AstraCoreClient:
    """Embeddable AstraCore async client with full feature parity to the HTTP service.

    Must be used as an async context manager::

        async with AstraCoreClient() as client:
            result = await client.chat("你好")

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
        self._rag_pipeline = rag_pipeline
        self._tool_adapter: ToolAdapter = self._new_native_adapter()
        self._mcp_adapter: Any = None
        self._orchestrator = ChatOrchestrator(
            config=cfg,
            memory=memory,
            rag_pipeline=rag_pipeline,
            policy=PolicyEngine(),
        )

    def _new_native_adapter(self) -> ToolAdapter:
        from astracore.service.builtin_tools import build_tool_adapter  # noqa: PLC0415

        return build_tool_adapter()

    # ------------------------------------------------------------------
    # Async context manager lifecycle
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "AstraCoreClient":
        await self._start()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self._stop()

    async def _start(self) -> None:
        await init_db(self.config.memory.db_url)
        try:
            await seed_builtin_skills(self.config.memory.db_url)
        except Exception:
            logger.warning("内置 Skill 种子写入失败，继续启动")

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
    # Public API
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
        """Stream a chat response. Use ``async for event in client.chat_stream(...)``."""
        _session_id = session_id or uuid4()
        profile = self.config.llm.get_profile(model_profile)

        if (use_tools or enable_web) and not profile.capabilities.tools:
            raise ValueError(f"LLM profile '{profile.id}' does not support tool calling")

        inject_system = await self._orchestrator.build_system_prompt(
            skill_id, disable_skill, enable_rag, message
        )
        temperature_val = await self._orchestrator.resolve_temperature(temperature, profile)
        context_max = int(await self._orchestrator.get_setting("context_max_messages") or "20")

        llm_kwargs: dict[str, Any] = {}
        if enable_thinking and profile.capabilities.thinking:
            llm_kwargs["enable_thinking"] = True
            llm_kwargs["thinking_budget"] = thinking_budget

        if use_tools or enable_web:
            async for event in self._orchestrator.stream_with_tools(
                session_id=_session_id,
                message=message,
                profile=profile,
                tool_adapter=self._tool_adapter,
                inject_system=inject_system,
                temperature=temperature_val,
                context_max=context_max,
                enable_rag=enable_rag,
                enable_web=enable_web,
                llm_kwargs=llm_kwargs,
            ):
                yield event
        else:
            async for event in self._orchestrator.stream_normal(
                session_id=_session_id,
                message=message,
                profile=profile,
                inject_system=inject_system,
                temperature=temperature_val,
                context_max=context_max,
                llm_kwargs=llm_kwargs,
            ):
                yield event

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
        """Send a message and return the complete response."""
        _session_id = session_id or uuid4()
        profile = self.config.llm.get_profile(model_profile)
        content_parts: list[str] = []

        async for event in self.chat_stream(
            message,
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
        ):
            if event.event_type == StreamEventType.TEXT_DELTA and event.content:
                content_parts.append(event.content)

        return ChatResult(
            content="".join(content_parts),
            session_id=_session_id,
            model_profile=profile.id,
            model=profile.model,
        )

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

    def register_tool(
        self,
        name: str,
        func: Any,
        description: str,
        parameters: list[ToolParameter],
    ) -> None:
        """Register a custom tool available during tool-loop calls."""
        self._tool_adapter.register_tool(
            name=name,
            func=func,
            description=description,
            parameters=parameters,
        )

    async def clear_session(self, session_id: UUID) -> None:
        """Delete all memory for a session."""
        await self._memory.delete_session_memory(session_id)
