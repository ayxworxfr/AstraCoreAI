"""Built-in LLM model capability registry."""

from pydantic import BaseModel


class LLMCapabilities(BaseModel):
    """Resolved LLM capability flags used by adapters, API, and UI."""

    tools: bool = True
    thinking: bool = False
    temperature: bool = True
    anthropic_blocks: bool = False


_DEFAULT_CAPABILITIES = LLMCapabilities()


def infer_model_capabilities(
    *,
    provider: str,
    model: str,
    base_url: str | None = None,
) -> LLMCapabilities:
    """Infer capabilities from provider/model/endpoint conventions."""
    normalized_provider = provider.lower()
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
            normalized_provider == "anthropic" or "/anthropic" in normalized_base_url
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

    return _DEFAULT_CAPABILITIES.model_copy()
