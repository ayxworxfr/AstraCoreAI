"""LLM 轮次策略 —— 流式 / 非流式共用同一 Agent 循环，只换调用方式。"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Protocol

from astracore.modules.chat.domain.message import Message, ToolCall
from astracore.shared.observability.hooks import (
    HookRegistry,
    LLMCallInput,
    LLMCallOutput,
    ShortCircuit,
)
from astracore.shared.policy.engine import PolicyEngine
from astracore.shared.ports.llm import LLMAdapter, StreamEvent, StreamEventType

_ANTHROPIC_BLOCKS_KEY = "anthropic_content_blocks"


@dataclass(slots=True)
class RoundOutcome:
    """一轮 LLM 调用的结构化结果（不含中间 stream 事件）。"""

    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    parse_errors: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    short_circuit: bool = False
    duration_ms: int = 0


class LLMRoundStrategy(Protocol):
    """一轮 LLM 调用策略。"""

    def run(
        self,
        messages: list[Message],
        model: str | None,
        tools: list[dict[str, Any]] | None,
        **llm_kwargs: Any,
    ) -> AsyncIterator[StreamEvent | RoundOutcome]:
        """async generator：零或多个 StreamEvent，最后恰好一个 RoundOutcome。"""
        ...


class _HookedRoundBase:
    def __init__(
        self,
        llm: LLMAdapter,
        policy: PolicyEngine,
        hooks: HookRegistry | None,
    ) -> None:
        self._llm = llm
        self._policy = policy
        self._hooks = hooks

    async def _before(
        self,
        messages: list[Message],
        model: str | None,
        tools: list[dict[str, Any]] | None,
        kwargs: dict[str, Any],
    ) -> LLMCallInput | ShortCircuit:
        payload = LLMCallInput(messages=messages, model=model, tools=tools, kwargs=kwargs)
        if self._hooks:
            return await self._hooks.run_before_llm(payload)
        return payload

    async def _after(
        self,
        content: str,
        tool_calls: list[Any],
        metadata: dict[str, Any],
        duration_ms: int,
    ) -> None:
        payload = LLMCallOutput(
            content=content,
            tool_calls=tool_calls,
            metadata=metadata,
            duration_ms=duration_ms,
        )
        if self._hooks:
            await self._hooks.run_after_llm(payload)


class BlockingLLMRound(_HookedRoundBase):
    """非流式：llm.generate + retry。"""

    async def run(
        self,
        messages: list[Message],
        model: str | None,
        tools: list[dict[str, Any]] | None,
        **llm_kwargs: Any,
    ) -> AsyncIterator[StreamEvent | RoundOutcome]:
        before = await self._before(messages, model, tools, llm_kwargs)
        if isinstance(before, ShortCircuit):
            content = before.result.content
            yield RoundOutcome(content=content, short_circuit=True)
            return

        t0 = time.monotonic()
        # 非流式路径不透传 llm_kwargs（历史契约：retry 包一层 generate）
        response = await self._policy.apply_retry_policy(
            self._llm.generate,
            messages=messages,
            model=model,
            tools=tools,
        )
        duration_ms = int((time.monotonic() - t0) * 1000)
        meta: dict[str, Any] = {}
        usage = getattr(response, "usage", None) or {}
        if usage:
            meta["usage"] = dict(usage)
        await self._after(response.content, response.tool_calls, meta, duration_ms)
        yield RoundOutcome(
            content=response.content,
            tool_calls=list(response.tool_calls or []),
            metadata=meta,
            duration_ms=duration_ms,
        )


class StreamingLLMRound(_HookedRoundBase):
    """流式：透传 StreamEvent，汇总为 RoundOutcome。"""

    async def run(
        self,
        messages: list[Message],
        model: str | None,
        tools: list[dict[str, Any]] | None,
        **llm_kwargs: Any,
    ) -> AsyncIterator[StreamEvent | RoundOutcome]:
        before = await self._before(messages, model, tools, llm_kwargs)
        if isinstance(before, ShortCircuit):
            content = before.result.content
            yield StreamEvent(event_type=StreamEventType.TEXT_DELTA, content=content)
            yield RoundOutcome(content=content, short_circuit=True)
            return

        content = ""
        tool_calls: list[ToolCall] = []
        parse_errors: dict[str, str] = {}
        metadata: dict[str, Any] = {}
        t0 = time.monotonic()
        try:
            async for event in self._llm.generate_stream(
                messages=messages,
                model=model,
                tools=tools,
                **llm_kwargs,
            ):
                if event.event_type == StreamEventType.TEXT_DELTA and event.content:
                    content += event.content
                elif event.event_type == StreamEventType.TOOL_CALL and event.tool_call:
                    tool_calls.append(event.tool_call)
                elif event.event_type == StreamEventType.TOOL_CALL_ERROR and event.tool_call:
                    # 参数 JSON 无法修复：仍写入 tool_call 保持协议完整性
                    tool_calls.append(event.tool_call)
                    parse_errors[event.tool_call.id] = (
                        event.error or "工具参数 JSON 解析失败，请重新调用"
                    )
                elif event.event_type == StreamEventType.DONE:
                    raw_blocks = event.metadata.get(_ANTHROPIC_BLOCKS_KEY)
                    if isinstance(raw_blocks, list) and raw_blocks:
                        metadata[_ANTHROPIC_BLOCKS_KEY] = raw_blocks
                    usage = event.metadata.get("usage")
                    if isinstance(usage, dict) and usage:
                        metadata["usage"] = dict(usage)
                yield event
        except BaseException:
            # 中断时先把已累积内容带回 outcome，再向上抛，让上层保存 session
            if content.strip() or tool_calls:
                duration_ms = int((time.monotonic() - t0) * 1000)
                yield RoundOutcome(
                    content=content,
                    tool_calls=tool_calls,
                    parse_errors=parse_errors,
                    metadata=metadata,
                    duration_ms=duration_ms,
                )
            raise

        duration_ms = int((time.monotonic() - t0) * 1000)
        await self._after(content, tool_calls, metadata, duration_ms)
        yield RoundOutcome(
            content=content,
            tool_calls=tool_calls,
            parse_errors=parse_errors,
            metadata=metadata,
            duration_ms=duration_ms,
        )
