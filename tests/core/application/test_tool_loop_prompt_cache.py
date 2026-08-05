"""Tool loop must not poison the prompt-cache prefix."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from astracore.modules.chat.application.tool_loop import ToolLoopUseCase
from astracore.modules.chat.domain.message import Message, MessageRole, ToolCall
from astracore.modules.chat.domain.session import SessionState
from astracore.modules.chat.domain.session_context import SessionContext
from astracore.modules.tools.ports.tool import (
    ToolDefinition,
    ToolExecutionResult,
    ToolParameter,
    ToolParameterType,
)
from astracore.shared.policy.engine import PolicyEngine
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


@pytest.fixture
def mock_tools():
    t = MagicMock()
    t.get_definitions.return_value = [_tool_def()]
    t.execute = AsyncMock(
        return_value=ToolExecutionResult(
            tool_name="search", ok=True, data="results", execution_time_ms=1.0
        )
    )

    async def _fake_execute_streaming(tool_name, arguments, context=None):
        yield ToolExecutionResult(
            tool_name=tool_name, ok=True, data="results", execution_time_ms=1.0
        )

    t.execute_streaming = _fake_execute_streaming
    t.is_timeout_managed.return_value = False
    return t


def test_prepare_round_prompt_keeps_system_and_varies_tool_progress():
    """进度提示只能进 SessionContext，绝不能改写缓存的 system 前缀。"""
    uc = ToolLoopUseCase(AsyncMock(), MagicMock(), PolicyEngine(), max_iterations=5)
    static = "<security>guard</security>\n\n<identity>卡</identity>"
    msgs = [
        Message(role=MessageRole.SYSTEM, content=static),
        Message(role=MessageRole.USER, content="hi"),
    ]
    base = SessionContext.build(turn_context="", active_skill=None, rag_context=None)
    out_msgs, round_ctx = uc._prepare_round_prompt(msgs, iteration=2, session_context=base)
    assert out_msgs[0].content == static
    rendered = round_ctx.render()
    assert "第 2/5 轮" in rendered
    assert rendered.startswith("<session_context>")
    assert rendered.endswith("</session_context>")
    assert base.tool_progress_xml == ""


def test_main_loop_keeps_tool_definitions_stable(mock_tools):
    """主循环始终暴露同一 tools 列表（不再在末轮卸工具）。"""
    tools = MagicMock()
    tools.get_definitions.return_value = [_tool_def()]
    uc = ToolLoopUseCase(AsyncMock(), tools, PolicyEngine(), max_iterations=3)
    defs = uc._build_tool_definitions()
    assert len(defs) == 1
    assert defs[0]["name"] == "search"


async def test_stream_loop_passes_stable_system_and_session_context_object(mock_tools):
    """多轮工具调用：system 字节级稳定，session_context 为带轮次的 SessionContext。"""
    captured: list[dict] = []
    tool_call = ToolCall(name="search", arguments={"query": "x"})

    class FakeLLM:
        _n = 0

        async def generate_stream(self, **kwargs):
            FakeLLM._n += 1
            sc = kwargs.get("session_context")
            captured.append(
                {
                    "system": next(
                        (m.content for m in kwargs["messages"] if m.role == MessageRole.SYSTEM),
                        None,
                    ),
                    "session_context": sc,
                    "tools": kwargs.get("tools"),
                }
            )
            if FakeLLM._n <= 2:
                yield StreamEvent(event_type=StreamEventType.TOOL_CALL, tool_call=tool_call)
            else:
                yield StreamEvent(event_type=StreamEventType.TEXT_DELTA, content="done")
            yield StreamEvent(event_type=StreamEventType.DONE, metadata={})

    session = SessionState()
    session.add_message(Message(role=MessageRole.SYSTEM, content="STATIC_SYSTEM"))
    session.add_message(Message(role=MessageRole.USER, content="go"))
    uc = ToolLoopUseCase(FakeLLM(), mock_tools, PolicyEngine(), max_iterations=5)
    base = SessionContext.build()
    _ = [e async for e in uc.execute_stream_with_tools(session, session_context=base)]

    assert len(captured) >= 2
    systems = [c["system"] for c in captured if c["system"] is not None]
    assert systems
    assert all(s == "STATIC_SYSTEM" for s in systems)
    assert captured[0]["tools"] is not None
    assert captured[1]["tools"] is not None
    assert isinstance(captured[0]["session_context"], SessionContext)
    assert "第 1/5 轮" in captured[0]["session_context"].render()
    assert "第 2/5 轮" in captured[1]["session_context"].render()


async def test_blocking_loop_forwards_session_context_object(mock_tools):
    """非流式路径也必须透传 SessionContext / enable_prompt_cache。"""
    mock_llm = AsyncMock()
    mock_llm.generate.return_value = LLMResponse(content="ok", model="m")
    uc = ToolLoopUseCase(mock_llm, mock_tools, PolicyEngine(), max_iterations=5)
    session = SessionState()
    session.add_message(Message(role=MessageRole.SYSTEM, content="STATIC"))
    base = SessionContext.build()
    await uc.execute_with_tools(
        session,
        session_context=base,
        enable_prompt_cache=True,
    )
    kwargs = mock_llm.generate.call_args.kwargs
    assert kwargs.get("enable_prompt_cache") is True
    sc = kwargs.get("session_context")
    assert isinstance(sc, SessionContext)
    assert "第 1/5 轮" in sc.render()
    msgs = kwargs["messages"]
    assert msgs[0].content == "STATIC"
