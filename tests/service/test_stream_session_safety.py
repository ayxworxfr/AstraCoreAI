"""Streaming session safety regression tests."""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from sqlalchemy.pool import NullPool

from astracore.infrastructure.db.session import get_engine
from astracore.modules.chat.api import (
    _ACTIVE_RUNS,
    _ActiveRun,
    _broadcast_run_event,
)
from astracore.modules.chat.domain.chat_context import ChatContext
from astracore.modules.chat.domain.message import Message, MessageRole, ToolCall, ToolResult
from astracore.modules.chat.domain.session import SessionState
from astracore.modules.chat.pipeline import ChatPipeline
from astracore.modules.memory.ports.memory import MemoryAdapter
from astracore.shared.policy.engine import PolicyEngine
from astracore.shared.ports.llm import StreamEvent, StreamEventType


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


async def test_stream_tool_loop_passes_phase_boundary_from_closing_round() -> None:
    """pipeline._stream_tool_loop 应透传来自 tool_loop 的 DONE{"source":"tool_loop"} phase boundary。

    收尾轮由 ToolLoopUseCase 内部处理，pipeline 只需透传 phase boundary 信号供 api 层重置状态。
    """
    mock_memory = AsyncMock(spec=MemoryAdapter)
    mock_memory.save_short_term.return_value = None
    mock_profile = MagicMock()
    mock_profile.id = "test-profile"

    class FakeToolLoop:
        async def execute_stream_with_tools(self, session, **kwargs):
            yield StreamEvent(event_type=StreamEventType.ROUND_START, metadata={"round": 1})
            yield StreamEvent(event_type=StreamEventType.TEXT_DELTA, content="思考中")
            # 收尾轮发出 phase boundary
            yield StreamEvent(event_type=StreamEventType.DONE, metadata={"source": "tool_loop"})
            yield StreamEvent(event_type=StreamEventType.TEXT_DELTA, content="最终回答")
            # 普通 DONE（不应透传，应累计 usage）
            yield StreamEvent(
                event_type=StreamEventType.DONE,
                metadata={"usage": {"input_tokens": 10, "output_tokens": 5}},
            )
            session.add_message(Message(role=MessageRole.ASSISTANT, content="最终回答"))

    pipeline = ChatPipeline(
        config=MagicMock(),
        memory=mock_memory,
        rag_pipeline=AsyncMock(),
        policy=PolicyEngine(),
        tool_adapter=MagicMock(),
    )
    pipeline._make_tool_loop = lambda *args, **kwargs: FakeToolLoop()

    ctx = ChatContext(
        session_id=uuid4(),
        user_id="default",
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

    done_events = [e for e in events if e.event_type == StreamEventType.DONE]
    # 应有两个 DONE：一个是 phase boundary，一个是最终 DONE（带 usage）
    assert len(done_events) == 2
    assert done_events[0].metadata.get("source") == "tool_loop"
    assert "usage" in done_events[1].metadata
    assert done_events[1].metadata["usage"]["input_tokens"] == 10

    text_events = [e for e in events if e.event_type == StreamEventType.TEXT_DELTA]
    assert any("最终回答" in (e.content or "") for e in text_events)


async def test_stream_tool_loop_does_not_summarize_internally() -> None:
    """pipeline._stream_tool_loop 不再自行发起 LLM 总结调用；收尾由 tool_loop 内部负责。"""
    tool_call = ToolCall(name="read_text_file", arguments={"path": "/tmp/demo"})
    mock_memory = AsyncMock(spec=MemoryAdapter)
    mock_memory.save_short_term.return_value = None
    mock_profile = MagicMock()
    mock_profile.id = "test-profile"

    class FakeToolLoop:
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
            # 收尾轮已在 ToolLoopUseCase 内部完成
            yield StreamEvent(event_type=StreamEventType.DONE, metadata={"source": "tool_loop"})
            yield StreamEvent(event_type=StreamEventType.TEXT_DELTA, content="收尾文字")
            session.add_message(Message(role=MessageRole.ASSISTANT, content="收尾文字"))

    class TrackLLM:
        def __init__(self) -> None:
            self.calls: int = 0

        async def generate_stream(self, messages, **kwargs):
            self.calls += 1
            yield StreamEvent(event_type=StreamEventType.DONE)

    track_llm = TrackLLM()

    pipeline = ChatPipeline(
        config=MagicMock(),
        memory=mock_memory,
        rag_pipeline=AsyncMock(),
        policy=PolicyEngine(),
        tool_adapter=MagicMock(),
    )
    pipeline._llm_adapters[mock_profile.id] = track_llm
    pipeline._make_tool_loop = lambda *args, **kwargs: FakeToolLoop()

    ctx = ChatContext(
        session_id=uuid4(),
        user_id="default",
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

    async for _ in pipeline._stream_tool_loop(ctx, session):
        pass

    # pipeline 层不应发起任何 LLM 调用（总结由 tool_loop 内部负责）
    assert track_llm.calls == 0
