"""Tests for ChatUseCase — session restore (no token double-count), LLM call, save."""
import pytest
from unittest.mock import AsyncMock
from uuid import uuid4

from astracore.core.application.chat import ChatUseCase
from astracore.core.domain.message import Message, MessageRole
from astracore.core.ports.llm import LLMResponse
from astracore.core.ports.memory import MemoryAdapter
from astracore.runtime.policy.engine import PolicyEngine


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
def mock_llm():
    llm = AsyncMock()
    llm.generate.return_value = LLMResponse(
        content="Hello from assistant", model="claude-sonnet-4-6"
    )
    return llm


@pytest.fixture
def chat(mock_llm, mock_memory):
    return ChatUseCase(
        llm_adapter=mock_llm,
        memory_adapter=mock_memory,
        policy_engine=PolicyEngine(),
    )


# ---------- execute ----------

async def test_execute_returns_assistant_message(chat, session_id):
    msg = await chat.execute(session_id, "Hi there")
    assert msg.role == MessageRole.ASSISTANT
    assert msg.content == "Hello from assistant"


async def test_execute_saves_session_after_response(chat, session_id, mock_memory):
    await chat.execute(session_id, "Hi")
    mock_memory.save_short_term.assert_called_once()


async def test_execute_includes_user_message_in_llm_call(chat, session_id, mock_llm):
    await chat.execute(session_id, "Tell me a joke")
    call_args = mock_llm.generate.call_args
    messages = call_args.kwargs.get("messages") or call_args.args[0]
    assert any(m.content == "Tell me a joke" for m in messages)


async def test_execute_does_not_double_count_tokens_on_existing_session(
    session_id, mock_memory
):
    """When session already has messages in Redis, restore_messages must be used
    so token budget is recalculated, not accumulated on top of stored count."""
    existing_msgs = [
        Message(role=MessageRole.USER, content="hello " * 10),
        Message(role=MessageRole.ASSISTANT, content="hi " * 10),
    ]
    mock_memory.load_short_term.return_value = existing_msgs

    # Capture the messages list *at the moment generate is called* (before mutation).
    captured: list[Message] = []

    async def capture_generate(messages, **kwargs):
        captured.extend(list(messages))  # snapshot before assistant msg is appended
        return LLMResponse(content="response", model="test")

    mock_llm = AsyncMock()
    mock_llm.generate.side_effect = capture_generate

    chat = ChatUseCase(
        llm_adapter=mock_llm,
        memory_adapter=mock_memory,
        policy_engine=PolicyEngine(),
    )

    await chat.execute(session_id, "new question")

    # 2 restored messages + 1 new user message = exactly 3
    assert len(captured) == 3
    assert any(m.content == "new question" for m in captured)


async def test_execute_saved_messages_include_both_user_and_assistant(
    session_id, mock_llm, mock_memory
):
    chat = ChatUseCase(
        llm_adapter=mock_llm,
        memory_adapter=mock_memory,
        policy_engine=PolicyEngine(),
    )
    await chat.execute(session_id, "question")
    saved_messages = (
        mock_memory.save_short_term.call_args.kwargs.get("messages")
        or mock_memory.save_short_term.call_args.args[1]
    )
    roles = [m.role for m in saved_messages]
    assert MessageRole.USER in roles
    assert MessageRole.ASSISTANT in roles
