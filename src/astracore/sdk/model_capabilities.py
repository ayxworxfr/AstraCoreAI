"""Built-in LLM model capability registry."""

from typing import Literal

from pydantic import BaseModel


class LLMCapabilities(BaseModel):
    """Resolved LLM capability flags used by adapters, API, and UI."""

    tools: bool = True
    thinking: bool = False
    adaptive_thinking_only: bool = False
    """Opus 4.7+ only supports adaptive thinking; budget_tokens is not accepted."""
    temperature: bool = True
    anthropic_blocks: bool = False
    structured_output_via_tools: bool = True
    prompt_cache: bool = False
    """Anthropic prompt caching via cache_control blocks."""
    reasoning_effort_protocol: Literal["responses", "extra_body"] | None = None
    """How reasoning_effort is sent to the provider.
    'responses' = OpenAI Responses API (GPT-5), sends reasoning.effort.
    'extra_body' = Chat Completions extra_body (DeepSeek, GLM-5.2+).
    None = not supported.
    """
    top_k: bool = False
    """Supports top_k sampling parameter (Anthropic native models only)."""
    vision: bool = False
    """Supports image attachments (image_url or Anthropic image blocks)."""
    documents: bool = False
    """Supports native PDF document blocks (Anthropic only)."""


_DEFAULT_CAPABILITIES = LLMCapabilities()


def infer_model_capabilities(
    *,
    protocol: str,
    model: str,
    base_url: str | None = None,
) -> LLMCapabilities:
    """Infer capabilities from API protocol/model/endpoint conventions."""
    normalized_protocol = protocol.lower()
    normalized_model = model.lower()
    normalized_base_url = (base_url or "").lower()

    if normalized_model == "claude-sonnet-4-6":
        return LLMCapabilities(
            tools=True,
            thinking=True,
            adaptive_thinking_only=False,
            temperature=True,
            top_k=True,  # thinking 关闭时可用；anthropic.py 在 thinking 开启时自动忽略
            anthropic_blocks=False,
            structured_output_via_tools=False,  # thinking 模式不支持 tool_choice
            prompt_cache=True,
            vision=True,
            documents=True,
        )

    if normalized_model == "claude-opus-4-6":
        return LLMCapabilities(
            tools=True,
            thinking=True,
            adaptive_thinking_only=False,
            temperature=True,
            top_k=True,
            anthropic_blocks=False,
            structured_output_via_tools=False,
            prompt_cache=True,
            vision=True,
            documents=True,
        )

    if normalized_model == "claude-opus-4-7":
        # 始终处于 adaptive thinking 模式，top_k 在 thinking 下不可用，故不暴露。
        return LLMCapabilities(
            tools=True,
            thinking=True,
            adaptive_thinking_only=True,  # Opus 4.7+ 只支持 adaptive，不接受 budget_tokens
            temperature=False,
            top_k=False,
            anthropic_blocks=False,
            prompt_cache=True,
            vision=True,
            documents=True,
        )

    if normalized_model in ("gpt-5", "gpt-5.5") or (
        "gpt-5" in normalized_model and normalized_protocol in ("openai", "responses")
    ):
        return LLMCapabilities(
            tools=True,
            thinking=False,
            temperature=False,
            anthropic_blocks=False,
            reasoning_effort_protocol="responses",
            vision=True,
            documents=False,
        )

    if normalized_model.startswith("glm-5.2"):
        return LLMCapabilities(
            tools=True,
            thinking=True,
            temperature=True,
            anthropic_blocks=False,
            structured_output_via_tools=False,  # GLM thinking 模式与 tool_choice 不兼容
            reasoning_effort_protocol="extra_body",
            vision=False,
        )

    if normalized_model in ("glm-5.1", "glm-5", "glm-5-plus") or normalized_model.startswith(
        "glm-5"
    ):
        return LLMCapabilities(
            tools=True,
            thinking=True,
            temperature=True,
            anthropic_blocks=False,
            structured_output_via_tools=False,  # GLM thinking 模式与 tool_choice 不兼容
            vision=False,
        )

    if normalized_model.startswith("deepseek-v4"):
        uses_anthropic_protocol = (
            normalized_protocol == "anthropic" or "/anthropic" in normalized_base_url
        )
        if uses_anthropic_protocol:
            return LLMCapabilities(
                tools=True,
                thinking=True,
                temperature=True,
                anthropic_blocks=True,
                structured_output_via_tools=False,  # thinking 模式不支持 tool_choice
            )
        # OpenAI 协议：DeepSeek-V4 系列（flash/pro/…）支持 thinking.type=enabled（via extra_body）。
        # reasoning_effort（high/max）需配合 thinking 开启才生效。
        # 无自适应模式（adaptive）。
        return LLMCapabilities(
            tools=True,
            thinking=True,
            temperature=True,
            anthropic_blocks=False,
            structured_output_via_tools=False,  # thinking 模式不支持 tool_choice
            reasoning_effort_protocol="extra_body",
        )

    # 第三方 Anthropic 兼容端点（如 DeepSeek via api.deepseek.com/anthropic）
    # 不支持强制 tool_choice，结构化输出必须走 system prompt JSON 注入
    if normalized_protocol == "anthropic" and "/anthropic" in normalized_base_url:
        return LLMCapabilities(
            tools=True,
            thinking=False,
            temperature=True,
            anthropic_blocks=False,
            structured_output_via_tools=False,
        )

    return _DEFAULT_CAPABILITIES.model_copy()
