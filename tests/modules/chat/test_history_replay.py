"""Transcript replay → short-term 回填闭环。"""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from astracore.modules.chat.application.history import load_history
from astracore.modules.chat.domain.message import Message, MessageRole


@pytest.mark.asyncio
async def test_load_history_replays_when_short_term_empty():
    session_id = uuid4()
    replayed = [
        Message(role=MessageRole.USER, content="hi"),
        Message(role=MessageRole.ASSISTANT, content="hello"),
        Message(role=MessageRole.SYSTEM, content="should-drop"),
    ]
    memory = AsyncMock()
    memory.load_short_term = AsyncMock(return_value=[])
    memory.save_short_term = AsyncMock()
    transcript = AsyncMock()
    transcript.load_messages = AsyncMock(return_value=replayed)

    loaded = await load_history(memory, transcript, session_id)

    assert [m.content for m in loaded] == ["hi", "hello"]
    memory.save_short_term.assert_awaited_once()
    args, kwargs = memory.save_short_term.await_args
    sid = kwargs.get("session_id", args[0] if args else None)
    saved = kwargs.get("messages", args[1] if len(args) > 1 else None)
    assert sid == session_id
    assert saved is not None
    assert all(m.role != MessageRole.SYSTEM for m in saved)


@pytest.mark.asyncio
async def test_load_history_prefers_short_term():
    session_id = uuid4()
    short = [Message(role=MessageRole.USER, content="cached")]
    memory = AsyncMock()
    memory.load_short_term = AsyncMock(return_value=short)
    memory.save_short_term = AsyncMock()
    transcript = AsyncMock()
    transcript.load_messages = AsyncMock()

    loaded = await load_history(memory, transcript, session_id)

    assert loaded[0].content == "cached"
    transcript.load_messages.assert_not_awaited()
    memory.save_short_term.assert_not_awaited()
