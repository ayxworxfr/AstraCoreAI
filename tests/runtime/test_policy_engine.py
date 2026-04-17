"""Tests for PolicyEngine — retry (tenacity), timeout, security, budget policies."""
import asyncio

import pytest

from astracore.runtime.policy.engine import PolicyConfig, PolicyEngine, _make_retry_predicate
from astracore.runtime.policy.rules import RetryRule, SecurityRule, TimeoutRule


# ---------- _make_retry_predicate ----------

def test_retry_predicate_retries_generic_exception():
    pred = _make_retry_predicate([429, 500])
    assert pred(ValueError("boom")) is True


def test_retry_predicate_skips_non_listed_status_code():
    pred = _make_retry_predicate([429, 500])
    exc = ValueError("client error")
    exc.status_code = 400  # type: ignore[attr-defined]
    assert pred(exc) is False


def test_retry_predicate_retries_listed_status_code():
    pred = _make_retry_predicate([429, 500])
    exc = ValueError("rate limited")
    exc.status_code = 429  # type: ignore[attr-defined]
    assert pred(exc) is True


# ---------- apply_retry_policy ----------

async def test_apply_retry_policy_succeeds_on_third_attempt():
    config = PolicyConfig(retry=RetryRule(max_retries=3, initial_delay_ms=0, max_delay_ms=0))
    engine = PolicyEngine(config)
    call_count = 0

    async def flaky():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise ValueError("transient error")
        return "ok"

    result = await engine.apply_retry_policy(flaky)
    assert result == "ok"
    assert call_count == 3


async def test_apply_retry_policy_reraises_after_max_retries():
    config = PolicyConfig(retry=RetryRule(max_retries=2, initial_delay_ms=0, max_delay_ms=0))
    engine = PolicyEngine(config)

    async def always_fails():
        raise RuntimeError("always bad")

    with pytest.raises(RuntimeError, match="always bad"):
        await engine.apply_retry_policy(always_fails)


async def test_apply_retry_policy_does_not_retry_non_listed_status_code():
    config = PolicyConfig(retry=RetryRule(max_retries=3, initial_delay_ms=0, max_delay_ms=0))
    engine = PolicyEngine(config)
    call_count = 0

    async def client_error():
        nonlocal call_count
        call_count += 1
        exc = ValueError("bad request")
        exc.status_code = 400  # type: ignore[attr-defined]
        raise exc

    with pytest.raises(ValueError):
        await engine.apply_retry_policy(client_error)
    assert call_count == 1  # no retries for non-listed status codes


# ---------- apply_timeout_policy ----------

async def test_apply_timeout_policy_raises_on_slow_function():
    config = PolicyConfig(timeout=TimeoutRule(llm_timeout_ms=50))
    engine = PolicyEngine(config)

    async def slow():
        await asyncio.sleep(10)
        return "done"

    with pytest.raises(asyncio.TimeoutError):
        await engine.apply_timeout_policy(slow, timeout_type="llm")


async def test_apply_timeout_policy_returns_result_on_fast_function():
    engine = PolicyEngine()

    async def fast():
        return "result"

    result = await engine.apply_timeout_policy(fast, timeout_type="llm")
    assert result == "result"


async def test_apply_timeout_policy_uses_correct_timeout_for_type():
    # retrieval default = 10_000 ms → quick() at 10ms should succeed
    engine = PolicyEngine()

    async def quick():
        await asyncio.sleep(0.01)
        return "done"

    result = await engine.apply_timeout_policy(quick, timeout_type="retrieval")
    assert result == "done"


# ---------- check_security_policy ----------

def test_check_security_policy_allows_all_when_no_whitelist():
    engine = PolicyEngine()
    assert engine.check_security_policy("any_tool", {}) is True


def test_check_security_policy_blocks_tool_not_in_whitelist():
    config = PolicyConfig(security=SecurityRule(tool_whitelist=["allowed_tool"]))
    engine = PolicyEngine(config)
    assert engine.check_security_policy("forbidden_tool", {}) is False


def test_check_security_policy_allows_whitelisted_tool():
    config = PolicyConfig(security=SecurityRule(tool_whitelist=["safe_tool"]))
    engine = PolicyEngine(config)
    assert engine.check_security_policy("safe_tool", {}) is True


def test_check_security_policy_blocks_sensitive_field_in_args():
    engine = PolicyEngine()
    # "password" is in the default sensitive_fields list
    assert engine.check_security_policy("tool", {"password": "s3cr3t"}) is False


def test_check_security_policy_blocks_api_key_field():
    engine = PolicyEngine()
    assert engine.check_security_policy("tool", {"api_key": "sk-xxx"}) is False


def test_check_security_policy_allows_clean_args():
    engine = PolicyEngine()
    assert engine.check_security_policy("tool", {"city": "NYC", "count": 5}) is True
