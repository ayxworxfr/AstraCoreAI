"""Parallel Agent Tool — spawns multiple Worker Agents for concurrent sub-task execution."""

import asyncio
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from astracore.infrastructure.llm.anthropic import AnthropicAdapter
from astracore.infrastructure.llm.openai import OpenAIAdapter
from astracore.infrastructure.tools._coerce import coerce_tool_arguments
from astracore.modules.chat.application.tool_loop import ToolLoopUseCase
from astracore.modules.chat.domain.message import Message, MessageRole
from astracore.modules.chat.domain.session import SessionState
from astracore.modules.tools.ports.tool import (
    ToolAdapter,
    ToolDefinition,
    ToolError,
    ToolErrorCode,
    ToolExecutionResult,
    ToolParameter,
    ToolParameterType,
)
from astracore.sdk.config import AstraCoreConfig
from astracore.shared.observability.logger import get_logger
from astracore.shared.policy.engine import PolicyEngine
from astracore.shared.ports.llm import LLMAdapter, StreamEvent, StreamEventType

logger = get_logger(__name__)

_WORKER_MAX_ITERATIONS = 15

# Worker events that get forwarded as AGENT_* events
_EVENT_TYPE_MAP: dict[StreamEventType, StreamEventType] = {
    StreamEventType.TEXT_DELTA: StreamEventType.AGENT_TEXT_DELTA,
    StreamEventType.THINKING_DELTA: StreamEventType.AGENT_THINKING_DELTA,
    StreamEventType.TOOL_CALL: StreamEventType.AGENT_TOOL_CALL,
    StreamEventType.TOOL_RESULT: StreamEventType.AGENT_TOOL_RESULT,
}


@dataclass
class _AgentTask:
    task: str
    context: str | None = None
    agent_id: str = field(default_factory=lambda: uuid4().hex[:8])


def _wrap_agent_event(agent_id: str, event: StreamEvent) -> StreamEvent | None:
    new_type = _EVENT_TYPE_MAP.get(event.event_type)
    if new_type is None:
        return None
    return StreamEvent(
        event_type=new_type,
        content=event.content,
        tool_call=event.tool_call,
        metadata={**event.metadata, "agent_id": agent_id},
    )


class ParallelAgentTool(ToolAdapter):
    """Implements the ``spawn_agents`` tool for parallel sub-agent execution.

    Each worker gets an independent LLMAdapter + ToolLoopUseCase + fresh SessionState.
    Workers run concurrently via asyncio.Queue; intermediate events are forwarded as
    AGENT_* StreamEvents so the frontend can show real-time per-agent progress.
    """

    def __init__(
        self,
        config: AstraCoreConfig,
        worker_tools: ToolAdapter,
        policy: PolicyEngine,
    ) -> None:
        self._config = config
        self._worker_tools = worker_tools
        self._policy = policy
        self._llm_adapters: dict[str | None, LLMAdapter] = {}

    # ------------------------------------------------------------------
    # ToolAdapter interface
    # ------------------------------------------------------------------

    def is_timeout_managed(self, tool_name: str) -> bool:
        # spawn_agents manages per-worker timeouts internally; skip outer timeout in tool_loop
        return tool_name == "spawn_agents"

    def get_definitions(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name="spawn_agents",
                description=(
                    "当任务需要同时从多个独立来源收集信息时，启动多个并行 Agent 分别执行，"
                    "速度远快于串行。适用场景：对比分析、多数据源聚合、可拆解为相互独立的子问题。"
                    "每个子 Agent 拥有完整工具访问权限，独立推理，结果返回后由你综合。"
                ),
                parameters=[
                    ToolParameter(
                        name="tasks",
                        type=ToolParameterType.ARRAY,
                        description=(
                            "子任务列表（2–5 个）。每项为对象，包含："
                            "task（string，必填，子任务完整描述）；"
                            "context（string，可选，传给子 Agent 的背景信息）"
                        ),
                        required=True,
                    ),
                ],
            )
        ]

    async def execute(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> ToolExecutionResult:
        result: ToolExecutionResult | None = None
        async for item in self.execute_streaming(tool_name, arguments, context):
            if isinstance(item, ToolExecutionResult):
                result = item
        return result or ToolExecutionResult(
            tool_name=tool_name,
            ok=False,
            error=ToolError(
                code=ToolErrorCode.EXECUTION_ERROR,
                message="spawn_agents: 未返回任何结果",
                retryable=False,
            ),
            execution_time_ms=0.0,
        )

    async def execute_parallel(
        self,
        tool_calls: list[tuple[str, dict[str, Any]]],
        context: dict[str, Any] | None = None,
    ) -> list[ToolExecutionResult]:
        return [await self.execute(name, args, context) for name, args in tool_calls]

    async def execute_streaming(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> AsyncIterator[StreamEvent | ToolExecutionResult]:
        overall_start = time.monotonic()

        coerced = coerce_tool_arguments(arguments, {"tasks": ToolParameterType.ARRAY})
        raw_tasks = coerced.get("tasks", [])
        if not isinstance(raw_tasks, list) or not raw_tasks:
            yield ToolExecutionResult(
                tool_name=tool_name,
                ok=False,
                error=ToolError(
                    code=ToolErrorCode.INVALID_ARGUMENT,
                    message="spawn_agents: 'tasks' 参数必须为非空数组",
                    retryable=False,
                ),
                execution_time_ms=0.0,
            )
            return

        tasks = [
            _AgentTask(
                task=str(t.get("task", "")),
                context=str(t["context"]) if t.get("context") else None,
            )
            for t in raw_tasks
            if isinstance(t, dict) and t.get("task")
        ][:5]  # 最多 5 个子任务

        if not tasks:
            yield ToolExecutionResult(
                tool_name=tool_name,
                ok=False,
                error=ToolError(
                    code=ToolErrorCode.INVALID_ARGUMENT,
                    message="spawn_agents: 没有有效的子任务",
                    retryable=False,
                ),
                execution_time_ms=0.0,
            )
            return

        ctx = context or {}
        profile_id: str | None = ctx.get("profile_id")
        # 使用请求时的完整 tool_adapter（含 MCP），回退到初始化时的 worker_tools
        full_adapter: ToolAdapter = ctx.get("tool_adapter") or self._worker_tools
        allowed_tools_ctx = ctx.get("allowed_tools")
        # 始终排除 spawn_agents，防止子 Agent 递归调用
        if allowed_tools_ctx is not None:
            worker_allowed: frozenset[str] | None = frozenset(allowed_tools_ctx) - {"spawn_agents"}
        else:
            all_tool_names = {d.name for d in full_adapter.get_definitions()}
            worker_allowed = (
                frozenset(all_tool_names - {"spawn_agents"}) if all_tool_names else None
            )
        model_name = self._config.llm.get_profile(profile_id).model

        # 通知前端所有子 Agent 即将启动
        for task in tasks:
            yield StreamEvent(
                event_type=StreamEventType.AGENT_START,
                metadata={"agent_id": task.agent_id, "task": task.task, "model": model_name},
            )

        # asyncio.Queue 作为多 Worker 事件汇集点
        # 项目格式: ("event", agent_id, StreamEvent) | ("done", agent_id, duration_ms, error)
        queue: asyncio.Queue[Any] = asyncio.Queue()
        worker_text: dict[str, str] = {t.agent_id: "" for t in tasks}
        agent_start_times: dict[str, float] = {}

        async def run_agent(task: _AgentTask) -> None:
            agent_start_times[task.agent_id] = time.monotonic()
            error: str | None = None
            try:
                tool_loop = self._build_tool_loop(profile_id, worker_tool_adapter=full_adapter)
                session = SessionState()
                # 默认自主执行提示：防止子 Agent 停下来询问用户确认
                system_parts = [
                    "你是一个自主执行任务的 Agent。\n"
                    "执行规范：\n"
                    "- 直接完成任务，不要询问用户确认，不要停下来等待指示\n"
                    "- 减少不必要的探索步骤，先用最少的工具调用了解必要信息，再执行操作\n"
                    "- 任务完成的唯一标志是你已通过工具完成了所有操作（例如写入文件、提交数据）；"
                    '仅输出文字描述"我将要做..."不算完成\n'
                    "- 如果任务要求写入文件，必须调用写文件工具并收到成功确认，才算任务结束"
                ]
                if task.context:
                    system_parts.append(task.context)
                session.add_message(
                    Message(role=MessageRole.SYSTEM, content="\n\n".join(system_parts))
                )
                session.add_message(Message(role=MessageRole.USER, content=task.task))

                async for event in tool_loop.execute_stream_with_tools(
                    session,
                    allowed_tools=set(worker_allowed) if worker_allowed is not None else None,
                ):
                    if event.event_type == StreamEventType.TEXT_DELTA and event.content:
                        worker_text[task.agent_id] += event.content
                    if event.event_type in _EVENT_TYPE_MAP:
                        await queue.put(("event", task.agent_id, event))
            except asyncio.CancelledError:
                raise
            except Exception as e:
                error = str(e)
                logger.exception("Worker agent %s failed: %s", task.agent_id, e)
            finally:
                duration_ms = int(
                    (time.monotonic() - agent_start_times.get(task.agent_id, time.monotonic()))
                    * 1000
                )
                await queue.put(("done", task.agent_id, str(duration_ms), error or ""))

        worker_asyncio_tasks = [asyncio.create_task(run_agent(t)) for t in tasks]
        pending = len(tasks)
        done_durations: dict[str, int] = {}

        try:
            while pending > 0:
                item = await queue.get()
                if item[0] == "done":
                    _, agent_id, duration_str, error_str = item
                    duration_ms = int(duration_str)
                    done_durations[agent_id] = duration_ms
                    pending -= 1
                    yield StreamEvent(
                        event_type=StreamEventType.AGENT_DONE,
                        metadata={
                            "agent_id": agent_id,
                            "duration_ms": duration_ms,
                            "error": error_str or None,
                        },
                    )
                else:
                    _, agent_id, event = item
                    wrapped = _wrap_agent_event(str(agent_id), event)
                    if wrapped is not None:
                        yield wrapped
        except (asyncio.CancelledError, GeneratorExit):
            # 父任务被取消（用户停止生成）——立即取消所有 worker
            for t in worker_asyncio_tasks:
                if not t.done():
                    t.cancel()
            await asyncio.gather(*worker_asyncio_tasks, return_exceptions=True)
            raise
        else:
            await asyncio.gather(*worker_asyncio_tasks, return_exceptions=True)

        total_ms = int((time.monotonic() - overall_start) * 1000)

        sections: list[str] = []
        for task in tasks:
            text = worker_text[task.agent_id].strip() or "（无输出）"
            sections.append(f"## 子任务: {task.task}\n\n{text}")

        yield ToolExecutionResult(
            tool_name=tool_name,
            ok=True,
            data="\n\n---\n\n".join(sections),
            execution_time_ms=total_ms,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _get_llm_adapter(self, profile_id: str | None = None) -> LLMAdapter:
        if profile_id not in self._llm_adapters:
            profile = self._config.llm.get_profile(profile_id)
            if profile.protocol == "anthropic":
                self._llm_adapters[profile_id] = AnthropicAdapter(
                    api_key=profile.api_key,
                    default_model=profile.model,
                    base_url=profile.base_url,
                    extra_headers=profile.extra_headers,
                    max_tokens=profile.max_tokens,
                    supports_temperature=profile.capabilities.temperature,
                    use_anthropic_blocks=profile.capabilities.anthropic_blocks,
                )
            else:
                self._llm_adapters[profile_id] = OpenAIAdapter(
                    api_key=profile.api_key,
                    default_model=profile.model,
                    base_url=profile.base_url,
                    extra_headers=profile.extra_headers,
                    protocol=profile.protocol,
                    max_tokens=profile.max_tokens,
                )
        return self._llm_adapters[profile_id]

    def _build_tool_loop(
        self,
        profile_id: str | None = None,
        worker_tool_adapter: ToolAdapter | None = None,
    ) -> ToolLoopUseCase:
        tool_adapter: ToolAdapter = worker_tool_adapter or self._worker_tools
        return ToolLoopUseCase(
            llm_adapter=self._get_llm_adapter(profile_id),
            tool_adapter=tool_adapter,
            policy_engine=self._policy,
            max_iterations=_WORKER_MAX_ITERATIONS,
            max_tool_result_chars=self._config.agent.max_tool_result_chars,
            tool_timeout_s=self._config.agent.tool_timeout_s,
        )
