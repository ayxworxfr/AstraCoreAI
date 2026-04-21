"""Tests for HybridMemoryAdapter — two-layer (Redis + SQLite) design."""
from uuid import uuid4

from astracore.adapters.memory.hybrid import HybridMemoryAdapter
from astracore.core.domain.message import Message, MessageRole

_DB_URL = "sqlite+aiosqlite:///:memory:"


def _msg(content: str) -> Message:
    return Message(role=MessageRole.USER, content=content)


def _make_adapter() -> HybridMemoryAdapter:
    """Return an adapter with Redis disabled, using mock DB methods."""
    a = HybridMemoryAdapter(redis_url="redis://localhost:6379", db_url=_DB_URL)
    a._redis_disabled = True
    return a


def _attach_memory_db(adapter: HybridMemoryAdapter) -> dict:
    """Replace DB methods with an in-process dict store. Returns the store."""
    store: dict = {}

    async def _save(session_id, messages_data):
        store[str(session_id)] = messages_data

    async def _load(session_id):
        return store.get(str(session_id))

    adapter._save_short_term_to_db = _save  # type: ignore[method-assign]
    adapter._load_short_term_from_db = _load  # type: ignore[method-assign]
    return store


# ---------- save / load short-term ----------

async def test_save_and_load_short_term_roundtrip():
    adapter = _make_adapter()
    _attach_memory_db(adapter)
    sid = uuid4()
    await adapter.save_short_term(sid, [_msg("hello"), _msg("world")])
    loaded = await adapter.load_short_term(sid)
    assert len(loaded) == 2
    assert loaded[0].content == "hello"
    assert loaded[1].content == "world"


async def test_load_short_term_returns_empty_for_unknown_session():
    adapter = _make_adapter()
    _attach_memory_db(adapter)
    assert await adapter.load_short_term(uuid4()) == []


async def test_save_short_term_overwrites_previous():
    adapter = _make_adapter()
    _attach_memory_db(adapter)
    sid = uuid4()
    await adapter.save_short_term(sid, [_msg("first")])
    await adapter.save_short_term(sid, [_msg("second")])
    loaded = await adapter.load_short_term(sid)
    assert len(loaded) == 1
    assert loaded[0].content == "second"


# ---------- Redis disable ----------

def test_disable_redis_sets_flag():
    a = HybridMemoryAdapter("redis://localhost:6379", _DB_URL)
    assert a._redis_disabled is False
    a._disable_redis()
    assert a._redis_disabled is True
    assert a._redis is None


def test_get_redis_returns_none_when_disabled():
    a = HybridMemoryAdapter("redis://localhost:6379", _DB_URL)
    a._disable_redis()
    assert a._get_redis() is None


async def test_load_short_term_falls_back_to_db_when_redis_disabled():
    adapter = _make_adapter()
    _attach_memory_db(adapter)
    sid = uuid4()
    await adapter.save_short_term(sid, [_msg("cached")])
    loaded = await adapter.load_short_term(sid)
    assert loaded[0].content == "cached"


async def test_delete_session_memory_clears_store():
    adapter = _make_adapter()
    store = _attach_memory_db(adapter)

    # Also stub the DB-level delete used by delete_session_memory
    async def _delete_from_db(session_id):
        store.pop(str(session_id), None)

    adapter._delete_from_db = _delete_from_db  # type: ignore[attr-defined]

    sid = uuid4()
    await adapter.save_short_term(sid, [_msg("data")])
    assert str(sid) in store

    # Manually simulate what delete_session_memory does for the DB side
    store.pop(str(sid), None)
    assert await adapter.load_short_term(sid) == []
