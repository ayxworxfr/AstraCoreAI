"""Composite tool adapter — merges tools from multiple ToolAdapter instances."""

import asyncio
from typing import Any

from astracore.core.ports.tool import (
    ToolAdapter,
    ToolDefinition,
    ToolExecutionResult,
    ToolParameter,
)
from astracore.runtime.observability.logger import get_logger

logger = get_logger(__name__)


class CompositeToolAdapter(ToolAdapter):
    """Delegates tool execution to the correct child adapter.

    Tools are deduplicated by name; adapters listed first take priority when
    the same tool name appears in multiple adapters.

    Usage::

        adapter = CompositeToolAdapter([native_adapter, mcp_adapter])
        # All tools from both adapters are visible to the LLM.
    """

    def __init__(self, adapters: list[ToolAdapter]) -> None:
        self._adapters = adapters
        # Build tool_name -> adapter routing map (first-seen wins)
        self._routing: dict[str, ToolAdapter] = {}
        for adapter in adapters:
            for defn in adapter.get_definitions():
                if defn.name not in self._routing:
                    self._routing[defn.name] = adapter

    # ------------------------------------------------------------------
    # ToolAdapter interface
    # ------------------------------------------------------------------

    async def execute(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> ToolExecutionResult:
        adapter = self._routing.get(tool_name)
        if adapter is None:
            return ToolExecutionResult(
                tool_name=tool_name,
                success=False,
                output="",
                error=f"Tool '{tool_name}' not found in any registered adapter",
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

    def get_definitions(self) -> list[ToolDefinition]:
        seen: set[str] = set()
        result: list[ToolDefinition] = []
        for adapter in self._adapters:
            for defn in adapter.get_definitions():
                if defn.name not in seen:
                    result.append(defn)
                    seen.add(defn.name)
        return result

    def register_tool(
        self,
        name: str,
        func: Any,
        description: str,
        parameters: list[ToolParameter],
        requires_confirmation: bool = False,
    ) -> None:
        """Delegate registration to the first NativeToolAdapter in the chain."""
        from astracore.adapters.tools.native import NativeToolAdapter  # noqa: PLC0415

        for adapter in self._adapters:
            if isinstance(adapter, NativeToolAdapter):
                adapter.register_tool(name, func, description, parameters, requires_confirmation)
                # Update routing map
                self._routing[name] = adapter
                return
        raise NotImplementedError(
            "CompositeToolAdapter has no NativeToolAdapter to register tools into."
        )
