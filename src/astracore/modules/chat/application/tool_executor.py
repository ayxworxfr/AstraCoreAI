"""单工具执行管线 —— Schema → Hook → HITL → Policy → call。

流式与非流式共用同一套 gate；仅最后一跳（execute / execute_streaming）不同。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import AsyncIterator, Callable, Coroutine
from typing import Any, cast

from astracore.modules.chat.domain.message import ToolCall, ToolResult
from astracore.modules.tools.application.validate import validate_tool_arguments
from astracore.modules.tools.ports.tool import (
    ToolAdapter,
    ToolDefinition,
    ToolError,
    ToolErrorCode,
    ToolExecutionResult,
)
from astracore.shared.domain.hitl import HITLOption, PendingQuestion
from astracore.shared.observability.hooks import (
    HookRegistry,
    ShortCircuit,
    ToolCallInput,
    ToolCallOutput,
)
from astracore.shared.policy.engine import PolicyEngine
from astracore.shared.ports.llm import StreamEvent
from astracore.shared.security.external_data import wrap_external

HitlCallback = Callable[..., Coroutine[Any, Any, dict[str, Any]]]


async def ask_tool_confirmation(
    tool_call: ToolCall,
    hitl_callback: HitlCallback,
) -> bool:
    """向用户请求工具执行审批。"""
    args_preview = json.dumps(tool_call.arguments, ensure_ascii=False)[:200]
    q = PendingQuestion(
        question=(f"AI 即将执行工具 `{tool_call.name}`，参数预览：\n```json\n{args_preview}\n```"),
        header="工具确认",
        options=[
            HITLOption(label="允许", description="继续执行此工具"),
            HITLOption(label="拒绝", description="取消执行"),
        ],
        allow_freeform=False,
    )
    answer = await hitl_callback(q)
    return "允许" in answer.get("selected", [])


class ToolExecutor:
    """单工具协议对象执行器（策略：gate 固定，调用方式可切换）。"""

    def __init__(
        self,
        tools: ToolAdapter,
        policy: PolicyEngine,
        *,
        extra_context: dict[str, Any],
        profile_id: str | None,
        max_tool_result_chars: int,
        tool_timeout_s: float,
        hooks: HookRegistry | None = None,
    ) -> None:
        self._tools = tools
        self._policy = policy
        self._ctx = extra_context
        self._profile_id = profile_id
        self._max_chars = max_tool_result_chars
        self._timeout_s = tool_timeout_s
        self._hooks = hooks

    def definition_map(self) -> dict[str, ToolDefinition]:
        return {d.name: d for d in self._tools.get_definitions()}

    def truncate(self, content: str, tool_name: str = "") -> str:
        limit = self.max_chars_for(tool_name)
        if len(content) <= limit:
            return content
        return (
            content[:limit] + f"\n\n[内容已截断，原始长度 {len(content)} 字符。"
            "如需查看更多，请使用 offset/page 参数重新调用工具。]"
        )

    def max_chars_for(self, tool_name: str) -> int:
        defn = self.definition_map().get(tool_name)
        if defn is not None:
            per_tool = defn.metadata.get("max_output_chars")
            if per_tool is not None:
                return int(per_tool)
        return self._max_chars

    def timeout_for(self, tool_name: str) -> float | None:
        defn = self.definition_map().get(tool_name)
        if defn is not None:
            per_tool = defn.metadata.get("timeout_s")
            if per_tool is not None:
                return float(per_tool) or None
        return self._timeout_s or None

    def _should_ask_confirmation(self, tool_name: str) -> bool:
        defn = self.definition_map().get(tool_name)
        if not (defn and defn.requires_confirmation):
            return False
        hitl = self._ctx.get("hitl")
        if hitl is None:
            return True
        if not getattr(hitl, "enabled", True):
            return False
        if not getattr(hitl, "require_tool_approval", True):
            return False
        return True

    def _validate(self, tool_call: ToolCall) -> ToolResult | None:
        defn = self.definition_map().get(tool_call.name)
        if defn is None:
            return None
        result = validate_tool_arguments(defn, tool_call.arguments)
        if result.ok:
            return None
        return ToolResult(
            tool_call_id=tool_call.id,
            name=tool_call.name,
            content=result.error_message(),
            is_error=True,
        )

    async def _fire_before(self, tool_call: ToolCall) -> ToolCallInput | ShortCircuit:
        payload = ToolCallInput(
            tool_call_id=tool_call.id,
            tool_name=tool_call.name,
            arguments=tool_call.arguments,
        )
        if self._hooks:
            return await self._hooks.run_before_tool(payload)
        return payload

    async def fire_after(self, tool_result: ToolResult, duration_ms: int = 0) -> ToolCallOutput:
        """对外暴露 after_tool hook（解析失败等短路路径也会走这里）。"""
        payload = ToolCallOutput(
            tool_call_id=tool_result.tool_call_id,
            tool_name=tool_result.name,
            content=tool_result.content,
            is_error=tool_result.is_error,
            duration_ms=duration_ms,
            metadata=tool_result.metadata or {},
        )
        if self._hooks:
            payload = await self._hooks.run_after_tool(payload)
        return payload

    async def _apply_gates(self, tool_call: ToolCall) -> ToolCallInput | ToolResult:
        """跑完前置门禁；失败/短路返回 ToolResult，通过返回可执行的 ToolCallInput。"""
        validation_error = self._validate(tool_call)
        if validation_error is not None:
            await self.fire_after(validation_error)
            return validation_error

        hook_result = await self._fire_before(tool_call)
        if isinstance(hook_result, ShortCircuit):
            sc_out = cast(ToolCallOutput, hook_result.result)
            result = ToolResult(
                tool_call_id=sc_out.tool_call_id,
                name=sc_out.tool_name,
                content=sc_out.content,
                is_error=sc_out.is_error,
            )
            return result

        hook_input = hook_result

        if self._should_ask_confirmation(hook_input.tool_name):
            hitl_callback = self._ctx.get("hitl_callback")
            if hitl_callback is None:
                result = ToolResult(
                    tool_call_id=hook_input.tool_call_id,
                    name=hook_input.tool_name,
                    content="当前环境不支持工具审批，已拒绝执行此工具。",
                    is_error=True,
                )
                await self.fire_after(result)
                return result
            approved = await ask_tool_confirmation(tool_call, hitl_callback)
            if not approved:
                result = ToolResult(
                    tool_call_id=hook_input.tool_call_id,
                    name=hook_input.tool_name,
                    content="用户拒绝执行此工具。",
                    is_error=False,
                )
                await self.fire_after(result)
                return result

        if not self._policy.check_security_policy(hook_input.tool_name, hook_input.arguments):
            result = ToolResult(
                tool_call_id=hook_input.tool_call_id,
                name=hook_input.tool_name,
                content="Tool execution blocked by security policy",
                is_error=True,
            )
            await self.fire_after(result)
            return result

        return hook_input

    def _wrap_exec_result(
        self,
        hook_input: ToolCallInput,
        exec_result: ToolExecutionResult,
        duration_ms: int,
    ) -> ToolResult:
        raw = (
            (exec_result.data if isinstance(exec_result.data, str) else str(exec_result.data or ""))
            if exec_result.ok
            else (exec_result.error.message if exec_result.error else "Tool execution failed")
        )
        content = wrap_external(
            self.truncate(raw, exec_result.tool_name),
            source=f"tool:{exec_result.tool_name}",
        )
        return ToolResult(
            tool_call_id=hook_input.tool_call_id,
            name=exec_result.tool_name,
            content=content,
            is_error=not exec_result.ok,
            metadata=exec_result.metadata,
        )

    def _timeout_result(self, hook_input: ToolCallInput, timeout_s: float) -> ToolResult:
        return ToolResult(
            tool_call_id=hook_input.tool_call_id,
            name=hook_input.tool_name,
            content=(
                f"[超时] 工具 '{hook_input.tool_name}' 执行超过 {timeout_s:.0f}s，"
                "已中止。请换用更精确的参数重试。"
            ),
            is_error=True,
        )

    def _exec_context(self) -> dict[str, Any]:
        return {**self._ctx, "profile_id": self._profile_id}

    def _soft_exec_result(self, hook_input: ToolCallInput) -> ToolResult | None:
        """破坏性工具软执行：只返回参数预览，不调用真实工具。"""
        if not self._ctx.get("soft_exec"):
            return None
        defn = self.definition_map().get(hook_input.tool_name)
        if defn is None or not defn.is_destructive:
            return None
        preview = json.dumps(hook_input.arguments, ensure_ascii=False)[:2000]
        return ToolResult(
            tool_call_id=hook_input.tool_call_id,
            name=hook_input.tool_name,
            content=(
                f"[soft_exec] 已跳过破坏性工具 `{hook_input.tool_name}`，"
                f"参数预览：\n```json\n{preview}\n```"
            ),
            is_error=False,
            metadata={"soft_exec": True},
        )

    async def run(self, tool_call: ToolCall) -> ToolResult:
        """非流式执行完整管线。"""
        prepared = await self._apply_gates(tool_call)
        if isinstance(prepared, ToolResult):
            return prepared

        soft = self._soft_exec_result(prepared)
        if soft is not None:
            await self.fire_after(soft)
            return soft

        timeout = self.timeout_for(prepared.tool_name)
        t0 = time.monotonic()
        try:
            exec_result = await asyncio.wait_for(
                self._tools.execute(
                    tool_name=prepared.tool_name,
                    arguments=prepared.arguments,
                    context=self._exec_context(),
                ),
                timeout=timeout,
            )
        except TimeoutError:
            duration_ms = int((time.monotonic() - t0) * 1000)
            result = self._timeout_result(prepared, timeout or 0)
            await self.fire_after(result, duration_ms=duration_ms)
            return result

        duration_ms = int((time.monotonic() - t0) * 1000)
        result = self._wrap_exec_result(prepared, exec_result, duration_ms)
        await self.fire_after(result, duration_ms=duration_ms)
        return result

    async def run_streaming(self, tool_call: ToolCall) -> AsyncIterator[StreamEvent | ToolResult]:
        """流式执行：中间 StreamEvent 原样透出，最后一项恒为 ToolResult。"""
        prepared = await self._apply_gates(tool_call)
        if isinstance(prepared, ToolResult):
            yield prepared
            return

        soft = self._soft_exec_result(prepared)
        if soft is not None:
            await self.fire_after(soft)
            yield soft
            return

        timeout = self.timeout_for(prepared.tool_name)
        t0 = time.monotonic()
        exec_result: ToolExecutionResult | None = None
        timeout_cm = (
            contextlib.nullcontext()
            if self._tools.is_timeout_managed(prepared.tool_name)
            else asyncio.timeout(timeout)
        )
        try:
            async with timeout_cm:
                async for item in self._tools.execute_streaming(
                    tool_name=prepared.tool_name,
                    arguments=prepared.arguments,
                    context=self._exec_context(),
                ):
                    if isinstance(item, StreamEvent):
                        yield item
                    else:
                        exec_result = item
        except TimeoutError:
            duration_ms = int((time.monotonic() - t0) * 1000)
            result = self._timeout_result(prepared, timeout or 0)
            await self.fire_after(result, duration_ms=duration_ms)
            yield result
            return

        if exec_result is None:
            exec_result = ToolExecutionResult(
                tool_name=prepared.tool_name,
                ok=False,
                error=ToolError(
                    code=ToolErrorCode.EXECUTION_ERROR,
                    message="Tool returned no result",
                    retryable=True,
                ),
                execution_time_ms=int((time.monotonic() - t0) * 1000),
            )

        duration_ms = int((time.monotonic() - t0) * 1000)
        result = self._wrap_exec_result(prepared, exec_result, duration_ms)
        await self.fire_after(result, duration_ms=duration_ms)
        yield result
