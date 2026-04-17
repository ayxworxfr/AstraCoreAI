"""SDK configuration."""

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMConfig(BaseModel):
    """LLM configuration."""

    provider: str = "anthropic"
    api_key: str
    default_model: str = "claude-3-5-sonnet-20241022"
    temperature: float = 0.7


class MemoryConfig(BaseModel):
    """Memory configuration."""

    redis_url: str = "redis://localhost:6379/0"
    postgres_url: str = "postgresql+asyncpg://localhost/astracore"


class RetrievalConfig(BaseModel):
    """Retrieval configuration."""

    collection_name: str = "astracore"
    persist_directory: str | None = None


class AstraCoreConfig(BaseSettings):
    """AstraCore SDK configuration."""

    model_config = SettingsConfigDict(
        env_prefix="ASTRACORE_",
        env_nested_delimiter="__",
    )

    llm: LLMConfig
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig)
