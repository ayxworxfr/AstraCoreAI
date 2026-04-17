"""Tests for SessionState, ContextWindow, TokenBudget domain models."""
from astracore.core.domain.message import Message, MessageRole
from astracore.core.domain.session import ContextWindow, SessionState, TokenBudget


def _msg(content: str) -> Message:
    return Message(role=MessageRole.USER, content=content)


# ---------- TokenBudget ----------

def test_token_budget_add_and_available():
    budget = TokenBudget(max_input_tokens=1000)
    budget.add_input_tokens(300)
    assert budget.current_input_tokens == 300
    assert budget.available_input_tokens() == 700


def test_token_budget_exceeded_when_at_limit():
    budget = TokenBudget(max_input_tokens=100)
    budget.add_input_tokens(100)
    assert budget.is_input_budget_exceeded() is True


def test_token_budget_not_exceeded_when_below_limit():
    budget = TokenBudget(max_input_tokens=100)
    budget.add_input_tokens(50)
    assert budget.is_input_budget_exceeded() is False


# ---------- ContextWindow.truncate_to_budget ----------

def test_truncate_to_budget_noop_when_under_limit():
    cw = ContextWindow()
    for i in range(3):
        cw.add_message(_msg(f"short {i}"))
    original_count = len(cw.messages)
    cw.truncate_to_budget(max_tokens=10_000)
    assert len(cw.messages) == original_count


def test_truncate_to_budget_removes_oldest_messages():
    cw = ContextWindow()
    # 40 chars → 10 tokens each (len // 4). 6 messages = 60 tokens total.
    for _ in range(6):
        cw.add_message(_msg("a" * 40))
    # Budget of 25 → drop until ≤ 25: drop 4, keep 2 (20 tokens)
    cw.truncate_to_budget(max_tokens=25)
    assert len(cw.messages) == 2


def test_truncate_to_budget_result_fits_within_budget():
    cw = ContextWindow()
    for _ in range(10):
        cw.add_message(_msg("x" * 100))  # 25 tokens each → 250 total
    cw.truncate_to_budget(max_tokens=100)
    assert cw.total_tokens() <= 100


def test_truncate_to_budget_empty_after_total_truncation():
    cw = ContextWindow()
    cw.add_message(_msg("a" * 40))  # 10 tokens
    cw.truncate_to_budget(max_tokens=0)
    assert cw.messages == []


# ---------- SessionState.restore_messages ----------

def test_restore_messages_does_not_double_count():
    session = SessionState()
    msgs = [_msg("hello " * 10) for _ in range(3)]
    for m in msgs:
        session.add_message(m)
    tokens_after_add = session.token_budget.current_input_tokens
    # restore must recalculate, not accumulate on top of add_message count
    session.restore_messages(msgs)
    assert session.token_budget.current_input_tokens == tokens_after_add


def test_restore_messages_sets_exact_token_count():
    session = SessionState()
    msgs = [_msg("a" * 40) for _ in range(3)]  # 3 × 10 = 30 tokens
    session.restore_messages(msgs)
    expected = sum(m.token_estimate() for m in msgs)
    assert session.token_budget.current_input_tokens == expected


def test_restore_messages_replaces_existing_messages():
    session = SessionState()
    session.add_message(_msg("original"))
    new_msgs = [_msg("new")]
    session.restore_messages(new_msgs)
    assert session.get_messages() == new_msgs


def test_restore_messages_on_empty_list_zeroes_token_count():
    session = SessionState()
    session.add_message(_msg("something"))
    session.restore_messages([])
    assert session.token_budget.current_input_tokens == 0
    assert session.get_messages() == []
