"""RunRegistry 进程内路径（无 Redis）。"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from astracore.infrastructure.chat.run_registry import ActiveRun, RunRegistry
from astracore.infrastructure.db.models import ChatRunRow


def _row() -> ChatRunRow:
    now = datetime.now(UTC)
    return ChatRunRow(
        id=str(uuid4()),
        session_id=str(uuid4()),
        user_id="u1",
        status="running",
        request={"message": "hi"},
        user_message="hi",
        created_at=now,
        updated_at=now,
    )


def test_register_update_broadcast_local():
    registry = RunRegistry(redis_url=None)
    row = _row()
    active = ActiveRun(row)
    registry.register(row.id, active)

    assert registry.get_local(row.id) is active
    registry.update_state(row.id, assistant_content="hello")
    assert active.state["assistant_content"] == "hello"

    queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
    active.subscribers.add(queue)
    registry.broadcast(row.id, "token", {"t": "x"})
    event, data = queue.get_nowait()
    assert event == "token"
    assert '"t": "x"' in data or '"t":"x"' in data

    popped = registry.pop(row.id)
    assert popped is active
    assert registry.get_local(row.id) is None


@pytest.mark.asyncio
async def test_load_state_prefers_local():
    registry = RunRegistry(redis_url=None)
    row = _row()
    registry.register(row.id, ActiveRun(row))
    state = await registry.load_state(row.id)
    assert state is not None
    assert state["run_id"] == row.id
