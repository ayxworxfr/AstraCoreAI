"""Eval dataset types."""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from astracore.modules.chat.domain.chat_options import ChatOptions


@dataclass
class EvalCase:
    """单条评估用例。

    Parameters
    ----------
    input:
        发给 Agent 的用户消息。
    expected_output:
        期望的输出文本，用于 LLM-as-judge 相关性评分（可选）。
    expected_tool_calls:
        期望按顺序调用的工具名列表，用于精确匹配评分（可选）。
    options:
        执行此用例时使用的 ChatOptions，控制工具调用、模型、RAG 等。
    tags:
        自定义标签，用于报告分组过滤。
    session_id:
        多轮对话时传入已有 session，默认每个 case 独立 session。
    workflow_name:
        Workflow 模式：设置后走 client.workflow.run() 而非 chat_stream。
    workflow_tasks:
        Workflow 任务列表，见 examples/eval_cases.json 格式说明。
    """

    input: str
    expected_output: str | None = None
    expected_tool_calls: list[str] | None = None
    options: ChatOptions = field(default_factory=ChatOptions)
    tags: list[str] = field(default_factory=list)
    session_id: UUID | None = None
    workflow_name: str | None = None
    workflow_tasks: list[dict] | None = None
