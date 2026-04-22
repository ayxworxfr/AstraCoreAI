"""SDK configuration."""

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEFAULT_MODELS: dict[str, str] = {
    "deepseek": "deepseek-chat",
    "anthropic": "claude-sonnet-4-6",
}

_DEEPSEEK_DEFAULT_BASE_URL = "https://api.deepseek.com"


class LLMConfig(BaseModel):
    """LLM configuration."""

    provider: Literal["deepseek", "anthropic"] = "anthropic"
    api_key: str
    base_url: str | None = None
    model: str = ""
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(default=8192, ge=1)

    @model_validator(mode="after")
    def _apply_provider_defaults(self) -> "LLMConfig":
        if not self.model:
            self.model = _DEFAULT_MODELS[self.provider]
        if self.provider == "deepseek" and self.base_url is None:
            self.base_url = _DEEPSEEK_DEFAULT_BASE_URL
        return self


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
    max_tool_iterations: int = Field(default=10, ge=1, le=50)


class MCPConfig(BaseModel):
    """MCP server connection configuration.

    Set via environment variable (JSON-encoded list)::

        ASTRACORE__MCP__SERVERS='[
            {"type":"filesystem","paths":["D:/project"]},
            {"type":"shell","allow_dirs":["D:/project"]}
        ]'
    """

    servers: list[MCPServerEntry] = []


class AstraCoreConfig(BaseSettings):
    """AstraCore SDK configuration.

    All settings are read from environment variables with the prefix ``ASTRACORE__``
    and nested fields separated by ``__``.  Example::

        ASTRACORE__LLM__PROVIDER=anthropic
        ASTRACORE__LLM__API_KEY=sk-...
        ASTRACORE__LLM__BASE_URL=https://api.example.com
        ASTRACORE__LLM__MODEL=claude-sonnet-4-6
        ASTRACORE__MEMORY__REDIS_URL=redis://localhost:6379/0
        ASTRACORE__RETRIEVAL__COLLECTION_NAME=astracore
        ASTRACORE__MCP__SERVERS='[{"type":"filesystem","paths":["D:/project"]},{"type":"shell","allow_dirs":["D:/project"]}]'
    """

    model_config = SettingsConfigDict(
        env_prefix="ASTRACORE__",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    llm: LLMConfig
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    mcp: MCPConfig = Field(default_factory=MCPConfig)


@lru_cache(maxsize=1)
def get_settings() -> AstraCoreConfig:
    """Return the cached application settings."""
    return AstraCoreConfig()
