"""Built-in LLM model capability registry."""

from pydantic import BaseModel


class LLMCapabilities(BaseModel):
    """Resolved LLM capability flags used by adapters, API, and UI."""

    tools: bool = True
    thinking: bool = False
    temperature: bool = True
    anthropic_blocks: bool = False
    structured_output_via_tools: bool = True


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
            temperature=True,
            anthropic_blocks=False,
        )

    if normalized_model == "claude-opus-4-7":
        return LLMCapabilities(
            tools=True,
            thinking=False,
            temperature=False,
            anthropic_blocks=False,
        )

    if normalized_model == "deepseek-v4-flash":
        uses_anthropic_protocol = (
            normalized_protocol == "anthropic" or "/anthropic" in normalized_base_url
        )
        if uses_anthropic_protocol:
            return LLMCapabilities(
                tools=True,
                thinking=True,
                temperature=True,
                anthropic_blocks=True,
            )
        return LLMCapabilities(
            tools=True,
            thinking=False,
            temperature=True,
            anthropic_blocks=False,
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
