"""Anthropic Claude adapter implementation."""

from collections.abc import AsyncIterator
from typing import Any

from astracore.core.domain.message import Message, MessageRole, ToolCall
from astracore.core.ports.llm import LLMAdapter, LLMResponse, StreamEvent, StreamEventType


class AnthropicAdapter(LLMAdapter):
    """Anthropic Claude LLM adapter."""

    def __init__(self, api_key: str, default_model: str = "claude-3-5-sonnet-20241022"):
        self.api_key = api_key
        self.default_model = default_model
        self._client: Any = None

    def _get_client(self) -> Any:
        """Lazy load Anthropic client."""
        if self._client is None:
            try:
                from anthropic import AsyncAnthropic

                self._client = AsyncAnthropic(api_key=self.api_key)
            except ImportError as e:
                raise ImportError(
                    "anthropic package not installed. Install with: pip install anthropic"
                ) from e
        return self._client

    def _convert_messages(self, messages: list[Message]) -> list[dict[str, Any]]:
        """Convert framework messages to Anthropic format.

        Anthropic Messages API 只允许 "user" / "assistant" 两种 role。
        工具结果消息必须以 role="user" + type="tool_result" 形式发送。
        """
        converted = []
        for msg in messages:
            if msg.role == MessageRole.SYSTEM:
                continue

            # 工具结果：role 必须是 "user"，content 是 tool_result 块列表
            if msg.has_tool_results():
                content: Any = [
                    {
                        "type": "tool_result",
                        "tool_use_id": tr.tool_call_id,
                        "content": tr.content,
                        "is_error": tr.is_error,
                    }
                    for tr in msg.tool_results
                ]
                converted.append({"role": "user", "content": content})
                continue

            # assistant 调用工具：content 是 text + tool_use 块列表
            if msg.has_tool_calls():
                blocks: list[dict[str, Any]] = []
                if msg.content:
                    blocks.append({"type": "text", "text": msg.content})
                blocks.extend(
                    {
                        "type": "tool_use",
                        "id": tc.id,
                        "name": tc.name,
                        "input": tc.arguments,
                    }
                    for tc in msg.tool_calls
                )
                converted.append({"role": "assistant", "content": blocks})
                continue

            converted.append({"role": msg.role.value, "content": msg.content})

        return converted

    def _get_system_message(self, messages: list[Message]) -> str | None:
        """Extract system message."""
        for msg in messages:
            if msg.role == MessageRole.SYSTEM:
                return msg.content
        return None

    async def generate(
        self,
        messages: list[Message],
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.7,
        **kwargs: Any,
    ) -> LLMResponse:
        """Generate a complete response."""
        client = self._get_client()
        model = model or self.default_model
        max_tokens = max_tokens or 4096

        system = self._get_system_message(messages)
        converted_messages = self._convert_messages(messages)

        request_params: dict[str, Any] = {
            "model": model,
            "messages": converted_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        if system:
            request_params["system"] = system

        if "tools" in kwargs:
            request_params["tools"] = kwargs["tools"]

        response = await client.messages.create(**request_params)

        content_text = ""
        tool_calls: list[ToolCall] = []

        for block in response.content:
            if block.type == "text":
                content_text += block.text
            elif block.type == "tool_use":
                tool_calls.append(
                    ToolCall(
                        id=block.id,
                        name=block.name,
                        arguments=block.input,
                    )
                )

        return LLMResponse(
            content=content_text,
            tool_calls=tool_calls,
            model=model,
            usage={
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            },
        )

    async def generate_stream(
        self,
        messages: list[Message],
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.7,
        **kwargs: Any,
    ) -> AsyncIterator[StreamEvent]:
        """Generate a streaming response.

        额外支持的 kwargs：
        - enable_thinking (bool): 开启 Claude Extended Thinking
        - thinking_budget (int): thinking token 预算，默认 8000
        """
        import json as _json

        enable_thinking: bool = kwargs.get("enable_thinking", False)
        thinking_budget: int = kwargs.get("thinking_budget", 8000)

        client = self._get_client()
        model = model or self.default_model
        max_tokens = max_tokens or 16000

        system = self._get_system_message(messages)
        converted_messages = self._convert_messages(messages)

        request_params: dict[str, Any] = {
            "model": model,
            "messages": converted_messages,
            "max_tokens": max_tokens,
            # Extended Thinking 要求 temperature=1，普通模式保留用户设置
            "temperature": 1.0 if enable_thinking else temperature,
        }

        if system:
            request_params["system"] = system

        if "tools" in kwargs:
            request_params["tools"] = kwargs["tools"]

        if enable_thinking:
            request_params["thinking"] = {
                "type": "enabled",
                "budget_tokens": thinking_budget,
            }

        # index → {type, id?, name?, input_str?}
        block_buffers: dict[int, dict[str, Any]] = {}

        async with client.messages.stream(**request_params) as stream:
            async for event in stream:
                if not hasattr(event, "type"):
                    continue

                if event.type == "content_block_start":
                    block = getattr(event, "content_block", None)
                    if block is None:
                        continue
                    idx = getattr(event, "index", 0)
                    block_type = getattr(block, "type", None)
                    if block_type == "tool_use":
                        block_buffers[idx] = {
                            "kind": "tool",
                            "id": block.id,
                            "name": block.name,
                            "input_str": "",
                        }
                    elif block_type == "thinking":
                        block_buffers[idx] = {"kind": "thinking"}

                elif event.type == "content_block_delta":
                    delta = getattr(event, "delta", None)
                    if delta is None:
                        continue
                    delta_type = getattr(delta, "type", None)
                    idx = getattr(event, "index", 0)

                    if delta_type == "text_delta":
                        yield StreamEvent(
                            event_type=StreamEventType.TEXT_DELTA,
                            content=delta.text,
                        )
                    elif delta_type == "thinking_delta":
                        yield StreamEvent(
                            event_type=StreamEventType.THINKING_DELTA,
                            content=getattr(delta, "thinking", ""),
                        )
                    elif delta_type == "input_json_delta":
                        if idx in block_buffers and block_buffers[idx].get("kind") == "tool":
                            block_buffers[idx]["input_str"] += delta.partial_json

                elif event.type == "content_block_stop":
                    idx = getattr(event, "index", 0)
                    buf = block_buffers.pop(idx, None)
                    if buf and buf.get("kind") == "tool":
                        arguments = _json.loads(buf["input_str"]) if buf["input_str"] else {}
                        yield StreamEvent(
                            event_type=StreamEventType.TOOL_CALL,
                            tool_call=ToolCall(
                                id=buf["id"],
                                name=buf["name"],
                                arguments=arguments,
                            ),
                        )

        yield StreamEvent(event_type=StreamEventType.DONE)

    async def count_tokens(self, messages: list[Message]) -> int:
        """Count tokens in messages."""
        client = self._get_client()
        converted = self._convert_messages(messages)

        try:
            response = await client.messages.count_tokens(
                model=self.default_model,
                messages=converted,
            )
            return response.input_tokens
        except Exception:
            return sum(msg.token_estimate() for msg in messages)

    def supports_tools(self) -> bool:
        """Check if provider supports tool calling."""
        return True
