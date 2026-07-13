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
import base64
import dataclasses
import json
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any
from uuid import UUID

from astracore.infrastructure.db.models import UserSettingsRow
from astracore.infrastructure.db.session import get_session
from astracore.infrastructure.llm.anthropic import AnthropicAdapter
from astracore.infrastructure.llm.openai import OpenAIAdapter
from astracore.infrastructure.memory.hybrid import HybridMemoryAdapter
from astracore.infrastructure.memory.store import SQLMemoryStore
from astracore.modules.attachments.domain import AttachmentCapabilityError, AttachmentRef
from astracore.modules.attachments.ports import AttachmentStoragePort
from astracore.modules.chat.application.compactor import HistoryCompactor
from astracore.modules.chat.application.prompt_builder import SystemPromptBuilder
from astracore.modules.chat.application.tool_loop import ToolLoopUseCase
from astracore.modules.chat.domain.chat_context import ChatContext
from astracore.modules.chat.domain.chat_options import ChatOptions
from astracore.modules.chat.domain.message import Message, MessageRole
from astracore.modules.chat.domain.session import SessionState
from astracore.modules.memory.application.engine import MemoryEngine
from astracore.modules.rag.application.pipeline import RAGPipeline
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
    session_context: str | None = None,
) -> None:
    """Print the full LLM input to stdout when debug.log_prompts is enabled."""
    lines: list[str] = [
        "",
        _PROMPT_DEBUG_SEP,
        f"  [PROMPT DEBUG]  session={session_id}",
        _PROMPT_DEBUG_SEP,
    ]
    if system_prompt:
        lines += ["  ── SYSTEM PROMPT (static, cached) ──", system_prompt, ""]
    if session_context:
        lines += ["  ── SESSION CONTEXT (dynamic, not cached) ──", session_context, ""]
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


def _attachment_metadata(refs: list["AttachmentRef"]) -> dict[str, Any]:
    """Encode loaded AttachmentRef list as JSON-serialisable metadata for a user message."""
    if not refs:
        return {}
    return {
        "attachment_refs": [
            {
                "id": r.id,
                "mime_type": r.mime_type,
                "filename": r.filename,
                "storage_key": r.storage_key,
                "data_b64": base64.b64encode(r.data).decode("ascii") if r.data else None,
            }
            for r in refs
        ]
    }


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
        rag_pipeline: RAGPipeline | None,
        policy: PolicyEngine,
        tool_adapter: ToolAdapter,
        memory_engine: MemoryEngine | None = None,
        vector_adapter: "MemoryVectorAdapter | None" = None,
        hooks: HookRegistry | None = None,
        attachment_storage: AttachmentStoragePort | None = None,
    ) -> None:
        self._config = config
        self._memory = memory
        self._rag_pipeline = rag_pipeline
        self._policy = policy
        self._default_tool_adapter = tool_adapter
        self._injected_memory_engine = memory_engine
        self._vector_adapter = vector_adapter
        self._hooks = hooks
        self._attachment_storage = attachment_storage
        self._llm_adapters: dict[str, LLMAdapter] = {}
        # All system-prompt composition is delegated to a dedicated builder so this
        # class can stay focused on orchestration (DB → context → stream).
        self._prompt_builder = SystemPromptBuilder(
            config=config,
            rag_pipeline=rag_pipeline,
            memory_engine=memory_engine,
        )

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
                    timeout=self._config.policy.timeout.build_llm_httpx_timeout(
                        overall_override=profile.timeout_s
                    ),
                )
            else:
                self._llm_adapters[profile.id] = OpenAIAdapter(
                    api_key=profile.api_key,
                    default_model=profile.model,
                    base_url=profile.base_url,
                    extra_headers=profile.extra_headers,
                    protocol=profile.protocol,
                    max_tokens=profile.max_tokens,
                    timeout=self._config.policy.timeout.build_llm_httpx_timeout(
                        overall_override=profile.timeout_s
                    ),
                )
        return self._llm_adapters[profile.id]

    def _make_tool_loop(
        self,
        profile: LLMProfileConfig,
        tool_adapter: ToolAdapter,
        allowed_tools: frozenset[str] = frozenset(),
        session_id: UUID | None = None,
        user_id: str = "default",
        extra_context_overlay: dict[str, Any] | None = None,
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
        if extra_context_overlay:
            extra_context.update(extra_context_overlay)
        policy = self._policy
        if profile.timeout_s is not None or profile.max_retries is not None:
            # Per-profile overrides: create a derived PolicyEngine without mutating the shared one.
            merged_cfg = self._policy.config.model_copy(
                update={
                    "retry": self._policy.config.retry.model_copy(
                        update={"max_retries": profile.max_retries}
                    )
                    if profile.max_retries is not None
                    else self._policy.config.retry,
                    "timeout": self._policy.config.timeout.model_copy(
                        update={"llm_timeout_s": profile.timeout_s}
                    )
                    if profile.timeout_s is not None
                    else self._policy.config.timeout,
                }
            )
            policy = PolicyEngine(config=merged_cfg)

        return ToolLoopUseCase(
            llm_adapter=self.get_llm_adapter(profile),
            tool_adapter=tool_adapter,
            policy_engine=policy,
            max_iterations=cfg.max_tool_iterations,
            max_tool_result_chars=cfg.max_tool_result_chars,
            tool_timeout_s=self._config.policy.timeout.tool_timeout_s,
            profile_id=profile.id,
            extra_context=extra_context or None,
            hooks=self._hooks,
        )

    # ------------------------------------------------------------------
    # DB helpers
    # ------------------------------------------------------------------

    async def _get_setting(self, key: str, user_id: str = "default") -> str:
        async with get_session(self._config.storage.db_url) as db:
            row = await db.get(UserSettingsRow, {"user_id": user_id, "key": key})
            return row.value if row else ""

    async def _load_attachments(
        self,
        refs: list[AttachmentRef],
        profile_id: str,
        vision_capable: bool,
    ) -> list[AttachmentRef]:
        """Capability-check then load bytes for each AttachmentRef."""
        if not refs:
            return []
        if not vision_capable:
            raise AttachmentCapabilityError(
                f"LLM profile '{profile_id}' does not support vision/document attachments"
            )
        if self._attachment_storage is None:
            return list(refs)
        loaded: list[AttachmentRef] = []
        for ref in refs:
            try:
                data = await self._attachment_storage.load(ref.storage_key)
            except FileNotFoundError:
                # Placeholder for deleted attachments — adapters must handle data=None.
                loaded.append(ref)
                continue
            loaded.append(dataclasses.replace(ref, data=data))
        return loaded

    # ------------------------------------------------------------------
    # Prompt composition helpers
    # ------------------------------------------------------------------

    async def _build_turn_context(self, session_id: UUID, message: str, user_id: str) -> str:
        """Build Tier-2 turn context (session+project scope, Chroma or SQL fallback)."""
        try:
            if self._injected_memory_engine is not None:
                return await self._injected_memory_engine.build_turn_context(
                    session_id=session_id, message=message
                )
            engine = MemoryEngine(
                SQLMemoryStore(self._config.storage.db_url),
                user_id=user_id,
                vector_adapter=self._vector_adapter,
            )
            return await engine.build_turn_context(session_id=session_id, message=message)
        except Exception:
            logger.exception("Tier-2 记忆上下文构建失败，跳过")
            return ""

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

        # 1. Compose static system prompt layers (security + identity + skills + Tier-1).
        #    datetime and RAG are excluded from the system prompt so it never changes
        #    between turns — see _build_user_context() for where they are injected.
        #    Per-turn <session_context> (active-skill + Tier-2) is appended in stream()
        #    once the loaded message history is available for active-skill detection.
        system_prompt = await self._prompt_builder.build_static(user_id=user_id)

        # 1b. RAG retrieval — stored in context, injected into user message in stream().
        rag_context: str | None = None
        if opts.enable_rag:
            rag_context = await self._prompt_builder.retrieve_rag_context(message, user_id) or None

        # 1c. Tier-2: dynamic session/project context (folded into system prompt in stream())
        turn_context = await self._build_turn_context(session_id, message, user_id)

        # 2. Resolve temperature and context window size
        resolved_temp = opts.temperature if opts.temperature is not None else profile.temperature
        context_max = int(
            await self._get_setting("context_max_messages", user_id)
            or str(self._config.policy.compaction.default_max_messages)
        )

        # 3. Build LLM kwargs
        llm_kwargs: dict[str, Any] = {}

        # Slice B: resolve thinking_mode — opts override > profile default > capability inference
        effective_thinking_mode = (
            opts.thinking_mode if opts.thinking_mode is not None else profile.thinking_mode
        )
        if (
            effective_thinking_mode
            and effective_thinking_mode != "off"
            and profile.capabilities.thinking
        ):
            if profile.capabilities.adaptive_thinking_only:
                llm_kwargs["thinking_mode"] = "adaptive"
            else:
                llm_kwargs["thinking_mode"] = "on"
                llm_kwargs["thinking_budget"] = opts.thinking_budget

        # Slice B: reasoning_effort and verbosity — opts > profile defaults
        # Both protocols receive reasoning_effort; openai.py routes extra_body vs responses.
        # verbosity is Responses API only (GPT-5); extra_body providers do not support it.
        if profile.capabilities.reasoning_effort_protocol:
            resolved_effort = opts.reasoning_effort or profile.reasoning_effort
            if resolved_effort:
                llm_kwargs["reasoning_effort"] = resolved_effort
            if profile.capabilities.reasoning_effort_protocol == "responses":
                resolved_verbosity = opts.verbosity or profile.verbosity
                if resolved_verbosity:
                    llm_kwargs["verbosity"] = resolved_verbosity

        # Slice A: sampling params — mutually exclusive.
        # Priority: explicit top_p > explicit top_k > temperature/profile default.
        # Adapters also guard this, but resolving here keeps SDK/HTTP behavior predictable.
        effective_top_p = opts.top_p if opts.top_p is not None else profile.top_p
        if profile.capabilities.temperature and effective_top_p is not None:
            llm_kwargs["top_p"] = effective_top_p
        elif profile.capabilities.top_k and opts.top_k is not None:
            llm_kwargs["top_k"] = opts.top_k

        saved_stop = await self._get_setting("stop_sequences", user_id)
        if saved_stop:
            try:
                parsed_stop = json.loads(saved_stop)
                effective_stop: list[str] = (
                    parsed_stop if isinstance(parsed_stop, list) else profile.stop_sequences
                )
            except (ValueError, TypeError):
                effective_stop = profile.stop_sequences
        else:
            effective_stop = profile.stop_sequences
        if effective_stop:
            llm_kwargs["stop_sequences"] = effective_stop

        if profile.enable_prompt_cache and profile.capabilities.prompt_cache:
            llm_kwargs["enable_prompt_cache"] = True

        # Slice C: service_tier (OpenAI 'auto'/'default'/'flex'; Anthropic priority tiers)
        if profile.service_tier:
            llm_kwargs["service_tier"] = profile.service_tier

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

        # 6. Load attachment bytes (capability guard + storage read)
        attachment_refs = await self._load_attachments(
            opts.attachments, profile.id, profile.capabilities.vision
        )

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
            rag_context=rag_context,
            attachment_refs=attachment_refs,
        )

    # ------------------------------------------------------------------
    # stream: pure execution, consumes ChatContext
    # ------------------------------------------------------------------

    async def stream(
        self, ctx: ChatContext, extra_context: dict[str, Any] | None = None
    ) -> AsyncIterator[StreamEvent]:
        """Stream a chat turn.  Requires a fully-resolved ``ChatContext`` from ``prepare()``.

        ``extra_context`` — optional per-call key/value pairs merged into the tool execution
        context (e.g. ``hitl_callback``).  These supplement but never override the context
        built by ``_make_tool_loop()``.
        """
        loaded = [
            m
            for m in await self._memory.load_short_term(ctx.session_id)
            if m.role != MessageRole.SYSTEM
        ]
        _memory_engine = self._injected_memory_engine or MemoryEngine(
            SQLMemoryStore(self._config.storage.db_url), user_id=ctx.user_id
        )
        compactor = HistoryCompactor(
            llm_adapter=self.get_llm_adapter(ctx.profile),
            memory_engine=_memory_engine,
            model=ctx.profile.model,
            rule=self._config.policy.compaction,
        )
        stored = await compactor.maybe_compact(
            loaded,
            session_id=ctx.session_id,
            trim_limit=ctx.context_max_messages,
        )

        session = SessionState(session_id=ctx.session_id)

        # Build per-turn session_context: datetime + RAG + active-skill + Tier-2 memory.
        # Passed to the LLM adapter as a separate non-cached system block so the static
        # layers in ctx.system_prompt remain an unchanged cached prefix across turns.
        active_skill = SystemPromptBuilder.detect_active_skill(stored)
        session_layer = SystemPromptBuilder.build_session_layer(
            ctx.turn_context, active_skill, ctx.rag_context
        )

        if ctx.mode == "tool_loop":
            # tool_loop embeds the static system message inside the session messages list;
            # session_layer is passed via kwarg to the LLM adapter as the dynamic block.
            initial: list[Message] = []
            if ctx.system_prompt:
                initial.append(Message(role=MessageRole.SYSTEM, content=ctx.system_prompt))
            initial.extend(stored)
            session.restore_messages(initial)
            session.add_message(
                Message(
                    role=MessageRole.USER,
                    content=ctx.message,
                    metadata=_attachment_metadata(ctx.attachment_refs),
                )
            )
            if self._config.debug.log_prompts:
                # Show static system first, then session_context, then non-system messages —
                # matching the actual two-block order sent to the LLM adapter.
                non_system = [m for m in session.get_messages() if m.role != MessageRole.SYSTEM]
                _print_prompt_debug(
                    ctx.system_prompt, non_system, ctx.session_id, session_layer or None
                )
            async for event in self._stream_tool_loop(
                ctx, session, session_layer=session_layer, extra_context=extra_context
            ):
                yield event
        else:
            # normal mode: static system is prepended at the LLM call site.
            session.restore_messages(stored)
            session.add_message(
                Message(
                    role=MessageRole.USER,
                    content=ctx.message,
                    metadata=_attachment_metadata(ctx.attachment_refs),
                )
            )
            if self._config.debug.log_prompts:
                _print_prompt_debug(
                    ctx.system_prompt, session.get_messages(), ctx.session_id, session_layer or None
                )
            async for event in self._stream_normal(
                ctx, session, ctx.system_prompt, session_layer=session_layer
            ):
                yield event

    async def _stream_normal(
        self,
        ctx: ChatContext,
        session: SessionState,
        system_prompt: str | None,
        session_layer: str = "",
    ) -> AsyncIterator[StreamEvent]:
        """Stream a single LLM call without tool execution.

        ``system_prompt`` is the static cached layer (``ctx.system_prompt``).
        ``session_layer`` is the per-turn dynamic ``<session_context>`` block passed
        to the adapter as a separate non-cached system block via ``session_context`` kwarg.
        """
        llm_messages = session.get_messages()
        if system_prompt:
            llm_messages = [Message(role=MessageRole.SYSTEM, content=system_prompt)] + llm_messages

        call_kwargs = dict(ctx.llm_kwargs)
        if session_layer:
            call_kwargs["session_context"] = session_layer

        accumulated_content = ""
        assistant_metadata: dict[str, Any] = {}
        try:
            async for event in self.get_llm_adapter(ctx.profile).generate_stream(
                messages=llm_messages,
                temperature=ctx.temperature,
                **call_kwargs,
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
        self,
        ctx: ChatContext,
        session: SessionState,
        session_layer: str = "",
        extra_context: dict[str, Any] | None = None,
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
            extra_context_overlay=extra_context,
        )
        call_kwargs = dict(ctx.llm_kwargs)
        if session_layer:
            call_kwargs["session_context"] = session_layer
        completed = False
        total_input_tokens = 0
        total_output_tokens = 0
        total_cache_read_input_tokens = 0
        total_cache_creation_input_tokens = 0
        try:
            async for event in tool_loop.execute_stream_with_tools(
                session, allowed_tools=ctx.allowed_tools, **call_kwargs
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
                        total_cache_read_input_tokens += int(_u.get("cache_read_input_tokens", 0))
                        total_cache_creation_input_tokens += int(
                            _u.get("cache_creation_input_tokens", 0)
                        )
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
                "usage": {
                    "input_tokens": total_input_tokens,
                    "output_tokens": total_output_tokens,
                    "cache_read_input_tokens": total_cache_read_input_tokens,
                    "cache_creation_input_tokens": total_cache_creation_input_tokens,
                }
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
