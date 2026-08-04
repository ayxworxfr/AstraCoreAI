"""soft_exec：破坏性工具只预览、不落盘。"""

from __future__ import annotations

import pytest

from astracore.modules.chat.application.tool_executor import ToolExecutor
from astracore.modules.chat.domain.message import ToolCall
from astracore.modules.tools.ports.tool import (
    ToolAdapter,
    ToolDefinition,
    ToolExecutionResult,
    ToolParameter,
    ToolParameterType,
)
from astracore.shared.policy.engine import PolicyEngine


class _RecordingTools(ToolAdapter):
    def __init__(self, *, destructive: bool) -> None:
        self.called = False
        self._destructive = destructive

    def get_definitions(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name="wipe",
                description="destructive",
                parameters=[
                    ToolParameter(
                        name="path",
                        type=ToolParameterType.STRING,
                        description="path",
                        required=True,
                    )
                ],
                is_destructive=self._destructive,
                requires_confirmation=False,
            )
        ]

    async def execute(self, tool_name: str, arguments: dict, context=None) -> ToolExecutionResult:
        self.called = True
        return ToolExecutionResult(tool_name=tool_name, ok=True, data="done", execution_time_ms=1)

    async def execute_parallel(self, tool_calls: list, context=None) -> list[ToolExecutionResult]:
        return [await self.execute(n, a, context) for n, a in tool_calls]


@pytest.mark.asyncio
async def test_soft_exec_skips_destructive_tool():
    tools = _RecordingTools(destructive=True)
    executor = ToolExecutor(
        tools,
        PolicyEngine(),
        extra_context={"soft_exec": True},
        profile_id=None,
        max_tool_result_chars=2000,
        tool_timeout_s=5.0,
    )
    result = await executor.run(ToolCall(name="wipe", arguments={"path": "/tmp/x"}))
    assert tools.called is False
    assert result.is_error is False
    assert "[soft_exec]" in result.content
    assert result.metadata.get("soft_exec") is True


@pytest.mark.asyncio
async def test_soft_exec_still_runs_non_destructive():
    tools = _RecordingTools(destructive=False)
    executor = ToolExecutor(
        tools,
        PolicyEngine(),
        extra_context={"soft_exec": True},
        profile_id=None,
        max_tool_result_chars=2000,
        tool_timeout_s=5.0,
    )
    result = await executor.run(ToolCall(name="wipe", arguments={"path": "/tmp/x"}))
    assert tools.called is True
    assert result.is_error is False
