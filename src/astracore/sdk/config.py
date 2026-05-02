"""SDK configuration."""

import os
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator
from dotenv import load_dotenv
import yaml

from astracore.sdk.model_capabilities import LLMCapabilities, infer_model_capabilities

_DEFAULT_MODELS: dict[str, str] = {
    "deepseek": "deepseek-v4-flash",
    "anthropic": "claude-sonnet-4-6",
}

_DEEPSEEK_DEFAULT_BASE_URL = "https://api.deepseek.com"


class LLMProfileConfig(BaseModel):
    """Configuration for one selectable LLM profile."""

    id: str
    label: str | None = None
    provider: Literal["deepseek", "anthropic"]
    api_key: str = ""
    api_key_env: str | None = None
    base_url: str | None = None
    model: str
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(default=8192, ge=1)
    capabilities: LLMCapabilities = Field(default_factory=LLMCapabilities)

    @model_validator(mode="before")
    @classmethod
    def _merge_builtin_capabilities(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data

        inferred = infer_model_capabilities(
            provider=str(data.get("provider", "")),
            model=str(data.get("model", "")),
            base_url=data.get("base_url") if isinstance(data.get("base_url"), str) else None,
        ).model_dump()
        overrides = data.get("capabilities") or {}
        if isinstance(overrides, LLMCapabilities):
            overrides = overrides.model_dump()
        if not isinstance(overrides, dict):
            overrides = {}

        merged = dict(data)
        merged["capabilities"] = {**inferred, **overrides}
        return merged

    @model_validator(mode="after")
    def _apply_provider_defaults(self) -> "LLMProfileConfig":
        if self.provider == "deepseek" and self.base_url is None:
            self.base_url = _DEEPSEEK_DEFAULT_BASE_URL
        if not self.api_key and self.api_key_env:
            self.api_key = os.getenv(self.api_key_env, "").strip()
        if not self.api_key:
            raise ValueError(f"LLM profile '{self.id}' requires api_key or api_key_env")
        return self


class LLMConfig(BaseModel):
    """LLM profile registry configuration."""

    default_profile: str
    profiles: list[LLMProfileConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_profiles(self) -> "LLMConfig":
        profile_ids = [profile.id for profile in self.profiles]
        duplicate_ids = sorted({profile_id for profile_id in profile_ids if profile_ids.count(profile_id) > 1})
        if duplicate_ids:
            raise ValueError(f"Duplicate LLM profile id: {', '.join(duplicate_ids)}")
        if self.default_profile not in profile_ids:
            raise ValueError(f"default_profile '{self.default_profile}' does not match any LLM profile")
        return self

    def get_profile(self, profile_id: str | None = None) -> LLMProfileConfig:
        """Return the requested profile, or the configured default profile."""
        resolved_id = profile_id or self.default_profile
        for profile in self.profiles:
            if profile.id == resolved_id:
                return profile
        raise ValueError(f"Unknown LLM profile: {resolved_id}")


class MemoryConfig(BaseModel):
    """Memory configuration."""

    redis_url: str = "redis://localhost:6379/0"
    db_url: str = "sqlite+aiosqlite:///./astracore.db"


class RetrievalConfig(BaseModel):
    """Retrieval configuration."""

    collection_name: str = "astracore"
    persist_directory: str | None = None


class FilesystemServerConfig(BaseModel):
    """Configuration for @modelcontextprotocol/server-filesystem."""

    type: Literal["filesystem"] = "filesystem"
    name: str = "filesystem"
    paths: list[str]


class ShellServerConfig(BaseModel):
    """Configuration for the built-in AstraCore shell MCP server."""

    type: Literal["shell"] = "shell"
    name: str = "shell"
    allow_dirs: list[str] = []
    timeout: float = 30.0


class CustomServerConfig(BaseModel):
    """Configuration for any external MCP server process."""

    type: Literal["custom"] = "custom"
    name: str
    command: str
    args: list[str] = []
    env: dict[str, str] = {}


MCPServerEntry = Annotated[
    FilesystemServerConfig | ShellServerConfig | CustomServerConfig,
    Field(discriminator="type"),
]


class AgentConfig(BaseModel):
    """Agent / tool-loop behavior configuration."""

    max_tool_result_chars: int = Field(default=20_000, ge=100)
    max_tool_iterations: int = Field(default=10, ge=0)  # 0 = 不限轮次
    tool_timeout_s: float = Field(default=120.0, ge=1.0)


class MCPConfig(BaseModel):
    """MCP server connection configuration.

    Set via environment variable (JSON-encoded list)::

        ASTRACORE__MCP__SERVERS='[
            {"type":"filesystem","paths":["D:/project"]},
            {"type":"shell","allow_dirs":["D:/project"]}
        ]'
    """

    servers: list[MCPServerEntry] = []


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _resolve_config_path(raw_path: str | None = None) -> Path:
    path = Path(raw_path or os.getenv("ASTRACORE_CONFIG", "config/config.yaml"))
    if path.is_absolute():
        return path

    cwd_path = Path.cwd() / path
    if cwd_path.exists():
        return cwd_path
    return _project_root() / path


def _load_yaml_config() -> dict:
    load_dotenv()
    config_path = _resolve_config_path()
    if not config_path.exists():
        raise FileNotFoundError(
            f"Config file not found: {config_path}. Set ASTRACORE_CONFIG or create config/config.yaml."
        )

    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError(f"Config file must contain a YAML object: {config_path}")
    return raw


class AstraCoreConfig(BaseModel):
    """AstraCore SDK configuration.

    Structured settings are read from ``config/config.yaml`` by default. Secrets should be
    stored in ``.env`` and referenced from YAML with ``api_key_env``. Example::

        llm:
          default_profile: claude-sonnet
          profiles:
            - id: claude-sonnet
              provider: anthropic
              api_key_env: ANTHROPIC_API_KEY
              model: claude-sonnet-4-6
    """

    llm: LLMConfig
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    mcp: MCPConfig = Field(default_factory=MCPConfig)

    def __init__(self, **data: object) -> None:
        if not data:
            data = _load_yaml_config()
        super().__init__(**data)


@lru_cache(maxsize=1)
def get_settings() -> AstraCoreConfig:
    """Return the cached application settings."""
    return AstraCoreConfig()
