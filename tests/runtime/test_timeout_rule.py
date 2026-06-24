"""Tests for TimeoutRule.build_llm_httpx_timeout — 验证分段超时正确传给 SDK。"""

import httpx

from astracore.shared.policy.rules import TimeoutRule


def test_build_llm_httpx_timeout_uses_segment_values():
    """各分段字段单独配置时，应直接落到 httpx.Timeout 对应槽位。"""
    rule = TimeoutRule(
        llm_timeout_s=180.0,
        llm_connect_s=5.0,
        llm_read_s=300.0,
        llm_write_s=30.0,
        llm_pool_s=8.0,
    )
    t = rule.build_llm_httpx_timeout()
    assert isinstance(t, httpx.Timeout)
    assert t.connect == 5.0
    assert t.read == 300.0
    assert t.write == 30.0
    assert t.pool == 8.0


def test_build_llm_httpx_timeout_falls_back_to_overall_when_segment_none():
    """分段字段为 None 时落到 llm_timeout_s 作为兜底。"""
    rule = TimeoutRule(
        llm_timeout_s=120.0,
        llm_connect_s=None,
        llm_read_s=None,
        llm_write_s=None,
        llm_pool_s=None,
    )
    t = rule.build_llm_httpx_timeout()
    assert t.connect == 120.0
    assert t.read == 120.0
    assert t.write == 120.0
    assert t.pool == 120.0


def test_build_llm_httpx_timeout_overall_override_replaces_fallback():
    """profile.timeout_s 通过 overall_override 传入时替换 fallback，且分段值仍优先。"""
    rule = TimeoutRule(
        llm_timeout_s=180.0,
        llm_connect_s=5.0,
        llm_read_s=None,  # 走 fallback
        llm_write_s=None,
        llm_pool_s=None,
    )
    t = rule.build_llm_httpx_timeout(overall_override=600.0)
    assert t.connect == 5.0  # 分段优先，不被 override 覆盖
    assert t.read == 600.0  # 走 override 作为 fallback
    assert t.write == 600.0
    assert t.pool == 600.0


def test_build_llm_httpx_timeout_default_values():
    """默认 read=600s（治 stale stream），其它分段保留合理默认。"""
    t = TimeoutRule().build_llm_httpx_timeout()
    assert t.read == 600.0  # 长流式生成核心防线
    assert t.connect == 10.0
    assert t.write == 60.0
    assert t.pool == 10.0
