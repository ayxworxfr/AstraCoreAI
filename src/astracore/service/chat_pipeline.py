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
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import select

from astracore.adapters.db.models import SkillReferenceRow, SkillRow, UserSettingsRow
from astracore.adapters.db.session import get_session
from astracore.adapters.llm.anthropic import AnthropicAdapter
from astracore.adapters.llm.openai import OpenAIAdapter
from astracore.adapters.memory.hybrid import HybridMemoryAdapter
from astracore.adapters.memory.store import SQLMemoryStore
from astracore.adapters.tools.composite import CompositeToolAdapter
from astracore.core.application.memory_engine import MemoryEngine
from astracore.core.application.rag import RAGPipeline
from astracore.core.application.tool_loop import ToolLoopUseCase
from astracore.core.domain.chat_context import ChatContext
from astracore.core.domain.message import Message, MessageRole
from astracore.core.domain.session import SessionState
from astracore.core.ports.llm import LLMAdapter, StreamEvent, StreamEventType
from astracore.core.ports.tool import ToolAdapter
from astracore.runtime.observability.logger import get_logger
from astracore.runtime.policy.engine import PolicyEngine
from astracore.sdk.config import AstraCoreConfig, LLMProfileConfig
from astracore.service.prompt_utils import render_skill_prompt
from astracore.service.skill_router import SkillRouter

logger = get_logger(__name__)

_ANTHROPIC_BLOCKS_KEY = "anthropic_content_blocks"


# ------------------------------------------------------------------
# Module-level pure helpers (no I/O, easily testable in isolation)
# ------------------------------------------------------------------


def _compose_skill_section(skills: list[SkillRow], ai_name: str, owner_name: str) -> str:
    """Build a system-prompt section for one or more matched skills.

    Primary skill (index 0): full rendered system_prompt.
    Secondary skills (index 1+): name + description only, appended as a brief
    capability list to keep token usage bounded.
    """
    primary = render_skill_prompt(skills[0].system_prompt, ai_name, owner_name)
    if len(skills) == 1:
        return primary
    sec_lines = [f"- **{s.name}**：{s.description}" for s in skills[1:]]
    secondary = "## 你同时具备以下辅助能力\n\n" + "\n".join(sec_lines)
    return primary + "\n\n---\n\n" + secondary


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


def _needs_summary_fallback(messages: list[Message]) -> bool:
    """Return True when the tool loop ended without producing visible assistant text."""
    visible = [m for m in messages if m.role != MessageRole.SYSTEM]
    if not visible:
        return False
    last = visible[-1]
    if last.role == MessageRole.TOOL and last.has_tool_results():
        return True
    return last.role == MessageRole.ASSISTANT and not last.content.strip()


def _build_summary_fallback_messages(
    messages: list[Message], *, hit_iteration_limit: bool
) -> list[Message]:
    """Construct a message list that instructs the LLM to summarise without tool calls."""
    prompt = (
        "你现在处于工具调用收尾阶段。请只基于已有对话和工具结果给出最终回答，"
        "不要继续调用工具，也不要继续规划下一步。"
        "如果信息不足，请明确说明已确认内容和仍然缺失的信息。"
    )
    if hit_iteration_limit:
        prompt = (
            "你已达到工具循环最大轮次，请停止继续探索，直接基于当前工具结果完成总结。" + prompt
        )
    copied = [m.model_copy(deep=True) for m in messages]
    if copied and copied[0].role == MessageRole.SYSTEM:
        copied[0] = copied[0].model_copy(
            update={"content": f"{copied[0].content}\n\n---\n\n{prompt}"}
        )
    else:
        copied.insert(0, Message(role=MessageRole.SYSTEM, content=prompt))
    return copied


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
        skill_router: SkillRouter | None = None,
        memory_engine: MemoryEngine | None = None,
    ) -> None:
        self._config = config
        self._memory = memory
        self._rag_pipeline = rag_pipeline
        self._policy = policy
        self._default_tool_adapter = tool_adapter
        self._skill_router = skill_router
        self._memory_engine = memory_engine or MemoryEngine(SQLMemoryStore(config.memory.db_url))
        self._llm_adapters: dict[str, LLMAdapter] = {}

    # ------------------------------------------------------------------
    # LLM / tool-loop factories (cached by profile id)
    # ------------------------------------------------------------------

    def _get_llm_adapter(self, profile: LLMProfileConfig) -> LLMAdapter:
        if profile.id not in self._llm_adapters:
            if profile.provider == "anthropic":
                self._llm_adapters[profile.id] = AnthropicAdapter(
                    api_key=profile.api_key,
                    default_model=profile.model,
                    base_url=profile.base_url,
                    extra_headers=profile.extra_headers,
                    max_tokens=profile.max_tokens,
                    supports_temperature=profile.capabilities.temperature,
                    use_anthropic_blocks=profile.capabilities.anthropic_blocks,
                )
            else:
                self._llm_adapters[profile.id] = OpenAIAdapter(
                    api_key=profile.api_key,
                    default_model=profile.model,
                    base_url=profile.base_url,
                    max_tokens=profile.max_tokens,
                )
        return self._llm_adapters[profile.id]

    def _make_tool_loop(
        self,
        profile: LLMProfileConfig,
        tool_adapter: ToolAdapter,
        anchor_id: str | None = None,
        allowed_tools: frozenset[str] = frozenset(),
    ) -> ToolLoopUseCase:
        cfg = self._config.agent
        extra_context: dict[str, Any] = {}
        if anchor_id is not None:
            extra_context["anchor_id"] = anchor_id
            extra_context["db_url"] = self._config.memory.db_url
        if allowed_tools:
            extra_context["allowed_tools"] = allowed_tools
        extra_context["tool_adapter"] = tool_adapter
        return ToolLoopUseCase(
            llm_adapter=self._get_llm_adapter(profile),
            tool_adapter=tool_adapter,
            policy_engine=self._policy,
            max_iterations=cfg.max_tool_iterations,
            max_tool_result_chars=cfg.max_tool_result_chars,
            tool_timeout_s=cfg.tool_timeout_s,
            profile_id=profile.id,
            extra_context=extra_context or None,
        )

    # ------------------------------------------------------------------
    # DB helpers
    # ------------------------------------------------------------------

    async def _get_setting(self, key: str) -> str:
        async with get_session(self._config.memory.db_url) as db:
            row = await db.get(UserSettingsRow, key)
            return row.value if row else ""

    async def _load_skill(self, skill_id: str) -> SkillRow | None:
        async with get_session(self._config.memory.db_url) as db:
            return await db.get(SkillRow, skill_id)

    async def _load_skill_references(self, skill_id: str) -> list[SkillReferenceRow]:
        async with get_session(self._config.memory.db_url) as db:
            result = await db.execute(
                select(SkillReferenceRow)
                .where(SkillReferenceRow.skill_id == skill_id)
                .order_by(SkillReferenceRow.sort_order)
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
                f"[来源: {c.citation.title or c.citation.source_id}]\n{c.content}"
                for c in chunks
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
        skill_id: UUID | None,
        disable_skill: bool,
        enable_rag: bool,
        message: str,
    ) -> tuple[str | None, str | None, list[str], bool, str | None]:
        """Compose the three-layer system prompt: skill → global instruction → RAG context.

        Returns ``(system_prompt, anchor_name, routed_names, skill_has_refs, anchor_id)``.
        - ``anchor_name``: the primary/default skill name, or None if no skill is active.
        - ``routed_names``: names of additional skills added automatically by routing.
        - ``skill_has_refs``: True when the anchor skill has attached reference documents.
        - ``anchor_id``: the resolved anchor skill's DB id (str), or None.
        """
        parts: list[str] = []
        anchor_name: str | None = None
        anchor_id: str | None = None
        routed_names: list[str] = []
        skill_has_refs = False

        if not disable_skill:
            ai_name = await self._get_setting("ai_name") or "小卡"
            owner_name = await self._get_setting("owner_name")

            anchor: SkillRow | None = None
            if skill_id is not None:
                anchor = await self._load_skill(str(skill_id))
            if anchor is None:
                default_id = await self._get_setting("default_skill_id")
                if default_id:
                    anchor = await self._load_skill(default_id)

            routed: list[SkillRow] = []
            if self._skill_router is not None:
                routed = await self._skill_router.route(message)
                if anchor:
                    routed = [s for s in routed if s.id != anchor.id]

            if anchor:
                anchor_name = anchor.name
                anchor_id = anchor.id
            routed_names = [s.name for s in routed]

            if anchor and anchor.system_prompt:
                refs = await self._load_skill_references(anchor.id)
                skill_has_refs = bool(refs)
                skill_section = _compose_skill_section([anchor, *routed], ai_name, owner_name)
                if refs:
                    toc_lines = [
                        f"- **{r.title}**：{r.description}"
                        if r.description
                        else f"- **{r.title}**"
                        for r in refs
                    ]
                    toc = (
                        "## 可用参考文档\n\n"
                        "以下参考文档可按需加载，使用 `get_skill_reference` 工具并传入标题即可获取内容：\n\n"
                        + "\n".join(toc_lines)
                    )
                    skill_section = skill_section + "\n\n---\n\n" + toc
                parts.append(skill_section)
            elif routed:
                parts.append(_compose_skill_section(routed, ai_name, owner_name))

        instruction = await self._get_setting("global_instruction")
        if instruction:
            parts.append(instruction)

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

        return "\n\n---\n\n".join(parts) or None, anchor_name, routed_names, skill_has_refs, anchor_id

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
        *,
        tool_adapter: ToolAdapter | None = None,
        model_profile: str | None = None,
        temperature: float | None = None,
        use_tools: bool = False,
        enable_thinking: bool = False,
        thinking_budget: int = 8000,
        enable_rag: bool = False,
        enable_web: bool = False,
        skill_id: UUID | None = None,
        disable_skill: bool = False,
    ) -> ChatContext:
        """Resolve all parameters and return an immutable ``ChatContext``.

        This is the **only** method that issues DB queries for business logic.
        ``stream()`` and ``execute()`` consume the context as pure data.
        """
        profile = self._config.llm.get_profile(model_profile)
        if (use_tools or enable_web) and not profile.capabilities.tools:
            raise ValueError(f"LLM profile '{profile.id}' does not support tool calling")

        # 1. Compose system prompt (skill + global instruction + RAG context in one pass)
        system_prompt, anchor_name, routed_names, skill_has_refs, anchor_id = (
            await self._build_system_prompt(
                session_id=session_id,
                skill_id=skill_id,
                disable_skill=disable_skill,
                enable_rag=enable_rag,
                message=message,
            )
        )

        # 2. Resolve temperature and context window size
        resolved_temp = await self._resolve_temperature(temperature, profile)
        context_max = int(await self._get_setting("context_max_messages") or "20")

        # 3. Build LLM kwargs
        llm_kwargs: dict[str, Any] = {}
        if enable_thinking and profile.capabilities.thinking:
            llm_kwargs["enable_thinking"] = True
            llm_kwargs["thinking_budget"] = thinking_budget

        # 4. Resolve tool adapter: per-call override takes precedence
        base_adapter = tool_adapter if tool_adapter is not None else self._default_tool_adapter

        # 5. Compose ref adapter when skill has reference documents.
        # Use anchor_id (resolved inside _build_system_prompt) instead of the caller-supplied
        # skill_id, which may be None when the skill is auto-loaded from default_skill_id.
        effective_adapter: ToolAdapter = base_adapter
        if skill_has_refs and anchor_id is not None:
            from astracore.service.builtin_tools import (
                build_skill_reference_adapter,  # noqa: PLC0415
            )

            ref_adapter = build_skill_reference_adapter(anchor_id, self._config.memory.db_url)
            effective_adapter = CompositeToolAdapter([ref_adapter, base_adapter])

        # 6. Determine execution mode and allowed tools
        needs_tool_loop = use_tools or enable_web or skill_has_refs
        allowed_tools: frozenset[str]
        mode: Literal["normal", "tool_loop"]
        if needs_tool_loop:
            mode = "tool_loop"
            if not use_tools and not enable_web:
                # Reference-only mode: expose only get_skill_reference.
                allowed_tools = frozenset({"get_skill_reference"})
            else:
                all_tools = frozenset(d.name for d in effective_adapter.get_definitions())
                excluded: set[str] = set()
                if not enable_rag:
                    excluded.add("search_knowledge_base")
                if not enable_web:
                    excluded.add("web_search")
                allowed_tools = all_tools - excluded
        else:
            mode = "normal"
            allowed_tools = frozenset()

        return ChatContext(
            session_id=session_id,
            message=message,
            profile=profile,
            temperature=resolved_temp,
            system_prompt=system_prompt,
            context_max_messages=context_max,
            mode=mode,
            llm_kwargs=llm_kwargs,
            tool_adapter=effective_adapter,
            allowed_tools=allowed_tools,
            anchor_skill=anchor_name,
            routed_skills=tuple(routed_names),
            skill_has_refs=skill_has_refs,
            anchor_id=anchor_id,
        )

    # ------------------------------------------------------------------
    # stream: pure execution, consumes ChatContext
    # ------------------------------------------------------------------

    async def stream(self, ctx: ChatContext) -> AsyncIterator[StreamEvent]:
        """Stream a chat turn.  Requires a fully-resolved ``ChatContext`` from ``prepare()``."""
        stored = [
            m
            for m in await self._memory.load_short_term(ctx.session_id)
            if m.role != MessageRole.SYSTEM
        ]

        session = SessionState(session_id=ctx.session_id)

        if ctx.mode == "tool_loop":
            # For tool-loop mode: trim stored messages first, then prepend system and add user.
            # The tool loop operates on the whole session including system message.
            trimmed = (
                stored[-ctx.context_max_messages:]
                if ctx.context_max_messages and len(stored) > ctx.context_max_messages
                else stored
            )
            initial: list[Message] = []
            if ctx.system_prompt:
                initial.append(Message(role=MessageRole.SYSTEM, content=ctx.system_prompt))
            initial.extend(trimmed)
            session.restore_messages(initial)
            session.add_message(Message(role=MessageRole.USER, content=ctx.message))
            async for event in self._stream_tool_loop(ctx, session):
                yield event
        else:
            # For normal mode: restore stored, add user, then prepend system only for LLM call.
            session.restore_messages(stored)
            session.add_message(Message(role=MessageRole.USER, content=ctx.message))
            async for event in self._stream_normal(ctx, session):
                yield event

    async def _stream_normal(
        self, ctx: ChatContext, session: SessionState
    ) -> AsyncIterator[StreamEvent]:
        """Stream a single LLM call without tool execution."""
        llm_messages = session.get_messages()
        if ctx.context_max_messages and len(llm_messages) > ctx.context_max_messages:
            llm_messages = llm_messages[-ctx.context_max_messages:]
        if ctx.system_prompt:
            llm_messages = [
                Message(role=MessageRole.SYSTEM, content=ctx.system_prompt)
            ] + llm_messages

        accumulated_content = ""
        assistant_metadata: dict[str, Any] = {}
        try:
            async for event in self._get_llm_adapter(ctx.profile).generate_stream(
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
            ctx.profile, ctx.tool_adapter, anchor_id=ctx.anchor_id, allowed_tools=ctx.allowed_tools
        )
        round_count = 0
        completed = False
        try:
            async for event in tool_loop.execute_stream_with_tools(
                session, allowed_tools=ctx.allowed_tools, **ctx.llm_kwargs
            ):
                if event.event_type == StreamEventType.ROUND_START:
                    round_count = int(event.metadata.get("round", round_count + 1))
                # Filter LLM-level DONE events; we emit the authoritative ones below.
                if event.event_type == StreamEventType.DONE:
                    continue
                yield event
            completed = True
        finally:
            if not completed:
                await self._save_session_safe(ctx.session_id, session.get_messages())

        safe_messages = _strip_dangling_tool_calls(session.get_messages())
        if _needs_summary_fallback(safe_messages):
            hit_limit = (
                not tool_loop.unlimited
                and round_count >= tool_loop.max_iterations
                and bool(safe_messages)
                and safe_messages[-1].role == MessageRole.TOOL
            )
            # Phase boundary: consumers use this to separate tool-phase from summary text.
            yield StreamEvent(event_type=StreamEventType.DONE, metadata={"source": "tool_loop"})

            summary_text = ""
            async for event in self._get_llm_adapter(ctx.profile).generate_stream(
                messages=_build_summary_fallback_messages(
                    safe_messages, hit_iteration_limit=hit_limit
                ),
                temperature=ctx.temperature,
            ):
                if event.event_type == StreamEventType.TEXT_DELTA and event.content:
                    summary_text += event.content
                if event.event_type == StreamEventType.DONE:
                    continue
                yield event

            if summary_text.strip():
                session.add_message(Message(role=MessageRole.ASSISTANT, content=summary_text))
            else:
                hint = "信息量较大，本轮分析已暂停。会话已保存，请发送「继续」让 AI 继续完成分析。"
                session.add_message(Message(role=MessageRole.ASSISTANT, content=hint))
                yield StreamEvent(event_type=StreamEventType.TEXT_DELTA, content=hint)

        await self._save_session_safe(ctx.session_id, session.get_messages())
        yield StreamEvent(event_type=StreamEventType.DONE)

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
        try:
            await asyncio.shield(
                self._memory.save_short_term(
                    session_id=session_id,
                    messages=_prepare_for_save(messages),
                )
            )
        except asyncio.CancelledError:
            logger.warning("会话保存被取消，session_id=%s", session_id)
        except Exception:
            logger.exception("会话保存失败，session_id=%s", session_id)
