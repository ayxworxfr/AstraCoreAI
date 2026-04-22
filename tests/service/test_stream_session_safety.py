"""Streaming session safety regression tests."""

import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

from sqlalchemy.pool import NullPool

from astracore.adapters.db.session import get_engine
from astracore.core.domain.message import Message, MessageRole
from astracore.service.api.chat import _save_short_term_safe


async def test_save_short_term_safe_swallows_cancelled_error(monkeypatch) -> None:
    """请求取消时，会话保存不应抛出取消异常污染日志。"""
    mock_memory = AsyncMock()
    mock_memory.save_short_term.side_effect = asyncio.CancelledError()

    monkeypatch.setattr("astracore.service.api.chat._get_memory_adapter", lambda: mock_memory)

    await _save_short_term_safe(
        session_id=uuid4(),
        messages=[Message(role=MessageRole.USER, content="hello")],
    )

    assert mock_memory.save_short_term.await_count == 1


def test_get_engine_uses_null_pool_for_sqlite() -> None:
    """SQLite engine 应使用 NullPool，减少连接复用终止冲突。"""
    get_engine.cache_clear()
    engine = get_engine("sqlite+aiosqlite:///./test_stream_safety.db")
    try:
        assert isinstance(engine.sync_engine.pool, NullPool)
    finally:
        get_engine.cache_clear()
