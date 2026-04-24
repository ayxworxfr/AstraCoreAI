"""Tool loop use case implementation."""

import asyncio
import time
from collections.abc import AsyncIterator
from typing import Any

from astracore.core.domain.message import Message, MessageRole, ToolResult
from astracore.core.domain.session import SessionState
from astracore.core.ports.llm import LLMAdapter, StreamEvent, StreamEventType
from astracore.core.ports.tool import ToolAdapter
from astracore.runtime.policy.engine import PolicyEngine


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
    ):
        self.llm = llm_adapter
        self.tools = tool_adapter
        self.policy = policy_engine
        self.max_iterations = max_iterations
        self.max_tool_result_chars = max_tool_result_chars
        self.tool_timeout_s = tool_timeout_s

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
                lines.append(
                    "⚠️ 本轮不提供工具调用，请基于以上已获取的工具结果直接给出最终回答。"
                )
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
            content[:limit]
            + f"\n\n[内容已截断，原始长度 {len(content)} 字符。"
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

            tool_results = []
            for tool_call in response.tool_calls:
                if not self.policy.check_security_policy(tool_call.name, tool_call.arguments):
                    tool_results.append(
                        ToolResult(
                            tool_call_id=tool_call.id,
                            name=tool_call.name,
                            content="Tool execution blocked by security policy",
                            is_error=True,
                        )
                    )
                    continue

                try:
                    exec_result = await asyncio.wait_for(
                        self.tools.execute(
                            tool_name=tool_call.name,
                            arguments=tool_call.arguments,
                        ),
                        timeout=self.tool_timeout_s,
                    )
                except asyncio.TimeoutError:
                    tool_results.append(
                        ToolResult(
                            tool_call_id=tool_call.id,
                            name=tool_call.name,
                            content=f"[超时] 工具 '{tool_call.name}' 执行超过 {self.tool_timeout_s:.0f}s，已中止。请换用更精确的参数重试。",
                            is_error=True,
                        )
                    )
                    continue
                tool_results.append(
                    ToolResult(
                        tool_call_id=tool_call.id,
                        name=exec_result.tool_name,
                        content=self._truncate_tool_result(
                            exec_result.output or exec_result.error or "Tool execution failed"
                        ),
                        is_error=not exec_result.success,
                        metadata=exec_result.metadata,
                    )
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

            async for event in self.llm.generate_stream(
                messages=self._inject_guidance(session.get_messages(), iterations),
                model=model,
                tools=tools_for_llm,
                **llm_kwargs,
            ):
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

            tool_results = []
            for tool_call in accumulated_tool_calls:
                if not self.policy.check_security_policy(tool_call.name, tool_call.arguments):
                    blocked = "Tool execution blocked by security policy"
                    yield StreamEvent(
                        event_type=StreamEventType.TOOL_RESULT,
                        content=tool_call.name,
                        metadata={"tool": tool_call.name, "input": tool_call.arguments,
                                  "result": blocked, "is_error": True, "duration_ms": 0},
                    )
                    tool_results.append(
                        ToolResult(
                            tool_call_id=tool_call.id,
                            name=tool_call.name,
                            content=blocked,
                            is_error=True,
                        )
                    )
                    continue

                tool_start_time = time.monotonic()
                try:
                    exec_result = await asyncio.wait_for(
                        self.tools.execute(
                            tool_name=tool_call.name,
                            arguments=tool_call.arguments,
                        ),
                        timeout=self.tool_timeout_s,
                    )
                except asyncio.TimeoutError:
                    timeout_duration_ms = int((time.monotonic() - tool_start_time) * 1000)
                    timeout_msg = (
                        f"[超时] 工具 '{tool_call.name}' 执行超过 {self.tool_timeout_s:.0f}s，"
                        "已中止。请换用更精确的参数重试。"
                    )
                    yield StreamEvent(
                        event_type=StreamEventType.TOOL_RESULT,
                        content=tool_call.name,
                        metadata={"tool": tool_call.name, "input": tool_call.arguments,
                                  "result": timeout_msg, "is_error": True,
                                  "duration_ms": timeout_duration_ms},
                    )
                    tool_results.append(
                        ToolResult(
                            tool_call_id=tool_call.id,
                            name=tool_call.name,
                            content=timeout_msg,
                            is_error=True,
                        )
                    )
                    continue

                duration_ms = int((time.monotonic() - tool_start_time) * 1000)
                content = self._truncate_tool_result(
                    exec_result.output or exec_result.error or "Tool execution failed"
                )
                yield StreamEvent(
                    event_type=StreamEventType.TOOL_RESULT,
                    content=exec_result.tool_name,
                    metadata={"tool": exec_result.tool_name, "input": tool_call.arguments,
                              "result": content, "is_error": not exec_result.success,
                              "duration_ms": duration_ms},
                )
                tool_results.append(
                    ToolResult(
                        tool_call_id=tool_call.id,
                        name=exec_result.tool_name,
                        content=content,
                        is_error=not exec_result.success,
                    )
                )

            session.add_message(
                Message(role=MessageRole.TOOL, content="", tool_results=tool_results)
            )
