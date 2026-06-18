"""Policy rule definitions."""

from pydantic import BaseModel, Field


class RetryRule(BaseModel):
    """Retry policy rules."""

    max_retries: int = 3
    initial_delay_ms: int = 1000
    max_delay_ms: int = 30_000
    exponential_base: float = 2.0
    retry_on_status_codes: list[int] = Field(default_factory=lambda: [429, 500, 502, 503, 504])


class TimeoutRule(BaseModel):
    """Timeout policy rules (seconds)."""

    llm_timeout_s: float = Field(default=180.0, ge=0)
    tool_timeout_s: float = Field(default=120.0, ge=0)  # 0 = 永不超时
    retrieval_timeout_s: float = Field(default=10.0, ge=0)


class CompactionRule(BaseModel):
    """History compaction policy rules.

    控制 :class:`HistoryCompactor` 的 token 估算与触发阈值，避免将参数硬编码进实现。
    """

    context_window_tokens: int = Field(default=200_000, ge=1)
    """估算的上下文窗口 token 上限，作为触发与目标尺寸的基准。"""

    trigger_ratio: float = Field(default=0.5, ge=0.0, le=1.0)
    """超过 ``context_window_tokens * trigger_ratio`` 时触发摘要压缩。"""

    compact_batch_ratio: float = Field(default=0.6, ge=0.0, le=1.0)
    """单次压缩最旧的 N% 消息。"""

    chars_per_token: float = Field(default=0.6, gt=0.0)
    """字符数到 token 的近似换算系数（中文混合场景默认 0.6）。"""

    default_max_messages: int = Field(default=20, ge=1)
    """未配置 ``user_settings.context_max_messages`` 时的历史消息数兜底。"""


class SecurityRule(BaseModel):
    """Security policy rules."""

    tool_whitelist: list[str] = Field(default_factory=list)
    enable_tool_confirmation: bool = False
    sensitive_fields: list[str] = Field(default_factory=lambda: ["password", "api_key", "token"])
    enable_content_filtering: bool = True
