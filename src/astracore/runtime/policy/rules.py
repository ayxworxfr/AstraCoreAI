"""Policy rule definitions."""

from pydantic import BaseModel, Field


class BudgetRule(BaseModel):
    """Token budget allocation rules."""

    max_input_tokens: int = 100_000
    max_output_tokens: int = 4_096
    max_tool_tokens: int = 10_000
    max_memory_tokens: int = 5_000
    truncation_threshold: float = 0.8


class RetryRule(BaseModel):
    """Retry policy rules."""

    max_retries: int = 3
    initial_delay_ms: int = 1000
    max_delay_ms: int = 30_000
    exponential_base: float = 2.0
    retry_on_status_codes: list[int] = Field(default_factory=lambda: [429, 500, 502, 503, 504])


class TimeoutRule(BaseModel):
    """Timeout policy rules."""

    llm_timeout_ms: int = 60_000
    tool_timeout_ms: int = 30_000
    retrieval_timeout_ms: int = 10_000


class TruncationRule(BaseModel):
    """Context truncation rules."""

    enable_auto_truncation: bool = True
    keep_recent_messages: int = 20
    summarize_older: bool = True
    summary_max_tokens: int = 2000


class SecurityRule(BaseModel):
    """Security policy rules."""

    tool_whitelist: list[str] = Field(default_factory=list)
    enable_tool_confirmation: bool = False
    sensitive_fields: list[str] = Field(default_factory=lambda: ["password", "api_key", "token"])
    enable_content_filtering: bool = True
