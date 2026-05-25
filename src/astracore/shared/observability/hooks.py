"""Hook/callback system for LLM and tool call interception.

Hooks use list-based chaining: each hook receives the current value and may
return a modified value (replacing it), None (no-op / pass-through), or a
ShortCircuit object to skip the underlying LLM / tool execution entirely.
Exceptions in individual hooks are suppressed and logged.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, cast

from astracore.shared.observability.logger import get_logger

_logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Input / output payload types
# ---------------------------------------------------------------------------


@dataclass
class LLMCallInput:
    messages: list[Any]
    model: str | None
    tools: list[dict[str, Any]] | None
    kwargs: dict[str, Any]


@dataclass
class LLMCallOutput:
    content: str
    tool_calls: list[Any]
    metadata: dict[str, Any]
    duration_ms: int


@dataclass
class ToolCallInput:
    tool_call_id: str
    tool_name: str
    arguments: dict[str, Any]


@dataclass
class ToolCallOutput:
    tool_call_id: str
    tool_name: str
    content: str
    is_error: bool
    duration_ms: int
    metadata: dict[str, Any]


# ---------------------------------------------------------------------------
# Short-circuit sentinel
# ---------------------------------------------------------------------------


@dataclass
class ShortCircuit:
    """Hook 返回此对象时，跳过后续执行，直接使用 result 作为调用结果。

    用途：缓存命中直接返回、测试 mock、guardrail 拦截。

    - before_llm 钩子中返回 ``ShortCircuit(result=LLMCallOutput(...))`` 可完全跳过 LLM 调用。
    - before_tool 钩子中返回 ``ShortCircuit(result=ToolCallOutput(...))`` 可完全跳过工具执行。
    """

    result: LLMCallOutput | ToolCallOutput


# ---------------------------------------------------------------------------
# Hook type aliases
# ---------------------------------------------------------------------------

# A hook may be sync or async.  It receives the payload and optionally returns
# a modified payload (None == keep as-is), or ShortCircuit to abort execution.
BeforeLLMHook = Callable[
    [LLMCallInput],
    LLMCallInput | ShortCircuit | None | Awaitable[LLMCallInput | ShortCircuit | None],
]
AfterLLMHook = Callable[[LLMCallOutput], LLMCallOutput | None | Awaitable[LLMCallOutput | None]]
BeforeToolHook = Callable[
    [ToolCallInput],
    ToolCallInput | ShortCircuit | None | Awaitable[ToolCallInput | ShortCircuit | None],
]
AfterToolHook = Callable[[ToolCallOutput], ToolCallOutput | None | Awaitable[ToolCallOutput | None]]


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


@dataclass
class HookRegistry:
    """Container for all registered hook lists.

    Usage::

        registry = HookRegistry()
        registry.before_llm.append(my_hook)

        modified_input = await registry.run_before_llm(llm_input)
    """

    before_llm: list[BeforeLLMHook] = field(default_factory=list)
    after_llm: list[AfterLLMHook] = field(default_factory=list)
    before_tool: list[BeforeToolHook] = field(default_factory=list)
    after_tool: list[AfterToolHook] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Internal helper
    # ------------------------------------------------------------------

    async def _run_hooks(self, hooks: list[Any], value: Any) -> Any:
        """Chain-execute hooks.

        - Hook returns non-None non-ShortCircuit → replace payload, continue chain.
        - Hook returns ShortCircuit → stop chain immediately, return the ShortCircuit.
        - Hook returns None → pass-through, continue chain.
        - Hook raises → log, skip to next hook.
        """
        for hook in hooks:
            try:
                result = hook(value)
                if asyncio.iscoroutine(result):
                    result = await result
                if isinstance(result, ShortCircuit):
                    return result
                if result is not None:
                    value = result
            except Exception:
                _logger.exception("Hook %r raised an exception; skipping", hook)
        return value

    # ------------------------------------------------------------------
    # Public run methods
    # ------------------------------------------------------------------

    async def run_before_llm(self, payload: LLMCallInput) -> LLMCallInput | ShortCircuit:
        return cast(LLMCallInput | ShortCircuit, await self._run_hooks(self.before_llm, payload))

    async def run_after_llm(self, payload: LLMCallOutput) -> LLMCallOutput:
        return cast(LLMCallOutput, await self._run_hooks(self.after_llm, payload))

    async def run_before_tool(self, payload: ToolCallInput) -> ToolCallInput | ShortCircuit:
        return cast(ToolCallInput | ShortCircuit, await self._run_hooks(self.before_tool, payload))

    async def run_after_tool(self, payload: ToolCallOutput) -> ToolCallOutput:
        return cast(ToolCallOutput, await self._run_hooks(self.after_tool, payload))
