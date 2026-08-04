"""按 profile 缓存 LLM 适配器 —— 从 pipeline 拆出。"""

from __future__ import annotations

from astracore.infrastructure.llm.anthropic import AnthropicAdapter
from astracore.infrastructure.llm.openai import OpenAIAdapter
from astracore.sdk.config import AstraCoreConfig, LLMProfileConfig
from astracore.shared.ports.llm import LLMAdapter


class LLMAdapterFactory:
    """按 profile.id 缓存适配器实例，避免重复建连。"""

    def __init__(self, config: AstraCoreConfig) -> None:
        self._config = config
        self._adapters: dict[str, LLMAdapter] = {}

    def get(self, profile: LLMProfileConfig) -> LLMAdapter:
        if profile.id not in self._adapters:
            self._adapters[profile.id] = self._create(profile)
        return self._adapters[profile.id]

    def _create(self, profile: LLMProfileConfig) -> LLMAdapter:
        timeout = self._config.policy.timeout.build_llm_httpx_timeout(
            overall_override=profile.timeout_s
        )
        if profile.protocol == "anthropic":
            return AnthropicAdapter(
                api_key=profile.api_key,
                default_model=profile.model,
                base_url=profile.base_url,
                extra_headers=profile.extra_headers,
                max_tokens=profile.max_tokens,
                supports_temperature=profile.capabilities.temperature,
                use_anthropic_blocks=profile.capabilities.anthropic_blocks,
                structured_output_via_tools=profile.capabilities.structured_output_via_tools,
                timeout=timeout,
            )
        return OpenAIAdapter(
            api_key=profile.api_key,
            default_model=profile.model,
            base_url=profile.base_url,
            extra_headers=profile.extra_headers,
            protocol=profile.protocol,
            max_tokens=profile.max_tokens,
            timeout=timeout,
        )
