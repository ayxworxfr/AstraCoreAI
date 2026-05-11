"""Streaming session safety regression tests."""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from sqlalchemy.pool import NullPool

from astracore.adapters.db.session import get_engine
from astracore.core.domain.chat_context import ChatContext
from astracore.core.domain.message import Message, MessageRole, ToolCall, ToolResult
from astracore.core.domain.session import SessionState
from astracore.core.ports.llm import StreamEvent, StreamEventType
from astracore.core.ports.memory import MemoryAdapter
from astracore.runtime.policy.engine import PolicyEngine
from astracore.service.api.chat import (
    _ACTIVE_RUNS,
    _ActiveRun,
    _broadcast_run_event,
)
from astracore.service.chat_pipeline import ChatPipeline


async def test_save_session_safe_swallows_cancelled_error() -> None:
    """请求取消时，会话保存不应抛出取消异常污染日志。"""
    mock_memory = AsyncMock(spec=MemoryAdapter)
    mock_memory.save_short_term.side_effect = asyncio.CancelledError()

    pipeline = ChatPipeline(
        config=MagicMock(),
        memory=mock_memory,
        rag_pipeline=AsyncMock(),
        policy=PolicyEngine(),
        tool_adapter=MagicMock(),
    )

    await pipeline._save_session_safe(
        session_id=uuid4(),
        messages=[Message(role=MessageRole.USER, content="hello")],
    )

    assert mock_memory.save_short_term.await_count == 1


def test_broadcast_keeps_subscriber_when_queue_is_full() -> None:
    """订阅队列满时应丢旧事件而不是静默移除订阅者。"""

    now = datetime.now(UTC)
    run_id = "run-full-queue"
    row = SimpleNamespace(
        id=run_id,
        session_id=str(uuid4()),
        status="running",
        user_message="hello",
        assistant_content="",
        thinking_blocks=[],
        tool_activity=[],
        error="",
        created_at=now,
        updated_at=now,
        completed_at=None,
    )
    queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue(maxsize=2)
    queue.put_nowait(("message", '{"text":"old-1"}'))
    queue.put_nowait(("message", '{"text":"old-2"}'))

    active = _ActiveRun(row)
    active.subscribers.add(queue)
    _ACTIVE_RUNS[run_id] = active
    try:
        _broadcast_run_event(run_id, "run_state", {"assistant_content": "完整内容"})
        _broadcast_run_event(run_id, "done", {})

        queued_events = [queue.get_nowait()[0], queue.get_nowait()[0]]
        assert queue in active.subscribers
        assert queued_events == ["run_state", "done"]
    finally:
        _ACTIVE_RUNS.pop(run_id, None)


def test_get_engine_uses_null_pool_for_sqlite() -> None:
    """SQLite engine 应使用 NullPool，减少连接复用终止冲突。"""
    get_engine.cache_clear()
    engine = get_engine("sqlite+aiosqlite:///./test_stream_safety.db")
    try:
        assert isinstance(engine.sync_engine.pool, NullPool)
    finally:
        get_engine.cache_clear()


async def test_stream_tool_loop_auto_summarizes_when_no_final_text() -> None:
    """工具循环结束但没有最终助手文字时，pipeline 应自动发起总结 LLM 调用。"""
    tool_call = ToolCall(name="read_text_file", arguments={"path": "/tmp/demo"})
    mock_memory = AsyncMock(spec=MemoryAdapter)
    mock_memory.save_short_term.return_value = None
    mock_profile = MagicMock()
    mock_profile.id = "test-profile"

    class FakeToolLoop:
        max_iterations = 3
        unlimited = False

        async def execute_stream_with_tools(self, session, **kwargs):
            yield StreamEvent(event_type=StreamEventType.ROUND_START, metadata={"round": 1})
            yield StreamEvent(event_type=StreamEventType.TOOL_CALL, tool_call=tool_call)
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
            yield StreamEvent(event_type=StreamEventType.TEXT_DELTA, content="基于工具结果的总结")
            yield StreamEvent(event_type=StreamEventType.DONE)

    fake_llm = FakeSummaryLLM()

    pipeline = ChatPipeline(
        config=MagicMock(),
        memory=mock_memory,
        rag_pipeline=AsyncMock(),
        policy=PolicyEngine(),
        tool_adapter=MagicMock(),
    )
    pipeline._llm_adapters[mock_profile.id] = fake_llm
    pipeline._make_tool_loop = lambda *args, **kwargs: FakeToolLoop()

    ctx = ChatContext(
        session_id=uuid4(),
        message="帮我分析一下",
        profile=mock_profile,
        temperature=0.7,
        system_prompt=None,
        context_max_messages=20,
        mode="tool_loop",
        tool_adapter=MagicMock(),
    )
    session = SessionState(session_id=ctx.session_id)
    session.add_message(Message(role=MessageRole.USER, content=ctx.message))

    events: list[StreamEvent] = []
    async for event in pipeline._stream_tool_loop(ctx, session):
        events.append(event)

    text_events = [e for e in events if e.event_type == StreamEventType.TEXT_DELTA]
    assert any("总结" in (e.content or "") for e in text_events)
    assert len(fake_llm.calls) == 1


async def test_stream_tool_loop_auto_summarizes_at_iteration_limit() -> None:
    """工具循环达到最大轮次时，总结提示应包含"已达到工具循环最大轮次"。"""
    tool_call = ToolCall(name="read_text_file", arguments={"path": "/tmp/demo"})
    mock_memory = AsyncMock(spec=MemoryAdapter)
    mock_memory.save_short_term.return_value = None
    mock_profile = MagicMock()
    mock_profile.id = "test-profile"

    class FakeToolLoopOneIteration:
        max_iterations = 1
        unlimited = False

        async def execute_stream_with_tools(self, session, **kwargs):
            yield StreamEvent(event_type=StreamEventType.ROUND_START, metadata={"round": 1})
            yield StreamEvent(event_type=StreamEventType.TOOL_CALL, tool_call=tool_call)
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
            yield StreamEvent(event_type=StreamEventType.TEXT_DELTA, content="已到达轮次上限的总结")
            yield StreamEvent(event_type=StreamEventType.DONE)

    fake_llm = FakeSummaryLLM()

    pipeline = ChatPipeline(
        config=MagicMock(),
        memory=mock_memory,
        rag_pipeline=AsyncMock(),
        policy=PolicyEngine(),
        tool_adapter=MagicMock(),
    )
    pipeline._llm_adapters[mock_profile.id] = fake_llm
    pipeline._make_tool_loop = lambda *args, **kwargs: FakeToolLoopOneIteration()

    ctx = ChatContext(
        session_id=uuid4(),
        message="帮我分析一下",
        profile=mock_profile,
        temperature=0.7,
        system_prompt=None,
        context_max_messages=20,
        mode="tool_loop",
        tool_adapter=MagicMock(),
    )
    session = SessionState(session_id=ctx.session_id)
    session.add_message(Message(role=MessageRole.USER, content=ctx.message))

    events: list[StreamEvent] = []
    async for event in pipeline._stream_tool_loop(ctx, session):
        events.append(event)

    assert len(fake_llm.calls) == 1
    assert any(
        "已达到工具循环最大轮次" in message.content
        for message in fake_llm.calls[0]
        if message.role == MessageRole.SYSTEM
    )
