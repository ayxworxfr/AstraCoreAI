"""Policy engine implementation."""

import asyncio
from typing import Any

from pydantic import BaseModel
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from astracore.core.domain.session import SessionState
from astracore.runtime.policy.rules import (
    BudgetRule,
    RetryRule,
    SecurityRule,
    TimeoutRule,
    TruncationRule,
)


def _make_retry_predicate(status_codes: list[int]):
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

    budget: BudgetRule = BudgetRule()
    retry: RetryRule = RetryRule()
    timeout: TimeoutRule = TimeoutRule()
    truncation: TruncationRule = TruncationRule()
    security: SecurityRule = SecurityRule()


class PolicyEngine:
    """Central policy enforcement engine."""

    def __init__(self, config: PolicyConfig | None = None):
        self.config = config or PolicyConfig()

    def apply_budget_policy(self, session: SessionState) -> SessionState:
        """Apply token budget policy to session."""
        budget = session.token_budget
        rules = self.config.budget

        budget.max_input_tokens = rules.max_input_tokens
        budget.max_output_tokens = rules.max_output_tokens
        budget.max_tool_tokens = rules.max_tool_tokens
        budget.max_memory_tokens = rules.max_memory_tokens

        if budget.is_input_budget_exceeded():
            self._apply_truncation(session)

        return session

    def _apply_truncation(self, session: SessionState) -> None:
        """Apply context truncation: keep the N most recent messages."""
        rules = self.config.truncation

        if not rules.enable_auto_truncation:
            return

        context = session.context_window
        available_tokens = session.token_budget.available_input_tokens()

        if len(context.messages) > rules.keep_recent_messages:
            context.messages = context.messages[-rules.keep_recent_messages:]
        else:
            context.truncate_to_budget(available_tokens)

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
        """
        rules = self.config.retry
        predicate = _make_retry_predicate(rules.retry_on_status_codes)

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
            return await func(*args, **kwargs)

        return await _attempt()

    async def apply_timeout_policy(
        self,
        func: Any,
        timeout_type: str,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """Wrap func in asyncio.wait_for with configured timeout."""
        timeout_map = {
            "llm": self.config.timeout.llm_timeout_ms,
            "tool": self.config.timeout.tool_timeout_ms,
            "retrieval": self.config.timeout.retrieval_timeout_ms,
        }

        timeout_ms = timeout_map.get(timeout_type, 30_000)
        timeout_sec = timeout_ms / 1000.0

        return await asyncio.wait_for(func(*args, **kwargs), timeout=timeout_sec)
