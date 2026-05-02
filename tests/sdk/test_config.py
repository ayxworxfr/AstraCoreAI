"""Tests for SDK configuration."""

import pytest
from pydantic import ValidationError

from astracore.sdk.config import AstraCoreConfig, LLMConfig, LLMProfileConfig


def test_llm_config_resolves_default_profile() -> None:
    profile = LLMProfileConfig(
        id="claude-sonnet",
        label="Claude Sonnet",
        provider="anthropic",
        api_key="test-key",
        base_url="https://proxy.example.com/aws",
        model="claude-sonnet-4-6",
    )

    cfg = LLMConfig(default_profile="claude-sonnet", profiles=[profile])

    assert cfg.get_profile().id == "claude-sonnet"
    assert cfg.get_profile("claude-sonnet").model == "claude-sonnet-4-6"


def test_llm_config_rejects_missing_default_profile() -> None:
    profile = LLMProfileConfig(
        id="deepseek-v4-flash",
        provider="deepseek",
        api_key="test-key",
        model="deepseek-v4-flash",
    )

    with pytest.raises(ValidationError, match="default_profile"):
        LLMConfig(default_profile="missing", profiles=[profile])


def test_llm_config_rejects_duplicate_profile_ids() -> None:
    profile = LLMProfileConfig(
        id="duplicate",
        provider="anthropic",
        api_key="test-key",
        model="claude-sonnet-4-6",
    )

    with pytest.raises(ValidationError, match="Duplicate LLM profile id"):
        LLMConfig(default_profile="duplicate", profiles=[profile, profile])


def test_deepseek_profile_applies_default_base_url() -> None:
    profile = LLMProfileConfig(
        id="deepseek-v4-flash",
        provider="deepseek",
        api_key="test-key",
        model="deepseek-v4-flash",
    )

    assert profile.base_url == "https://api.deepseek.com"


def test_llm_profile_infers_claude_opus_capabilities() -> None:
    profile = LLMProfileConfig(
        id="claude-opus",
        provider="anthropic",
        api_key="test-key",
        model="claude-opus-4-7",
    )

    assert profile.capabilities.tools is True
    assert profile.capabilities.thinking is False
    assert profile.capabilities.temperature is False
    assert profile.capabilities.anthropic_blocks is False


def test_llm_profile_infers_deepseek_anthropic_capabilities() -> None:
    profile = LLMProfileConfig(
        id="deepseek-v4-flash",
        provider="anthropic",
        base_url="https://api.deepseek.com/anthropic",
        api_key="test-key",
        model="deepseek-v4-flash",
    )

    assert profile.capabilities.tools is True
    assert profile.capabilities.thinking is True
    assert profile.capabilities.temperature is True
    assert profile.capabilities.anthropic_blocks is True


def test_llm_profile_allows_yaml_capability_override() -> None:
    profile = LLMProfileConfig(
        id="custom",
        provider="anthropic",
        api_key="test-key",
        model="unknown-model",
        capabilities={"thinking": True},
    )

    assert profile.capabilities.tools is True
    assert profile.capabilities.thinking is True
    assert profile.capabilities.temperature is True
    assert profile.capabilities.anthropic_blocks is False


def test_astracore_config_loads_yaml_profiles_and_mcp(tmp_path, monkeypatch) -> None:
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
llm:
  default_profile: claude-sonnet
  profiles:
    - id: claude-sonnet
      label: Claude Sonnet
      provider: anthropic
      base_url: https://proxy.example.com/aws
      api_key_env: ANTHROPIC_PROXY_API_KEY
      model: claude-sonnet-4-6
memory:
  redis_url: redis://localhost:6379/0
  db_url: sqlite+aiosqlite:///./astracore.db
retrieval:
  collection_name: astracore
  persist_directory: ./chroma_db
mcp:
  servers:
    - type: filesystem
      paths:
        - D:/project
    - type: shell
      allow_dirs:
        - D:/project
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("ASTRACORE_CONFIG", str(config_file))
    monkeypatch.setenv("ANTHROPIC_PROXY_API_KEY", "test-key")

    cfg = AstraCoreConfig()

    profile = cfg.llm.get_profile()
    assert profile.id == "claude-sonnet"
    assert profile.api_key == "test-key"
    assert profile.capabilities.thinking is True
    assert len(cfg.mcp.servers) == 2
    assert cfg.mcp.servers[0].name == "filesystem"
