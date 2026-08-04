"""TurnBudget 硬上限。"""

from __future__ import annotations

import pytest

from astracore.modules.chat.domain.budget import BudgetExceeded, TurnBudget


def test_input_budget_exceeded():
    budget = TurnBudget(max_input_tokens=100)
    budget.add_usage(input_tokens=40)
    with pytest.raises(BudgetExceeded) as exc:
        budget.add_usage(input_tokens=70)
    assert exc.value.kind == "input_tokens"
    assert exc.value.used == 110
    assert exc.value.limit == 100


def test_output_budget_exceeded():
    budget = TurnBudget(max_output_tokens=10)
    with pytest.raises(BudgetExceeded) as exc:
        budget.add_usage(output_tokens=11)
    assert exc.value.kind == "output_tokens"


def test_zero_means_unlimited():
    budget = TurnBudget()
    budget.add_usage(input_tokens=1_000_000, output_tokens=1_000_000)
    budget.check_iteration(999)


def test_iteration_budget():
    budget = TurnBudget(max_tool_iterations=2)
    budget.check_iteration(2)
    with pytest.raises(BudgetExceeded):
        budget.check_iteration(3)
