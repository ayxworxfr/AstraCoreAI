"""Native tool adapter implementation."""

import asyncio
import inspect
import time
from collections.abc import Callable
from typing import Any

from astracore.core.ports.tool import (
    ToolAdapter,
    ToolDefinition,
    ToolExecutionResult,
    ToolParameter,
)


class NativeToolAdapter(ToolAdapter):
    """Native Python function tool adapter."""

    def __init__(self):
        self._tools: dict[str, Callable[..., Any]] = {}
        self._definitions: dict[str, ToolDefinition] = {}

    def register_tool(
        self,
        name: str,
        func: Callable[..., Any],
        description: str,
        parameters: list[ToolParameter],
        requires_confirmation: bool = False,
    ) -> None:
        """Register a new tool."""
        self._tools[name] = func
        self._definitions[name] = ToolDefinition(
            name=name,
            description=description,
            parameters=parameters,
            requires_confirmation=requires_confirmation,
        )

    async def execute(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> ToolExecutionResult:
        """Execute a tool."""
        if tool_name not in self._tools:
            return ToolExecutionResult(
                tool_name=tool_name,
                success=False,
                output="",
                error=f"Tool '{tool_name}' not found",
                execution_time_ms=0.0,
            )

        func = self._tools[tool_name]
        start_time = time.time()

        try:
            if inspect.iscoroutinefunction(func):
                result = await func(**arguments)
            else:
                result = func(**arguments)

            execution_time = (time.time() - start_time) * 1000

            return ToolExecutionResult(
                tool_name=tool_name,
                success=True,
                output=str(result),
                execution_time_ms=execution_time,
                metadata={"context": context} if context else {},
            )

        except Exception as e:
            execution_time = (time.time() - start_time) * 1000
            return ToolExecutionResult(
                tool_name=tool_name,
                success=False,
                output="",
                error=str(e),
                execution_time_ms=execution_time,
            )

    async def execute_parallel(
        self,
        tool_calls: list[tuple[str, dict[str, Any]]],
        context: dict[str, Any] | None = None,
    ) -> list[ToolExecutionResult]:
        """Execute multiple tools in parallel."""
        tasks = [self.execute(name, args, context) for name, args in tool_calls]
        return await asyncio.gather(*tasks)

    def get_definitions(self) -> list[ToolDefinition]:
        """Get all tool definitions."""
        return list(self._definitions.values())
