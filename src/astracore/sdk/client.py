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
from astracore.core.ports.llm import StreamEvent
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
        )

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
                # Rebuild pipeline with updated composite tool adapter.
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
        async for event in self._pipeline.stream(ctx):
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
        return ChatResult(
            content=content,
            session_id=_session_id,
            model_profile=ctx.profile.id,
            model=ctx.profile.model,
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
