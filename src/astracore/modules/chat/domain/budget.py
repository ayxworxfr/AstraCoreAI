"""Token / 轮次预算 —— 超限抛明确异常，由上层转成用户可见错误。"""

from __future__ import annotations

from dataclasses import dataclass


class BudgetExceeded(Exception):
    """本轮对话超出配置的硬预算。"""

    def __init__(self, kind: str, used: int, limit: int) -> None:
        self.kind = kind
        self.used = used
        self.limit = limit
        super().__init__(f"Budget exceeded: {kind} used={used} limit={limit}")


@dataclass(slots=True)
class TurnBudget:
    """单轮可消耗预算；``0`` 表示不限制。"""

    max_tool_iterations: int = 0
    max_input_tokens: int = 0
    max_output_tokens: int = 0

    input_tokens: int = 0
    output_tokens: int = 0

    def add_usage(self, *, input_tokens: int = 0, output_tokens: int = 0) -> None:
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        if self.max_input_tokens and self.input_tokens > self.max_input_tokens:
            raise BudgetExceeded("input_tokens", self.input_tokens, self.max_input_tokens)
        if self.max_output_tokens and self.output_tokens > self.max_output_tokens:
            raise BudgetExceeded("output_tokens", self.output_tokens, self.max_output_tokens)

    def check_iteration(self, iteration: int) -> None:
        if self.max_tool_iterations and iteration > self.max_tool_iterations:
            raise BudgetExceeded("tool_iterations", iteration, self.max_tool_iterations)
