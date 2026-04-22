"""Streaming session safety regression tests."""

import asyncio
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from fastapi import FastAPI
from starlette.requests import Request
from sqlalchemy.pool import NullPool

from astracore.adapters.db.session import get_engine
from astracore.core.domain.message import Message, MessageRole, ToolCall, ToolResult
from astracore.core.ports.llm import LLMResponse, StreamEvent, StreamEventType
from astracore.service.api.chat import ChatRequest, _run_with_tools, _save_short_term_safe, chat_stream


async def test_save_short_term_safe_swallows_cancelled_error(monkeypatch) -> None:
    """请求取消时，会话保存不应抛出取消异常污染日志。"""
    mock_memory = AsyncMock()
    mock_memory.save_short_term.side_effect = asyncio.CancelledError()

    monkeypatch.setattr("astracore.service.api.chat._get_memory_adapter", lambda: mock_memory)

    await _save_short_term_safe(
        session_id=uuid4(),
        messages=[Message(role=MessageRole.USER, content="hello")],
    )

    assert mock_memory.save_short_term.await_count == 1


def test_get_engine_uses_null_pool_for_sqlite() -> None:
    """SQLite engine 应使用 NullPool，减少连接复用终止冲突。"""
    get_engine.cache_clear()
    engine = get_engine("sqlite+aiosqlite:///./test_stream_safety.db")
    try:
        assert isinstance(engine.sync_engine.pool, NullPool)
    finally:
        get_engine.cache_clear()


async def test_run_with_tools_auto_summarizes_when_tool_loop_ends_without_final_text(
    monkeypatch,
) -> None:
    """非流式工具模式在只留下工具结果时，应自动补一轮最终总结。"""

    session_id = uuid4()
    tool_call = ToolCall(name="read_text_file", arguments={"path": "/tmp/demo"})
    fake_memory = AsyncMock()
    fake_memory.load_short_term.return_value = []

    async def fake_execute_with_tools(session, model=None):
        session.add_message(
            Message(role=MessageRole.ASSISTANT, content="", tool_calls=[tool_call])
        )
        session.add_message(
            Message(
                role=MessageRole.TOOL,
                content="",
                tool_results=[
                    ToolResult(
                        tool_call_id=tool_call.id,
                        name=tool_call.name,
                        content="file content",
                    )
                ],
            )
        )
        return session

    fake_tool_loop = MagicMock(max_iterations=3)
    fake_tool_loop.execute_with_tools = fake_execute_with_tools

    fake_llm = AsyncMock()
    fake_llm.generate.return_value = LLMResponse(content="这是自动补出来的总结", model="test")

    monkeypatch.setattr("astracore.service.api.chat._get_memory_adapter", lambda: fake_memory)
    monkeypatch.setattr("astracore.service.api.chat._get_tool_loop_use_case", lambda _: fake_tool_loop)
    monkeypatch.setattr("astracore.service.api.chat._get_llm_adapter", lambda: fake_llm)
    monkeypatch.setattr("astracore.service.api.chat._get_setting_value", AsyncMock(return_value=""))

    result = await _run_with_tools(
        ChatRequest(message="帮我分析一下", use_tools=True),
        session_id,
        MagicMock(),
    )

    assert result == "这是自动补出来的总结"
    assert fake_llm.generate.await_count == 1


async def test_chat_stream_auto_summarizes_when_tool_loop_stops_at_tool_results(
    monkeypatch,
) -> None:
    """流式工具模式在没有最终正文时，应自动补发总结文本而不是空响应。"""

    tool_call = ToolCall(name="read_text_file", arguments={"path": "/tmp/demo"})
    fake_memory = AsyncMock()
    fake_memory.load_short_term.return_value = []

    class FakeToolLoop:
        max_iterations = 1

        async def execute_stream_with_tools(self, session, **kwargs):
            yield StreamEvent(
                event_type=StreamEventType.ROUND_START,
                metadata={"round": 1},
            )
            yield StreamEvent(
                event_type=StreamEventType.TOOL_CALL,
                tool_call=tool_call,
            )
            session.add_message(
                Message(role=MessageRole.ASSISTANT, content="", tool_calls=[tool_call])
            )
            session.add_message(
                Message(
                    role=MessageRole.TOOL,
                    content="",
                    tool_results=[
                        ToolResult(
                            tool_call_id=tool_call.id,
                            name=tool_call.name,
                            content="file content",
                        )
                    ],
                )
            )

    class FakeSummaryLLM:
        def __init__(self) -> None:
            self.calls: list[list[Message]] = []

        async def generate_stream(self, messages, **kwargs):
            self.calls.append(messages)
            yield StreamEvent(
                event_type=StreamEventType.TEXT_DELTA,
                content="基于工具结果的最终总结",
            )
            yield StreamEvent(event_type=StreamEventType.DONE)

    fake_llm = FakeSummaryLLM()

    monkeypatch.setattr("astracore.service.api.chat._get_memory_adapter", lambda: fake_memory)
    monkeypatch.setattr("astracore.service.api.chat._get_tool_loop_use_case", lambda _: FakeToolLoop())
    monkeypatch.setattr("astracore.service.api.chat._get_llm_adapter", lambda: fake_llm)
    monkeypatch.setattr("astracore.service.api.chat._get_setting_value", AsyncMock(return_value=""))

    app = FastAPI()
    request = Request({"type": "http", "app": app, "headers": []})
    response = await chat_stream(ChatRequest(message="帮我分析一下", use_tools=True), request)
    events = [event async for event in response.body_iterator]

    assert {"event": "message", "data": "基于工具结果的最终总结"} in events
    assert events[-1] == {"event": "done", "data": "[DONE]"}
    assert any(
        "已达到工具循环最大轮次" in message.content
        for message in fake_llm.calls[0]
        if message.role == MessageRole.SYSTEM
    )
