"""Read-before-edit enforcement adapter for MCP filesystem tools."""

from collections.abc import AsyncIterator
from typing import Any

from astracore.modules.tools.ports.tool import (
    ToolAdapter,
    ToolDefinition,
    ToolError,
    ToolErrorCode,
    ToolExecutionResult,
)
from astracore.shared.ports.llm import StreamEvent

_READ_TOOLS = frozenset({"read_file", "read_multiple_files"})
_EDIT_TOOLS = frozenset({"edit_file"})


def _normalize(path: str) -> str:
    return path.replace("\\", "/")


def _read_paths(tool_name: str, arguments: dict[str, Any]) -> list[str]:
    if tool_name == "read_multiple_files":
        paths = arguments.get("paths", [])
        return [_normalize(p) for p in paths if isinstance(p, str)]
    raw = arguments.get("path", "")
    return [_normalize(raw)] if raw else []


class ReadTrackedToolAdapter(ToolAdapter):
    """Decorator that enforces read_file before edit_file on MCP filesystem tools.

    Tracks which file paths have been successfully read_file'd via
    context["_read_files"] (a mutable set). Because ToolLoopUseCase passes
    context as a shallow copy ({**self._extra_context, ...}), the set object
    is shared by reference across all tool calls in the same run, making
    per-run tracking work without any additional wiring.
    """

    def __init__(self, inner: ToolAdapter) -> None:
        self._inner = inner

    def _read_set(self, context: dict[str, Any] | None) -> set[str]:
        if not context:
            return set()
        rs = context.get("_read_files")
        return rs if isinstance(rs, set) else set()

    def _track(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        context: dict[str, Any] | None,
        result: ToolExecutionResult,
    ) -> None:
        if not result.ok or not context:
            return
        rs = context.get("_read_files")
        if isinstance(rs, set):
            rs.update(_read_paths(tool_name, arguments))

    def _edit_blocked(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        context: dict[str, Any] | None,
    ) -> ToolExecutionResult | None:
        path = _normalize(arguments.get("path", ""))
        if not path:
            return None
        if path not in self._read_set(context):
            return ToolExecutionResult(
                tool_name=tool_name,
                ok=False,
                error=ToolError(
                    code=ToolErrorCode.POLICY_BLOCKED,
                    message=f"[read_before_edit] '{path}' 尚未读取。",
                    retryable=True,
                    hint=(
                        f"请先调用 read_file(path='{path}')，"
                        "将返回内容完整复制为 old_string，再重试 edit_file。"
                    ),
                ),
                execution_time_ms=0.0,
            )
        return None

    async def execute(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> ToolExecutionResult:
        if tool_name in _EDIT_TOOLS:
            blocked = self._edit_blocked(tool_name, arguments, context)
            if blocked is not None:
                return blocked
        result = await self._inner.execute(
            tool_name=tool_name, arguments=arguments, context=context
        )
        if tool_name in _READ_TOOLS:
            self._track(tool_name, arguments, context, result)
        return result

    async def execute_parallel(
        self,
        tool_calls: list[tuple[str, dict[str, Any]]],
        context: dict[str, Any] | None = None,
    ) -> list[ToolExecutionResult]:
        results: list[ToolExecutionResult] = []
        for tool_name, arguments in tool_calls:
            results.append(await self.execute(tool_name, arguments, context))
        return results

    async def execute_streaming(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> AsyncIterator[StreamEvent | ToolExecutionResult]:
        if tool_name in _EDIT_TOOLS:
            blocked = self._edit_blocked(tool_name, arguments, context)
            if blocked is not None:
                yield blocked
                return

        last_result: ToolExecutionResult | None = None
        async for item in self._inner.execute_streaming(
            tool_name=tool_name, arguments=arguments, context=context
        ):
            if isinstance(item, ToolExecutionResult):
                last_result = item
            yield item

        if tool_name in _READ_TOOLS and last_result is not None:
            self._track(tool_name, arguments, context, last_result)

    def is_timeout_managed(self, tool_name: str) -> bool:
        return self._inner.is_timeout_managed(tool_name)

    def get_definitions(self) -> list[ToolDefinition]:
        return self._inner.get_definitions()
