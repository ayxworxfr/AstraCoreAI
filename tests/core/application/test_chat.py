"""Tests for ChatPipeline — session restore (no token double-count), LLM call, save."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from astracore.modules.chat.domain.chat_context import ChatContext
from astracore.modules.chat.domain.message import Message, MessageRole
from astracore.modules.chat.pipeline import ChatPipeline
from astracore.modules.memory.ports.memory import MemoryAdapter
from astracore.shared.policy.engine import PolicyEngine
from astracore.shared.ports.llm import StreamEvent, StreamEventType


@pytest.fixture
def session_id():
    return uuid4()


@pytest.fixture
def mock_memory():
    m = AsyncMock(spec=MemoryAdapter)
    m.load_short_term.return_value = []
    m.save_short_term.return_value = None
    return m


@pytest.fixture
def mock_profile():
    p = MagicMock()
    p.id = "test-profile"
    return p


@pytest.fixture
def mock_llm():
    llm = MagicMock()

    async def _stream(messages, temperature=None, **kwargs):
        yield StreamEvent(event_type=StreamEventType.TEXT_DELTA, content="Hello from assistant")
        yield StreamEvent(event_type=StreamEventType.DONE)

    llm.generate_stream = _stream
    return llm


@pytest.fixture
def pipeline(mock_memory, mock_llm, mock_profile):
    p = ChatPipeline(
        config=MagicMock(),
        memory=mock_memory,
        rag_pipeline=AsyncMock(),
        policy=PolicyEngine(),
        tool_adapter=MagicMock(),
    )
    p._llm_adapters[mock_profile.id] = mock_llm
    return p


@pytest.fixture
def ctx(session_id, mock_profile):
    return ChatContext(
        session_id=session_id,
        user_id="default",
        message="Hi there",
        profile=mock_profile,
        temperature=0.7,
        system_prompt=None,
        context_max_messages=20,
        mode="normal",
    )


# ---------- execute ----------


async def test_execute_returns_assistant_text(pipeline, ctx):
    result = await pipeline.execute(ctx)
    assert result == "Hello from assistant"


async def test_execute_saves_session_after_response(pipeline, ctx, mock_memory):
    await pipeline.execute(ctx)
    mock_memory.save_short_term.assert_called_once()


async def test_execute_includes_user_message_in_llm_call(
    pipeline, session_id, mock_llm, mock_profile
):
    captured: list[Message] = []

    async def capturing_stream(messages, temperature=None, **kwargs):
        captured.extend(messages)
        yield StreamEvent(event_type=StreamEventType.TEXT_DELTA, content="ok")
        yield StreamEvent(event_type=StreamEventType.DONE)

    mock_llm.generate_stream = capturing_stream

    ctx = ChatContext(
        session_id=session_id,
        user_id="default",
        message="Tell me a joke",
        profile=mock_profile,
        temperature=0.7,
        system_prompt=None,
        context_max_messages=20,
        mode="normal",
    )
    await pipeline.execute(ctx)
    assert any(m.content == "Tell me a joke" for m in captured)


async def test_execute_does_not_double_count_tokens_on_existing_session(
    session_id, mock_memory, mock_profile
):
    """When session already has messages in memory, restore_messages must be used
    so token budget is recalculated, not accumulated on top of the stored count."""
    existing = [
        Message(role=MessageRole.USER, content="hello " * 10),
        Message(role=MessageRole.ASSISTANT, content="hi " * 10),
    ]
    mock_memory.load_short_term.return_value = existing

    captured: list[Message] = []

    mock_llm = MagicMock()

    async def capturing_stream(messages, temperature=None, **kwargs):
        captured.extend(messages)  # snapshot before assistant msg is appended
        yield StreamEvent(event_type=StreamEventType.TEXT_DELTA, content="response")
        yield StreamEvent(event_type=StreamEventType.DONE)

    mock_llm.generate_stream = capturing_stream

    p = ChatPipeline(
        config=MagicMock(),
        memory=mock_memory,
        rag_pipeline=AsyncMock(),
        policy=PolicyEngine(),
        tool_adapter=MagicMock(),
    )
    p._llm_adapters[mock_profile.id] = mock_llm

    ctx = ChatContext(
        session_id=session_id,
        user_id="default",
        message="new question",
        profile=mock_profile,
        temperature=0.7,
        system_prompt=None,
        context_max_messages=20,
        mode="normal",
    )
    await p.execute(ctx)

    # 2 restored messages + 1 new user message = exactly 3
    assert len(captured) == 3
    assert any(m.content == "new question" for m in captured)


async def test_execute_saved_messages_include_both_roles(pipeline, ctx, mock_memory):
    await pipeline.execute(ctx)
    saved = mock_memory.save_short_term.call_args.kwargs.get("messages")
    roles = [m.role for m in saved]
    assert MessageRole.USER in roles
    assert MessageRole.ASSISTANT in roles
