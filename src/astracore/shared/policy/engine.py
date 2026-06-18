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


def _make_retry_predicate(status_codes: list[int]) -> Any:
    """Return a tenacity retry predicate.

    Retries on all exceptions unless the exception has a `status_code` attribute
    that is NOT in the configured status_codes list.
    """

    def should_retry(exc: BaseException) -> bool:
        code = getattr(exc, "status_code", None)
        if code is not None and code not in status_codes:
            return False
        return isinstance(exc, Exception)

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
        predicate = _make_retry_predicate(rules.retry_on_status_codes)
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
