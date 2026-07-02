"""SDK configuration."""

import os
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml  # type: ignore[import-untyped]
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, model_validator

from astracore.sdk.model_capabilities import LLMCapabilities, infer_model_capabilities
from astracore.shared.policy.rules import CompactionRule, RetryRule, TimeoutRule


class LLMProfileConfig(BaseModel):
    """Configuration for one selectable LLM profile."""

    model_config = ConfigDict(extra="forbid")

    id: str
    label: str | None = None
    protocol: Literal["anthropic", "openai", "responses"]
    api_key: str = ""
    api_key_env: str | None = None
    base_url: str | None = None
    extra_headers: dict[str, str] = Field(default_factory=dict)
    model: str
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(default=8192, ge=1)
    capabilities: LLMCapabilities = Field(default_factory=LLMCapabilities)

    # ── Slice A: 采样参数 ──────────────────────────────────────────────
    top_p: float | None = Field(default=None, ge=0.0, le=1.0)
    """核采样概率截断。null = 不发送，使用 provider 默认值。与 temperature 二选一调整。"""
    stop_sequences: list[str] = Field(default_factory=list)
    """强终止序列，最多 4 条。OpenAI 和 Anthropic 均支持。"""
    enable_prompt_cache: bool = True
    """仅 Anthropic 协议生效（同时需要 capabilities.prompt_cache=True）。
    为 system prompt 注入 cache_control，显著降低输入 token 成本。"""

    # ── Slice B: 推理控制 ──────────────────────────────────────────────
    thinking_mode: str | None = None
    """profile 默认思考模式。'off'=禁用，'on'=启用（Anthropic），'adaptive'=自适应（Opus 4.7+）。
    None 表示按 capabilities 推断默认值。"""
    thinking_budget: int = Field(default=8000, ge=1000)
    """Claude Extended Thinking 的 token 预算，仅 thinking_mode='on' 时生效。"""
    reasoning_effort: str | None = None
    """GPT-5/5.5 推理深度默认值。'low'|'medium'|'high'|'xhigh'。None = 不发送（provider 默认 medium）。"""
    verbosity: str | None = None
    """GPT-5 回答长度控制。'low'|'medium'|'high'。None = 不发送（provider 默认 medium）。"""

    # ── Slice C: 运维覆盖 ──────────────────────────────────────────────
    timeout_s: float | None = Field(default=None, ge=0)
    """LLM 调用超时（秒），覆盖全局 policy.timeout.llm_timeout_s。"""
    max_retries: int | None = None
    """最大重试次数，覆盖全局 policy.retry.max_retries。"""
    service_tier: str | None = None
    """Anthropic: 'priority'|'standard'|'batch'。OpenAI: 'auto'|'default'|'flex'。"""

    @model_validator(mode="before")
    @classmethod
    def _merge_builtin_capabilities(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data

        inferred = infer_model_capabilities(
            protocol=str(data.get("protocol", "")),
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
    def _load_api_key_from_env(self) -> "LLMProfileConfig":
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
        duplicate_ids = sorted(
            {profile_id for profile_id in profile_ids if profile_ids.count(profile_id) > 1}
        )
        if duplicate_ids:
            raise ValueError(f"Duplicate LLM profile id: {', '.join(duplicate_ids)}")
        if self.default_profile not in profile_ids:
            raise ValueError(
                f"default_profile '{self.default_profile}' does not match any LLM profile"
            )
        return self

    def get_profile(self, profile_id: str | None = None) -> LLMProfileConfig:
        """Return the requested profile, or the configured default profile."""
        resolved_id = profile_id or self.default_profile
        for profile in self.profiles:
            if profile.id == resolved_id:
                return profile
        raise ValueError(f"Unknown LLM profile: {resolved_id}")


class VectorConfig(BaseModel):
    """Vector store (RAG / memory retrieval) configuration."""

    enabled: bool = True
    collection_name: str = "astracore"
    persist_directory: str | None = None
    embedding_model: str = "all-MiniLM-L6-v2"
    """Chroma ONNX embedding 模型名，当前仅支持 all-MiniLM-L6-v2。"""


class StorageConfig(BaseModel):
    """Storage layer configuration (database, cache, vector store)."""

    db_url: str = "sqlite+aiosqlite:///./astracore.db"
    redis_url: str = "redis://localhost:6379/0"
    vector: VectorConfig = Field(default_factory=VectorConfig)


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
    enable_spawn_agents: bool = True
    """Whether to expose the spawn_agents tool to the LLM.
    Set to false to disable parallel multi-agent execution entirely."""


class SkillsConfig(BaseModel):
    """Skills directory configuration."""

    extra_dirs: list[str] = []
    """Additional directories to scan for skill .md files (appended after the built-in dir)."""


class HITLConfig(BaseModel):
    """Human-in-the-loop (HITL) configuration."""

    enabled: bool = True
    """Master switch; false disables all HITL interactions."""
    inline_question_timeout: int = Field(default=300, ge=10)
    """Seconds to wait for user response to ask_user before timing out and resuming."""
    require_tool_approval: bool = True
    """When true, tools with requires_confirmation=True pause for user approval before execution."""
    require_memory_promotion_approval: bool = True
    """When true, session→user/project memory promotions are written to a pending queue
    instead of being applied immediately; user reviews them in the approvals page."""


class AuthConfig(BaseModel):
    """Authentication configuration."""

    secret_key: str = "change-me-in-production"
    token_expire_days: int = 30
    allow_registration: bool = True


class SchedulingConfig(BaseModel):
    """Scheduled-task system configuration."""

    enabled: bool = True
    """Master switch; false disables the scheduler and all related routes."""
    max_tasks_per_user: int = Field(default=50, ge=1)
    """Maximum scheduled tasks a single user may own (409 Conflict when exceeded)."""
    default_timezone: str = "Asia/Shanghai"
    """IANA timezone used when the user does not specify one."""
    misfire_grace_seconds: int = Field(default=300, ge=0)
    """APScheduler misfire_grace_time: missed trigger is still fired within this window."""
    max_concurrent_runs: int = Field(default=5, ge=1)
    """Global asyncio.Semaphore capacity for concurrent task runs."""


class PolicyConfig(BaseModel):
    """Global policy defaults. retry / timeout 字段可在 LLMProfileConfig 内按 profile 覆盖。"""

    retry: RetryRule = Field(default_factory=RetryRule)
    timeout: TimeoutRule = Field(default_factory=TimeoutRule)
    compaction: CompactionRule = Field(default_factory=CompactionRule)


class TavilySearchConfig(BaseModel):
    """Tavily search provider configuration."""

    api_key_env: str = "TAVILY_API_KEY"
    """Environment variable name that holds the Tavily API key."""


class SearXNGSearchConfig(BaseModel):
    """SearXNG search provider configuration."""

    base_url: str = "http://localhost:8080"
    """Base URL of the SearXNG instance (self-hosted or public)."""
    engines: str = ""
    """Comma-separated engine list forwarded to SearXNG (e.g. 'google,bing').
    Empty string defers to the instance's default engine selection."""


class WebSearchConfig(BaseModel):
    """Web search tool configuration."""

    provider: Literal["tavily", "searxng", "duckduckgo"] = "duckduckgo"
    """Active search provider. Must be one of: tavily, searxng, duckduckgo."""
    tavily: TavilySearchConfig = Field(default_factory=TavilySearchConfig)
    searxng: SearXNGSearchConfig = Field(default_factory=SearXNGSearchConfig)


class DebugConfig(BaseModel):
    """Developer debug configuration."""

    log_prompts: bool = False
    """Print the full LLM prompt (system prompt + message list) to stdout before each LLM call.
    Useful for inspecting Tier-1/Tier-2 memory injection and system prompt composition."""


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
    path = Path(raw_path or os.getenv("ASTRACORE_CONFIG", "config/config.yaml"))  # type: ignore[arg-type]
    if path.is_absolute():
        return path

    cwd_path = Path.cwd() / path
    if cwd_path.exists():
        return cwd_path
    return _project_root() / path


def _load_yaml_config() -> dict[str, Any]:
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
              protocol: anthropic
              api_key_env: ANTHROPIC_API_KEY
              model: claude-sonnet-4-6
    """

    llm: LLMConfig
    storage: StorageConfig = Field(default_factory=StorageConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    mcp: MCPConfig = Field(default_factory=MCPConfig)
    skills: SkillsConfig = Field(default_factory=SkillsConfig)
    hitl: HITLConfig = Field(default_factory=HITLConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    scheduling: SchedulingConfig = Field(default_factory=SchedulingConfig)
    web_search: WebSearchConfig = Field(default_factory=WebSearchConfig)
    debug: DebugConfig = Field(default_factory=DebugConfig)
    policy: PolicyConfig = Field(default_factory=PolicyConfig)
    """Global policy defaults (retry / timeout / compaction). retry / timeout 可在
    LLMProfileConfig 中通过 timeout_s / max_retries 按 profile 覆盖。"""

    def __init__(self, **data: object) -> None:
        if not data:
            data = _load_yaml_config()
        super().__init__(**data)


@lru_cache(maxsize=1)
def get_settings() -> AstraCoreConfig:
    """Return the cached application settings."""
    return AstraCoreConfig()
