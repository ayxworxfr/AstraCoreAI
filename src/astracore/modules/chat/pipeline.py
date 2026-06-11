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
from typing import TYPE_CHECKING, Any
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

if TYPE_CHECKING:
    from astracore.infrastructure.memory.vector import MemoryVectorAdapter

logger = get_logger(__name__)

_ANTHROPIC_BLOCKS_KEY = "anthropic_content_blocks"
_PROMPT_DEBUG_SEP = "═" * 64


def _print_prompt_debug(
    system_prompt: str | None,
    messages: list["Message"],
    session_id: "UUID",
) -> None:
    """Print the full LLM input to stdout when debug.log_prompts is enabled."""
    lines: list[str] = [
        "",
        _PROMPT_DEBUG_SEP,
        f"  [PROMPT DEBUG]  session={session_id}",
        _PROMPT_DEBUG_SEP,
    ]
    if system_prompt:
        lines += ["  ── SYSTEM PROMPT ──", system_prompt, ""]
    lines.append(f"  ── MESSAGES ({len(messages)}) ──")
    for msg in messages:
        role = msg.role.value if hasattr(msg.role, "value") else str(msg.role)
        content = msg.content or ""
        prefix = f"  [{role}] "
        # indent continuation lines to keep it readable
        indented = content.replace("\n", "\n" + " " * len(prefix))
        lines.append(f"{prefix}{indented}")
    lines += [_PROMPT_DEBUG_SEP, ""]
    print("\n".join(lines), flush=True)


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
    """Drop SYSTEM / tool-loop-internal / synthetic messages before persisting chat history.

    Exception: assistant messages that contain a ``load_skill`` tool call are replaced with a
    thin text record (``metadata["skill_loaded"] = skill_id``) so the skill-state tracker can
    detect an active skill even after the full tool-call pair is stripped.
    """
    msgs: list[Message] = []
    for m in messages:
        if m.role == MessageRole.SYSTEM:
            continue
        if m.role == MessageRole.TOOL:
            continue
        if m.metadata.get("synthetic"):
            continue
        if m.role == MessageRole.ASSISTANT and m.tool_calls:
            load_skill_calls = [tc for tc in m.tool_calls if tc.name == "load_skill"]
            if load_skill_calls:
                skill_id = str(load_skill_calls[-1].arguments.get("skill_id", "")).strip()
                if skill_id:
                    msgs.append(
                        Message(
                            role=MessageRole.ASSISTANT,
                            content=m.content,
                            metadata={"skill_loaded": skill_id},
                        )
                    )
            continue
        msgs.append(m)
    return _strip_dangling_tool_calls(msgs)


def _detect_active_skill(messages: list[Message], lookback_turns: int = 3) -> str | None:
    """Scan recent assistant messages for an active skill.

    Two detection paths:
    - ``tool_calls``: live in-session calls (before messages are persisted).
    - ``metadata["skill_loaded"]``: thin markers written by ``_prepare_for_save`` for
      load_skill calls, surviving after the full tool-call pair is stripped on save.

    Returns the most recently used skill_id within the last *lookback_turns* assistant
    messages, or None.  The window prevents stale reminders after the skill task ends.
    """
    assistant_count = 0
    for msg in reversed(messages):
        if msg.role != MessageRole.ASSISTANT:
            continue
        assistant_count += 1
        if assistant_count > lookback_turns:
            break
        # Path 1: saved marker from _prepare_for_save
        skill_id = str(msg.metadata.get("skill_loaded", "")).strip()
        if skill_id:
            return skill_id
        # Path 2: live tool_calls still in session (current turn, not yet persisted)
        for tc in msg.tool_calls:
            if tc.name == "load_skill":
                sid = str(tc.arguments.get("skill_id", "")).strip()
                if sid:
                    return sid
    return None


def _build_active_skill_reminder(skill_id: str) -> list[Message]:
    """Build a synthetic user/assistant pair that reminds the model to reload the active skill.

    Injected between session history and the current user message so the model
    sees it immediately before generating its next reply.  Both messages carry
    ``synthetic=True`` metadata so they are never persisted to chat history.
    """
    return [
        Message(
            role=MessageRole.USER,
            content="[技能续接]",
            metadata={"synthetic": True},
        ),
        Message(
            role=MessageRole.ASSISTANT,
            content=(
                f"【当前激活技能：{skill_id}】\n"
                f"本轮对话仍在执行「{skill_id}」技能任务。"
                f'我将在回复前先调用 load_skill("{skill_id}") 重新加载技能指令，'
                "确保严格按照技能规范执行，不跳过。"
            ),
            metadata={"synthetic": True},
        ),
    ]


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
        vector_adapter: "MemoryVectorAdapter | None" = None,
        hooks: HookRegistry | None = None,
    ) -> None:
        self._config = config
        self._memory = memory
        self._rag_pipeline = rag_pipeline
        self._policy = policy
        self._default_tool_adapter = tool_adapter
        self._injected_memory_engine = memory_engine
        self._vector_adapter = vector_adapter
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
        user_id: str = "default",
    ) -> ToolLoopUseCase:
        cfg = self._config.agent
        extra_context: dict[str, Any] = {}
        if allowed_tools:
            extra_context["allowed_tools"] = allowed_tools
        extra_context["tool_adapter"] = tool_adapter
        extra_context["user_id"] = user_id
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

    async def _get_setting(self, key: str, user_id: str = "default") -> str:
        async with get_session(self._config.memory.db_url) as db:
            row = await db.get(UserSettingsRow, {"user_id": user_id, "key": key})
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

    async def _build_rag_context(self, query: str, user_id: str = "default") -> str | None:
        try:
            top_k = int(await self._get_setting("rag_top_k", user_id) or "4")
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
        user_id: str = "default",
    ) -> str | None:
        """Compose system prompt: identity layer + skill manifest + memory + RAG context."""
        ai_name = await self._get_setting("ai_name", user_id) or "小卡"
        owner_name = await self._get_setting("owner_name", user_id)
        global_instruction = await self._get_setting("global_instruction", user_id)

        identity = build_identity_layer(ai_name, owner_name, global_instruction)

        all_skills = await self._load_all_skills()
        manifest = build_skill_manifest(all_skills)

        parts: list[str] = [identity]
        if manifest:
            parts.append(manifest)

        try:
            memory_engine = self._injected_memory_engine or MemoryEngine(
                SQLMemoryStore(self._config.memory.db_url), user_id=user_id
            )
            profile_context = await memory_engine.build_profile_context()
            if profile_context:
                parts.append(profile_context)
        except Exception:
            logger.exception("Profile context 构建失败，跳过本轮记忆注入")

        if enable_rag:
            rag_ctx = await self._build_rag_context(message, user_id)
            if rag_ctx:
                parts.append(rag_ctx)

        return "\n\n---\n\n".join(parts) or None

    async def _build_turn_context(self, session_id: UUID, message: str, user_id: str) -> str:
        """Build Tier-2 turn context (session+project scope, Chroma or SQL fallback)."""
        try:
            if self._injected_memory_engine is not None:
                return await self._injected_memory_engine.build_turn_context(
                    session_id=session_id, message=message
                )
            engine = MemoryEngine(
                SQLMemoryStore(self._config.memory.db_url),
                user_id=user_id,
                vector_adapter=self._vector_adapter,
            )
            return await engine.build_turn_context(session_id=session_id, message=message)
        except Exception:
            logger.exception("Tier-2 记忆上下文构建失败，跳过")
            return ""

    @staticmethod
    def _build_turn_recall_messages(ctx: ChatContext) -> list[Message]:
        """Construct synthetic Tier-2 recall message pair (not persisted)."""
        if not ctx.turn_context:
            return []
        return [
            Message(
                role=MessageRole.USER,
                content="[记忆同步]",
                metadata={"synthetic": True},
            ),
            Message(
                role=MessageRole.ASSISTANT,
                content=ctx.turn_context,
                metadata={"synthetic": True},
            ),
        ]

    async def _resolve_temperature(
        self, temperature: float | None, profile: LLMProfileConfig, user_id: str = "default"
    ) -> float:
        if temperature is not None:
            return temperature
        saved = await self._get_setting("temperature", user_id)
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
        user_id: str = "default",
    ) -> ChatContext:
        """Resolve all options and return an immutable ``ChatContext``.

        This is the **only** method that issues DB queries for business logic.
        ``stream()`` and ``execute()`` consume the context as pure data.
        """
        opts = options or ChatOptions()
        profile = self._config.llm.get_profile(opts.model_profile)
        if (opts.use_tools or opts.enable_web) and not profile.capabilities.tools:
            raise ValueError(f"LLM profile '{profile.id}' does not support tool calling")

        # 1. Compose system prompt (identity + manifest + Tier-1 profile + RAG)
        system_prompt = await self._build_system_prompt(
            session_id=session_id,
            enable_rag=opts.enable_rag,
            message=message,
            user_id=user_id,
        )

        # 1b. Tier-2: dynamic session/project context (injected as synthetic messages in stream)
        turn_context = await self._build_turn_context(session_id, message, user_id)

        # 2. Resolve temperature and context window size
        resolved_temp = await self._resolve_temperature(opts.temperature, profile, user_id)
        context_max = int(await self._get_setting("context_max_messages", user_id) or "20")

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
            user_id=user_id,
            message=message,
            profile=profile,
            temperature=resolved_temp,
            system_prompt=system_prompt,
            context_max_messages=context_max,
            mode="tool_loop" if needs_tool_loop else "normal",
            llm_kwargs=llm_kwargs,
            tool_adapter=effective_adapter,
            allowed_tools=allowed_tools,
            turn_context=turn_context,
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

        recall = self._build_turn_recall_messages(ctx)
        # Skill state tracking: if a skill was loaded recently, remind the model to reload it.
        active_skill = _detect_active_skill(stored)
        skill_reminder = _build_active_skill_reminder(active_skill) if active_skill else []

        if ctx.mode == "tool_loop":
            # tool_loop needs the system message inside the session so the tool loop can see it.
            initial: list[Message] = []
            if ctx.system_prompt:
                initial.append(Message(role=MessageRole.SYSTEM, content=ctx.system_prompt))
            initial.extend(stored)
            initial.extend(recall)
            initial.extend(skill_reminder)
            session.restore_messages(initial)
            session.add_message(Message(role=MessageRole.USER, content=ctx.message))
            if self._config.debug.log_prompts:
                # system is embedded in messages; pass None to avoid double-printing
                _print_prompt_debug(None, session.get_messages(), ctx.session_id)
            async for event in self._stream_tool_loop(ctx, session):
                yield event
        else:
            # normal mode: system is prepended only at the LLM call site.
            session.restore_messages(stored + recall + skill_reminder)
            session.add_message(Message(role=MessageRole.USER, content=ctx.message))
            if self._config.debug.log_prompts:
                _print_prompt_debug(ctx.system_prompt, session.get_messages(), ctx.session_id)
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
            user_id=ctx.user_id,
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
