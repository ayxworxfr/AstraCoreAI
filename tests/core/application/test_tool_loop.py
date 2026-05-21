"""Tests for ToolLoopUseCase — tool execution, security block, max_iterations, build_defs."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from astracore.modules.chat.application.tool_loop import ToolLoopUseCase
from astracore.modules.chat.domain.message import MessageRole, ToolCall
from astracore.modules.chat.domain.session import SessionState
from astracore.modules.tools.ports.tool import (
    ToolDefinition,
    ToolExecutionResult,
    ToolParameter,
    ToolParameterType,
)
from astracore.shared.policy.engine import PolicyConfig, PolicyEngine
from astracore.shared.policy.rules import SecurityRule
from astracore.shared.ports.llm import LLMResponse, StreamEvent, StreamEventType


def _tool_def(name: str = "search") -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description="Search the web",
        parameters=[
            ToolParameter(
                name="query",
                type=ToolParameterType.STRING,
                description="Search query",
                required=True,
            )
        ],
    )


def _exec_result(name: str = "search", output: str = "results") -> ToolExecutionResult:
    return ToolExecutionResult(tool_name=name, success=True, output=output, execution_time_ms=10.0)


@pytest.fixture
def mock_llm():
    llm = AsyncMock()
    llm.generate.return_value = LLMResponse(content="Done", model="test-model")
    return llm


@pytest.fixture
def mock_tools():
    # get_definitions() is synchronous — use MagicMock; only execute() is async
    t = MagicMock()
    t.get_definitions.return_value = [_tool_def()]
    t.execute = AsyncMock(return_value=_exec_result())

    async def _fake_execute_streaming(tool_name, arguments, context=None):
        from astracore.modules.tools.ports.tool import ToolExecutionResult

        yield ToolExecutionResult(
            tool_name=tool_name, success=True, output="results", execution_time_ms=10.0
        )

    t.execute_streaming = _fake_execute_streaming
    t.is_timeout_managed.return_value = False
    return t


@pytest.fixture
def loop_uc(mock_llm, mock_tools):
    return ToolLoopUseCase(
        llm_adapter=mock_llm,
        tool_adapter=mock_tools,
        policy_engine=PolicyEngine(),
        max_iterations=5,
    )


# ---------- execute_with_tools ----------


async def test_execute_with_tools_breaks_immediately_when_no_tool_calls(loop_uc, mock_llm):
    session = SessionState()
    await loop_uc.execute_with_tools(session)
    assert mock_llm.generate.call_count == 1


async def test_execute_with_tools_calls_tool_and_continues(loop_uc, mock_llm, mock_tools):
    tool_call = ToolCall(name="search", arguments={"query": "Python"})
    mock_llm.generate.side_effect = [
        LLMResponse(content="", tool_calls=[tool_call], model="test"),
        LLMResponse(content="Final answer", model="test"),
    ]
    session = SessionState()
    await loop_uc.execute_with_tools(session)

    mock_tools.execute.assert_called_once_with(
        tool_name="search", arguments={"query": "Python"}, context={"profile_id": None}
    )
    assert mock_llm.generate.call_count == 2


async def test_execute_with_tools_blocks_tool_via_security_policy(mock_llm, mock_tools):
    config = PolicyConfig(security=SecurityRule(tool_whitelist=["allowed_tool"]))
    uc = ToolLoopUseCase(mock_llm, mock_tools, PolicyEngine(config))

    tool_call = ToolCall(name="forbidden_tool", arguments={})
    mock_llm.generate.side_effect = [
        LLMResponse(content="", tool_calls=[tool_call], model="test"),
        LLMResponse(content="Done", model="test"),
    ]
    session = SessionState()
    await uc.execute_with_tools(session)

    mock_tools.execute.assert_not_called()


async def test_execute_with_tools_blocked_result_is_error_message(mock_llm, mock_tools):
    config = PolicyConfig(security=SecurityRule(tool_whitelist=["allowed"]))
    uc = ToolLoopUseCase(mock_llm, mock_tools, PolicyEngine(config))

    tool_call = ToolCall(name="blocked", arguments={})
    mock_llm.generate.side_effect = [
        LLMResponse(content="", tool_calls=[tool_call], model="test"),
        LLMResponse(content="Done", model="test"),
    ]
    session = SessionState()
    result = await uc.execute_with_tools(session)

    tool_msgs = [m for m in result.get_messages() if m.role == MessageRole.TOOL]
    assert any(tr.is_error for msg in tool_msgs for tr in msg.tool_results)


async def test_execute_with_tools_respects_max_iterations(mock_llm, mock_tools):
    tool_call = ToolCall(name="search", arguments={"query": "loop"})
    mock_llm.generate.return_value = LLMResponse(content="", tool_calls=[tool_call], model="test")
    uc = ToolLoopUseCase(mock_llm, mock_tools, PolicyEngine(), max_iterations=3)
    session = SessionState()
    await uc.execute_with_tools(session)

    assert mock_llm.generate.call_count == 3


async def test_execute_with_tools_skips_tool_execution_on_final_iteration(mock_llm, mock_tools):
    tool_call = ToolCall(name="search", arguments={"query": "loop"})
    mock_llm.generate.return_value = LLMResponse(content="", tool_calls=[tool_call], model="test")
    uc = ToolLoopUseCase(mock_llm, mock_tools, PolicyEngine(), max_iterations=1)
    session = SessionState()

    result = await uc.execute_with_tools(session)

    mock_tools.execute.assert_not_called()
    assert result.get_messages()[-1].role == MessageRole.ASSISTANT


async def test_execute_stream_with_tools_skips_tool_execution_on_final_iteration(
    mock_tools,
):
    tool_call = ToolCall(name="search", arguments={"query": "loop"})

    class FakeLLM:
        async def generate_stream(self, **kwargs):
            _ = kwargs
            yield StreamEvent(
                event_type=StreamEventType.TOOL_CALL,
                tool_call=tool_call,
            )

    uc = ToolLoopUseCase(FakeLLM(), mock_tools, PolicyEngine(), max_iterations=1)
    session = SessionState()

    events = [event async for event in uc.execute_stream_with_tools(session)]

    mock_tools.execute.assert_not_called()
    assert len(events) == 3
    assert events[0].event_type == StreamEventType.ROUND_START
    assert events[1].tool_call == tool_call
    assert events[2].event_type == StreamEventType.THINKING_STOP
    assert session.get_messages()[-1].role == MessageRole.ASSISTANT


async def test_execute_with_tools_parallel_executes_multiple_tools(mock_llm, mock_tools):
    """Both tool calls in one LLM response run concurrently (asyncio.gather)."""
    tc1 = ToolCall(name="search", arguments={"query": "A"})
    tc2 = ToolCall(name="search", arguments={"query": "B"})
    mock_llm.generate.side_effect = [
        LLMResponse(content="", tool_calls=[tc1, tc2], model="test"),
        LLMResponse(content="Done", model="test"),
    ]
    session = SessionState()
    result = await ToolLoopUseCase(mock_llm, mock_tools, PolicyEngine()).execute_with_tools(session)

    assert mock_tools.execute.call_count == 2
    tool_msgs = [m for m in result.get_messages() if m.role == MessageRole.TOOL]
    assert len(tool_msgs) == 1
    assert len(tool_msgs[0].tool_results) == 2


async def test_execute_stream_with_tools_parallel_results_ordered(mock_tools):
    """Streaming path: two parallel tools emit TOOL_RESULT events; results preserved in order."""
    tc1 = ToolCall(name="search", arguments={"query": "A"})
    tc2 = ToolCall(name="search", arguments={"query": "B"})

    class FakeLLM:
        _call = 0

        async def generate_stream(self, **kwargs):
            _ = kwargs
            FakeLLM._call += 1
            if FakeLLM._call == 1:
                yield StreamEvent(event_type=StreamEventType.TOOL_CALL, tool_call=tc1)
                yield StreamEvent(event_type=StreamEventType.TOOL_CALL, tool_call=tc2)
            else:
                yield StreamEvent(event_type=StreamEventType.TEXT_DELTA, content="Done")

    session = SessionState()
    uc = ToolLoopUseCase(FakeLLM(), mock_tools, PolicyEngine())
    events = [e async for e in uc.execute_stream_with_tools(session)]

    tool_result_events = [e for e in events if e.event_type == StreamEventType.TOOL_RESULT]
    assert len(tool_result_events) == 2

    tool_msgs = [m for m in session.get_messages() if m.role == MessageRole.TOOL]
    assert len(tool_msgs) == 1
    assert len(tool_msgs[0].tool_results) == 2
    # Ordering must match original tool_call order
    assert tool_msgs[0].tool_results[0].tool_call_id == tc1.id
    assert tool_msgs[0].tool_results[1].tool_call_id == tc2.id


# ---------- _build_tool_definitions ----------


def test_build_tool_definitions_shape(loop_uc, mock_tools):
    defs = loop_uc._build_tool_definitions()
    assert len(defs) == 1
    d = defs[0]
    assert d["name"] == "search"
    assert "input_schema" in d
    assert d["input_schema"]["type"] == "object"
    assert "query" in d["input_schema"]["properties"]
    assert "query" in d["input_schema"]["required"]


def test_build_tool_definitions_excludes_optional_params(mock_llm):
    tools = MagicMock()
    tools.get_definitions.return_value = [
        ToolDefinition(
            name="tool",
            description="desc",
            parameters=[
                ToolParameter(
                    name="required_p",
                    type=ToolParameterType.STRING,
                    description="req",
                    required=True,
                ),
                ToolParameter(
                    name="optional_p",
                    type=ToolParameterType.NUMBER,
                    description="opt",
                    required=False,
                ),
            ],
        )
    ]
    uc = ToolLoopUseCase(mock_llm, tools, PolicyEngine())
    defs = uc._build_tool_definitions()
    required = defs[0]["input_schema"]["required"]
    assert "required_p" in required
    assert "optional_p" not in required
