"""Tool loop use case implementation."""

import asyncio
import contextlib
import json
import time
from collections.abc import AsyncIterator, Callable, Coroutine
from typing import Any, cast

from astracore.infrastructure.tools.read_tracked import ReadTrackedToolAdapter
from astracore.modules.chat.domain.message import Message, MessageRole, ToolCall, ToolResult
from astracore.modules.chat.domain.session import SessionState
from astracore.modules.tools.ports.tool import (
    ToolAdapter,
    ToolError,
    ToolErrorCode,
    ToolExecutionResult,
)
from astracore.shared.domain.hitl import HITLOption, PendingQuestion
from astracore.shared.observability.hooks import (
    HookRegistry,
    LLMCallInput,
    LLMCallOutput,
    ShortCircuit,
    ToolCallInput,
    ToolCallOutput,
)
from astracore.shared.observability.logger import get_logger
from astracore.shared.policy.engine import PolicyEngine
from astracore.shared.ports.llm import LLMAdapter, StreamEvent, StreamEventType
from astracore.shared.security.external_data import wrap_external

_TOOL_DONE = object()  # sentinel for parallel streaming tool execution
_logger = get_logger(__name__)


async def _ask_tool_confirmation(
    tool_call: ToolCall,
    hitl_callback: Callable[..., Coroutine[Any, Any, dict[str, Any]]],
) -> bool:
    """Ask the user to approve or deny execution of a requires_confirmation tool.

    Returns True if approved, False if denied.
    """
    args_preview = json.dumps(tool_call.arguments, ensure_ascii=False)[:200]
    q = PendingQuestion(
        question=(f"AI 即将执行工具 `{tool_call.name}`，参数预览：\n```json\n{args_preview}\n```"),
        header="工具确认",
        options=[
            HITLOption(label="允许", description="继续执行此工具"),
            HITLOption(label="拒绝", description="取消执行"),
        ],
        allow_freeform=False,
    )
    answer = await hitl_callback(q)
    return "允许" in answer.get("selected", [])


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
        hooks: HookRegistry | None = None,
    ):
        self.llm = llm_adapter
        self.tools = ReadTrackedToolAdapter(tool_adapter)
        self.policy = policy_engine
        self.max_iterations = max_iterations
        self.max_tool_result_chars = max_tool_result_chars
        self.tool_timeout_s = tool_timeout_s
        self.profile_id = profile_id
        ctx: dict[str, Any] = dict(extra_context) if extra_context else {}
        ctx.setdefault("_read_files", set())
        self._extra_context = ctx
        self._hooks = hooks

    @property
    def unlimited(self) -> bool:
        """max_iterations == 0 时不限制工具调用轮次。"""
        return self.max_iterations == 0

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

    def _truncate_tool_result(self, content: str, limit: int | None = None) -> str:
        """Truncate oversized tool results, appending a hint for pagination."""
        effective_limit = limit if limit is not None else self.max_tool_result_chars
        if len(content) <= effective_limit:
            return content
        return (
            content[:effective_limit] + f"\n\n[内容已截断，原始长度 {len(content)} 字符。"
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

    def _needs_closing_round(self, messages: list[Message]) -> bool:
        """Return True if the loop ended without a text response from the assistant."""
        visible = [m for m in messages if m.role != MessageRole.SYSTEM]
        if not visible:
            return False
        last = visible[-1]
        return (last.role == MessageRole.TOOL and last.has_tool_results()) or (
            last.role == MessageRole.ASSISTANT and not last.content.strip()
        )

    def _build_closing_messages(self, messages: list[Message]) -> list[Message]:
        """Inject a closing instruction so the LLM must respond without calling tools."""
        note = "工具调用阶段已结束。请直接基于以上工具结果给出最终回答，禁止继续调用工具。"
        msgs = list(messages)
        if msgs and msgs[0].role == MessageRole.SYSTEM:
            msgs[0] = msgs[0].model_copy(update={"content": f"{msgs[0].content}\n\n---\n\n{note}"})
        else:
            msgs.insert(0, Message(role=MessageRole.SYSTEM, content=note))
        return msgs

    # ------------------------------------------------------------------
    # Hook helpers
    # ------------------------------------------------------------------

    async def _fire_before_llm(
        self,
        messages: list[Message],
        model: str | None,
        tools: list[dict[str, Any]] | None,
        kwargs: dict[str, Any],
    ) -> LLMCallInput | ShortCircuit:
        payload = LLMCallInput(
            messages=messages,
            model=model,
            tools=tools,
            kwargs=kwargs,
        )
        if self._hooks:
            return await self._hooks.run_before_llm(payload)
        return payload

    async def _fire_after_llm(
        self,
        content: str,
        tool_calls: list[Any],
        metadata: dict[str, Any],
        duration_ms: int,
    ) -> LLMCallOutput:
        payload = LLMCallOutput(
            content=content,
            tool_calls=tool_calls,
            metadata=metadata,
            duration_ms=duration_ms,
        )
        if self._hooks:
            payload = await self._hooks.run_after_llm(payload)
        return payload

    async def _fire_before_tool(self, tool_call: ToolCall) -> ToolCallInput | ShortCircuit:
        payload = ToolCallInput(
            tool_call_id=tool_call.id,
            tool_name=tool_call.name,
            arguments=tool_call.arguments,
        )
        if self._hooks:
            return await self._hooks.run_before_tool(payload)
        return payload

    async def _fire_after_tool(
        self,
        tool_result: ToolResult,
        duration_ms: int,
    ) -> ToolCallOutput:
        payload = ToolCallOutput(
            tool_call_id=tool_result.tool_call_id,
            tool_name=tool_result.name,
            content=tool_result.content,
            is_error=tool_result.is_error,
            duration_ms=duration_ms,
            metadata=tool_result.metadata or {},
        )
        if self._hooks:
            payload = await self._hooks.run_after_tool(payload)
        return payload

    # ------------------------------------------------------------------
    # Tool execution
    # ------------------------------------------------------------------

    def _tool_requires_confirmation(self, tool_name: str) -> bool:
        """Return True if the tool definition has requires_confirmation=True."""
        for defn in self.tools.get_definitions():
            if defn.name == tool_name:
                return defn.requires_confirmation
        return False

    def _get_tool_timeout(self, tool_name: str) -> float | None:
        """Return effective timeout (seconds) for a tool: per-tool metadata overrides global."""
        for defn in self.tools.get_definitions():
            if defn.name == tool_name:
                per_tool = defn.metadata.get("timeout_s")
                if per_tool is not None:
                    return float(per_tool) or None
                break
        return self.tool_timeout_s or None

    def _get_tool_max_chars(self, tool_name: str) -> int:
        """Return effective max output chars for a tool: per-tool metadata overrides global."""
        for defn in self.tools.get_definitions():
            if defn.name == tool_name:
                per_tool = defn.metadata.get("max_output_chars")
                if per_tool is not None:
                    return int(per_tool)
                break
        return self.max_tool_result_chars

    async def _execute_one_tool(self, tool_call: ToolCall) -> ToolResult:
        """Execute a single tool call (non-streaming). Used for parallel gather."""
        hook_result = await self._fire_before_tool(tool_call)

        if isinstance(hook_result, ShortCircuit):
            sc_out = cast(ToolCallOutput, hook_result.result)
            return ToolResult(
                tool_call_id=sc_out.tool_call_id,
                name=sc_out.tool_name,
                content=sc_out.content,
                is_error=sc_out.is_error,
            )

        hook_input = hook_result

        # requires_confirmation: pause the run and ask the user to approve.
        if self._tool_requires_confirmation(hook_input.tool_name):
            hitl_callback = self._extra_context.get("hitl_callback")
            if hitl_callback is not None:
                approved = await _ask_tool_confirmation(tool_call, hitl_callback)
                if not approved:
                    result = ToolResult(
                        tool_call_id=hook_input.tool_call_id,
                        name=hook_input.tool_name,
                        content="用户拒绝执行此工具。",
                        is_error=False,
                    )
                    await self._fire_after_tool(result, duration_ms=0)
                    return result

        if not self.policy.check_security_policy(hook_input.tool_name, hook_input.arguments):
            result = ToolResult(
                tool_call_id=hook_input.tool_call_id,
                name=hook_input.tool_name,
                content="Tool execution blocked by security policy",
                is_error=True,
            )
            await self._fire_after_tool(result, duration_ms=0)
            return result

        tool_timeout = self._get_tool_timeout(hook_input.tool_name)
        tool_max_chars = self._get_tool_max_chars(hook_input.tool_name)
        t0 = time.monotonic()
        try:
            exec_result = await asyncio.wait_for(
                self.tools.execute(
                    tool_name=hook_input.tool_name,
                    arguments=hook_input.arguments,
                    context={**self._extra_context, "profile_id": self.profile_id},
                ),
                timeout=tool_timeout,
            )
        except TimeoutError:
            duration_ms = int((time.monotonic() - t0) * 1000)
            timeout_s = tool_timeout or 0
            result = ToolResult(
                tool_call_id=hook_input.tool_call_id,
                name=hook_input.tool_name,
                content=f"[超时] 工具 '{hook_input.tool_name}' 执行超过 {timeout_s:.0f}s，已中止。请换用更精确的参数重试。",
                is_error=True,
            )
            await self._fire_after_tool(result, duration_ms=duration_ms)
            return result

        duration_ms = int((time.monotonic() - t0) * 1000)
        raw = (
            (exec_result.data if isinstance(exec_result.data, str) else str(exec_result.data or ""))
            if exec_result.ok
            else (exec_result.error.message if exec_result.error else "Tool execution failed")
        )
        content = wrap_external(
            self._truncate_tool_result(raw, limit=tool_max_chars),
            source=f"tool:{exec_result.tool_name}",
        )
        result = ToolResult(
            tool_call_id=hook_input.tool_call_id,
            name=exec_result.tool_name,
            content=content,
            is_error=not exec_result.ok,
            metadata=exec_result.metadata,
        )
        await self._fire_after_tool(result, duration_ms=duration_ms)
        return result

    async def _enqueue_parse_error(
        self,
        tool_call: ToolCall,
        idx: int,
        error_msg: str,
        queue: asyncio.Queue[Any],
    ) -> None:
        """JSON 解析失败的工具调用不执行，直接将错误结果写入队列。"""
        result = ToolResult(
            tool_call_id=tool_call.id,
            name=tool_call.name,
            content=error_msg,
            is_error=True,
        )
        await self._fire_after_tool(result, duration_ms=0)
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
                        "result": error_msg,
                        "is_error": True,
                        "duration_ms": 0,
                    },
                ),
                None,
            )
        )
        await queue.put((idx, _TOOL_DONE, result))

    async def _run_tool_to_queue(
        self,
        tool_call: ToolCall,
        idx: int,
        queue: asyncio.Queue[Any],
    ) -> None:
        """Execute a single streaming tool call, pushing events and a final sentinel to queue."""
        hook_result = await self._fire_before_tool(tool_call)

        if isinstance(hook_result, ShortCircuit):
            sc_out = cast(ToolCallOutput, hook_result.result)
            result = ToolResult(
                tool_call_id=sc_out.tool_call_id,
                name=sc_out.tool_name,
                content=sc_out.content,
                is_error=sc_out.is_error,
            )
            await queue.put(
                (
                    idx,
                    StreamEvent(
                        event_type=StreamEventType.TOOL_RESULT,
                        content=result.name,
                        metadata={
                            "tool": result.name,
                            "tool_call_id": result.tool_call_id,
                            "input": {},
                            "result": result.content,
                            "is_error": result.is_error,
                            "duration_ms": 0,
                        },
                    ),
                    None,
                )
            )
            await queue.put((idx, _TOOL_DONE, result))
            return

        hook_input = hook_result

        # requires_confirmation: pause the run and ask the user to approve.
        if self._tool_requires_confirmation(hook_input.tool_name):
            hitl_callback = self._extra_context.get("hitl_callback")
            if hitl_callback is not None:
                approved = await _ask_tool_confirmation(tool_call, hitl_callback)
                if not approved:
                    denied_msg = "用户拒绝执行此工具。"
                    result = ToolResult(
                        tool_call_id=hook_input.tool_call_id,
                        name=hook_input.tool_name,
                        content=denied_msg,
                        is_error=False,
                    )
                    await self._fire_after_tool(result, duration_ms=0)
                    await queue.put(
                        (
                            idx,
                            StreamEvent(
                                event_type=StreamEventType.TOOL_RESULT,
                                content=hook_input.tool_name,
                                metadata={
                                    "tool": hook_input.tool_name,
                                    "tool_call_id": hook_input.tool_call_id,
                                    "input": hook_input.arguments,
                                    "result": denied_msg,
                                    "is_error": False,
                                    "duration_ms": 0,
                                },
                            ),
                            None,
                        )
                    )
                    await queue.put((idx, _TOOL_DONE, result))
                    return

        if not self.policy.check_security_policy(hook_input.tool_name, hook_input.arguments):
            blocked = "Tool execution blocked by security policy"
            result = ToolResult(
                tool_call_id=hook_input.tool_call_id,
                name=hook_input.tool_name,
                content=blocked,
                is_error=True,
            )
            await self._fire_after_tool(result, duration_ms=0)
            await queue.put(
                (
                    idx,
                    StreamEvent(
                        event_type=StreamEventType.TOOL_RESULT,
                        content=hook_input.tool_name,
                        metadata={
                            "tool": hook_input.tool_name,
                            "tool_call_id": hook_input.tool_call_id,
                            "input": hook_input.arguments,
                            "result": blocked,
                            "is_error": True,
                            "duration_ms": 0,
                        },
                    ),
                    None,
                )
            )
            await queue.put((idx, _TOOL_DONE, result))
            return

        tool_timeout = self._get_tool_timeout(hook_input.tool_name)
        tool_max_chars = self._get_tool_max_chars(hook_input.tool_name)
        tool_start_time = time.monotonic()
        exec_result: ToolExecutionResult | None = None
        timeout_cm = (
            contextlib.nullcontext()
            if self.tools.is_timeout_managed(hook_input.tool_name)
            else asyncio.timeout(tool_timeout)
        )
        try:
            async with timeout_cm:
                async for item in self.tools.execute_streaming(
                    tool_name=hook_input.tool_name,
                    arguments=hook_input.arguments,
                    context={**self._extra_context, "profile_id": self.profile_id},
                ):
                    if isinstance(item, StreamEvent):
                        await queue.put((idx, item, None))
                    else:
                        exec_result = item
        except TimeoutError:
            duration_ms = int((time.monotonic() - tool_start_time) * 1000)
            timeout_s = tool_timeout or 0
            timeout_msg = (
                f"[超时] 工具 '{hook_input.tool_name}' 执行超过 {timeout_s:.0f}s，"
                "已中止。请换用更精确的参数重试。"
            )
            result = ToolResult(
                tool_call_id=hook_input.tool_call_id,
                name=hook_input.tool_name,
                content=timeout_msg,
                is_error=True,
            )
            await self._fire_after_tool(result, duration_ms=duration_ms)
            await queue.put(
                (
                    idx,
                    StreamEvent(
                        event_type=StreamEventType.TOOL_RESULT,
                        content=hook_input.tool_name,
                        metadata={
                            "tool": hook_input.tool_name,
                            "tool_call_id": hook_input.tool_call_id,
                            "input": hook_input.arguments,
                            "result": timeout_msg,
                            "is_error": True,
                            "duration_ms": duration_ms,
                        },
                    ),
                    None,
                )
            )
            await queue.put((idx, _TOOL_DONE, result))
            return

        if exec_result is None:
            exec_result = ToolExecutionResult(
                tool_name=hook_input.tool_name,
                ok=False,
                error=ToolError(
                    code=ToolErrorCode.EXECUTION_ERROR,
                    message="Tool returned no result",
                    retryable=True,
                ),
                execution_time_ms=int((time.monotonic() - tool_start_time) * 1000),
            )

        duration_ms = int((time.monotonic() - tool_start_time) * 1000)
        raw = (
            (exec_result.data if isinstance(exec_result.data, str) else str(exec_result.data or ""))
            if exec_result.ok
            else (exec_result.error.message if exec_result.error else "Tool execution failed")
        )
        content = wrap_external(
            self._truncate_tool_result(raw, limit=tool_max_chars),
            source=f"tool:{exec_result.tool_name}",
        )
        result = ToolResult(
            tool_call_id=hook_input.tool_call_id,
            name=exec_result.tool_name,
            content=content,
            is_error=not exec_result.ok,
        )
        await self._fire_after_tool(result, duration_ms=duration_ms)
        await queue.put(
            (
                idx,
                StreamEvent(
                    event_type=StreamEventType.TOOL_RESULT,
                    content=exec_result.tool_name,
                    metadata={
                        "tool": exec_result.tool_name,
                        "tool_call_id": hook_input.tool_call_id,
                        "input": hook_input.arguments,
                        "result": content,
                        "is_error": not exec_result.ok,
                        "duration_ms": duration_ms,
                    },
                ),
                None,
            )
        )
        await queue.put((idx, _TOOL_DONE, result))

    # ------------------------------------------------------------------
    # Non-streaming loop
    # ------------------------------------------------------------------

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
            tools_for_llm = None if is_last else (tool_definitions if tool_definitions else None)

            injected_messages = self._inject_guidance(session.get_messages(), iterations)
            before_result = await self._fire_before_llm(
                messages=injected_messages,
                model=model,
                tools=tools_for_llm,
                kwargs={},
            )
            if isinstance(before_result, ShortCircuit):
                sc_out = before_result.result
                session.add_message(
                    Message(
                        role=MessageRole.ASSISTANT,
                        content=sc_out.content,
                        tool_calls=[],
                    )
                )
                break

            t0 = time.monotonic()
            response = await self.policy.apply_retry_policy(
                self.llm.generate,
                messages=injected_messages,
                model=model,
                tools=tools_for_llm,
            )
            await self._fire_after_llm(
                content=response.content,
                tool_calls=response.tool_calls,
                metadata={},
                duration_ms=int((time.monotonic() - t0) * 1000),
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

            tool_results: list[ToolResult] = list(
                await asyncio.gather(*[self._execute_one_tool(tc) for tc in response.tool_calls])
            )

            session.add_message(
                Message(role=MessageRole.TOOL, content="", tool_results=tool_results)
            )

        if self._needs_closing_round(session.get_messages()):
            msgs = self._build_closing_messages(session.get_messages())
            response = await self.policy.apply_retry_policy(
                self.llm.generate, messages=msgs, model=model, tools=None
            )
            session.add_message(
                Message(
                    role=MessageRole.ASSISTANT,
                    content=response.content or "工具执行完成。",
                    tool_calls=[],
                )
            )

        return session

    # ------------------------------------------------------------------
    # Streaming loop
    # ------------------------------------------------------------------

    async def execute_stream_with_tools(
        self,
        session: SessionState,
        model: str | None = None,
        allowed_tools: frozenset[str] | set[str] | None = None,
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
            tools_for_llm = None if is_last else (tool_definitions if tool_definitions else None)

            yield StreamEvent(
                event_type=StreamEventType.ROUND_START,
                metadata={"round": iterations},
            )
            round_start_time = time.monotonic()

            injected_messages = self._inject_guidance(session.get_messages(), iterations)
            before_result = await self._fire_before_llm(
                messages=injected_messages,
                model=model,
                tools=tools_for_llm,
                kwargs=llm_kwargs,
            )
            if isinstance(before_result, ShortCircuit):
                sc_out = before_result.result
                sc_content = sc_out.content
                yield StreamEvent(event_type=StreamEventType.TEXT_DELTA, content=sc_content)
                yield StreamEvent(
                    event_type=StreamEventType.THINKING_STOP,
                    metadata={"duration_ms": 0},
                )
                session.add_message(
                    Message(role=MessageRole.ASSISTANT, content=sc_content, tool_calls=[])
                )
                break

            accumulated_content = ""
            accumulated_tool_calls: list[ToolCall] = []
            # tool_call_id → error message：JSON 解析失败的工具调用，跳过实际执行
            parse_error_results: dict[str, str] = {}
            assistant_metadata: dict[str, Any] = {}
            _llm_completed = False

            try:
                async for event in self.llm.generate_stream(
                    messages=injected_messages,
                    model=model,
                    tools=tools_for_llm,
                    **llm_kwargs,
                ):
                    if event.event_type == StreamEventType.TEXT_DELTA and event.content:
                        accumulated_content += event.content
                    elif event.event_type == StreamEventType.TOOL_CALL and event.tool_call:
                        accumulated_tool_calls.append(event.tool_call)
                    elif event.event_type == StreamEventType.TOOL_CALL_ERROR and event.tool_call:
                        # 参数 JSON 无法修复：仍将 tool_call 写入 assistant 消息保持协议完整性，
                        # 工具执行阶段会直接注入 is_error=True 的结果，LLM 下一轮可自行重试。
                        accumulated_tool_calls.append(event.tool_call)
                        parse_error_results[event.tool_call.id] = (
                            event.error or "工具参数 JSON 解析失败，请重新调用"
                        )
                    elif event.event_type == StreamEventType.DONE:
                        raw_blocks = event.metadata.get(self._ANTHROPIC_BLOCKS_KEY)
                        if isinstance(raw_blocks, list) and raw_blocks:
                            assistant_metadata[self._ANTHROPIC_BLOCKS_KEY] = raw_blocks
                    yield event
                _llm_completed = True
            finally:
                # 流式中断时（连接断开、请求取消等）保存已生成的部分回答，
                # 避免中断导致本轮已累积内容完全丢失。
                if not _llm_completed and accumulated_content.strip():
                    session.add_message(
                        Message(
                            role=MessageRole.ASSISTANT,
                            content=accumulated_content,
                            tool_calls=accumulated_tool_calls,
                            metadata=assistant_metadata,
                        )
                    )

            llm_duration_ms = int((time.monotonic() - round_start_time) * 1000)
            await self._fire_after_llm(
                content=accumulated_content,
                tool_calls=accumulated_tool_calls,
                metadata=assistant_metadata,
                duration_ms=llm_duration_ms,
            )

            yield StreamEvent(
                event_type=StreamEventType.THINKING_STOP,
                metadata={"duration_ms": llm_duration_ms},
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

            queue: asyncio.Queue[Any] = asyncio.Queue()
            tasks = [
                asyncio.create_task(
                    self._enqueue_parse_error(tc, i, parse_error_results[tc.id], queue)
                    if tc.id in parse_error_results
                    else self._run_tool_to_queue(tc, i, queue)
                )
                for i, tc in enumerate(accumulated_tool_calls)
            ]
            results_by_idx: dict[int, ToolResult] = {}
            done_count = 0
            try:
                while done_count < len(tasks):
                    idx, item, result = await queue.get()
                    if item is _TOOL_DONE:
                        results_by_idx[idx] = result
                        done_count += 1
                    else:
                        yield item
            except (asyncio.CancelledError, GeneratorExit):
                for t in tasks:
                    t.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                raise
            tool_results = [results_by_idx[i] for i in range(len(accumulated_tool_calls))]

            session.add_message(
                Message(role=MessageRole.TOOL, content="", tool_results=tool_results)
            )

        if self._needs_closing_round(session.get_messages()):
            # Phase boundary: resets in_tool_round in the API layer.
            yield StreamEvent(event_type=StreamEventType.DONE, metadata={"source": "tool_loop"})
            msgs = self._build_closing_messages(session.get_messages())
            closing_content = ""
            _closing_completed = False
            try:
                async for event in self.llm.generate_stream(
                    messages=msgs, model=model, tools=None, **llm_kwargs
                ):
                    if event.event_type == StreamEventType.TEXT_DELTA and event.content:
                        closing_content += event.content
                    yield event
                _closing_completed = True
            finally:
                if not _closing_completed and closing_content.strip():
                    session.add_message(
                        Message(
                            role=MessageRole.ASSISTANT,
                            content=closing_content,
                            tool_calls=[],
                        )
                    )
            if _closing_completed:
                session.add_message(
                    Message(
                        role=MessageRole.ASSISTANT,
                        content=closing_content or "工具执行完成。",
                        tool_calls=[],
                    )
                )
