"""OpenAI 兼容 API 适配器（OpenAI、DeepSeek 等）。"""

import json
from collections.abc import AsyncIterator
from typing import Any

from astracore.core.domain.message import Message, MessageRole, ToolCall
from astracore.core.ports.llm import LLMAdapter, LLMResponse, StreamEvent, StreamEventType


class OpenAIAdapter(LLMAdapter):
    """OpenAI Chat Completions 协议适配器。"""

    def __init__(
        self,
        api_key: str,
        default_model: str = "gpt-4o",
        base_url: str | None = None,
    ):
        self.api_key = api_key
        self.default_model = default_model
        self._base_url = base_url
        self._client: Any = None

    def _get_client(self) -> Any:
        """懒加载 OpenAI 兼容客户端。"""
        if self._client is None:
            try:
                from openai import AsyncOpenAI

                kwargs: dict[str, Any] = {"api_key": self.api_key}
                if self._base_url:
                    kwargs["base_url"] = self._base_url
                self._client = AsyncOpenAI(**kwargs)
            except ImportError as e:
                raise ImportError(
                    "openai package not installed. Install with: pip install openai"
                ) from e
        return self._client

    @staticmethod
    def _tools_for_openai(kwargs: dict[str, Any]) -> list[dict[str, Any]] | None:
        """将 ToolLoop 的 Anthropic 风格工具定义转为 OpenAI tools格式。"""
        raw = kwargs.get("tools")
        if not raw:
            return None
        out: list[dict[str, Any]] = []
        for t in raw:
            if t.get("type") == "function" and "function" in t:
                out.append(t)
                continue
            if "name" in t and "input_schema" in t:
                out.append(
                    {
                        "type": "function",
                        "function": {
                            "name": t["name"],
                            "description": t.get("description") or "",
                            "parameters": t["input_schema"],
                        },
                    }
                )
                continue
            out.append(t)
        return out

    def _convert_messages(self, messages: list[Message]) -> list[dict[str, Any]]:
        """转为 OpenAI messages 列表。"""
        converted = []
        for msg in messages:
            # 单条 TOOL 消息可挂多条 tool_result，需拆成多条 role=tool
            if msg.role == MessageRole.TOOL and msg.has_tool_results():
                for tr in msg.tool_results:
                    converted.append(
                        {
                            "role": "tool",
                            "tool_call_id": tr.tool_call_id,
                            "content": tr.content,
                        }
                    )
                continue

            message_dict: dict[str, Any] = {
                "role": msg.role.value,
                "content": msg.content,
            }

            if msg.has_tool_calls():
                message_dict["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments),
                        },
                    }
                    for tc in msg.tool_calls
                ]

            converted.append(message_dict)

        return converted

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

        converted_messages = self._convert_messages(messages)

        request_params: dict[str, Any] = {
            "model": model,
            "messages": converted_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        tools = self._tools_for_openai(kwargs)
        if tools:
            request_params["tools"] = tools

        response = await client.chat.completions.create(**request_params)

        choice = response.choices[0]
        content = choice.message.content or ""
        tool_calls: list[ToolCall] = []

        if choice.message.tool_calls:
            for tc in choice.message.tool_calls:
                tool_calls.append(
                    ToolCall(
                        id=tc.id,
                        name=tc.function.name,
                        arguments=json.loads(tc.function.arguments),
                    )
                )

        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            model=model,
            usage={
                "input_tokens": response.usage.prompt_tokens if response.usage else 0,
                "output_tokens": response.usage.completion_tokens if response.usage else 0,
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
        """Generate a streaming response."""
        client = self._get_client()
        model = model or self.default_model
        max_tokens = max_tokens or 4096

        converted_messages = self._convert_messages(messages)

        request_params: dict[str, Any] = {
            "model": model,
            "messages": converted_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
        }

        tools = self._tools_for_openai(kwargs)
        if tools:
            request_params["tools"] = tools

        stream = await client.chat.completions.create(**request_params)

        tool_call_buffer: dict[str, dict[str, Any]] = {}

        async for chunk in stream:
            if not chunk.choices:
                continue

            delta = chunk.choices[0].delta

            if delta.content:
                yield StreamEvent(
                    event_type=StreamEventType.TEXT_DELTA,
                    content=delta.content,
                )

            if delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    if tc_delta.id:
                        tool_call_buffer[tc_delta.id] = {
                            "id": tc_delta.id,
                            "name": tc_delta.function.name if tc_delta.function else "",
                            "arguments": "",
                        }

                    if tc_delta.function and tc_delta.function.arguments:
                        if tc_delta.id in tool_call_buffer:
                            tool_call_buffer[tc_delta.id]["arguments"] += (
                                tc_delta.function.arguments
                            )

        for tc_data in tool_call_buffer.values():
            yield StreamEvent(
                event_type=StreamEventType.TOOL_CALL,
                tool_call=ToolCall(
                    id=tc_data["id"],
                    name=tc_data["name"],
                    arguments=json.loads(tc_data["arguments"]) if tc_data["arguments"] else {},
                ),
            )

        yield StreamEvent(event_type=StreamEventType.DONE)

    async def count_tokens(self, messages: list[Message]) -> int:
        """Count tokens in messages."""
        return sum(msg.token_estimate() for msg in messages)

    def supports_tools(self) -> bool:
        """Check if provider supports tool calling."""
        return True
