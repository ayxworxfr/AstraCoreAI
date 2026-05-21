"""Tool loop use case implementation."""

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator
from typing import Any

from astracore.modules.chat.domain.message import Message, MessageRole, ToolCall, ToolResult
from astracore.modules.chat.domain.session import SessionState
from astracore.modules.tools.ports.tool import ToolAdapter, ToolExecutionResult
from astracore.shared.observability.logger import get_logger
from astracore.shared.policy.engine import PolicyEngine
from astracore.shared.ports.llm import LLMAdapter, StreamEvent, StreamEventType

_TOOL_DONE = object()  # sentinel for parallel streaming tool execution
_logger = get_logger(__name__)
_LLM_RETRY_MAX = 2


class ToolLoopUseCase:
    _ANTHROPIC_BLOCKS_KEY = "anthropic_content_blocks"

    """Tool calling loop with automatic execution."""

    def __init__(
        self,
        llm_adapter: LLMAdapter,
        tool_adapter: ToolAdapter,
        policy_engine: PolicyEngine,
        max_iterations: int = 10,
        max_tool_result_chars: int = 20_000,
        tool_timeout_s: float = 120.0,
        profile_id: str | None = None,
        extra_context: dict[str, Any] | None = None,
    ):
        self.llm = llm_adapter
        self.tools = tool_adapter
        self.policy = policy_engine
        self.max_iterations = max_iterations
        self.max_tool_result_chars = max_tool_result_chars
        self.tool_timeout_s = tool_timeout_s
        self.profile_id = profile_id
        self._extra_context: dict[str, Any] = extra_context or {}

    @property
    def unlimited(self) -> bool:
        """max_iterations == 0 时不限制工具调用轮次。"""
        return self.max_iterations == 0

    async def _collect_llm_stream(self, **kwargs: Any) -> list[StreamEvent]:
        """缓冲 generate_stream 的所有事件，遇到 tool-call JSON 截断时自动重试。"""
        for attempt in range(_LLM_RETRY_MAX + 1):
            buffer: list[StreamEvent] = []
            try:
                async for event in self.llm.generate_stream(**kwargs):
                    buffer.append(event)
                return buffer
            except ValueError as exc:
                if attempt < _LLM_RETRY_MAX:
                    _logger.warning(
                        "LLM stream ValueError (attempt %d/%d), retrying: %s",
                        attempt + 1,
                        _LLM_RETRY_MAX,
                        exc,
                    )
                    continue
                raise
        return []  # unreachable

    def _build_tool_guidance(self, iteration: int) -> str:
        """每轮注入给 LLM 的工具使用进度提示（不存入 session）。"""
        if self.unlimited:
            lines = [
                f"[工具调用进度] 第 {iteration} 轮（无轮次限制）。",
                "工具使用规范：",
                "- 搜索文件时避免 **/* 等宽泛模式，优先指定具体目录和文件扩展名",
                "- 先用少量调用探索目录结构，再针对性深入",
                "- 单次工具结果过长时，使用 offset/page 参数分页读取",
            ]
        else:
            remaining = self.max_iterations - iteration + 1
            lines = [
                f"[工具调用进度] 第 {iteration}/{self.max_iterations} 轮，剩余 {remaining} 次机会。",
                "工具使用规范：",
                "- 搜索文件时避免 **/* 等宽泛模式，优先指定具体目录和文件扩展名",
                "- 先用少量调用探索目录结构，再针对性深入",
                "- 单次工具结果过长时，使用 offset/page 参数分页读取",
            ]
            if remaining == 1:
                lines.append("⚠️ 本轮不提供工具调用，请基于以上已获取的工具结果直接给出最终回答。")
        return "\n".join(lines)

    def _inject_guidance(self, messages: list[Message], iteration: int) -> list[Message]:
        """将工具进度提示注入消息列表，供本次 LLM 调用使用，不修改 session。"""
        guidance = self._build_tool_guidance(iteration)
        msgs = list(messages)
        if msgs and msgs[0].role == MessageRole.SYSTEM:
            merged = msgs[0].model_copy(
                update={"content": f"{msgs[0].content}\n\n---\n\n{guidance}"}
            )
            return [merged] + msgs[1:]
        return [Message(role=MessageRole.SYSTEM, content=guidance)] + msgs

    def _truncate_tool_result(self, content: str) -> str:
        """Truncate oversized tool results, appending a hint for pagination."""
        limit = self.max_tool_result_chars
        if len(content) <= limit:
            return content
        return (
            content[:limit] + f"\n\n[内容已截断，原始长度 {len(content)} 字符。"
            "如需查看更多，请使用 offset/page 参数重新调用工具。]"
        )

    def _build_tool_definitions(self) -> list[dict[str, Any]]:
        """Build tool definitions dict for LLM. Single source of truth."""
        return [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": {
                    "type": "object",
                    "properties": {
                        p.name: {"type": p.type.value, "description": p.description}
                        for p in t.parameters
                    },
                    "required": [p.name for p in t.parameters if p.required],
                },
            }
            for t in self.tools.get_definitions()
        ]

    async def _execute_one_tool(self, tool_call: ToolCall) -> ToolResult:
        """Execute a single tool call (non-streaming). Used for parallel gather."""
        if not self.policy.check_security_policy(tool_call.name, tool_call.arguments):
            return ToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                content="Tool execution blocked by security policy",
                is_error=True,
            )
        try:
            exec_result = await asyncio.wait_for(
                self.tools.execute(
                    tool_name=tool_call.name,
                    arguments=tool_call.arguments,
                    context={**self._extra_context, "profile_id": self.profile_id},
                ),
                timeout=self.tool_timeout_s,
            )
        except TimeoutError:
            return ToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                content=f"[超时] 工具 '{tool_call.name}' 执行超过 {self.tool_timeout_s:.0f}s，已中止。请换用更精确的参数重试。",
                is_error=True,
            )
        return ToolResult(
            tool_call_id=tool_call.id,
            name=exec_result.tool_name,
            content=self._truncate_tool_result(
                exec_result.output or exec_result.error or "Tool execution failed"
            ),
            is_error=not exec_result.success,
            metadata=exec_result.metadata,
        )

    async def _run_tool_to_queue(
        self,
        tool_call: ToolCall,
        idx: int,
        queue: asyncio.Queue[Any],
    ) -> None:
        """Execute a single streaming tool call, pushing events and a final sentinel to queue."""
        if not self.policy.check_security_policy(tool_call.name, tool_call.arguments):
            blocked = "Tool execution blocked by security policy"
            await queue.put(
                (
                    idx,
                    StreamEvent(
                        event_type=StreamEventType.TOOL_RESULT,
                        content=tool_call.name,
                        metadata={
                            "tool": tool_call.name,
                            "tool_call_id": tool_call.id,
                            "input": tool_call.arguments,
                            "result": blocked,
                            "is_error": True,
                            "duration_ms": 0,
                        },
                    ),
                    None,
                )
            )
            await queue.put(
                (
                    idx,
                    _TOOL_DONE,
                    ToolResult(
                        tool_call_id=tool_call.id,
                        name=tool_call.name,
                        content=blocked,
                        is_error=True,
                    ),
                )
            )
            return

        tool_start_time = time.monotonic()
        exec_result: ToolExecutionResult | None = None
        timeout_cm = (
            contextlib.nullcontext()
            if self.tools.is_timeout_managed(tool_call.name)
            else asyncio.timeout(self.tool_timeout_s)
        )
        try:
            async with timeout_cm:
                async for item in self.tools.execute_streaming(
                    tool_name=tool_call.name,
                    arguments=tool_call.arguments,
                    context={**self._extra_context, "profile_id": self.profile_id},
                ):
                    if isinstance(item, StreamEvent):
                        await queue.put((idx, item, None))
                    else:
                        exec_result = item
        except TimeoutError:
            duration_ms = int((time.monotonic() - tool_start_time) * 1000)
            timeout_msg = (
                f"[超时] 工具 '{tool_call.name}' 执行超过 {self.tool_timeout_s:.0f}s，"
                "已中止。请换用更精确的参数重试。"
            )
            await queue.put(
                (
                    idx,
                    StreamEvent(
                        event_type=StreamEventType.TOOL_RESULT,
                        content=tool_call.name,
                        metadata={
                            "tool": tool_call.name,
                            "tool_call_id": tool_call.id,
                            "input": tool_call.arguments,
                            "result": timeout_msg,
                            "is_error": True,
                            "duration_ms": duration_ms,
                        },
                    ),
                    None,
                )
            )
            await queue.put(
                (
                    idx,
                    _TOOL_DONE,
                    ToolResult(
                        tool_call_id=tool_call.id,
                        name=tool_call.name,
                        content=timeout_msg,
                        is_error=True,
                    ),
                )
            )
            return

        if exec_result is None:
            exec_result = ToolExecutionResult(
                tool_name=tool_call.name,
                success=False,
                output="",
                error="Tool returned no result",
                execution_time_ms=int((time.monotonic() - tool_start_time) * 1000),
            )

        duration_ms = int((time.monotonic() - tool_start_time) * 1000)
        content = self._truncate_tool_result(
            exec_result.output or exec_result.error or "Tool execution failed"
        )
        await queue.put(
            (
                idx,
                StreamEvent(
                    event_type=StreamEventType.TOOL_RESULT,
                    content=exec_result.tool_name,
                    metadata={
                        "tool": exec_result.tool_name,
                        "tool_call_id": tool_call.id,
                        "input": tool_call.arguments,
                        "result": content,
                        "is_error": not exec_result.success,
                        "duration_ms": duration_ms,
                    },
                ),
                None,
            )
        )
        await queue.put(
            (
                idx,
                _TOOL_DONE,
                ToolResult(
                    tool_call_id=tool_call.id,
                    name=exec_result.tool_name,
                    content=content,
                    is_error=not exec_result.success,
                ),
            )
        )

    async def execute_with_tools(
        self,
        session: SessionState,
        model: str | None = None,
        allowed_tools: set[str] | None = None,
    ) -> SessionState:
        """Execute tool loop until completion."""
        tool_definitions = self._build_tool_definitions()
        if allowed_tools is not None:
            tool_definitions = [t for t in tool_definitions if t["name"] in allowed_tools]
        iterations = 0

        while self.unlimited or iterations < self.max_iterations:
            iterations += 1
            is_last = (not self.unlimited) and (iterations == self.max_iterations)
            # 最后一轮不传工具，强制 LLM 给文本答案，避免产生无对应 tool_result 的 tool_use
            tools_for_llm = None if is_last else (tool_definitions if tool_definitions else None)

            response = await self.policy.apply_retry_policy(
                self.llm.generate,
                messages=self._inject_guidance(session.get_messages(), iterations),
                model=model,
                tools=tools_for_llm,
            )

            assistant_msg = Message(
                role=MessageRole.ASSISTANT,
                content=response.content,
                tool_calls=response.tool_calls,
            )
            session.add_message(assistant_msg)

            if not response.tool_calls:
                break
            if not self.unlimited and iterations >= self.max_iterations:
                break

            # Parallel execution: all tool calls in this round run concurrently
            tool_results: list[ToolResult] = list(
                await asyncio.gather(*[self._execute_one_tool(tc) for tc in response.tool_calls])
            )

            session.add_message(
                Message(role=MessageRole.TOOL, content="", tool_results=tool_results)
            )

        return session

    async def execute_stream_with_tools(
        self,
        session: SessionState,
        model: str | None = None,
        allowed_tools: set[str] | None = None,
        **llm_kwargs: Any,
    ) -> AsyncIterator[StreamEvent]:
        """Execute tool loop with streaming.

        每轮开始时 yield ROUND_START，前端用来分隔思考块。
        allowed_tools: 若指定，则只将名称在集合内的工具暴露给 LLM。
        llm_kwargs 透传给 LLM 适配器（如 enable_thinking）。
        """
        tool_definitions = self._build_tool_definitions()
        if allowed_tools is not None:
            tool_definitions = [t for t in tool_definitions if t["name"] in allowed_tools]
        iterations = 0

        while self.unlimited or iterations < self.max_iterations:
            iterations += 1
            is_last = (not self.unlimited) and (iterations == self.max_iterations)
            # 最后一轮不传工具，强制 LLM 给文本答案，避免产生无对应 tool_result 的 tool_use
            tools_for_llm = None if is_last else (tool_definitions if tool_definitions else None)

            # 通知前端新一轮开始，携带轮次编号
            yield StreamEvent(
                event_type=StreamEventType.ROUND_START,
                metadata={"round": iterations},
            )
            round_start_time = time.monotonic()

            accumulated_content = ""
            accumulated_tool_calls = []
            assistant_metadata: dict[str, Any] = {}

            buffered_events = await self._collect_llm_stream(
                messages=self._inject_guidance(session.get_messages(), iterations),
                model=model,
                tools=tools_for_llm,
                **llm_kwargs,
            )
            for event in buffered_events:
                # 只累积文本，不要把 thinking 内容混入
                if event.event_type == StreamEventType.TEXT_DELTA and event.content:
                    accumulated_content += event.content
                if event.event_type == StreamEventType.DONE:
                    raw_blocks = event.metadata.get(self._ANTHROPIC_BLOCKS_KEY)
                    if isinstance(raw_blocks, list) and raw_blocks:
                        assistant_metadata[self._ANTHROPIC_BLOCKS_KEY] = raw_blocks
                if event.tool_call:
                    accumulated_tool_calls.append(event.tool_call)
                yield event

            # 本轮 LLM 生成结束，告知前端耗时
            yield StreamEvent(
                event_type=StreamEventType.THINKING_STOP,
                metadata={"duration_ms": int((time.monotonic() - round_start_time) * 1000)},
            )

            session.add_message(
                Message(
                    role=MessageRole.ASSISTANT,
                    content=accumulated_content,
                    tool_calls=accumulated_tool_calls,
                    metadata=assistant_metadata,
                )
            )

            if not accumulated_tool_calls:
                break
            if not self.unlimited and iterations >= self.max_iterations:
                break

            # Parallel streaming: all tool calls in this round run as concurrent tasks,
            # pushing events into a shared queue; outer generator drains and yields them.
            queue: asyncio.Queue[Any] = asyncio.Queue()
            tasks = [
                asyncio.create_task(self._run_tool_to_queue(tc, i, queue))
                for i, tc in enumerate(accumulated_tool_calls)
            ]
            results_by_idx: dict[int, ToolResult] = {}
            done_count = 0
            try:
                while done_count < len(tasks):
                    idx, item, result = await queue.get()
                    if item is _TOOL_DONE:
                        results_by_idx[idx] = result  # type: ignore[assignment]
                        done_count += 1
                    else:
                        yield item  # type: ignore[misc]
            except (asyncio.CancelledError, GeneratorExit):
                for t in tasks:
                    t.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                raise
            tool_results = [results_by_idx[i] for i in range(len(accumulated_tool_calls))]

            session.add_message(
                Message(role=MessageRole.TOOL, content="", tool_results=tool_results)
            )
