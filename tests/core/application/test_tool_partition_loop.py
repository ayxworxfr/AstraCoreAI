"""ToolLoop 分区调度：写工具不与读工具并行。"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from astracore.modules.chat.application.tool_loop import ToolLoopUseCase
from astracore.modules.chat.domain.message import ToolCall
from astracore.modules.chat.domain.session import SessionState
from astracore.modules.tools.ports.tool import ToolDefinition, ToolExecutionResult
from astracore.shared.policy.engine import PolicyEngine
from astracore.shared.ports.llm import LLMResponse


@pytest.mark.asyncio
async def test_write_tool_not_parallel_with_reads():
    """同轮 [read, write, read]：write 独占批次，不与任何工具 gather。"""
    barrier = asyncio.Event()
    active = 0
    max_active = 0
    lock = asyncio.Lock()

    async def execute(tool_name, arguments, context=None):
        nonlocal active, max_active
        async with lock:
            active += 1
            max_active = max(max_active, active)
        if tool_name == "write_mem":
            # 写工具执行期间不应有其他工具在跑
            await asyncio.sleep(0.05)
            assert active == 1
        else:
            await asyncio.sleep(0.02)
        async with lock:
            active -= 1
        return ToolExecutionResult(tool_name=tool_name, ok=True, data="ok", execution_time_ms=1.0)

    tools = MagicMock()
    tools.get_definitions.return_value = [
        ToolDefinition(name="read_a", description="r", is_concurrency_safe=True, is_readonly=True),
        ToolDefinition(name="write_mem", description="w", is_concurrency_safe=False),
        ToolDefinition(name="read_b", description="r", is_concurrency_safe=True, is_readonly=True),
    ]
    tools.execute = AsyncMock(side_effect=execute)
    tools.is_timeout_managed.return_value = False

    async def _stream(*a, **k):
        if False:
            yield  # pragma: no cover

    tools.execute_streaming = _stream

    llm = AsyncMock()
    llm.generate.side_effect = [
        LLMResponse(
            content="",
            tool_calls=[
                ToolCall(name="read_a", arguments={}),
                ToolCall(name="write_mem", arguments={}),
                ToolCall(name="read_b", arguments={}),
            ],
            model="t",
        ),
        LLMResponse(content="done", model="t"),
    ]

    uc = ToolLoopUseCase(
        llm_adapter=llm,
        tool_adapter=tools,
        policy_engine=PolicyEngine(),
        max_iterations=5,
    )
    await uc.execute_with_tools(SessionState())

    assert tools.execute.await_count == 3
    # 若三者全并行，max_active 会到 3；分区后写独占，峰值应为 1 或 2（仅两读可并行）
    assert max_active <= 2
    barrier.set()


@pytest.mark.asyncio
async def test_schema_validation_error_returns_to_model():
    tools = MagicMock()
    tools.get_definitions.return_value = [
        ToolDefinition(
            name="search",
            description="s",
            parameters=[],
            # 用 validate 侧的 required 参数：这里直接构造带 required 的定义
        )
    ]
    from astracore.modules.tools.ports.tool import ToolParameter, ToolParameterType

    tools.get_definitions.return_value = [
        ToolDefinition(
            name="search",
            description="s",
            parameters=[
                ToolParameter(
                    name="query",
                    type=ToolParameterType.STRING,
                    description="q",
                    required=True,
                )
            ],
            is_concurrency_safe=True,
            is_readonly=True,
        )
    ]
    tools.execute = AsyncMock()
    tools.is_timeout_managed.return_value = False

    async def _stream(*a, **k):
        if False:
            yield

    tools.execute_streaming = _stream

    llm = AsyncMock()
    llm.generate.side_effect = [
        LLMResponse(
            content="",
            tool_calls=[ToolCall(name="search", arguments={})],  # missing query
            model="t",
        ),
        LLMResponse(content="fixed", model="t"),
    ]

    uc = ToolLoopUseCase(
        llm_adapter=llm,
        tool_adapter=tools,
        policy_engine=PolicyEngine(),
        max_iterations=5,
    )
    session = SessionState()
    await uc.execute_with_tools(session)

    tools.execute.assert_not_awaited()
    # 错误结果已写入 session tool message
    tool_msgs = [m for m in session.get_messages() if m.tool_results]
    assert tool_msgs
    assert tool_msgs[0].tool_results[0].is_error is True
    assert "required" in tool_msgs[0].tool_results[0].content
