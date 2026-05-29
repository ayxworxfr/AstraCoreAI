"""OpenAI 兼容 API 适配器（OpenAI、DeepSeek 等）。"""

import json
from collections.abc import AsyncIterator
from typing import Any

from pydantic import BaseModel

from astracore.modules.chat.domain.message import Message, MessageRole, ToolCall
from astracore.shared.observability.logger import get_logger
from astracore.shared.ports.llm import LLMAdapter, LLMResponse, StreamEvent, StreamEventType
from astracore.shared.utils.json_utils import repair_json

_logger = get_logger(__name__)


class OpenAIAdapter(LLMAdapter):
    """OpenAI 兼容协议适配器，支持 Chat Completions 与 Responses API。"""

    def __init__(
        self,
        api_key: str,
        default_model: str = "gpt-4o",
        base_url: str | None = None,
        extra_headers: dict[str, str] | None = None,
        protocol: str = "openai",
        max_tokens: int = 8192,
    ):
        self.api_key = api_key
        self.default_model = default_model
        self._base_url = base_url
        self._extra_headers = extra_headers or {}
        self.protocol = protocol
        self.max_tokens = max_tokens
        self._client: Any = None

    def _get_client(self) -> Any:
        """懒加载 OpenAI 兼容客户端。"""
        if self._client is None:
            try:
                from openai import AsyncOpenAI

                kwargs: dict[str, Any] = {"api_key": self.api_key}
                if self._base_url:
                    kwargs["base_url"] = self._base_url
                if self._extra_headers:
                    kwargs["default_headers"] = self._extra_headers
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

    def _responses_input(self, messages: list[Message]) -> tuple[str | None, list[dict[str, str]]]:
        """转为 Responses API input，并将 system 消息提取为 instructions。"""
        instructions: list[str] = []
        input_messages: list[dict[str, str]] = []

        for msg in messages:
            if msg.role == MessageRole.SYSTEM:
                if msg.content:
                    instructions.append(msg.content)
                continue
            if msg.role in {MessageRole.USER, MessageRole.ASSISTANT}:
                input_messages.append({"role": msg.role.value, "content": msg.content})

        return "\n\n".join(instructions) if instructions else None, input_messages

    @staticmethod
    def _response_text(response: Any) -> str:
        output_text = getattr(response, "output_text", None)
        if isinstance(output_text, str):
            return output_text

        parts: list[str] = []
        for item in getattr(response, "output", []) or []:
            for content in getattr(item, "content", []) or []:
                text = getattr(content, "text", None)
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)

    @staticmethod
    def _response_usage(response: Any) -> dict[str, int]:
        usage = getattr(response, "usage", None)
        return {
            "input_tokens": getattr(usage, "input_tokens", 0) if usage else 0,
            "output_tokens": getattr(usage, "output_tokens", 0) if usage else 0,
        }

    async def _generate_responses(
        self,
        messages: list[Message],
        model: str,
        max_tokens: int,
        temperature: float,
    ) -> LLMResponse:
        client = self._get_client()
        instructions, input_messages = self._responses_input(messages)
        request_params: dict[str, Any] = {
            "model": model,
            "input": input_messages,
            "max_output_tokens": max_tokens,
            "temperature": temperature,
        }
        if instructions:
            request_params["instructions"] = instructions

        response = await client.responses.create(**request_params)
        return LLMResponse(
            content=self._response_text(response),
            tool_calls=[],
            model=model,
            usage=self._response_usage(response),
        )

    async def _generate_responses_stream(
        self,
        messages: list[Message],
        model: str,
        max_tokens: int,
        temperature: float,
    ) -> AsyncIterator[StreamEvent]:
        client = self._get_client()
        instructions, input_messages = self._responses_input(messages)
        request_params: dict[str, Any] = {
            "model": model,
            "input": input_messages,
            "max_output_tokens": max_tokens,
            "temperature": temperature,
        }
        if instructions:
            request_params["instructions"] = instructions

        async with client.responses.stream(**request_params) as stream:
            async for event in stream:
                if getattr(event, "type", "") == "response.output_text.delta":
                    delta = getattr(event, "delta", "")
                    if delta:
                        yield StreamEvent(
                            event_type=StreamEventType.TEXT_DELTA,
                            content=delta,
                        )

        yield StreamEvent(event_type=StreamEventType.DONE)

    async def generate(
        self,
        messages: list[Message],
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.7,
        response_format: type[BaseModel] | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """Generate a complete response.

        When *response_format* is provided and the protocol is ``openai`` / ``deepseek``
        (i.e. Chat Completions), ``response_format`` is set to ``json_schema`` so the
        model is constrained to output valid JSON matching the Pydantic schema.
        The JSON string is returned in ``LLMResponse.content``.

        The Responses API (``protocol == "responses"``) does not support structured
        output; *response_format* is ignored for that protocol.
        """
        client = self._get_client()
        model = model or self.default_model
        max_tokens = max_tokens or self.max_tokens

        if self.protocol == "responses":
            return await self._generate_responses(
                messages=messages,
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
            )

        converted_messages = self._convert_messages(messages)

        request_params: dict[str, Any] = {
            "model": model,
            "messages": converted_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        if response_format is not None:
            schema = response_format.model_json_schema()
            request_params["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": response_format.__name__,
                    "schema": schema,
                    "strict": True,
                },
            }
        else:
            tools = self._tools_for_openai(kwargs)
            if tools:
                request_params["tools"] = tools

        response = await client.chat.completions.create(**request_params)

        choice = response.choices[0]
        content = choice.message.content or ""
        tool_calls: list[ToolCall] = []

        if response_format is None and choice.message.tool_calls:
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
        max_tokens = max_tokens or self.max_tokens

        if self.protocol == "responses":
            async for event in self._generate_responses_stream(
                messages=messages,
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
            ):
                yield event
            return

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
        tool_call_index_map: dict[int, str] = {}

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
                    index = getattr(tc_delta, "index", None)
                    buffer_key: str | None = None

                    if tc_delta.id:
                        buffer_key = tc_delta.id
                        if (
                            isinstance(index, int)
                            and index in tool_call_index_map
                            and tool_call_index_map[index] != buffer_key
                        ):
                            buffered = tool_call_buffer.pop(
                                tool_call_index_map[index],
                                {"id": tc_delta.id, "name": "", "arguments": ""},
                            )
                            buffered["id"] = tc_delta.id
                            tool_call_buffer[buffer_key] = buffered
                        else:
                            tool_call_buffer.setdefault(
                                buffer_key,
                                {"id": tc_delta.id, "name": "", "arguments": ""},
                            )
                        if isinstance(index, int):
                            tool_call_index_map[index] = buffer_key
                    elif isinstance(index, int):
                        buffer_key = tool_call_index_map.get(index)
                        if buffer_key is None:
                            buffer_key = f"index:{index}"
                            tool_call_index_map[index] = buffer_key
                            tool_call_buffer[buffer_key] = {
                                "id": "",
                                "name": "",
                                "arguments": "",
                            }

                    if buffer_key is None:
                        continue

                    if tc_delta.function and tc_delta.function.name:
                        tool_call_buffer[buffer_key]["name"] = tc_delta.function.name
                    if tc_delta.function and tc_delta.function.arguments:
                        tool_call_buffer[buffer_key]["arguments"] += tc_delta.function.arguments

        for tc_data in tool_call_buffer.values():
            raw_args = tc_data["arguments"]
            parse_error: str | None = None
            try:
                arguments = json.loads(raw_args) if raw_args else {}
            except json.JSONDecodeError as exc:
                try:
                    arguments = repair_json(tc_data["name"], raw_args, exc)
                except ValueError as repair_exc:
                    parse_error = str(repair_exc)
                    arguments = {}
            tool_call = ToolCall(
                id=tc_data["id"],
                name=tc_data["name"],
                arguments=arguments,
            )
            if parse_error:
                yield StreamEvent(
                    event_type=StreamEventType.TOOL_CALL_ERROR,
                    tool_call=tool_call,
                    error=parse_error,
                )
            else:
                yield StreamEvent(event_type=StreamEventType.TOOL_CALL, tool_call=tool_call)

        yield StreamEvent(event_type=StreamEventType.DONE)

    async def count_tokens(self, messages: list[Message]) -> int:
        """Count tokens in messages."""
        return sum(msg.token_estimate() for msg in messages)

    def supports_tools(self) -> bool:
        """Check if this adapter supports tool calling."""
        return True
