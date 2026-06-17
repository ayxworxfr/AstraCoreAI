"""Composite tool adapter — merges tools from multiple ToolAdapter instances."""

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from astracore.modules.tools.ports.tool import (
    MutableToolAdapter,
    ToolAdapter,
    ToolDefinition,
    ToolError,
    ToolErrorCode,
    ToolExecutionResult,
    ToolParameter,
)
from astracore.shared.observability.logger import get_logger
from astracore.shared.ports.llm import StreamEvent

logger = get_logger(__name__)


class CompositeToolAdapter(MutableToolAdapter):
    """Delegates tool execution to the correct child adapter.

    Tools are deduplicated by name; adapters listed first take priority when
    the same tool name appears in multiple adapters.

    The routing map is built eagerly at construction time and updated lazily
    when a tool is found during execution that was registered after construction
    (e.g. via a NativeToolAdapter that is also held as ``_user_adapter``).

    Usage::

        adapter = CompositeToolAdapter([builtin_adapter, user_adapter, mcp_adapter])
        # All tools from all adapters are visible to the LLM.
    """

    def __init__(self, adapters: list[ToolAdapter]) -> None:
        self._adapters = adapters
        self._routing: dict[str, ToolAdapter] = {}
        for adapter in adapters:
            for defn in adapter.get_definitions():
                if defn.name not in self._routing:
                    self._routing[defn.name] = adapter

    # ------------------------------------------------------------------
    # Routing helpers
    # ------------------------------------------------------------------

    def _find_adapter(self, tool_name: str) -> ToolAdapter | None:
        """Look up adapter for *tool_name*, updating the cache on a miss."""
        adapter = self._routing.get(tool_name)
        if adapter is not None:
            return adapter
        for a in self._adapters:
            if any(d.name == tool_name for d in a.get_definitions()):
                self._routing[tool_name] = a
                return a
        return None

    # ------------------------------------------------------------------
    # ToolAdapter interface
    # ------------------------------------------------------------------

    async def execute(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> ToolExecutionResult:
        adapter = self._find_adapter(tool_name)
        if adapter is None:
            return ToolExecutionResult(
                tool_name=tool_name,
                ok=False,
                error=ToolError(
                    code=ToolErrorCode.TOOL_NOT_FOUND,
                    message=f"Tool '{tool_name}' not found in any registered adapter",
                    retryable=False,
                ),
                execution_time_ms=0.0,
            )
        return await adapter.execute(tool_name, arguments, context)

    async def execute_parallel(
        self,
        tool_calls: list[tuple[str, dict[str, Any]]],
        context: dict[str, Any] | None = None,
    ) -> list[ToolExecutionResult]:
        tasks = [self.execute(name, args, context) for name, args in tool_calls]
        return list(await asyncio.gather(*tasks))

    async def execute_streaming(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> AsyncIterator[StreamEvent | ToolExecutionResult]:
        adapter = self._find_adapter(tool_name)
        if adapter is None:
            yield ToolExecutionResult(
                tool_name=tool_name,
                ok=False,
                error=ToolError(
                    code=ToolErrorCode.TOOL_NOT_FOUND,
                    message=f"Tool '{tool_name}' not found in any registered adapter",
                    retryable=False,
                ),
                execution_time_ms=0.0,
            )
            return
        async for item in adapter.execute_streaming(tool_name, arguments, context):
            yield item

    def is_timeout_managed(self, tool_name: str) -> bool:
        adapter = self._find_adapter(tool_name)
        return adapter.is_timeout_managed(tool_name) if adapter is not None else False

    def get_definitions(self) -> list[ToolDefinition]:
        seen: set[str] = set()
        result: list[ToolDefinition] = []
        for adapter in self._adapters:
            for defn in adapter.get_definitions():
                if defn.name not in seen:
                    result.append(defn)
                    seen.add(defn.name)
        return result

    # ------------------------------------------------------------------
    # MutableToolAdapter interface
    # ------------------------------------------------------------------

    def register_tool(
        self,
        name: str,
        func: Any,
        description: str,
        parameters: list[ToolParameter],
        requires_confirmation: bool = False,
    ) -> None:
        """Delegate registration to the first NativeToolAdapter in the chain."""
        from astracore.infrastructure.tools.native import NativeToolAdapter  # noqa: PLC0415

        for adapter in self._adapters:
            if isinstance(adapter, NativeToolAdapter):
                adapter.register_tool(name, func, description, parameters, requires_confirmation)
                self._routing[name] = adapter
                return
        raise NotImplementedError(
            "CompositeToolAdapter has no NativeToolAdapter to register tools into."
        )
