"""Anthropic Claude adapter implementation."""

from collections.abc import AsyncIterator
from typing import Any

from astracore.core.domain.message import Message, MessageRole, ToolCall
from astracore.core.ports.llm import LLMAdapter, LLMResponse, StreamEvent, StreamEventType
from astracore.runtime.observability.logger import get_logger

logger = get_logger(__name__)


class AnthropicAdapter(LLMAdapter):
    _ANTHROPIC_BLOCKS_KEY = "anthropic_content_blocks"

    """Anthropic Claude LLM adapter."""

    def __init__(
        self,
        api_key: str,
        default_model: str = "claude-sonnet-4-6",
        base_url: str | None = None,
        max_tokens: int = 8192,
        supports_temperature: bool = True,
        use_anthropic_blocks: bool = False,
    ):
        self.api_key = api_key
        self.default_model = default_model
        self._base_url = base_url
        self.max_tokens = max_tokens
        self.supports_temperature = supports_temperature
        self.use_anthropic_blocks = use_anthropic_blocks
        self._client: Any = None

    def _get_client(self) -> Any:
        """Lazy load Anthropic client."""
        if self._client is None:
            try:
                from anthropic import AsyncAnthropic

                kwargs: dict[str, Any] = {"api_key": self.api_key}
                if self._base_url:
                    kwargs["base_url"] = self._base_url
                self._client = AsyncAnthropic(**kwargs)
            except ImportError as e:
                raise ImportError(
                    "anthropic package not installed. Install with: pip install anthropic"
                ) from e
        return self._client

    def _convert_messages(self, messages: list[Message]) -> list[dict[str, Any]]:
        """Convert framework messages to Anthropic format.

        Anthropic Messages API 只允许 "user" / "assistant" 两种 role。
        工具结果消息必须以 role="user" + type="tool_result" 形式发送。
        若上下文裁剪导致 tool_result 丢失对应 tool_use，则跳过无效结果，
        避免触发 Anthropic 的 ``unexpected tool_use_id`` 请求错误。
        """
        converted = []
        known_tool_use_ids: set[str] = set()
        for msg in messages:
            if msg.role == MessageRole.SYSTEM:
                continue

            # 工具结果：role 必须是 "user"，content 是 tool_result 块列表
            if msg.has_tool_results():
                valid_results = [
                    tr for tr in msg.tool_results if tr.tool_call_id in known_tool_use_ids
                ]
                skipped_count = len(msg.tool_results) - len(valid_results)
                if skipped_count > 0:
                    logger.warning(
                        "Skipped %d orphan tool_result block(s) due to missing tool_use context",
                        skipped_count,
                    )
                if not valid_results:
                    continue

                content: Any = [
                    {
                        "type": "tool_result",
                        "tool_use_id": tr.tool_call_id,
                        # Anthropic API 要求 is_error=true 时 content 不能为空
                        "content": tr.content or "Tool execution failed",
                        "is_error": tr.is_error,
                    }
                    for tr in valid_results
                ]
                converted.append({"role": "user", "content": content})
                continue

            # assistant 调用工具：content 是 text + tool_use 块列表
            anthropic_blocks = msg.metadata.get(self._ANTHROPIC_BLOCKS_KEY)
            if (
                self.use_anthropic_blocks
                and msg.role == MessageRole.ASSISTANT
                and isinstance(anthropic_blocks, list)
            ):
                replay_block_types = {"thinking", "text", "tool_use"}
                replay_blocks = [
                    block
                    for block in anthropic_blocks
                    if isinstance(block, dict) and block.get("type") in replay_block_types
                ]
                for block in replay_blocks:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        tool_id = block.get("id")
                        if isinstance(tool_id, str) and tool_id:
                            known_tool_use_ids.add(tool_id)
                if replay_blocks:
                    converted.append({"role": "assistant", "content": replay_blocks})
                elif msg.content:
                    converted.append({"role": "assistant", "content": msg.content})
                continue

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
                for tc in msg.tool_calls:
                    known_tool_use_ids.add(tc.id)
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
        max_tokens = max_tokens or self.max_tokens

        system = self._get_system_message(messages)
        converted_messages = self._convert_messages(messages)

        request_params: dict[str, Any] = {
            "model": model,
            "messages": converted_messages,
            "max_tokens": max_tokens,
        }
        if self.supports_temperature:
            request_params["temperature"] = temperature

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
        max_tokens = max_tokens or self.max_tokens

        system = self._get_system_message(messages)
        converted_messages = self._convert_messages(messages)

        request_params: dict[str, Any] = {
            "model": model,
            "messages": converted_messages,
            "max_tokens": max_tokens,
        }
        if self.supports_temperature:
            # Extended Thinking 要求 temperature=1，普通模式保留用户设置
            request_params["temperature"] = 1.0 if enable_thinking else temperature

        if system:
            request_params["system"] = system

        if "tools" in kwargs:
            request_params["tools"] = kwargs["tools"]

        if enable_thinking:
            request_params["thinking"] = {
                "type": "enabled",
                "budget_tokens": thinking_budget,
            }

        # index → {kind, ...}
        block_buffers: dict[int, dict[str, Any]] = {}
        completed_blocks: list[tuple[int, dict[str, Any]]] = []

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
                        block_buffers[idx] = {
                            "kind": "thinking",
                            "thinking": "",
                            "signature": "",
                        }
                    elif block_type == "text":
                        block_buffers[idx] = {
                            "kind": "text",
                            "text": "",
                        }

                elif event.type == "content_block_delta":
                    delta = getattr(event, "delta", None)
                    if delta is None:
                        continue
                    delta_type = getattr(delta, "type", None)
                    idx = getattr(event, "index", 0)

                    if delta_type == "text_delta":
                        if idx in block_buffers and block_buffers[idx].get("kind") == "text":
                            block_buffers[idx]["text"] += getattr(delta, "text", "")
                        yield StreamEvent(
                            event_type=StreamEventType.TEXT_DELTA,
                            content=delta.text,
                        )
                    elif delta_type == "thinking_delta":
                        if idx in block_buffers and block_buffers[idx].get("kind") == "thinking":
                            block_buffers[idx]["thinking"] += getattr(delta, "thinking", "")
                        yield StreamEvent(
                            event_type=StreamEventType.THINKING_DELTA,
                            content=getattr(delta, "thinking", ""),
                        )
                    elif delta_type == "signature_delta":
                        if idx in block_buffers and block_buffers[idx].get("kind") == "thinking":
                            block_buffers[idx]["signature"] += getattr(delta, "signature", "")
                    elif delta_type == "input_json_delta":
                        if idx in block_buffers and block_buffers[idx].get("kind") == "tool":
                            block_buffers[idx]["input_str"] += delta.partial_json

                elif event.type == "content_block_stop":
                    idx = getattr(event, "index", 0)
                    buf = block_buffers.pop(idx, None)
                    if buf and buf.get("kind") == "tool":
                        arguments = _json.loads(buf["input_str"]) if buf["input_str"] else {}
                        completed_blocks.append(
                            (
                                idx,
                                {
                                    "type": "tool_use",
                                    "id": buf["id"],
                                    "name": buf["name"],
                                    "input": arguments,
                                },
                            )
                        )
                        yield StreamEvent(
                            event_type=StreamEventType.TOOL_CALL,
                            tool_call=ToolCall(
                                id=buf["id"],
                                name=buf["name"],
                                arguments=arguments,
                            ),
                        )
                    elif buf and buf.get("kind") == "thinking":
                        block: dict[str, Any] = {
                            "type": "thinking",
                            "thinking": buf.get("thinking", ""),
                        }
                        signature = buf.get("signature", "")
                        if signature:
                            block["signature"] = signature
                        completed_blocks.append((idx, block))
                    elif buf and buf.get("kind") == "text":
                        completed_blocks.append(
                            (
                                idx,
                                {
                                    "type": "text",
                                    "text": buf.get("text", ""),
                                },
                            )
                        )

        assistant_blocks = [block for _, block in sorted(completed_blocks, key=lambda item: item[0])]
        yield StreamEvent(
            event_type=StreamEventType.DONE,
            metadata={self._ANTHROPIC_BLOCKS_KEY: assistant_blocks},
        )

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
