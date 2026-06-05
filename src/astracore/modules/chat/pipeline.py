"""Chat execution pipeline: Command + Pipeline pattern.

``prepare()`` — single batch of DB queries + all business-logic decisions, returns
an immutable ``ChatContext``.
``stream()``  — pure execution, zero conditional branching on request fields.
``execute()`` — convenience wrapper that collects all text from ``stream()``.

Both the HTTP service and the embedded SDK use this module; HTTP-specific concerns
(SSE broadcasting, run tracking) remain in the API layer; SDK-specific concerns
(async context manager, MCP lifecycle) remain in the SDK client.
"""

import asyncio
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

from sqlalchemy import select

from astracore.infrastructure.db.models import SkillRow, UserSettingsRow
from astracore.infrastructure.db.session import get_session
from astracore.infrastructure.llm.anthropic import AnthropicAdapter
from astracore.infrastructure.llm.openai import OpenAIAdapter
from astracore.infrastructure.memory.hybrid import HybridMemoryAdapter
from astracore.infrastructure.memory.store import SQLMemoryStore
from astracore.modules.chat.application.tool_loop import ToolLoopUseCase
from astracore.modules.chat.domain.chat_context import ChatContext
from astracore.modules.chat.domain.chat_options import ChatOptions
from astracore.modules.chat.domain.message import Message, MessageRole
from astracore.modules.chat.domain.session import SessionState
from astracore.modules.memory.application.engine import MemoryEngine
from astracore.modules.rag.application.pipeline import RAGPipeline
from astracore.modules.skills.prompt_utils import build_identity_layer, build_skill_manifest
from astracore.modules.tools.ports.tool import ToolAdapter
from astracore.sdk.config import AstraCoreConfig, LLMProfileConfig
from astracore.shared.observability.hooks import HookRegistry
from astracore.shared.observability.logger import get_logger
from astracore.shared.policy.engine import PolicyEngine
from astracore.shared.ports.llm import LLMAdapter, StreamEvent, StreamEventType

logger = get_logger(__name__)

_ANTHROPIC_BLOCKS_KEY = "anthropic_content_blocks"


# ------------------------------------------------------------------
# Module-level pure helpers (no I/O, easily testable in isolation)
# ------------------------------------------------------------------


def _trim_history(messages: list[Message], limit: int) -> list[Message]:
    """Keep only the most recent *limit* messages; 0 means unlimited."""
    if limit > 0 and len(messages) > limit:
        return messages[-limit:]
    return messages


def _strip_dangling_tool_calls(messages: list[Message]) -> list[Message]:
    """Remove trailing ASSISTANT messages that have tool_calls but no following results."""
    msgs = list(messages)
    while msgs and msgs[-1].role == MessageRole.ASSISTANT and msgs[-1].tool_calls:
        msgs.pop()
    return msgs


def _prepare_for_save(messages: list[Message]) -> list[Message]:
    """Drop SYSTEM / tool-loop-internal messages before persisting visible chat history."""
    msgs = [
        m
        for m in messages
        if m.role != MessageRole.SYSTEM
        and m.role != MessageRole.TOOL
        and not (m.role == MessageRole.ASSISTANT and m.tool_calls)
    ]
    return _strip_dangling_tool_calls(msgs)


# ------------------------------------------------------------------
# ChatPipeline
# ------------------------------------------------------------------


class ChatPipeline:
    """Chat execution pipeline (Command + Pipeline pattern).

    ``prepare()`` resolves all parameters into an immutable ``ChatContext`` with a
    single batch of DB calls.  ``stream()`` consumes the context as pure data —
    zero extra DB queries, zero conditional business logic.

    The ``tool_adapter`` passed to the constructor is the default.  ``prepare()``
    accepts a per-call override so the HTTP layer can pass an ``app.state`` adapter
    (which may include MCP tools) while the SDK passes its own managed adapter.
    """

    def __init__(
        self,
        config: AstraCoreConfig,
        memory: HybridMemoryAdapter,
        rag_pipeline: RAGPipeline,
        policy: PolicyEngine,
        tool_adapter: ToolAdapter,
        memory_engine: MemoryEngine | None = None,
        hooks: HookRegistry | None = None,
    ) -> None:
        self._config = config
        self._memory = memory
        self._rag_pipeline = rag_pipeline
        self._policy = policy
        self._default_tool_adapter = tool_adapter
        self._memory_engine = memory_engine or MemoryEngine(SQLMemoryStore(config.memory.db_url))
        self._hooks = hooks
        self._llm_adapters: dict[str, LLMAdapter] = {}

    # ------------------------------------------------------------------
    # LLM / tool-loop factories (cached by profile id)
    # ------------------------------------------------------------------

    def get_llm_adapter(self, profile: LLMProfileConfig) -> LLMAdapter:
        if profile.id not in self._llm_adapters:
            if profile.protocol == "anthropic":
                self._llm_adapters[profile.id] = AnthropicAdapter(
                    api_key=profile.api_key,
                    default_model=profile.model,
                    base_url=profile.base_url,
                    extra_headers=profile.extra_headers,
                    max_tokens=profile.max_tokens,
                    supports_temperature=profile.capabilities.temperature,
                    use_anthropic_blocks=profile.capabilities.anthropic_blocks,
                    structured_output_via_tools=profile.capabilities.structured_output_via_tools,
                )
            else:
                self._llm_adapters[profile.id] = OpenAIAdapter(
                    api_key=profile.api_key,
                    default_model=profile.model,
                    base_url=profile.base_url,
                    extra_headers=profile.extra_headers,
                    protocol=profile.protocol,
                    max_tokens=profile.max_tokens,
                )
        return self._llm_adapters[profile.id]

    def _make_tool_loop(
        self,
        profile: LLMProfileConfig,
        tool_adapter: ToolAdapter,
        allowed_tools: frozenset[str] = frozenset(),
        session_id: UUID | None = None,
    ) -> ToolLoopUseCase:
        cfg = self._config.agent
        extra_context: dict[str, Any] = {}
        if allowed_tools:
            extra_context["allowed_tools"] = allowed_tools
        extra_context["tool_adapter"] = tool_adapter
        if session_id is not None:
            extra_context["session_id"] = str(session_id)
            extra_context["llm_adapter"] = self.get_llm_adapter(profile)
            extra_context["model"] = profile.model
        return ToolLoopUseCase(
            llm_adapter=self.get_llm_adapter(profile),
            tool_adapter=tool_adapter,
            policy_engine=self._policy,
            max_iterations=cfg.max_tool_iterations,
            max_tool_result_chars=cfg.max_tool_result_chars,
            tool_timeout_s=cfg.tool_timeout_s,
            profile_id=profile.id,
            extra_context=extra_context or None,
            hooks=self._hooks,
        )

    # ------------------------------------------------------------------
    # DB helpers
    # ------------------------------------------------------------------

    async def _get_setting(self, key: str) -> str:
        async with get_session(self._config.memory.db_url) as db:
            row = await db.get(UserSettingsRow, key)
            return row.value if row else ""

    async def _load_all_skills(self) -> list[SkillRow]:
        async with get_session(self._config.memory.db_url) as db:
            result = await db.execute(
                select(SkillRow).order_by(SkillRow.is_builtin.desc(), SkillRow.sort_order)
            )
            return list(result.scalars().all())

    # ------------------------------------------------------------------
    # Prompt composition helpers
    # ------------------------------------------------------------------

    async def _build_rag_context(self, query: str) -> str | None:
        try:
            top_k = int(await self._get_setting("rag_top_k") or "4")
            chunks = await self._rag_pipeline.retrieve_with_citations(query=query, top_k=top_k)
            if not chunks:
                return None
            parts = [
                f"[来源: {c.citation.title or c.citation.source_id}]\n{c.content}" for c in chunks
            ]
            context = "\n\n---\n\n".join(parts)
            return (
                "以下是从知识库检索到的相关内容，请优先基于这些内容回答用户问题，"
                "并在回答中注明引用的来源：\n\n" + context
            )
        except Exception:
            return None

    async def _build_system_prompt(
        self,
        session_id: UUID,
        enable_rag: bool,
        message: str,
    ) -> str | None:
        """Compose system prompt: identity layer + skill manifest + memory + RAG context."""
        ai_name = await self._get_setting("ai_name") or "小卡"
        owner_name = await self._get_setting("owner_name")
        global_instruction = await self._get_setting("global_instruction")

        identity = build_identity_layer(ai_name, owner_name, global_instruction)

        all_skills = await self._load_all_skills()
        manifest = build_skill_manifest(all_skills)

        parts: list[str] = [identity]
        if manifest:
            parts.append(manifest)

        try:
            memory_context = await self._memory_engine.build_memory_context(
                session_id=session_id,
                message=message,
            )
            if memory_context:
                parts.append(memory_context)
        except Exception:
            logger.exception("Memory context 构建失败，跳过本轮记忆注入")

        if enable_rag:
            rag_ctx = await self._build_rag_context(message)
            if rag_ctx:
                parts.append(rag_ctx)

        return "\n\n---\n\n".join(parts) or None

    async def _resolve_temperature(
        self, temperature: float | None, profile: LLMProfileConfig
    ) -> float:
        if temperature is not None:
            return temperature
        saved = await self._get_setting("temperature")
        return float(saved) if saved else profile.temperature

    # ------------------------------------------------------------------
    # prepare: all decisions resolved once, returned as frozen ChatContext
    # ------------------------------------------------------------------

    async def prepare(
        self,
        message: str,
        session_id: UUID,
        options: ChatOptions | None = None,
        *,
        tool_adapter: ToolAdapter | None = None,
    ) -> ChatContext:
        """Resolve all options and return an immutable ``ChatContext``.

        This is the **only** method that issues DB queries for business logic.
        ``stream()`` and ``execute()`` consume the context as pure data.
        """
        opts = options or ChatOptions()
        profile = self._config.llm.get_profile(opts.model_profile)
        if (opts.use_tools or opts.enable_web) and not profile.capabilities.tools:
            raise ValueError(f"LLM profile '{profile.id}' does not support tool calling")

        # 1. Compose system prompt (identity + manifest + memory + RAG in one pass)
        system_prompt = await self._build_system_prompt(
            session_id=session_id,
            enable_rag=opts.enable_rag,
            message=message,
        )

        # 2. Resolve temperature and context window size
        resolved_temp = await self._resolve_temperature(opts.temperature, profile)
        context_max = int(await self._get_setting("context_max_messages") or "20")

        # 3. Build LLM kwargs
        llm_kwargs: dict[str, Any] = {}
        if opts.enable_thinking and profile.capabilities.thinking:
            llm_kwargs["enable_thinking"] = True
            llm_kwargs["thinking_budget"] = opts.thinking_budget

        # 4. Resolve tool adapter: per-call override takes precedence
        effective_adapter: ToolAdapter = (
            tool_adapter if tool_adapter is not None else self._default_tool_adapter
        )

        # 5. Determine execution mode and allowed tools
        # Tool loop always active: skill tools (load_skill etc.) are available by default.
        # use_tools or enable_web additionally expose those specific tools.
        needs_tool_loop = True
        all_tools = frozenset(d.name for d in effective_adapter.get_definitions())
        excluded: set[str] = set()
        if not opts.enable_rag:
            excluded.add("search_knowledge_base")
        if not opts.enable_web:
            excluded.add("web_search")
        allowed_tools = all_tools - excluded

        return ChatContext(
            session_id=session_id,
            message=message,
            profile=profile,
            temperature=resolved_temp,
            system_prompt=system_prompt,
            context_max_messages=context_max,
            mode="tool_loop" if needs_tool_loop else "normal",
            llm_kwargs=llm_kwargs,
            tool_adapter=effective_adapter,
            allowed_tools=allowed_tools,
        )

    # ------------------------------------------------------------------
    # stream: pure execution, consumes ChatContext
    # ------------------------------------------------------------------

    async def stream(self, ctx: ChatContext) -> AsyncIterator[StreamEvent]:
        """Stream a chat turn.  Requires a fully-resolved ``ChatContext`` from ``prepare()``."""
        stored = _trim_history(
            [
                m
                for m in await self._memory.load_short_term(ctx.session_id)
                if m.role != MessageRole.SYSTEM
            ],
            ctx.context_max_messages,
        )

        session = SessionState(session_id=ctx.session_id)

        if ctx.mode == "tool_loop":
            # tool_loop needs the system message inside the session so the tool loop can see it.
            initial: list[Message] = []
            if ctx.system_prompt:
                initial.append(Message(role=MessageRole.SYSTEM, content=ctx.system_prompt))
            initial.extend(stored)
            session.restore_messages(initial)
            session.add_message(Message(role=MessageRole.USER, content=ctx.message))
            async for event in self._stream_tool_loop(ctx, session):
                yield event
        else:
            # normal mode: system is prepended only at the LLM call site.
            session.restore_messages(stored)
            session.add_message(Message(role=MessageRole.USER, content=ctx.message))
            async for event in self._stream_normal(ctx, session):
                yield event

    async def _stream_normal(
        self, ctx: ChatContext, session: SessionState
    ) -> AsyncIterator[StreamEvent]:
        """Stream a single LLM call without tool execution."""
        llm_messages = session.get_messages()
        if ctx.system_prompt:
            llm_messages = [
                Message(role=MessageRole.SYSTEM, content=ctx.system_prompt)
            ] + llm_messages

        accumulated_content = ""
        assistant_metadata: dict[str, Any] = {}
        try:
            async for event in self.get_llm_adapter(ctx.profile).generate_stream(
                messages=llm_messages,
                temperature=ctx.temperature,
                **ctx.llm_kwargs,
            ):
                if event.event_type == StreamEventType.TEXT_DELTA and event.content:
                    accumulated_content += event.content
                elif event.event_type == StreamEventType.DONE:
                    raw_blocks = event.metadata.get(_ANTHROPIC_BLOCKS_KEY)
                    if isinstance(raw_blocks, list) and raw_blocks:
                        assistant_metadata[_ANTHROPIC_BLOCKS_KEY] = raw_blocks
                yield event
        finally:
            session.add_message(
                Message(
                    role=MessageRole.ASSISTANT,
                    content=accumulated_content,
                    metadata=assistant_metadata,
                )
            )
            await self._save_session_safe(ctx.session_id, session.get_messages())

    async def _stream_tool_loop(
        self, ctx: ChatContext, session: SessionState
    ) -> AsyncIterator[StreamEvent]:
        """Stream a multi-round tool-loop execution.

        Event sequence emitted:
          1. All events from the tool loop (ROUND_START, TEXT_DELTA, TOOL_CALL, etc.)
          2. If summary fallback is needed:
               StreamEvent(DONE, metadata={"source": "tool_loop"})   ← phase boundary
               TEXT_DELTA* from the summary call
          3. StreamEvent(DONE)   ← always emitted last

        The intermediate DONE with ``source="tool_loop"`` lets consumers (e.g. the
        HTTP SSE layer) distinguish tool-phase text from final assistant text without
        knowledge of tool-loop internals.
        """
        assert ctx.tool_adapter is not None, "tool_adapter must be set for tool_loop mode"
        tool_loop = self._make_tool_loop(
            ctx.profile,
            ctx.tool_adapter,
            allowed_tools=ctx.allowed_tools,
            session_id=ctx.session_id,
        )
        completed = False
        total_input_tokens = 0
        total_output_tokens = 0
        try:
            async for event in tool_loop.execute_stream_with_tools(
                session, allowed_tools=ctx.allowed_tools, **ctx.llm_kwargs
            ):
                if event.event_type == StreamEventType.DONE:
                    if event.metadata.get("source") == "tool_loop":
                        # Phase boundary from closing round: pass through so the API
                        # layer can reset in_tool_round before closing-round text arrives.
                        yield event
                    else:
                        # Accumulate usage; filter out intermediate DONE events.
                        _u = event.metadata.get("usage", {})
                        total_input_tokens += int(_u.get("input_tokens", 0))
                        total_output_tokens += int(_u.get("output_tokens", 0))
                    continue
                yield event
            completed = True
        finally:
            if not completed:
                await self._save_session_safe(ctx.session_id, session.get_messages())

        await self._save_session_safe(ctx.session_id, session.get_messages())
        yield StreamEvent(
            event_type=StreamEventType.DONE,
            metadata={
                "usage": {"input_tokens": total_input_tokens, "output_tokens": total_output_tokens}
            },
        )

    # ------------------------------------------------------------------
    # execute: convenience wrapper, collects all TEXT_DELTA from stream()
    # ------------------------------------------------------------------

    async def execute(self, ctx: ChatContext) -> str:
        """Execute a chat turn and return the complete assistant text."""
        parts: list[str] = []
        async for event in self.stream(ctx):
            if event.event_type == StreamEventType.TEXT_DELTA and event.content:
                parts.append(event.content)
        return "".join(parts)

    # ------------------------------------------------------------------
    # Session persistence
    # ------------------------------------------------------------------

    async def _save_session_safe(self, session_id: UUID, messages: list[Message]) -> None:
        """Persist session, shielding against cancellation during cleanup."""
        to_save = _prepare_for_save(messages)
        logger.info("保存会话: session_id=%s, messages=%d", session_id, len(to_save))
        try:
            await asyncio.shield(
                self._memory.save_short_term(
                    session_id=session_id,
                    messages=to_save,
                )
            )
        except asyncio.CancelledError:
            logger.warning("会话保存被取消，session_id=%s", session_id)
        except Exception:
            logger.exception("会话保存失败，session_id=%s", session_id)
