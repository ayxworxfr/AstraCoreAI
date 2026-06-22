"""Policy rule definitions."""

from typing import Any

from pydantic import BaseModel, Field


class RetryRule(BaseModel):
    """Retry policy rules."""

    max_retries: int = 3
    initial_delay_ms: int = 1000
    max_delay_ms: int = 30_000
    exponential_base: float = 2.0
    retry_on_status_codes: list[int] = Field(default_factory=lambda: [429, 500, 502, 503, 504])
    retry_on_exception_types: list[str] = Field(
        default_factory=lambda: [
            # httpx 网络层瞬态异常：连接前 / TTFB 阶段挂掉可安全重试
            "httpx.ConnectError",
            "httpx.ConnectTimeout",
            "httpx.ReadTimeout",
            "httpx.WriteTimeout",
            "httpx.PoolTimeout",
            # 流式中途断开：仅在非流式调用上下文中安全（不会丢已发 token）
            "httpx.RemoteProtocolError",
            "httpx.ReadError",
            # anthropic / openai SDK 的瞬态包装
            "anthropic.APIConnectionError",
            "anthropic.APITimeoutError",
            "openai.APIConnectionError",
            "openai.APITimeoutError",
        ]
    )
    """允许重试的"瞬态异常"类型全名列表（``module.ClassName``）。
    没有 ``status_code`` 属性的异常需在此白名单内才会触发重试，避免 ValueError / JSONDecodeError
    等业务错误被无脑重试。流式调用本身不走 retry 包装，所以 RemoteProtocolError 仅在非流式
    场景被重试。"""


class TimeoutRule(BaseModel):
    """Timeout policy rules (seconds)."""

    llm_timeout_s: float = Field(default=180.0, ge=0)
    """LLM 调用兜底超时（秒）。当下方 connect/read/write/pool 任一字段未单独配置时，
    作为该子超时的默认值。"""

    llm_connect_s: float | None = Field(default=10.0, ge=0)
    """LLM HTTP 连接建立超时（秒）。null = 使用 ``llm_timeout_s``。"""

    llm_read_s: float | None = Field(default=300.0, ge=0)
    """LLM 流式 chunk 间最长间隔（秒）。治 stale stream：服务端长时间不下发新 chunk
    时主动断开。null = 使用 ``llm_timeout_s``。"""

    llm_write_s: float | None = Field(default=60.0, ge=0)
    """LLM 请求体写入超时（秒）。null = 使用 ``llm_timeout_s``。"""

    llm_pool_s: float | None = Field(default=10.0, ge=0)
    """LLM 连接池等待超时（秒）。null = 使用 ``llm_timeout_s``。"""

    tool_timeout_s: float = Field(default=120.0, ge=0)  # 0 = 永不超时
    retrieval_timeout_s: float = Field(default=10.0, ge=0)

    def build_llm_httpx_timeout(self, overall_override: float | None = None) -> Any:
        """构造给 LLM SDK 使用的 ``httpx.Timeout``。

        ``overall_override`` 来自 ``LLMProfileConfig.timeout_s``，存在时替换 fallback 默认值
        （但 connect/read/write/pool 的具体配置仍优先）。
        """
        import httpx  # noqa: PLC0415

        fallback = overall_override if overall_override is not None else self.llm_timeout_s
        return httpx.Timeout(
            connect=self.llm_connect_s if self.llm_connect_s is not None else fallback,
            read=self.llm_read_s if self.llm_read_s is not None else fallback,
            write=self.llm_write_s if self.llm_write_s is not None else fallback,
            pool=self.llm_pool_s if self.llm_pool_s is not None else fallback,
        )


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

    default_max_messages: int = Field(default=10, ge=1)
    """未配置 ``user_settings.context_max_messages`` 时的历史消息数兜底。"""


class SecurityRule(BaseModel):
    """Security policy rules."""

    tool_whitelist: list[str] = Field(default_factory=list)
    enable_tool_confirmation: bool = False
    sensitive_fields: list[str] = Field(default_factory=lambda: ["password", "api_key", "token"])
    enable_content_filtering: bool = True
