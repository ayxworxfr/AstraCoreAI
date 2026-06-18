"""Eval runner: executes EvalCases against AstraCoreClient and scores results."""

from __future__ import annotations

import asyncio
import re
import time
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from astracore.eval.dataset import EvalCase
from astracore.eval.report import EvalReport, EvalResult
from astracore.shared.observability.logger import get_logger
from astracore.shared.ports.llm import StreamEventType

if TYPE_CHECKING:
    pass

_logger = get_logger(__name__)

_JUDGE_SYSTEM = """你是一个客观的评估助手。
用户会给你一个问题、期望答案和实际答案。
请对实际答案的相关性和正确性打分，分值为 0.0-1.0（0.0=完全无关/错误，1.0=完全正确/相关）。
只输出一个裸数字，例如 0.8，不要加任何符号、标签、解释或其他文字。"""

_JUDGE_USER_TEMPLATE = """问题：{input}

期望答案：{expected}

实际答案：{actual}

打分（0.0-1.0）："""


class EvalRunner:
    """运行评估用例，返回 EvalReport。

    Parameters
    ----------
    client:
        已初始化的 AstraCoreClient（应在 async with 上下文中使用）。
    judge_profile:
        LLM-as-judge 使用的 profile id，None 时跳过相关性评分。
    relevance_threshold:
        相关性评分低于此值视为未通过，默认 0.7。
    concurrency:
        并发执行的用例数，默认 4。
    """

    def __init__(
        self,
        client: Any,
        *,
        judge_profile: str | None = None,
        relevance_threshold: float = 0.7,
        concurrency: int = 4,
    ) -> None:
        self._client = client
        self._judge_profile = judge_profile
        self._relevance_threshold = relevance_threshold
        self._concurrency = concurrency

    async def run(self, cases: list[EvalCase]) -> EvalReport:
        """并发执行所有用例，返回 EvalReport。"""
        semaphore = asyncio.Semaphore(self._concurrency)

        async def run_with_semaphore(case: EvalCase) -> EvalResult:
            async with semaphore:
                return await self._run_case(case)

        results = await asyncio.gather(
            *[run_with_semaphore(case) for case in cases],
            return_exceptions=False,
        )
        return EvalReport(results=list(results))

    async def _run_case(self, case: EvalCase) -> EvalResult:
        """执行单条用例并评分。"""
        actual_output = ""
        actual_tool_calls: list[str] = []
        error: str | None = None
        t0 = time.monotonic()

        try:
            if case.workflow_tasks is not None:
                actual_output = await self._run_workflow_case(case)
            else:
                session_id = case.session_id or uuid4()
                opts = case.options
                async for event in self._client.chat_stream(
                    case.input,
                    session_id=session_id,
                    model_profile=opts.model_profile,
                    use_tools=opts.use_tools,
                    thinking_mode=opts.thinking_mode,
                    thinking_budget=opts.thinking_budget,
                    enable_rag=opts.enable_rag,
                    enable_web=opts.enable_web,
                ):
                    if event.event_type == StreamEventType.TEXT_DELTA:
                        actual_output += event.content
                    elif event.event_type == StreamEventType.TOOL_CALL and event.tool_call:
                        actual_tool_calls.append(event.tool_call.name)
        except Exception as exc:
            error = str(exc)
            _logger.warning("EvalCase failed: %s — %s", case.input[:60], exc)

        latency_ms = int((time.monotonic() - t0) * 1000)

        tool_match_score = (
            self._score_tool_calls(case.expected_tool_calls, actual_tool_calls)
            if case.expected_tool_calls is not None
            else None
        )

        relevance_score: float | None = None
        if case.workflow_tasks is not None:
            judge_input = " → ".join(t["description"] for t in case.workflow_tasks)
        else:
            judge_input = case.input
        if case.expected_output is not None and not error:
            relevance_score = await self._llm_judge(
                input_text=judge_input,
                expected=case.expected_output,
                actual=actual_output,
            )

        return EvalResult(
            case=case,
            actual_output=actual_output,
            actual_tool_calls=actual_tool_calls,
            relevance_score=relevance_score,
            tool_match_score=tool_match_score,
            latency_ms=latency_ms,
            error=error,
        )

    async def _run_workflow_case(self, case: EvalCase) -> str:
        """执行 workflow 用例，返回所有已完成任务结果的拼接文本。"""
        from astracore.modules.agent.domain import AgentRole, AgentTask

        raw_tasks = case.workflow_tasks or []
        # 第一步：创建所有 AgentTask（先不设 depends_on）
        tasks: list[AgentTask] = [
            AgentTask(
                role=AgentRole(t.get("role", "executor")),
                description=t["description"],
                condition=t.get("condition"),
                metadata=t.get("metadata", {}),
            )
            for t in raw_tasks
        ]
        # 第二步：按索引映射 depends_on
        for i, raw in enumerate(raw_tasks):
            for dep_idx in raw.get("depends_on", []):
                tasks[i].depends_on.append(tasks[dep_idx].task_id)

        state = await self._client.workflow.run(
            case.workflow_name or "eval-workflow",
            tasks,
            use_tools=case.options.use_tools,
            model_profile=case.options.model_profile,
        )
        # 拼接所有完成任务的结果作为实际输出
        parts = [r for r in (state.task_results or {}).values() if r]
        return "\n\n".join(parts)

    @staticmethod
    def _score_tool_calls(expected: list[str], actual: list[str]) -> float:
        """精确顺序匹配评分。

        - 全部命中且顺序一致 → 1.0
        - 集合命中但顺序不同 → 0.5
        - 部分命中 → 命中比例 * 0.5
        - 无命中 → 0.0
        """
        if not expected:
            return 1.0
        expected_set = set(expected)
        actual_set = set(actual)
        hit_count = len(expected_set & actual_set)
        if hit_count == 0:
            return 0.0
        partial = hit_count / len(expected_set)
        if actual == expected:
            return 1.0
        if expected_set == actual_set:
            return 0.5
        return partial * 0.5

    async def _llm_judge(
        self,
        *,
        input_text: str,
        expected: str,
        actual: str,
    ) -> float:
        """用 LLM 对实际输出评分，返回 0.0-1.0。"""
        try:
            result = await self._client.chat(
                _JUDGE_USER_TEMPLATE.format(
                    input=input_text[:1000],
                    expected=expected[:1000],
                    actual=actual[:2000],
                ),
                model_profile=self._judge_profile,
            )
            raw = result.content.strip()
            m = re.search(r"(\d+(?:\.\d+)?)", raw)
            if not m:
                raise ValueError(f"no float found in judge response: {raw!r}")
            score = float(m.group(1))
            return max(0.0, min(1.0, score))
        except Exception as exc:
            _logger.warning("LLM judge failed: %s", exc)
            return 0.0
