"""Eval report types."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from astracore.eval.dataset import EvalCase


@dataclass
class EvalResult:
    """单条用例的执行结果。"""

    case: EvalCase
    actual_output: str
    actual_tool_calls: list[str]
    relevance_score: float | None = None  # LLM-as-judge, 0.0-1.0
    tool_match_score: float | None = None  # 精确匹配, 0.0-1.0
    latency_ms: int = 0
    error: str | None = None

    @property
    def passed(self) -> bool:
        """relevance 达到阈值且无错误。"""
        if self.error:
            return False
        if self.relevance_score is not None and self.relevance_score < 0.5:
            return False
        if self.tool_match_score is not None and self.tool_match_score < 1.0:
            return False
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "input": self.case.input,
            "expected_output": self.case.expected_output,
            "expected_tool_calls": self.case.expected_tool_calls,
            "actual_output": self.actual_output,
            "actual_tool_calls": self.actual_tool_calls,
            "relevance_score": self.relevance_score,
            "tool_match_score": self.tool_match_score,
            "latency_ms": self.latency_ms,
            "passed": self.passed,
            "error": self.error,
            "tags": self.case.tags,
        }


@dataclass
class EvalReport:
    """评估报告汇总。"""

    results: list[EvalResult] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def failed(self) -> int:
        return self.total - self.passed

    @property
    def pass_rate(self) -> float:
        return self.passed / self.total if self.total else 0.0

    @property
    def avg_relevance(self) -> float:
        scores = [r.relevance_score for r in self.results if r.relevance_score is not None]
        return sum(scores) / len(scores) if scores else 0.0

    @property
    def avg_tool_match(self) -> float:
        scores = [r.tool_match_score for r in self.results if r.tool_match_score is not None]
        return sum(scores) / len(scores) if scores else 0.0

    @property
    def avg_latency_ms(self) -> float:
        latencies = [r.latency_ms for r in self.results]
        return sum(latencies) / len(latencies) if latencies else 0.0

    def summary(self) -> str:
        lines = [
            f"EvalReport: {self.passed}/{self.total} passed ({self.pass_rate:.1%})",
            f"  avg_relevance  : {self.avg_relevance:.3f}",
            f"  avg_tool_match : {self.avg_tool_match:.3f}",
            f"  avg_latency_ms : {self.avg_latency_ms:.0f}",
        ]
        failed_cases = [r for r in self.results if not r.passed]
        if failed_cases:
            lines.append(f"\nFailed ({len(failed_cases)}):")
            for r in failed_cases[:10]:
                lines.append(f"  - [{r.error or 'low score'}] {r.case.input[:80]}")
        return "\n".join(lines)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(
            {
                "total": self.total,
                "passed": self.passed,
                "pass_rate": round(self.pass_rate, 4),
                "avg_relevance": round(self.avg_relevance, 4),
                "avg_tool_match": round(self.avg_tool_match, 4),
                "avg_latency_ms": round(self.avg_latency_ms, 1),
                "results": [r.to_dict() for r in self.results],
            },
            ensure_ascii=False,
            indent=indent,
        )
