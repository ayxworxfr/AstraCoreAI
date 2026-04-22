"""Tool loop use case implementation."""

from collections.abc import AsyncIterator
from typing import Any

from astracore.core.domain.message import Message, MessageRole, ToolResult
from astracore.core.domain.session import SessionState
from astracore.core.ports.llm import LLMAdapter, StreamEvent, StreamEventType
from astracore.core.ports.tool import ToolAdapter
from astracore.runtime.policy.engine import PolicyEngine


class ToolLoopUseCase:
    """Tool calling loop with automatic execution."""

    def __init__(
        self,
        llm_adapter: LLMAdapter,
        tool_adapter: ToolAdapter,
        policy_engine: PolicyEngine,
        max_iterations: int = 10,
        max_tool_result_chars: int = 20_000,
    ):
        self.llm = llm_adapter
        self.tools = tool_adapter
        self.policy = policy_engine
        self.max_iterations = max_iterations
        self.max_tool_result_chars = max_tool_result_chars

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
    ) -> SessionState:
        """Execute tool loop until completion."""
        tool_definitions = self._build_tool_definitions()
        iterations = 0

        while iterations < self.max_iterations:
            iterations += 1

            response = await self.policy.apply_retry_policy(
                self.llm.generate,
                messages=session.get_messages(),
                model=model,
                tools=tool_definitions if tool_definitions else None,
            )

            assistant_msg = Message(
                role=MessageRole.ASSISTANT,
                content=response.content,
                tool_calls=response.tool_calls,
            )
            session.add_message(assistant_msg)

            if not response.tool_calls:
                break
            if iterations >= self.max_iterations:
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

                exec_result = await self.policy.apply_timeout_policy(
                    self.tools.execute,
                    "tool",
                    tool_name=tool_call.name,
                    arguments=tool_call.arguments,
                )
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

        while iterations < self.max_iterations:
            iterations += 1

            # 通知前端新一轮开始，携带轮次编号
            yield StreamEvent(
                event_type=StreamEventType.ROUND_START,
                metadata={"round": iterations},
            )

            accumulated_content = ""
            accumulated_tool_calls = []

            async for event in self.llm.generate_stream(
                messages=session.get_messages(),
                model=model,
                tools=tool_definitions if tool_definitions else None,
                **llm_kwargs,
            ):
                # 只累积文本，不要把 thinking 内容混入
                if event.event_type == StreamEventType.TEXT_DELTA and event.content:
                    accumulated_content += event.content
                if event.tool_call:
                    accumulated_tool_calls.append(event.tool_call)
                yield event

            session.add_message(
                Message(
                    role=MessageRole.ASSISTANT,
                    content=accumulated_content,
                    tool_calls=accumulated_tool_calls,
                )
            )

            if not accumulated_tool_calls:
                break
            if iterations >= self.max_iterations:
                break

            tool_results = []
            for tool_call in accumulated_tool_calls:
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

                exec_result = await self.policy.apply_timeout_policy(
                    self.tools.execute,
                    "tool",
                    tool_name=tool_call.name,
                    arguments=tool_call.arguments,
                )
                tool_results.append(
                    ToolResult(
                        tool_call_id=tool_call.id,
                        name=exec_result.tool_name,
                        content=self._truncate_tool_result(
                            exec_result.output or exec_result.error or "Tool execution failed"
                        ),
                        is_error=not exec_result.success,
                    )
                )

            session.add_message(
                Message(role=MessageRole.TOOL, content="", tool_results=tool_results)
            )
