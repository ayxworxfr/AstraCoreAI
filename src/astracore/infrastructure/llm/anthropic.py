"""Anthropic Claude adapter implementation."""

import json as _json_stdlib
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from astracore.infrastructure.llm.prompt_cache import (
    allocate_message_cache_slots,
    mark_messages_cache_breakpoints,
    mark_tools_cache_breakpoint,
)
from astracore.modules.chat.domain.message import Message, MessageRole, ToolCall
from astracore.modules.chat.domain.session_context import as_openai_session_message_content
from astracore.shared.observability.logger import get_logger
from astracore.shared.ports.llm import LLMAdapter, LLMResponse, StreamEvent, StreamEventType
from astracore.shared.utils.json_utils import repair_json

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class CachedRequestParts:
    """Anthropic request pieces after prompt-cache breakpoints are applied."""

    system: list[dict[str, Any]] | str | None
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]] | None


def _build_anthropic_attachment_blocks(
    text: str,
    attachment_refs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build Anthropic content block list for a user message that carries attachments."""
    blocks: list[dict[str, Any]] = []
    if text:
        blocks.append({"type": "text", "text": text})
    for ref in attachment_refs:
        data_b64 = ref.get("data_b64")
        if not data_b64:
            continue
        mime = ref.get("mime_type", "")
        if mime == "application/pdf":
            blocks.append(
                {
                    "type": "document",
                    "source": {"type": "base64", "media_type": "application/pdf", "data": data_b64},
                }
            )
        else:
            blocks.append(
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": mime, "data": data_b64},
                }
            )
    return blocks


class AnthropicAdapter(LLMAdapter):
    _ANTHROPIC_BLOCKS_KEY = "anthropic_content_blocks"

    """Anthropic Claude LLM adapter."""

    def __init__(
        self,
        api_key: str,
        default_model: str = "claude-sonnet-4-6",
        base_url: str | None = None,
        extra_headers: dict[str, str] | None = None,
        max_tokens: int = 8192,
        supports_temperature: bool = True,
        use_anthropic_blocks: bool = False,
        structured_output_via_tools: bool = True,
        timeout: Any = None,
    ):
        self.api_key = api_key
        self.default_model = default_model
        self._base_url = base_url
        self._extra_headers = extra_headers or {}
        self.max_tokens = max_tokens
        self.supports_temperature = supports_temperature
        self.use_anthropic_blocks = use_anthropic_blocks
        self.structured_output_via_tools = structured_output_via_tools
        # ``timeout`` 接受 ``httpx.Timeout`` / float / None；None 时由 SDK 取自身默认（600s）。
        # 用 connect/read/write/pool 分段值（见 TimeoutRule.build_llm_httpx_timeout）治
        # stale stream：服务端长时间不下发 chunk 时主动断开重连。
        self._timeout = timeout
        self._client: Any = None

    def _get_client(self) -> Any:
        """Lazy load Anthropic client."""
        if self._client is None:
            try:
                from anthropic import AsyncAnthropic

                kwargs: dict[str, Any] = {"api_key": self.api_key}
                if self._base_url:
                    kwargs["base_url"] = self._base_url
                if self._extra_headers:
                    kwargs["default_headers"] = self._extra_headers
                if self._timeout is not None:
                    kwargs["timeout"] = self._timeout
                client = AsyncAnthropic(**kwargs)
                # SDK 会从 ANTHROPIC_AUTH_TOKEN 读 bearer token，并与 X-Api-Key 同时发送，
                # 导致第三方 anthropic 兼容端点（如 DeepSeek）拿错 key 后 401。
                client.auth_token = None
                self._client = client
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

            attachment_refs = msg.metadata.get("attachment_refs")
            if msg.role == MessageRole.USER and attachment_refs:
                blocks = _build_anthropic_attachment_blocks(msg.content, attachment_refs)
                converted.append({"role": "user", "content": blocks})
            else:
                converted.append({"role": msg.role.value, "content": msg.content})

        return converted

    def _get_system_message(self, messages: list[Message]) -> str | None:
        """Extract system message."""
        for msg in messages:
            if msg.role == MessageRole.SYSTEM:
                return msg.content
        return None

    @staticmethod
    def _extract_cache_tokens(usage: Any) -> tuple[int, int]:
        """Return (cache_read, cache_creation) from Anthropic or DeepSeek-compat usage."""
        if usage is None:
            return 0, 0
        read = getattr(usage, "cache_read_input_tokens", None)
        create = getattr(usage, "cache_creation_input_tokens", None)
        if read is not None or create is not None:
            return int(read or 0), int(create or 0)
        hit = getattr(usage, "prompt_cache_hit_tokens", None)
        if hit is not None:
            return int(hit), 0
        return 0, 0

    @staticmethod
    def _build_system_param(
        system: str | None,
        enable_prompt_cache: bool,
    ) -> list[dict[str, Any]] | str | None:
        """Build the Anthropic API ``system`` parameter (static layer only).

        Session context must not live here. Message-level cache prefixes include
        every system block; a per-turn datetime block would invalidate history.
        """
        if not system:
            return None
        if enable_prompt_cache:
            return [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        return system

    def _prepare_cached_request_parts(
        self,
        *,
        messages: list[Message],
        tools: list[dict[str, Any]] | None,
        session_context: Any,
        enable_prompt_cache: bool,
    ) -> CachedRequestParts:
        """Assemble system / messages / tools with prompt-cache breakpoints.

        Breakpoint budget (max 4): last tool → static system → history blocks.
        Dynamic ``session_context`` is appended as a trailing user message
        *after* breakpoints, so datetime / RAG / tool-progress never enter
        the cached prefix. DeepSeek automatic prefix cache relies on the
        same layout even when ``enable_prompt_cache`` is False.
        """
        system = self._get_system_message(messages)
        system_param = self._build_system_param(system, enable_prompt_cache)
        converted = self._convert_messages(messages)
        prepared_tools = tools

        if enable_prompt_cache:
            prepared_tools = mark_tools_cache_breakpoint(tools)
            has_cached_system = isinstance(system_param, list) and any(
                isinstance(b, dict) and "cache_control" in b for b in system_param
            )
            slots = allocate_message_cache_slots(
                has_tools=bool(prepared_tools),
                has_cached_system=has_cached_system,
            )
            converted = mark_messages_cache_breakpoints(converted, remaining_slots=slots)

        session_content = as_openai_session_message_content(session_context)
        if session_content:
            converted = [*converted, {"role": "user", "content": session_content}]

        return CachedRequestParts(
            system=system_param,
            messages=converted,
            tools=prepared_tools,
        )

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

        When *response_format* is provided the Anthropic tool_use trick is used:
        a single synthetic tool ``__structured_output__`` is injected with the
        Pydantic model's JSON schema, and ``tool_choice`` forces the model to call
        it.  The tool's ``input`` dict is serialised and returned in
        ``LLMResponse.content`` as a JSON string.
        """
        client = self._get_client()
        model = model or self.default_model
        max_tokens = max_tokens or self.max_tokens

        enable_prompt_cache: bool = kwargs.get("enable_prompt_cache", False)
        session_context: str | None = kwargs.get("session_context")
        top_p: float | None = kwargs.get("top_p", None)
        top_k: int | None = kwargs.get("top_k", None)
        stop_sequences: list[str] = kwargs.get("stop_sequences", [])
        raw_tools: list[dict[str, Any]] | None = kwargs.get("tools")

        parts = self._prepare_cached_request_parts(
            messages=messages,
            tools=raw_tools,
            session_context=session_context,
            enable_prompt_cache=enable_prompt_cache,
        )

        request_params: dict[str, Any] = {
            "model": model,
            "messages": parts.messages,
            "max_tokens": max_tokens,
        }
        # 采样参数互斥：top_p > top_k > temperature
        if self.supports_temperature and top_p is None and top_k is None:
            request_params["temperature"] = temperature

        if parts.system is not None:
            request_params["system"] = parts.system

        if top_p is not None:
            request_params["top_p"] = top_p
        elif top_k is not None:
            request_params["top_k"] = top_k
        if stop_sequences:
            request_params["stop_sequences"] = stop_sequences

        if response_format is not None:
            schema = response_format.model_json_schema()
            if self.structured_output_via_tools:
                # Claude 原生支持强制 tool_choice，用 tool_use 技巧获取结构化输出
                request_params["tools"] = [
                    {
                        "name": "__structured_output__",
                        "description": "Output structured data as specified.",
                        "input_schema": schema,
                    }
                ]
                request_params["tool_choice"] = {"type": "tool", "name": "__structured_output__"}
            else:
                # 第三方 Anthropic 兼容模型（如 DeepSeek）不支持强制 tool_choice，
                # 改为 system prompt 注入 JSON schema 约束
                json_hint = (
                    "Respond with ONLY a valid JSON object matching this schema, no markdown fences, "
                    f"no other text:\n{_json_stdlib.dumps(schema, ensure_ascii=False)}"
                )
                existing = request_params.get("system", "")
                request_params["system"] = f"{existing}\n\n{json_hint}" if existing else json_hint
        elif parts.tools is not None:
            request_params["tools"] = parts.tools

        response = await client.messages.create(**request_params)

        content_text = ""
        tool_calls: list[ToolCall] = []

        for block in response.content:
            if block.type == "text":
                content_text += block.text
            elif block.type == "tool_use":
                if response_format is not None and block.name == "__structured_output__":
                    content_text = _json_stdlib.dumps(block.input, ensure_ascii=False)
                else:
                    tool_calls.append(
                        ToolCall(
                            id=block.id,
                            name=block.name,
                            arguments=block.input,
                        )
                    )

        cache_read, cache_creation = self._extract_cache_tokens(response.usage)
        return LLMResponse(
            content=content_text,
            tool_calls=tool_calls,
            model=model,
            usage={
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
                "cache_read_input_tokens": cache_read,
                "cache_creation_input_tokens": cache_creation,
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
        - thinking_mode (str | None): 'on' = 标准 thinking，'adaptive' = 自适应（Opus 4.7+），'off' = 禁用
        - thinking_budget (int): thinking token 预算，仅 thinking_mode='on' 时生效
        - enable_prompt_cache (bool): 开启 Anthropic prompt caching
        - top_p (float | None): 核采样概率
        - stop_sequences (list[str]): 强终止序列
        """
        import json as _json

        thinking_mode: str | None = kwargs.get("thinking_mode", None)
        thinking_budget: int = kwargs.get("thinking_budget", 8000)
        enable_prompt_cache: bool = kwargs.get("enable_prompt_cache", False)
        session_context: str | None = kwargs.get("session_context")
        top_p: float | None = kwargs.get("top_p", None)
        top_k: int | None = kwargs.get("top_k", None)
        stop_sequences: list[str] = kwargs.get("stop_sequences", [])
        raw_tools: list[dict[str, Any]] | None = kwargs.get("tools")

        client = self._get_client()
        model = model or self.default_model
        max_tokens = max_tokens or self.max_tokens

        parts = self._prepare_cached_request_parts(
            messages=messages,
            tools=raw_tools,
            session_context=session_context,
            enable_prompt_cache=enable_prompt_cache,
        )

        request_params: dict[str, Any] = {
            "model": model,
            "messages": parts.messages,
            "max_tokens": max_tokens,
        }
        # Anthropic: thinking 模式下 top_p 必须 ≥ 0.95 或不发送
        if thinking_mode in ("on", "adaptive") and top_p is not None and top_p < 0.95:
            top_p = None
        # top_k is not available during thinking mode (Anthropic restriction)
        if thinking_mode in ("on", "adaptive"):
            top_k = None

        if self.supports_temperature:
            if thinking_mode == "on":
                # Extended Thinking 要求 temperature=1，且不能与其它采样参数同时发送
                request_params["temperature"] = 1.0
                top_p = None
                top_k = None
            elif top_p is None and top_k is None:
                # 采样参数互斥：top_p > top_k > temperature
                request_params["temperature"] = temperature

        if parts.system is not None:
            request_params["system"] = parts.system

        if top_p is not None:
            request_params["top_p"] = top_p
        elif top_k is not None:
            request_params["top_k"] = top_k
        if stop_sequences:
            request_params["stop_sequences"] = stop_sequences

        if parts.tools is not None:
            request_params["tools"] = parts.tools

        if thinking_mode == "on":
            # Anthropic: max_tokens is the *total* budget (thinking + text output).
            # If thinking_budget is close to max_tokens, almost no tokens remain for
            # the visible response. Ensure at least 8192 tokens for text output.
            min_total = thinking_budget + 8192
            if request_params["max_tokens"] < min_total:
                request_params["max_tokens"] = min_total
            request_params["thinking"] = {
                "type": "enabled",
                "budget_tokens": thinking_budget,
            }
        elif thinking_mode == "adaptive":
            # Opus 4.7+: adaptive thinking manages the budget automatically; budget_tokens
            # is not accepted and triggers an API error if sent.
            request_params["thinking"] = {"type": "enabled"}

        # index → {kind, ...}
        block_buffers: dict[int, dict[str, Any]] = {}
        completed_blocks: list[tuple[int, dict[str, Any]]] = []
        _input_tokens = 0
        _output_tokens = 0
        _cache_creation_input_tokens = 0
        _cache_read_input_tokens = 0

        async with client.messages.stream(**request_params) as stream:
            async for event in stream:
                if not hasattr(event, "type"):
                    continue

                # 从流式事件中直接采集 token 用量（比 get_final_message() 更可靠）
                if event.type == "message_start":
                    msg = getattr(event, "message", None)
                    if msg:
                        usage = getattr(msg, "usage", None)
                        if usage:
                            _input_tokens = getattr(usage, "input_tokens", 0) or 0
                            _cache_read_input_tokens, _cache_creation_input_tokens = (
                                self._extract_cache_tokens(usage)
                            )
                elif event.type == "message_delta":
                    usage = getattr(event, "usage", None)
                    if usage:
                        _output_tokens = getattr(usage, "output_tokens", 0) or 0

                if event.type == "content_block_start":
                    content_block = getattr(event, "content_block", None)
                    if content_block is None:
                        continue
                    idx = getattr(event, "index", 0)
                    block_type = getattr(content_block, "type", None)
                    if block_type == "tool_use":
                        block_buffers[idx] = {
                            "kind": "tool",
                            "id": content_block.id,
                            "name": content_block.name,
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
                        raw_input = buf["input_str"]
                        parse_error: str | None = None
                        try:
                            arguments = _json.loads(raw_input) if raw_input else {}
                        except _json.JSONDecodeError as exc:
                            try:
                                arguments = repair_json(buf["name"], raw_input, exc)
                            except ValueError as repair_exc:
                                parse_error = str(repair_exc)
                                arguments = {}
                        # 始终记录 block 以保证 extended thinking 多轮 replay 的完整性
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
                        tool_call = ToolCall(
                            id=buf["id"],
                            name=buf["name"],
                            arguments=arguments,
                        )
                        if parse_error:
                            yield StreamEvent(
                                event_type=StreamEventType.TOOL_CALL_ERROR,
                                tool_call=tool_call,
                                error=parse_error,
                            )
                        else:
                            yield StreamEvent(
                                event_type=StreamEventType.TOOL_CALL,
                                tool_call=tool_call,
                            )
                    elif buf and buf.get("kind") == "thinking":
                        thinking_block: dict[str, Any] = {
                            "type": "thinking",
                            "thinking": buf.get("thinking", ""),
                        }
                        signature = buf.get("signature", "")
                        if signature:
                            thinking_block["signature"] = signature
                        completed_blocks.append((idx, thinking_block))
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

        assistant_blocks = [
            block for _, block in sorted(completed_blocks, key=lambda item: item[0])
        ]
        yield StreamEvent(
            event_type=StreamEventType.DONE,
            metadata={
                self._ANTHROPIC_BLOCKS_KEY: assistant_blocks,
                "usage": {
                    "input_tokens": _input_tokens,
                    "output_tokens": _output_tokens,
                    "cache_creation_input_tokens": _cache_creation_input_tokens,
                    "cache_read_input_tokens": _cache_read_input_tokens,
                },
            },
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
            return int(response.input_tokens)
        except Exception:
            return sum(msg.token_estimate() for msg in messages)

    def supports_tools(self) -> bool:
        """Check if this adapter supports tool calling."""
        return True
