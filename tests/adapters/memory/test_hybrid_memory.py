"""Tests for HybridMemoryAdapter — in-memory fallback, TTL eviction, cap, Redis disable."""
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from astracore.adapters.memory.hybrid import HybridMemoryAdapter
from astracore.core.domain.message import Message, MessageRole


def _adapter() -> HybridMemoryAdapter:
    a = HybridMemoryAdapter(
        redis_url="redis://localhost:6379",
        postgres_url="postgresql+asyncpg://localhost/test",
    )
    a._redis_disabled = True  # bypass Redis — test in-memory path only
    return a


def _msg(content: str) -> Message:
    return Message(role=MessageRole.USER, content=content)


# ---------- save / load short-term (in-memory fallback) ----------

async def test_save_and_load_short_term_roundtrip():
    adapter = _adapter()
    sid = uuid4()
    msgs = [_msg("hello"), _msg("world")]
    await adapter.save_short_term(sid, msgs)
    loaded = await adapter.load_short_term(sid)
    assert len(loaded) == 2
    assert loaded[0].content == "hello"
    assert loaded[1].content == "world"


async def test_load_short_term_returns_empty_for_unknown_session():
    adapter = _adapter()
    result = await adapter.load_short_term(uuid4())
    assert result == []


async def test_save_short_term_overwrites_previous():
    adapter = _adapter()
    sid = uuid4()
    await adapter.save_short_term(sid, [_msg("first")])
    await adapter.save_short_term(sid, [_msg("second")])
    loaded = await adapter.load_short_term(sid)
    assert len(loaded) == 1
    assert loaded[0].content == "second"


# ---------- _evict_stale — TTL ----------

async def test_evict_stale_removes_expired_sessions():
    adapter = _adapter()
    sid = uuid4()
    await adapter.save_short_term(sid, [_msg("old")])
    key = adapter._session_key(sid)
    # Force timestamp to be expired
    adapter._session_timestamps[key] = datetime.now(UTC) - timedelta(hours=2)
    adapter._evict_stale()
    assert key not in adapter._in_memory_sessions


async def test_evict_stale_keeps_fresh_sessions():
    adapter = _adapter()
    sid = uuid4()
    await adapter.save_short_term(sid, [_msg("fresh")])
    key = adapter._session_key(sid)
    adapter._evict_stale()
    assert key in adapter._in_memory_sessions


# ---------- _evict_stale — cap ----------

async def test_evict_enforces_session_cap():
    adapter = _adapter()
    adapter._MAX_IN_MEMORY_SESSIONS = 3  # low cap for test

    sids = [uuid4() for _ in range(4)]
    for sid in sids:
        await adapter.save_short_term(sid, [_msg("data")])

    # After 4 saves the internal count exceeds cap; explicit evict enforces it
    adapter._evict_stale()
    assert len(adapter._in_memory_sessions) <= 3


# ---------- Redis disable ----------

def test_disable_redis_sets_flag():
    a = HybridMemoryAdapter("redis://localhost:6379", "postgresql+asyncpg://localhost/test")
    assert a._redis_disabled is False
    a._disable_redis()
    assert a._redis_disabled is True
    assert a._redis is None


def test_get_redis_returns_none_when_disabled():
    a = HybridMemoryAdapter("redis://localhost:6379", "postgresql+asyncpg://localhost/test")
    a._disable_redis()
    assert a._get_redis() is None


async def test_load_short_term_uses_memory_cache_when_redis_disabled():
    adapter = _adapter()
    sid = uuid4()
    await adapter.save_short_term(sid, [_msg("cached")])
    adapter._redis_disabled = True
    loaded = await adapter.load_short_term(sid)
    assert loaded[0].content == "cached"


async def test_delete_session_memory_clears_in_memory():
    adapter = _adapter()
    sid = uuid4()
    await adapter.save_short_term(sid, [_msg("data")])
    await adapter.delete_session_memory(sid)
    result = await adapter.load_short_term(sid)
    assert result == []
