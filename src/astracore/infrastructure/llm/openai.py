"""OpenAI 兼容 API 适配器（OpenAI、DeepSeek 等）。"""

import base64
import io
import json
from collections.abc import AsyncIterator
from typing import Any

from pydantic import BaseModel

from astracore.modules.attachments.domain import AttachmentProcessingError
from astracore.modules.chat.domain.message import Message, MessageRole, ToolCall
from astracore.modules.chat.domain.session_context import (
    as_stable_session_text,
    as_volatile_session_message_content,
)
from astracore.shared.observability.logger import get_logger
from astracore.shared.ports.llm import LLMAdapter, LLMResponse, StreamEvent, StreamEventType
from astracore.shared.utils.json_utils import repair_json

_logger = get_logger(__name__)


def _extract_pdf_text(data_b64: str, filename: str) -> str:
    """Extract plain text from a base64-encoded PDF via pypdf.

    Raises AttachmentProcessingError when the PDF is encrypted or contains no
    extractable text (e.g. scanned image-only PDFs).
    """
    try:
        from pypdf import PdfReader  # noqa: PLC0415
    except ImportError as exc:
        raise AttachmentProcessingError("pypdf not installed; cannot extract PDF text") from exc

    pdf_bytes = base64.b64decode(data_b64)
    reader = PdfReader(io.BytesIO(pdf_bytes))
    if reader.is_encrypted:
        raise AttachmentProcessingError(f"PDF '{filename}' 已加密，无法提取文本")

    pages: list[str] = []
    for page in reader.pages:
        text = page.extract_text() or ""
        pages.append(text)

    full_text = "\n".join(pages).strip()
    if not full_text:
        raise AttachmentProcessingError(f"PDF '{filename}' 无可提取文本（可能为扫描件）")
    return full_text


def _build_openai_user_content(
    text: str,
    attachment_refs: list[dict[str, Any]],
) -> str | list[dict[str, Any]]:
    """Build OpenAI content for a user message that carries attachments.

    Images → image_url blocks (base64 data URI).
    PDFs → extracted text prepended to the message text.
    Returns a plain string when no visual blocks exist, list otherwise.
    """
    pdf_segments: list[str] = []
    image_blocks: list[dict[str, Any]] = []

    for ref in attachment_refs:
        data_b64 = ref.get("data_b64")
        if not data_b64:
            continue
        mime = ref.get("mime_type", "")
        filename = ref.get("filename", "document")

        if mime == "application/pdf":
            extracted = _extract_pdf_text(data_b64, filename)
            pdf_segments.append(f"[PDF: {filename}]\n{extracted}\n---")
        else:
            image_blocks.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{data_b64}"},
                }
            )

    combined_text = "\n\n".join(pdf_segments + ([text] if text else []))

    if not image_blocks:
        return combined_text

    blocks: list[dict[str, Any]] = []
    if combined_text:
        blocks.append({"type": "text", "text": combined_text})
    blocks.extend(image_blocks)
    return blocks


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
        timeout: Any = None,
    ):
        self.api_key = api_key
        self.default_model = default_model
        self._base_url = base_url
        self._extra_headers = extra_headers or {}
        self.protocol = protocol
        self.max_tokens = max_tokens
        # ``timeout`` 接受 ``httpx.Timeout`` / float / None；None 时使用 SDK 默认（600s）。
        # 与 AnthropicAdapter 一致：connect/read/write/pool 分段值，用于截断 stale stream。
        self._timeout = timeout
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
                if self._timeout is not None:
                    kwargs["timeout"] = self._timeout
                self._client = AsyncOpenAI(**kwargs)
            except ImportError as e:
                raise ImportError(
                    "openai package not installed. Install with: pip install openai"
                ) from e
        return self._client

    @staticmethod
    def _tools_for_responses(kwargs: dict[str, Any]) -> list[dict[str, Any]] | None:
        """Convert tool defs to Responses API format (flat, not nested under 'function')."""
        raw = kwargs.get("tools")
        if not raw:
            return None
        out: list[dict[str, Any]] = []
        for t in raw:
            if t.get("type") == "function" and "function" in t:
                fn = t["function"]
                out.append(
                    {
                        "type": "function",
                        "name": fn.get("name", ""),
                        "description": fn.get("description") or "",
                        "parameters": fn.get("parameters", {}),
                    }
                )
            elif "name" in t and "input_schema" in t:
                out.append(
                    {
                        "type": "function",
                        "name": t["name"],
                        "description": t.get("description") or "",
                        "parameters": t["input_schema"],
                    }
                )
            else:
                out.append(t)
        return out

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

            attachment_refs = (
                msg.metadata.get("attachment_refs") if msg.role == MessageRole.USER else None
            )
            content: Any = (
                _build_openai_user_content(msg.content, attachment_refs)
                if attachment_refs
                else msg.content
            )
            message_dict: dict[str, Any] = {
                "role": msg.role.value,
                "content": content,
            }

            if msg.has_tool_calls():
                message_dict["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments, sort_keys=True),
                        },
                    }
                    for tc in msg.tool_calls
                ]

            converted.append(message_dict)

        return converted

    @staticmethod
    def _append_session_context(
        messages: list[dict[str, Any]],
        session_context: Any,
    ) -> list[dict[str, Any]]:
        """Place session context without breaking the cached prefix.

        Stable slice (date / active skill) goes immediately after existing
        system messages so it is part of the prefix. Volatile slice (RAG /
        memory / tool-progress) stays at the end.
        """
        stable = as_stable_session_text(session_context)
        volatile = as_volatile_session_message_content(session_context)
        if not stable and not volatile:
            return messages
        out = list(messages)
        if stable:
            insert_at = 0
            for i, msg in enumerate(out):
                if msg.get("role") == "system":
                    insert_at = i + 1
                else:
                    break
            role = "system" if insert_at > 0 else "user"
            out.insert(insert_at, {"role": role, "content": stable})
        if volatile:
            out.append({"role": "user", "content": volatile})
        return out

    def _responses_input(self, messages: list[Message]) -> tuple[str | None, list[dict[str, Any]]]:
        """转为 Responses API input，并将 system 消息提取为 instructions。"""
        instructions: list[str] = []
        input_messages: list[dict[str, Any]] = []

        for msg in messages:
            if msg.role == MessageRole.SYSTEM:
                if msg.content:
                    instructions.append(msg.content)
                continue

            # Tool results → function_call_output items
            if msg.role == MessageRole.TOOL and msg.has_tool_results():
                for tr in msg.tool_results:
                    input_messages.append(
                        {
                            "type": "function_call_output",
                            "call_id": tr.tool_call_id,
                            "output": tr.content,
                        }
                    )
                continue

            if msg.role not in {MessageRole.USER, MessageRole.ASSISTANT}:
                continue

            # Assistant tool calls → function_call items
            if msg.role == MessageRole.ASSISTANT and msg.has_tool_calls():
                if msg.content:
                    input_messages.append({"role": "assistant", "content": msg.content})
                for tc in msg.tool_calls:
                    input_messages.append(
                        {
                            "type": "function_call",
                            "call_id": tc.id,
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments, sort_keys=True),
                        }
                    )
                continue

            attachment_refs: list[dict[str, Any]] | None = (
                msg.metadata.get("attachment_refs") if msg.role == MessageRole.USER else None
            )
            if attachment_refs:
                # Responses API uses input_text / input_image content blocks.
                content_blocks: list[dict[str, Any]] = []
                if msg.content:
                    content_blocks.append({"type": "input_text", "text": msg.content})
                for ref in attachment_refs:
                    data_b64 = ref.get("data_b64")
                    if not data_b64:
                        continue
                    mime = ref.get("mime_type", "")
                    if mime == "application/pdf":
                        try:
                            pdf_text = _extract_pdf_text(data_b64, ref.get("filename", "document"))
                            content_blocks.append(
                                {
                                    "type": "input_text",
                                    "text": f"[PDF: {ref.get('filename', 'document')}]\n{pdf_text}",
                                }
                            )
                        except Exception:
                            pass
                    else:
                        content_blocks.append(
                            {
                                "type": "input_image",
                                "image_url": f"data:{mime};base64,{data_b64}",
                            }
                        )
                input_messages.append({"role": msg.role.value, "content": content_blocks})
            else:
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
    def _extract_cache_tokens(usage: Any) -> tuple[int, int]:
        """Return (cache_read_input_tokens, cache_creation_input_tokens) from a usage object.

        DeepSeek exposes prompt_cache_hit_tokens as a top-level field.
        OpenAI nests cached_tokens inside prompt_tokens_details.
        Neither provider charges for cache creation (unlike Anthropic), so cache_creation is always 0.
        """
        if usage is None:
            return 0, 0
        # DeepSeek: top-level prompt_cache_hit_tokens
        cache_hit = getattr(usage, "prompt_cache_hit_tokens", None)
        if cache_hit is not None:
            return int(cache_hit), 0
        # OpenAI: nested prompt_tokens_details.cached_tokens
        details = getattr(usage, "prompt_tokens_details", None)
        if details is not None:
            return int(getattr(details, "cached_tokens", 0) or 0), 0
        return 0, 0

    @staticmethod
    def _response_usage(response: Any) -> dict[str, int]:
        usage = getattr(response, "usage", None)
        cache_read, cache_creation = OpenAIAdapter._extract_cache_tokens(usage)
        return {
            "input_tokens": getattr(usage, "input_tokens", 0) if usage else 0,
            "output_tokens": getattr(usage, "output_tokens", 0) if usage else 0,
            "cache_read_input_tokens": cache_read,
            "cache_creation_input_tokens": cache_creation,
        }

    async def _generate_responses(
        self,
        messages: list[Message],
        model: str,
        max_tokens: int,
        reasoning_effort: str | None = None,
        verbosity: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        session_context: str | None = None,
        prompt_cache_key: str | None = None,
    ) -> LLMResponse:
        client = self._get_client()
        instructions, input_messages = self._responses_input(messages)
        # 动态上下文挂 input 末尾，instructions 只保留静态 system（前缀缓存友好）
        input_messages = self._append_session_context(input_messages, session_context)
        # Responses API (GPT-5 / o-series) does not accept temperature or top_p.
        request_params: dict[str, Any] = {
            "model": model,
            "input": input_messages,
            "max_output_tokens": max_tokens,
        }
        if instructions:
            request_params["instructions"] = instructions
        if reasoning_effort:
            request_params["reasoning"] = {"effort": reasoning_effort}
        if verbosity:
            request_params["text"] = {"format": {"verbosity": verbosity, "type": "text"}}
        if tools:
            request_params["tools"] = tools
        if prompt_cache_key:
            request_params["prompt_cache_key"] = prompt_cache_key

        response = await client.responses.create(**request_params)

        tool_calls: list[ToolCall] = []
        for item in getattr(response, "output", []) or []:
            if getattr(item, "type", "") == "function_call":
                raw_args = getattr(item, "arguments", "{}") or "{}"
                try:
                    arguments = json.loads(raw_args)
                except json.JSONDecodeError as exc:
                    try:
                        arguments = repair_json(getattr(item, "name", ""), raw_args, exc)
                    except ValueError:
                        arguments = {}
                call_id = getattr(item, "call_id", "") or getattr(item, "id", "")
                tool_calls.append(
                    ToolCall(
                        id=call_id,
                        name=getattr(item, "name", ""),
                        arguments=arguments,
                    )
                )

        return LLMResponse(
            content=self._response_text(response),
            tool_calls=tool_calls,
            model=model,
            usage=self._response_usage(response),
        )

    async def _generate_responses_stream(
        self,
        messages: list[Message],
        model: str,
        max_tokens: int,
        reasoning_effort: str | None = None,
        verbosity: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        session_context: str | None = None,
        prompt_cache_key: str | None = None,
    ) -> AsyncIterator[StreamEvent]:
        client = self._get_client()
        instructions, input_messages = self._responses_input(messages)
        # 动态上下文挂 input 末尾，instructions 只保留静态 system（前缀缓存友好）
        input_messages = self._append_session_context(input_messages, session_context)
        # Responses API (GPT-5 / o-series) does not accept temperature or top_p.
        request_params: dict[str, Any] = {
            "model": model,
            "input": input_messages,
            "max_output_tokens": max_tokens,
        }
        if instructions:
            request_params["instructions"] = instructions
        if reasoning_effort:
            request_params["reasoning"] = {"effort": reasoning_effort}
        if verbosity:
            request_params["text"] = {"format": {"verbosity": verbosity, "type": "text"}}
        if tools:
            request_params["tools"] = tools
        if prompt_cache_key:
            request_params["prompt_cache_key"] = prompt_cache_key

        usage: dict[str, int] = {}
        async with client.responses.stream(**request_params) as stream:
            async for event in stream:
                event_type = getattr(event, "type", "")
                if event_type == "response.output_text.delta":
                    delta = getattr(event, "delta", "")
                    if delta:
                        yield StreamEvent(event_type=StreamEventType.TEXT_DELTA, content=delta)

                elif event_type == "response.output_item.done":
                    item = getattr(event, "item", None)
                    if item and getattr(item, "type", "") == "function_call":
                        raw_args = getattr(item, "arguments", "{}") or "{}"
                        try:
                            arguments = json.loads(raw_args)
                        except json.JSONDecodeError as exc:
                            try:
                                arguments = repair_json(getattr(item, "name", ""), raw_args, exc)
                            except ValueError:
                                arguments = {}
                        call_id = getattr(item, "call_id", "") or getattr(item, "id", "")
                        yield StreamEvent(
                            event_type=StreamEventType.TOOL_CALL,
                            tool_call=ToolCall(
                                id=call_id,
                                name=getattr(item, "name", ""),
                                arguments=arguments,
                            ),
                        )

            try:
                final = stream.get_final_response()
                usage = self._response_usage(final)
            except Exception:
                pass

        yield StreamEvent(event_type=StreamEventType.DONE, metadata={"usage": usage})

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

        When *response_format* is provided, the Pydantic schema is injected into
        the system message and ``response_format={"type":"json_object"}`` is sent.
        ``json_object`` is supported by OpenAI and virtually all third-party proxies;
        the stricter ``json_schema`` is intentionally avoided because many providers
        return unexpected payloads when they receive unsupported parameters.

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
                reasoning_effort=kwargs.get("reasoning_effort"),
                verbosity=kwargs.get("verbosity"),
                tools=self._tools_for_responses(kwargs),
                session_context=kwargs.get("session_context"),
                prompt_cache_key=kwargs.get("prompt_cache_key"),
            )

        converted_messages = self._append_session_context(
            self._convert_messages(messages),
            kwargs.get("session_context"),
        )

        top_p: float | None = kwargs.get("top_p", None)
        stop_sequences: list[str] = kwargs.get("stop_sequences", [])

        request_params: dict[str, Any] = {
            "model": model,
            "messages": converted_messages,
            "max_tokens": max_tokens,
        }
        # temperature 和 top_p 互斥：设了 top_p 就不发 temperature（GLM 强制要求）
        if top_p is not None:
            request_params["top_p"] = top_p
        else:
            request_params["temperature"] = temperature
        if stop_sequences:
            request_params["stop"] = stop_sequences

        service_tier: str | None = kwargs.get("service_tier")
        if service_tier:
            request_params["service_tier"] = service_tier

        prompt_cache_key: str | None = kwargs.get("prompt_cache_key")
        if prompt_cache_key:
            request_params["prompt_cache_key"] = prompt_cache_key

        reasoning_effort_gen: str | None = kwargs.get("reasoning_effort")
        if reasoning_effort_gen:
            request_params["extra_body"] = {"reasoning_effort": reasoning_effort_gen}

        if response_format is not None:
            schema = response_format.model_json_schema()
            # Inject schema into the system message so the model knows the expected
            # shape. json_object is used universally: it is supported by native
            # OpenAI and virtually all third-party proxies, unlike json_schema/strict
            # which many providers do not implement.
            schema_hint = (
                "\n\n请严格以合法 JSON 格式输出，不包含任何额外文字，"
                f"结构符合以下 schema：\n{json.dumps(schema, ensure_ascii=False)}"
            )
            injected = False
            for msg in converted_messages:
                if msg.get("role") == "system":
                    msg["content"] = (msg.get("content") or "") + schema_hint
                    injected = True
                    break
            if not injected:
                converted_messages.insert(0, {"role": "system", "content": schema_hint.lstrip()})
            request_params["response_format"] = {"type": "json_object"}
        else:
            tools = self._tools_for_openai(kwargs)
            if tools:
                request_params["tools"] = tools

        response = await client.chat.completions.create(**request_params)

        # Defensive: some proxy implementations return a raw string instead of a
        # ChatCompletion object when they encounter unsupported request parameters.
        if isinstance(response, str):
            _logger.warning(
                "OpenAI proxy returned a raw string instead of ChatCompletion; "
                "treating as content. model=%s base_url=%s",
                model,
                self._base_url,
            )
            return LLMResponse(content=response, tool_calls=[], model=model)

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

        cache_read, cache_creation = self._extract_cache_tokens(
            response.usage if response.usage else None
        )
        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            model=model,
            usage={
                "input_tokens": response.usage.prompt_tokens if response.usage else 0,
                "output_tokens": response.usage.completion_tokens if response.usage else 0,
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
        """Generate a streaming response."""
        client = self._get_client()
        model = model or self.default_model
        max_tokens = max_tokens or self.max_tokens

        if self.protocol == "responses":
            async for event in self._generate_responses_stream(
                messages=messages,
                model=model,
                max_tokens=max_tokens,
                reasoning_effort=kwargs.get("reasoning_effort"),
                verbosity=kwargs.get("verbosity"),
                tools=self._tools_for_responses(kwargs),
                session_context=kwargs.get("session_context"),
                prompt_cache_key=kwargs.get("prompt_cache_key"),
            ):
                yield event
            return

        converted_messages = self._append_session_context(
            self._convert_messages(messages),
            kwargs.get("session_context"),
        )

        top_p: float | None = kwargs.get("top_p", None)
        stop_sequences: list[str] = kwargs.get("stop_sequences", [])

        request_params: dict[str, Any] = {
            "model": model,
            "messages": converted_messages,
            "max_tokens": max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        # temperature 和 top_p 互斥：设了 top_p 就不发 temperature（GLM 强制要求）
        if top_p is not None:
            request_params["top_p"] = top_p
        else:
            request_params["temperature"] = temperature
        if stop_sequences:
            request_params["stop"] = stop_sequences

        service_tier_s: str | None = kwargs.get("service_tier")
        if service_tier_s:
            request_params["service_tier"] = service_tier_s

        prompt_cache_key_s: str | None = kwargs.get("prompt_cache_key")
        if prompt_cache_key_s:
            request_params["prompt_cache_key"] = prompt_cache_key_s

        tools = self._tools_for_openai(kwargs)
        if tools:
            request_params["tools"] = tools

        # Build extra_body: GLM/DeepSeek thinking + reasoning_effort (via extra_body).
        # Both providers use {"thinking": {"type": "enabled"}} as the toggle format.
        # GLM streaming returns reasoning content as delta.reasoning_content.
        extra_body: dict[str, Any] = {}
        thinking_mode: str | None = kwargs.get("thinking_mode")
        _model_lower = (model or "").lower()
        if (
            thinking_mode
            and thinking_mode != "off"
            and ("glm" in _model_lower or "deepseek" in _model_lower)
        ):
            extra_body["thinking"] = {"type": "enabled"}
        reasoning_effort_param: str | None = kwargs.get("reasoning_effort")
        if reasoning_effort_param:
            extra_body["reasoning_effort"] = reasoning_effort_param
        if extra_body:
            request_params["extra_body"] = extra_body

        stream = await client.chat.completions.create(**request_params)

        tool_call_buffer: dict[str, dict[str, Any]] = {}
        tool_call_index_map: dict[int, str] = {}
        _input_tokens = 0
        _output_tokens = 0
        _cache_read_input_tokens = 0
        _cache_creation_input_tokens = 0

        async for chunk in stream:
            if not chunk.choices:
                # stream_options.include_usage delivers usage on a choices=[] chunk at end
                if chunk.usage:
                    _input_tokens = getattr(chunk.usage, "prompt_tokens", 0) or 0
                    _output_tokens = getattr(chunk.usage, "completion_tokens", 0) or 0
                    _cache_read_input_tokens, _cache_creation_input_tokens = (
                        self._extract_cache_tokens(chunk.usage)
                    )
                continue

            delta = chunk.choices[0].delta

            # GLM thinking: reasoning_content arrives before the final answer content.
            reasoning = getattr(delta, "reasoning_content", None)
            if reasoning:
                yield StreamEvent(event_type=StreamEventType.THINKING_DELTA, content=reasoning)

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

        yield StreamEvent(
            event_type=StreamEventType.DONE,
            metadata={
                "usage": {
                    "input_tokens": _input_tokens,
                    "output_tokens": _output_tokens,
                    "cache_read_input_tokens": _cache_read_input_tokens,
                    "cache_creation_input_tokens": _cache_creation_input_tokens,
                }
            },
        )

    async def count_tokens(self, messages: list[Message]) -> int:
        """Count tokens in messages."""
        return sum(msg.token_estimate() for msg in messages)

    def supports_tools(self) -> bool:
        """Check if this adapter supports tool calling."""
        return True
