"""Tests for HistoryCompactor — token estimation and compaction logic."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from astracore.modules.chat.application.compactor import HistoryCompactor
from astracore.modules.chat.domain.message import Message, MessageRole
from astracore.shared.policy.rules import CompactionRule
from astracore.shared.ports.llm import LLMResponse


def _msg(role: MessageRole, content: str) -> Message:
    return Message(role=role, content=content)


def _user(content: str) -> Message:
    return _msg(MessageRole.USER, content)


def _assistant(content: str) -> Message:
    return _msg(MessageRole.ASSISTANT, content)


def _system(content: str) -> Message:
    return _msg(MessageRole.SYSTEM, content)


def _make_compactor(
    *,
    llm: AsyncMock | None = None,
    engine: MagicMock | None = None,
    rule: CompactionRule | None = None,
) -> HistoryCompactor:
    """Build a HistoryCompactor with sensible mocks for tests that don't customise them."""
    if llm is None:
        llm = AsyncMock()
        llm.generate = AsyncMock(
            return_value=LLMResponse(content="这是一段摘要。", model="test-model")
        )
    if engine is None:
        engine = MagicMock()
        engine.create_memory = AsyncMock(return_value=None)
    return HistoryCompactor(
        llm_adapter=llm,
        memory_engine=engine,
        model="test-model",
        rule=rule,
    )


@pytest.fixture
def mock_llm():
    llm = AsyncMock()
    llm.generate = AsyncMock(return_value=LLMResponse(content="这是一段摘要。", model="test-model"))
    return llm


@pytest.fixture
def mock_engine():
    engine = MagicMock()
    engine.create_memory = AsyncMock(return_value=None)
    return engine


@pytest.fixture
def compactor(mock_llm, mock_engine):
    return HistoryCompactor(llm_adapter=mock_llm, memory_engine=mock_engine, model="test-model")


# ------ estimate_tokens -------


def test_estimate_tokens_empty():
    c = _make_compactor()
    assert c.estimate_tokens([]) == 0


def test_estimate_tokens_single_message():
    c = _make_compactor()
    msg = _user("hello")  # 5 chars * 0.6 = 3
    assert c.estimate_tokens([msg]) >= 1


def test_estimate_tokens_chinese(compactor):
    """Chinese messages should yield more tokens than 0.3 * len (conservative estimate)."""
    long_cn = "你好" * 500  # 1000 CJK chars
    msg = _user(long_cn)
    tokens = compactor.estimate_tokens([msg])
    # Conservative: at least 0.5 tokens per CJK char
    assert tokens >= 500


def test_estimate_tokens_multiple_messages(compactor):
    msgs = [_user("hello"), _assistant("world")]
    total = compactor.estimate_tokens(msgs)
    individual = sum(compactor.estimate_tokens([m]) for m in msgs)
    assert total == individual


# ------ maybe_compact: no trigger ------


@pytest.mark.asyncio
async def test_no_compact_when_under_threshold(mock_llm, mock_engine):
    """If token estimate is below threshold, messages returned unchanged."""
    c = HistoryCompactor(
        llm_adapter=mock_llm,
        memory_engine=mock_engine,
        rule=CompactionRule(context_window_tokens=200_000),
    )
    msgs = [_user("hi"), _assistant("hello")]
    result = await c.maybe_compact(msgs, uuid4())
    assert result is msgs


# ------ maybe_compact: trigger ------


@pytest.mark.asyncio
async def test_compact_triggered_reduces_message_count(mock_llm, mock_engine):
    """After compaction the returned list must be shorter than the input."""
    c = HistoryCompactor(
        llm_adapter=mock_llm,
        memory_engine=mock_engine,
        rule=CompactionRule(context_window_tokens=1000),
    )
    msgs = [_user("x" * 1000), _assistant("y" * 1000)] * 10  # 20 messages, large tokens
    result = await c.maybe_compact(msgs, uuid4())
    assert len(result) < len(msgs)


@pytest.mark.asyncio
async def test_compact_first_message_is_summary(mock_llm, mock_engine):
    """After compaction the first non-system message must be the SYSTEM summary."""
    c = HistoryCompactor(
        llm_adapter=mock_llm,
        memory_engine=mock_engine,
        rule=CompactionRule(context_window_tokens=100),
    )
    msgs = [_user("x" * 500)] * 20
    result = await c.maybe_compact(msgs, uuid4())
    summary_msgs = [
        m for m in result if m.role == MessageRole.SYSTEM and m.metadata.get("compacted")
    ]
    assert summary_msgs, "Expected a compacted summary system message"
    assert "【对话摘要】" in summary_msgs[0].content


@pytest.mark.asyncio
async def test_compact_persists_summary_to_memory_engine(mock_llm, mock_engine):
    """Successful compaction must persist to memory engine."""
    c = HistoryCompactor(
        llm_adapter=mock_llm,
        memory_engine=mock_engine,
        rule=CompactionRule(context_window_tokens=100),
    )
    msgs = [_user("x" * 500)] * 20
    await c.maybe_compact(msgs, uuid4())
    mock_engine.create_memory.assert_awaited_once()


# ------ fallback on LLM failure ------


@pytest.mark.asyncio
async def test_compact_fallback_on_llm_failure(mock_engine):
    """If LLM call raises, compactor falls back to tail-trim without propagating the error."""
    bad_llm = AsyncMock()
    bad_llm.generate = AsyncMock(side_effect=RuntimeError("LLM is down"))
    c = HistoryCompactor(
        llm_adapter=bad_llm,
        memory_engine=mock_engine,
        rule=CompactionRule(context_window_tokens=100),
    )
    msgs = [_user("x" * 500)] * 20
    # Must not raise
    result = await c.maybe_compact(msgs, uuid4(), trim_limit=5)
    assert len(result) <= 5


@pytest.mark.asyncio
async def test_compact_fallback_preserves_message_integrity(mock_engine):
    """Fallback trim must return actual Message objects without modification."""
    bad_llm = AsyncMock()
    bad_llm.generate = AsyncMock(side_effect=Exception("fail"))
    c = HistoryCompactor(
        llm_adapter=bad_llm,
        memory_engine=mock_engine,
        rule=CompactionRule(context_window_tokens=50),
    )
    msgs = [_user(f"msg {i}") for i in range(30)]
    result = await c.maybe_compact(msgs, uuid4(), trim_limit=10)
    assert all(isinstance(m, Message) for m in result)
    # Should be the last 10
    assert result == msgs[-10:]
