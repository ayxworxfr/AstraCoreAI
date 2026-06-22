"""Tests for PolicyEngine — retry (tenacity) and security policies."""

import httpx
import pytest

from astracore.shared.policy.engine import PolicyConfig, PolicyEngine, _make_retry_predicate
from astracore.shared.policy.rules import RetryRule, SecurityRule

# ---------- _make_retry_predicate ----------


def test_retry_predicate_skips_non_whitelisted_exception():
    """业务异常（如 ValueError）即便无 status_code 也不应被重试，避免烧钱式 retry。"""
    pred = _make_retry_predicate([429, 500], ["httpx.ConnectError"])
    assert pred(ValueError("boom")) is False


def test_retry_predicate_skips_non_listed_status_code():
    pred = _make_retry_predicate([429, 500], [])
    exc = ValueError("client error")
    exc.status_code = 400  # type: ignore[attr-defined]
    assert pred(exc) is False


def test_retry_predicate_retries_listed_status_code():
    pred = _make_retry_predicate([429, 500], [])
    exc = ValueError("rate limited")
    exc.status_code = 429  # type: ignore[attr-defined]
    assert pred(exc) is True


def test_retry_predicate_retries_whitelisted_exception_class():
    """httpx.ConnectError 在白名单 → 网络瞬态错误应被重试。"""
    pred = _make_retry_predicate([], ["httpx.ConnectError"])
    assert pred(httpx.ConnectError("network down")) is True


def test_retry_predicate_matches_via_base_class():
    """子类异常通过 MRO 命中白名单基类（httpx.ConnectTimeout < httpx.TimeoutException）。"""
    pred = _make_retry_predicate([], ["httpx.TimeoutException"])
    assert pred(httpx.ConnectTimeout("connect slow")) is True


def test_retry_predicate_default_whitelist_covers_remote_protocol_error():
    """RemoteProtocolError（流式中途断开）默认在白名单内。"""
    rules = RetryRule()
    pred = _make_retry_predicate(rules.retry_on_status_codes, rules.retry_on_exception_types)
    assert pred(httpx.RemoteProtocolError("peer closed")) is True


# ---------- apply_retry_policy ----------


async def test_apply_retry_policy_succeeds_on_third_attempt():
    """瞬态网络错误（httpx.ConnectError）属白名单 → 触发重试直至成功。"""
    config = PolicyConfig(retry=RetryRule(max_retries=3, initial_delay_ms=0, max_delay_ms=0))
    engine = PolicyEngine(config)
    call_count = 0

    async def flaky():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise httpx.ConnectError("transient network error")
        return "ok"

    result = await engine.apply_retry_policy(flaky)
    assert result == "ok"
    assert call_count == 3


async def test_apply_retry_policy_reraises_after_max_retries():
    config = PolicyConfig(retry=RetryRule(max_retries=2, initial_delay_ms=0, max_delay_ms=0))
    engine = PolicyEngine(config)

    async def always_fails():
        raise httpx.ConnectError("always bad")

    with pytest.raises(httpx.ConnectError, match="always bad"):
        await engine.apply_retry_policy(always_fails)


async def test_apply_retry_policy_does_not_retry_non_whitelisted_exception():
    """ValueError 不在白名单 → 直接抛出，不浪费重试预算。"""
    config = PolicyConfig(retry=RetryRule(max_retries=3, initial_delay_ms=0, max_delay_ms=0))
    engine = PolicyEngine(config)
    call_count = 0

    async def business_error():
        nonlocal call_count
        call_count += 1
        raise ValueError("bad input")

    with pytest.raises(ValueError):
        await engine.apply_retry_policy(business_error)
    assert call_count == 1  # 业务异常不重试


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
