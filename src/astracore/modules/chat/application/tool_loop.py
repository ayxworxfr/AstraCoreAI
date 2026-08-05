"""Tool loop use case — 单一 Agent 循环，流式/非流式只换 LLMRoundStrategy。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from astracore.infrastructure.tools.read_tracked import ReadTrackedToolAdapter
from astracore.modules.chat.application.llm_round import (
    BlockingLLMRound,
    LLMRoundStrategy,
    RoundOutcome,
    StreamingLLMRound,
)
from astracore.modules.chat.application.tool_executor import ToolExecutor
from astracore.modules.chat.application.tool_loop_config import ToolLoopConfig
from astracore.modules.chat.application.tool_scheduler import ToolScheduler
from astracore.modules.chat.domain.budget import BudgetExceeded, TurnBudget
from astracore.modules.chat.domain.message import Message, MessageRole, ToolCall, ToolResult
from astracore.modules.chat.domain.session import SessionState
from astracore.modules.chat.domain.session_context import SessionContext, coerce_session_context
from astracore.modules.tools.ports.tool import ToolAdapter
from astracore.shared.observability.hooks import HookRegistry
from astracore.shared.observability.logger import get_logger
from astracore.shared.policy.engine import PolicyEngine
from astracore.shared.ports.llm import LLMAdapter, StreamEvent, StreamEventType

_logger = get_logger(__name__)


class ToolLoopUseCase:
    """Tool calling loop with automatic execution."""

    _ANTHROPIC_BLOCKS_KEY = "anthropic_content_blocks"

    def __init__(
        self,
        llm_adapter: LLMAdapter,
        tool_adapter: ToolAdapter,
        policy_engine: PolicyEngine,
        config: ToolLoopConfig | None = None,
        *,
        hooks: HookRegistry | None = None,
        max_iterations: int | None = None,
        max_tool_result_chars: int | None = None,
        tool_timeout_s: float | None = None,
        profile_id: str | None = None,
        extra_context: dict[str, Any] | None = None,
    ):
        cfg = config or ToolLoopConfig()
        if max_iterations is not None:
            cfg.max_iterations = max_iterations
        if max_tool_result_chars is not None:
            cfg.max_tool_result_chars = max_tool_result_chars
        if tool_timeout_s is not None:
            cfg.tool_timeout_s = tool_timeout_s
        if profile_id is not None:
            cfg.profile_id = profile_id
        if extra_context is not None:
            cfg.extra_context = dict(extra_context)
            cfg.extra_context.setdefault("_read_files", set())

        self.config = cfg
        self.llm = llm_adapter
        self.tools = ReadTrackedToolAdapter(tool_adapter)
        self.policy = policy_engine
        self._hooks = hooks

        self.max_iterations = cfg.max_iterations
        self.max_tool_result_chars = cfg.max_tool_result_chars
        self.tool_timeout_s = cfg.tool_timeout_s
        self.profile_id = cfg.profile_id
        self._extra_context = cfg.extra_context

        self._executor = ToolExecutor(
            self.tools,
            policy_engine,
            extra_context=self._extra_context,
            profile_id=self.profile_id,
            max_tool_result_chars=self.max_tool_result_chars,
            tool_timeout_s=self.tool_timeout_s,
            hooks=hooks,
        )
        self._scheduler = ToolScheduler(self._executor)

    @property
    def unlimited(self) -> bool:
        """max_iterations == 0 时不限制工具调用轮次。"""
        return self.max_iterations == 0

    # ------------------------------------------------------------------
    # Guidance / definitions / closing
    # ------------------------------------------------------------------

    def _prepare_round_prompt(
        self,
        messages: list[Message],
        *,
        iteration: int,
        session_context: SessionContext,
        closing: bool = False,
    ) -> tuple[list[Message], SessionContext]:
        """Return (messages, session_context) for one LLM round.

        Messages keep the static SYSTEM content untouched so prompt-cache prefixes
        stay byte-stable. Per-round guidance is ``SessionContext.with_tool_round``.
        """
        round_ctx = session_context.with_tool_round(
            iteration=iteration,
            max_iterations=self.max_iterations,
            unlimited=self.unlimited,
            closing=closing,
        )
        return list(messages), round_ctx

    def _truncate_tool_result(self, content: str, limit: int | None = None) -> str:
        """Truncate oversized tool results（测试与外部仍可能直接调用）。"""
        if limit is not None:
            if len(content) <= limit:
                return content
            return (
                content[:limit] + f"\n\n[内容已截断，原始长度 {len(content)} 字符。"
                "如需查看更多，请使用 offset/page 参数重新调用工具。]"
            )
        return self._executor.truncate(content, tool_name="")

    def _build_tool_definitions(self) -> list[dict[str, Any]]:
        """Build tool definitions dict for LLM. Single source of truth."""
        return [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": {
                    "type": "object",
                    "properties": {
                        p.name: {"type": p.type.value, "description": p.description}
                        for p in t.parameters
                    },
                    "required": [p.name for p in t.parameters if p.required],
                },
            }
            for t in self.tools.get_definitions()
        ]

    def _filter_tool_defs(
        self,
        defs: list[dict[str, Any]],
        allowed_tools: frozenset[str] | set[str] | None,
    ) -> list[dict[str, Any]]:
        if allowed_tools is None:
            return defs
        return [t for t in defs if t["name"] in allowed_tools]

    def _should_stop_after_tools(self, iterations: int) -> bool:
        return (not self.unlimited) and iterations >= self.max_iterations

    def _needs_closing_round(self, messages: list[Message]) -> bool:
        """Return True if the loop ended without a text response from the assistant."""
        visible = [m for m in messages if m.role != MessageRole.SYSTEM]
        if not visible:
            return False
        last = visible[-1]
        return (last.role == MessageRole.TOOL and last.has_tool_results()) or (
            last.role == MessageRole.ASSISTANT and not last.content.strip()
        )

    # ------------------------------------------------------------------
    # 测试兼容薄包装
    # ------------------------------------------------------------------

    def _get_tool_max_chars(self, tool_name: str) -> int:
        return self._executor.max_chars_for(tool_name)

    def _get_tool_timeout(self, tool_name: str) -> float | None:
        return self._executor.timeout_for(tool_name)

    async def _execute_one_tool(self, tool_call: ToolCall) -> ToolResult:
        return await self._executor.run(tool_call)

    async def _execute_partitioned(self, calls: list[ToolCall]) -> list[ToolResult]:
        return await self._scheduler.run(calls)

    # ------------------------------------------------------------------
    # 统一 Agent 循环
    # ------------------------------------------------------------------

    async def _run_loop(
        self,
        session: SessionState,
        round_strategy: LLMRoundStrategy,
        *,
        model: str | None = None,
        allowed_tools: frozenset[str] | set[str] | None = None,
        emit_round_start: bool = False,
        stream_tools: bool = False,
        **llm_kwargs: Any,
    ) -> AsyncIterator[StreamEvent]:
        """唯一的 ReAct 循环：策略决定 LLM 怎么调，工具调度共用 ToolScheduler。"""
        tool_definitions = self._filter_tool_defs(self._build_tool_definitions(), allowed_tools)
        # 同一 tool 列表贯穿主循环，保证 tools→system 缓存前缀不被末轮卸工具打穿
        tools_for_llm = tool_definitions or None
        base_session = coerce_session_context(llm_kwargs.get("session_context"))
        iterations = 0
        budget = self._build_budget()

        while self.unlimited or iterations < self.max_iterations:
            iterations += 1
            budget.check_iteration(iterations)

            if emit_round_start:
                yield StreamEvent(
                    event_type=StreamEventType.ROUND_START,
                    metadata={"round": iterations},
                )

            round_messages, round_session_ctx = self._prepare_round_prompt(
                session.get_messages(),
                iteration=iterations,
                session_context=base_session,
            )
            round_kwargs = {**llm_kwargs, "session_context": round_session_ctx}
            outcome: RoundOutcome | None = None
            try:
                async for item in round_strategy.run(
                    round_messages, model, tools_for_llm, **round_kwargs
                ):
                    if isinstance(item, RoundOutcome):
                        outcome = item
                    else:
                        yield item
            except BudgetExceeded:
                raise

            assert outcome is not None
            self._apply_usage(budget, outcome.metadata.get("usage"))

            if emit_round_start and not outcome.short_circuit:
                yield StreamEvent(
                    event_type=StreamEventType.THINKING_STOP,
                    metadata={"duration_ms": outcome.duration_ms},
                )
            elif emit_round_start and outcome.short_circuit:
                yield StreamEvent(
                    event_type=StreamEventType.THINKING_STOP,
                    metadata={"duration_ms": 0},
                )

            session.add_message(
                Message(
                    role=MessageRole.ASSISTANT,
                    content=outcome.content,
                    tool_calls=outcome.tool_calls,
                    metadata=outcome.metadata,
                )
            )

            if outcome.short_circuit or not outcome.tool_calls:
                break
            if self._should_stop_after_tools(iterations):
                break

            if stream_tools:
                tool_results: list[ToolResult] = []
                async for sched_item in self._scheduler.run_streaming(
                    outcome.tool_calls, outcome.parse_errors
                ):
                    if isinstance(sched_item, list):
                        tool_results = sched_item
                    elif isinstance(sched_item, StreamEvent):
                        yield sched_item
            else:
                # 非流式：parse_errors 先落结果，其余走分区执行
                tool_results = await self._run_tools_blocking(
                    outcome.tool_calls, outcome.parse_errors
                )

            session.add_message(
                Message(role=MessageRole.TOOL, content="", tool_results=tool_results)
            )

        if self._needs_closing_round(session.get_messages()):
            if emit_round_start:
                # Phase boundary: resets in_tool_round in the API layer.
                yield StreamEvent(event_type=StreamEventType.DONE, metadata={"source": "tool_loop"})
            # Closing: tools=None forces a text reply; progress note stays in SessionContext
            # so the static SYSTEM prefix stays cache-stable for the next user turn.
            closing_messages, closing_session_ctx = self._prepare_round_prompt(
                session.get_messages(),
                iteration=iterations,
                session_context=base_session,
                closing=True,
            )
            closing_kwargs = {**llm_kwargs, "session_context": closing_session_ctx}
            closing: RoundOutcome | None = None
            async for item in round_strategy.run(closing_messages, model, None, **closing_kwargs):
                if isinstance(item, RoundOutcome):
                    closing = item
                else:
                    yield item
            assert closing is not None
            session.add_message(
                Message(
                    role=MessageRole.ASSISTANT,
                    content=closing.content or "工具执行完成。",
                    tool_calls=[],
                )
            )

    def _build_budget(self) -> TurnBudget:
        raw = self._extra_context.get("budget") or {}
        return TurnBudget(
            max_tool_iterations=0 if self.unlimited else self.max_iterations,
            max_input_tokens=int(raw.get("max_input_tokens") or 0),
            max_output_tokens=int(raw.get("max_output_tokens") or 0),
        )

    @staticmethod
    def _apply_usage(budget: TurnBudget, usage: Any) -> None:
        if not isinstance(usage, dict):
            return
        budget.add_usage(
            input_tokens=int(usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
        )

    async def _run_tools_blocking(
        self,
        calls: list[ToolCall],
        parse_errors: dict[str, str],
    ) -> list[ToolResult]:
        """非流式工具执行：解析失败直接回流，其余走分区调度。"""
        if not parse_errors:
            return await self._scheduler.run(calls)

        results: list[ToolResult | None] = [None] * len(calls)
        pending: list[ToolCall] = []
        pending_idx: list[int] = []
        for i, tc in enumerate(calls):
            if tc.id in parse_errors:
                err = ToolResult(
                    tool_call_id=tc.id,
                    name=tc.name,
                    content=parse_errors[tc.id],
                    is_error=True,
                )
                await self._executor.fire_after(err)
                results[i] = err
            else:
                pending.append(tc)
                pending_idx.append(i)
        if pending:
            executed = await self._scheduler.run(pending)
            for idx, result in zip(pending_idx, executed, strict=True):
                results[idx] = result
        return [r for r in results if r is not None]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def execute_with_tools(
        self,
        session: SessionState,
        model: str | None = None,
        allowed_tools: set[str] | None = None,
        **llm_kwargs: Any,
    ) -> SessionState:
        """非流式工具循环 —— 与 stream 共用同一编排，仅换 BlockingLLMRound。"""
        strategy = BlockingLLMRound(self.llm, self.policy, self._hooks)
        async for _ in self._run_loop(
            session,
            strategy,
            model=model,
            allowed_tools=allowed_tools,
            emit_round_start=False,
            stream_tools=False,
            **llm_kwargs,
        ):
            pass
        return session

    async def execute_stream_with_tools(
        self,
        session: SessionState,
        model: str | None = None,
        allowed_tools: frozenset[str] | set[str] | None = None,
        **llm_kwargs: Any,
    ) -> AsyncIterator[StreamEvent]:
        """流式工具循环。

        每轮开始时 yield ROUND_START，前端用来分隔思考块。
        allowed_tools: 若指定，则只将名称在集合内的工具暴露给 LLM。
        llm_kwargs 透传给 LLM 适配器（如 enable_thinking）。
        """
        strategy = StreamingLLMRound(self.llm, self.policy, self._hooks)
        async for event in self._run_loop(
            session,
            strategy,
            model=model,
            allowed_tools=allowed_tools,
            emit_round_start=True,
            stream_tools=True,
            **llm_kwargs,
        ):
            yield event
