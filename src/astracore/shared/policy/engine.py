"""Policy engine implementation."""

from typing import Any

from pydantic import BaseModel
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from astracore.shared.policy.circuit_breaker import CircuitBreaker
from astracore.shared.policy.rules import (
    CompactionRule,
    RetryRule,
    SecurityRule,
    TimeoutRule,
)


def _exception_full_name(exc: BaseException) -> str:
    """Return ``module.ClassName`` for an exception. ``httpx._exceptions.ConnectError``
    is normalised to ``httpx.ConnectError`` so config 字符串无需关心实现细节。"""
    cls = exc.__class__
    module = cls.__module__
    # 把内部子模块（_exceptions、_client 之类的下划线模块）裁掉，匹配公开 API 名
    if module.startswith("httpx."):
        module = "httpx"
    elif module.startswith("anthropic."):
        module = "anthropic"
    elif module.startswith("openai."):
        module = "openai"
    return f"{module}.{cls.__name__}"


def _matches_exception_name(exc: BaseException, allow_names: list[str]) -> bool:
    """True 当 exc 完整类名或任一基类完整类名命中白名单。"""
    if not allow_names:
        return False
    allow_set = set(allow_names)
    for cls in type(exc).__mro__:
        module = cls.__module__
        if module.startswith("httpx."):
            module = "httpx"
        elif module.startswith("anthropic."):
            module = "anthropic"
        elif module.startswith("openai."):
            module = "openai"
        if f"{module}.{cls.__name__}" in allow_set:
            return True
    return False


def _make_retry_predicate(status_codes: list[int], exception_types: list[str]) -> Any:
    """Return a tenacity retry predicate.

    判定规则：
    - 异常带 ``status_code`` 属性：仅当 code ∈ ``status_codes`` 才重试
    - 异常无 ``status_code``：仅当类名（含基类）∈ ``exception_types`` 才重试

    这样可避免 ValueError / JSONDecodeError / KeyError 等业务错误被无脑重试。
    流式调用的中途断开（``httpx.RemoteProtocolError``）虽在白名单内，但流式入口本身不走
    ``apply_retry_policy``，所以重试仅发生在非流式 ``generate`` 上下文——不会丢已下发 token。
    """

    def should_retry(exc: BaseException) -> bool:
        code = getattr(exc, "status_code", None)
        if code is not None:
            return code in status_codes
        if not isinstance(exc, Exception):
            return False
        return _matches_exception_name(exc, exception_types)

    return should_retry


class PolicyConfig(BaseModel):
    """Policy configuration."""

    retry: RetryRule = RetryRule()
    timeout: TimeoutRule = TimeoutRule()
    compaction: CompactionRule = CompactionRule()
    security: SecurityRule = SecurityRule()


class PolicyEngine:
    """Central policy enforcement engine."""

    def __init__(
        self,
        config: PolicyConfig | None = None,
        circuit_breaker: CircuitBreaker | None = None,
    ):
        self.config = config or PolicyConfig()
        self.circuit_breaker = circuit_breaker

    def check_security_policy(self, tool_name: str, arguments: dict[str, Any]) -> bool:
        """Check if tool execution is allowed."""
        rules = self.config.security

        if rules.tool_whitelist and tool_name not in rules.tool_whitelist:
            return False

        if rules.sensitive_fields:
            for field in rules.sensitive_fields:
                if field in arguments:
                    return False

        return True

    async def apply_retry_policy(
        self,
        func: Any,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """Retry func with exponential back-off using tenacity.

        Respects retry_on_status_codes: only retries on matching HTTP status codes
        or generic exceptions without a status_code attribute.

        Circuit breaker (if configured) is checked before each attempt.
        Success records a hit; any exception records a failure.
        """
        rules = self.config.retry
        predicate = _make_retry_predicate(
            rules.retry_on_status_codes, rules.retry_on_exception_types
        )
        cb = self.circuit_breaker

        @retry(
            stop=stop_after_attempt(rules.max_retries),
            wait=wait_exponential(
                multiplier=rules.initial_delay_ms / 1000.0,
                max=rules.max_delay_ms / 1000.0,
                exp_base=rules.exponential_base,
            ),
            retry=retry_if_exception(predicate),
            reraise=True,
        )
        async def _attempt() -> Any:
            if cb is not None:
                cb.check()
            try:
                result = await func(*args, **kwargs)
                if cb is not None:
                    cb.record_success()
                return result
            except Exception:
                if cb is not None:
                    cb.record_failure()
                raise

        return await _attempt()
