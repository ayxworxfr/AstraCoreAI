# Queued Chat Runs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a user sends a message while the AI is already processing one, the new message is queued server-side and auto-started when the current run completes — without interrupting the active run.

**Architecture:** Add a `"queued"` status to `ChatRunRow`. `_ACTIVE_RUNS` (in-process dict) remains the single source of truth for live runs; queued rows live only in the DB until dequeued. A `_QUEUED_TOOL_ADAPTERS` dict holds each queued run's resolved `ToolAdapter` (from the original HTTP request) so MCP tools are preserved when the run eventually starts. On completion, `_run_chat_in_background.finally` calls `_dequeue_next_run_for_session`, which updates the oldest queued row to `"running"`, creates its `_ActiveRun`, and starts the asyncio task. The SSE handler for a queued run polls `_ACTIVE_RUNS` every 0.5 s (up to 5 min) until the run transitions to active or terminal.

**Tech Stack:** Python asyncio, FastAPI, SQLAlchemy async, SSE (sse-starlette), SQLite/PostgreSQL via existing `get_session`.

---

## File Map

| File | Change |
|------|--------|
| `src/astracore/modules/chat/api.py` | All logic changes: new dicts, helpers, refactored endpoints |
| `src/astracore/infrastructure/db/models.py` | Add composite index for queued run queries |
| `tests/service/test_queued_runs.py` | New test file for queue behaviour |

---

## Task 1: Add DB index and `_create_run_row` status param

**Files:**
- Modify: `src/astracore/infrastructure/db/models.py`
- Modify: `src/astracore/modules/chat/api.py:353-369`

- [ ] **Step 1: Add index to `ChatRunRow`**

Open `src/astracore/infrastructure/db/models.py`. The current `__table_args__` on `ChatRunRow` is:

```python
__table_args__ = (
    Index("ix_chat_runs_session_status_updated", "session_id", "status", "updated_at"),
)
```

Replace with:

```python
__table_args__ = (
    Index("ix_chat_runs_session_status_updated", "session_id", "status", "updated_at"),
    Index("ix_chat_runs_session_status_created", "session_id", "status", "created_at"),
)
```

- [ ] **Step 2: Add `status` parameter to `_create_run_row`**

In `src/astracore/modules/chat/api.py`, find `_create_run_row` (currently line ~353). Replace:

```python
async def _create_run_row(request: ChatRequest, session_id: UUID) -> ChatRunRow:
    run_id = str(uuid4())
    now = datetime.now(UTC)
    row = ChatRunRow(
        id=run_id,
        session_id=str(session_id),
        status="running",
        request=request.model_dump(mode="json"),
        user_message=request.message,
        created_at=now,
        updated_at=now,
    )
    async with get_session(_get_settings().memory.db_url) as db:
        db.add(row)
        await db.commit()
        await db.refresh(row)
        return row
```

With:

```python
async def _create_run_row(
    request: ChatRequest, session_id: UUID, *, status: str = "running"
) -> ChatRunRow:
    run_id = str(uuid4())
    now = datetime.now(UTC)
    row = ChatRunRow(
        id=run_id,
        session_id=str(session_id),
        status=status,
        request=request.model_dump(mode="json"),
        user_message=request.message,
        created_at=now,
        updated_at=now,
    )
    async with get_session(_get_settings().memory.db_url) as db:
        db.add(row)
        await db.commit()
        await db.refresh(row)
        return row
```

- [ ] **Step 3: Run existing tests to confirm no regression**

```
make test
```

Expected: 155 passed.

- [ ] **Step 4: Commit**

```bash
git add src/astracore/infrastructure/db/models.py src/astracore/modules/chat/api.py
git commit -m "feat(chat): add queued-run DB index and status param to _create_run_row"
```

---

## Task 2: Queue infrastructure — dicts and helpers

**Files:**
- Modify: `src/astracore/modules/chat/api.py`

Add the following immediately after the `_ACTIVE_RUNS: dict[str, _ActiveRun] = {}` line (currently line 71).

- [ ] **Step 1: Add `_QUEUED_TOOL_ADAPTERS` dict and sync helper**

After line 71 (`_ACTIVE_RUNS: dict[str, _ActiveRun] = {}`), insert:

```python
# Maps queued run_id → the ToolAdapter resolved at request time.
# Populated in create_chat_run; consumed (and removed) in _dequeue_next_run_for_session.
_QUEUED_TOOL_ADAPTERS: dict[str, ToolAdapter] = {}


def _get_active_run_id_for_session(session_id: str) -> str | None:
    """Return the run_id of the in-process (not queued) run for *session_id*, or None."""
    for run_id, active in _ACTIVE_RUNS.items():
        if active.state.get("session_id") == session_id:
            return run_id
    return None
```

- [ ] **Step 2: Add async DB helpers**

After `_get_active_run_row` (currently around line 324), insert two new helpers:

```python
async def _get_oldest_queued_run(session_id: UUID) -> ChatRunRow | None:
    async with get_session(_get_settings().memory.db_url) as db:
        result = await db.execute(
            select(ChatRunRow)
            .where(
                ChatRunRow.session_id == str(session_id),
                ChatRunRow.status == "queued",
            )
            .order_by(ChatRunRow.created_at.asc())
            .limit(1)
        )
        return result.scalar_one_or_none()


async def _dequeue_next_run_for_session(session_id: UUID) -> None:
    """Start the oldest queued run for *session_id*, if one exists."""
    row = await _get_oldest_queued_run(session_id)
    if row is None:
        return
    tool_adapter = _QUEUED_TOOL_ADAPTERS.pop(row.id, None) or build_tool_adapter()
    updated = await _update_run_row(row.id, status="running")
    if updated is None:
        return
    request = ChatRequest(**updated.request)
    active_run = _ActiveRun(updated)
    _ACTIVE_RUNS[updated.id] = active_run
    active_run.task = asyncio.create_task(
        _run_chat_in_background(
            run_id=updated.id,
            request=request,
            session_id=session_id,
            tool_adapter=tool_adapter,
        )
    )
    logger.info("出队 chat run: run_id=%s, session=%s", updated.id, session_id)
```

- [ ] **Step 3: Run tests**

```
make test
```

Expected: 155 passed (new helpers not yet called by endpoints).

- [ ] **Step 4: Commit**

```bash
git add src/astracore/modules/chat/api.py
git commit -m "feat(chat): add queue infra — _QUEUED_TOOL_ADAPTERS, _get_active_run_id_for_session, _dequeue_next_run_for_session"
```

---

## Task 3: Refactor `create_chat_run` to enqueue when busy

**Files:**
- Modify: `src/astracore/modules/chat/api.py:721-743`

- [ ] **Step 1: Replace `create_chat_run` body**

Find `create_chat_run` (around line 721). Replace the entire function body:

```python
@router.post("/runs", response_model=ChatRunResponse, status_code=202)
async def create_chat_run(request: ChatRequest, http_request: Request) -> ChatRunResponse:
    session_id = request.session_id or uuid4()
    tool_adapter = _resolve_tool_adapter(http_request)

    # If session already has an in-process run, queue the new message.
    if _get_active_run_id_for_session(str(session_id)) is not None:
        row = await _create_run_row(request, session_id, status="queued")
        _QUEUED_TOOL_ADAPTERS[row.id] = tool_adapter
        logger.info("消息排队: run_id=%s, session=%s", row.id, session_id)
        return ChatRunResponse(run_id=row.id, session_id=str(session_id), status="queued")

    # No in-process run. Mark any stale DB "running" row as errored.
    stale = await _get_active_run_row(session_id)
    if stale is not None:
        await _update_run_row(stale.id, status="error", error="服务重启导致生成任务中断")

    row = await _create_run_row(request, session_id, status="running")
    active_run = _ActiveRun(row)
    _ACTIVE_RUNS[row.id] = active_run
    active_run.task = asyncio.create_task(
        _run_chat_in_background(
            run_id=row.id,
            request=request,
            session_id=session_id,
            tool_adapter=tool_adapter,
        )
    )
    logger.info("创建后台 chat run: run_id=%s, session=%s", row.id, session_id)
    return ChatRunResponse(run_id=row.id, session_id=str(session_id), status=row.status)
```

- [ ] **Step 2: Run tests**

```
make test
```

Expected: 155 passed.

- [ ] **Step 3: Commit**

```bash
git add src/astracore/modules/chat/api.py
git commit -m "feat(chat): enqueue new messages when session has active run"
```

---

## Task 4: Auto-dequeue on run completion

**Files:**
- Modify: `src/astracore/modules/chat/api.py` — `_run_chat_in_background`

- [ ] **Step 1: Add dequeue call in `finally`**

Find `_run_chat_in_background` (around line 607). Its `finally` block currently reads:

```python
    finally:
        _ACTIVE_RUNS.pop(run_id, None)
```

Replace with:

```python
    finally:
        _ACTIVE_RUNS.pop(run_id, None)
        await _dequeue_next_run_for_session(session_id)
```

- [ ] **Step 2: Run tests**

```
make test
```

Expected: 155 passed.

- [ ] **Step 3: Commit**

```bash
git add src/astracore/modules/chat/api.py
git commit -m "feat(chat): auto-dequeue next queued run after session run completes"
```

---

## Task 5: SSE handler — poll during `queued` status

**Files:**
- Modify: `src/astracore/modules/chat/api.py` — `stream_chat_run`

- [ ] **Step 1: Replace `stream_chat_run`**

The constant 600 iterations × 0.5 s = 300 s (5 min) queue timeout. Replace the entire `stream_chat_run` function:

```python
_QUEUE_POLL_INTERVAL = 0.5  # seconds between polls for queued runs
_QUEUE_POLL_MAX = 600       # 300 s total timeout


@router.get("/runs/{run_id}/stream")
async def stream_chat_run(run_id: UUID) -> EventSourceResponse:
    async def event_generator() -> AsyncIterator[dict[str, str]]:
        rid = str(run_id)
        row = await _get_run_row(rid)
        if row is None:
            yield {"event": "error", "data": _json_event({"message": "Run not found"})}
            return

        active = _ACTIVE_RUNS.get(rid)
        yield {
            "event": "run_state",
            "data": _json_event(active.payload() if active is not None else _run_row_to_state(row).model_dump()),
        }

        if row.status in _RUN_TERMINAL_STATUSES:
            yield {
                "event": "done" if row.status == "done" else "error",
                "data": _json_event({"message": row.error}),
            }
            return

        # Queued: poll until the run is promoted to active or reaches a terminal state.
        if row.status == "queued" and active is None:
            for _ in range(_QUEUE_POLL_MAX):
                await asyncio.sleep(_QUEUE_POLL_INTERVAL)
                active = _ACTIVE_RUNS.get(rid)
                if active is not None:
                    # Run has been dequeued — emit fresh state then stream normally.
                    yield {"event": "run_state", "data": _json_event(active.payload())}
                    break
                row = await _get_run_row(rid)
                if row is None:
                    yield {"event": "error", "data": _json_event({"message": "Run not found"})}
                    return
                if row.status in _RUN_TERMINAL_STATUSES:
                    yield {"event": "run_state", "data": _json_event(_run_row_to_state(row).model_dump())}
                    yield {
                        "event": "done" if row.status == "done" else "error",
                        "data": _json_event({"message": row.error}),
                    }
                    return
            else:
                # Timeout — mark the run as errored.
                row = await _update_run_row(rid, status="error", error="排队超时，请重新发送")
                _QUEUED_TOOL_ADAPTERS.pop(rid, None)
                if row:
                    yield {"event": "run_state", "data": _json_event(_run_row_to_state(row).model_dump())}
                yield {"event": "error", "data": _json_event({"message": "排队超时，请重新发送"})}
                return

        if active is None:
            # Status was "running" but run is not in _ACTIVE_RUNS — stale row from restart.
            row = await _update_run_row(rid, status="error", error="生成任务已中断，请重新发送")
            if row:
                yield {"event": "run_state", "data": _json_event(_run_row_to_state(row).model_dump())}
            yield {"event": "error", "data": _json_event({"message": "生成任务已中断，请重新发送"})}
            return

        queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue(maxsize=200)
        active.subscribers.add(queue)
        try:
            while True:
                event, data = await queue.get()
                yield {"event": event, "data": data}
                if event in {"done", "error"}:
                    break
        finally:
            active.subscribers.discard(queue)

    return EventSourceResponse(event_generator())
```

- [ ] **Step 2: Run tests**

```
make test
```

Expected: 155 passed.

- [ ] **Step 3: Commit**

```bash
git add src/astracore/modules/chat/api.py
git commit -m "feat(chat): SSE handler polls for queued run to become active (5 min timeout)"
```

---

## Task 6: `cancel_chat_run` — support queued runs

**Files:**
- Modify: `src/astracore/modules/chat/api.py` — `cancel_chat_run`

- [ ] **Step 1: Replace `cancel_chat_run` body**

Find `cancel_chat_run` (around line 792). Current body:

```python
    rid = str(run_id)
    active = _ACTIVE_RUNS.get(rid)
    if active is not None and active.task is not None:
        active.task.cancel()
        active.update(
            status="cancelled",
            error="用户已停止生成",
            completed_at=datetime.now(UTC).isoformat(),
        )
    row = await _update_run_row(rid, status="cancelled", error="用户已停止生成")
    if row is None:
        raise HTTPException(status_code=404, detail="Run not found")
    _broadcast_snapshot(rid, row)
    _broadcast_run_event(rid, "error", {"message": "用户已停止生成"})
    return _run_row_to_state(row)
```

Replace with:

```python
    rid = str(run_id)
    active = _ACTIVE_RUNS.get(rid)
    if active is not None and active.task is not None:
        active.task.cancel()
        active.update(
            status="cancelled",
            error="用户已停止生成",
            completed_at=datetime.now(UTC).isoformat(),
        )
    else:
        # May be a queued run — clean up stored adapter.
        _QUEUED_TOOL_ADAPTERS.pop(rid, None)
    row = await _update_run_row(rid, status="cancelled", error="用户已停止生成")
    if row is None:
        raise HTTPException(status_code=404, detail="Run not found")
    _broadcast_snapshot(rid, row)
    _broadcast_run_event(rid, "error", {"message": "用户已停止生成"})
    return _run_row_to_state(row)
```

- [ ] **Step 2: Run tests**

```
make test
```

Expected: 155 passed.

- [ ] **Step 3: Commit**

```bash
git add src/astracore/modules/chat/api.py
git commit -m "feat(chat): cancel_chat_run cleans up queued tool adapter"
```

---

## Task 7: Tests

**Files:**
- Create: `tests/service/test_queued_runs.py`

- [ ] **Step 1: Write the test file**

Create `tests/service/test_queued_runs.py`:

```python
"""Tests for server-side queued chat run behaviour."""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from astracore.infrastructure.db.session import get_engine, get_session, init_db
from astracore.modules.chat import api as chat
from astracore.modules.chat.api import (
    ChatRequest,
    ChatRunResponse,
    _ACTIVE_RUNS,
    _QUEUED_TOOL_ADAPTERS,
    _ActiveRun,
    _dequeue_next_run_for_session,
    _get_active_run_id_for_session,
    _get_oldest_queued_run,
)
from astracore.infrastructure.db.models import ChatRunRow


@pytest.fixture(autouse=True)
def _clear_module_state():
    """Isolate in-process dicts between tests."""
    _ACTIVE_RUNS.clear()
    _QUEUED_TOOL_ADAPTERS.clear()
    yield
    _ACTIVE_RUNS.clear()
    _QUEUED_TOOL_ADAPTERS.clear()


@pytest.fixture
async def queue_db(tmp_path, monkeypatch):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'queue.db'}"
    get_engine.cache_clear()
    await init_db(db_url)
    monkeypatch.setattr(
        chat,
        "_get_settings",
        lambda: SimpleNamespace(memory=SimpleNamespace(db_url=db_url)),
    )
    yield db_url
    get_engine.cache_clear()


async def _insert_run(db_url: str, session_id, status: str) -> str:
    run_id = str(uuid4())
    now = datetime.now(UTC)
    async with get_session(db_url) as db:
        db.add(
            ChatRunRow(
                id=run_id,
                session_id=str(session_id),
                status=status,
                request={"message": f"msg-{status}", "session_id": str(session_id)},
                user_message=f"msg-{status}",
                created_at=now,
                updated_at=now,
            )
        )
        await db.commit()
    return run_id


# ---------------------------------------------------------------------------
# _get_active_run_id_for_session
# ---------------------------------------------------------------------------


def test_get_active_run_id_returns_none_when_empty():
    assert _get_active_run_id_for_session("any-session") is None


def test_get_active_run_id_returns_run_id_when_active():
    session_id = str(uuid4())
    run_id = str(uuid4())
    fake_row = SimpleNamespace(
        id=run_id,
        session_id=session_id,
        status="running",
        user_message="hi",
        assistant_content="",
        thinking_blocks=[],
        tool_activity=[],
        error="",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        completed_at=None,
    )
    _ACTIVE_RUNS[run_id] = _ActiveRun(fake_row)
    assert _get_active_run_id_for_session(session_id) == run_id


def test_get_active_run_id_returns_none_for_different_session():
    session_id = str(uuid4())
    other_session = str(uuid4())
    run_id = str(uuid4())
    fake_row = SimpleNamespace(
        id=run_id,
        session_id=session_id,
        status="running",
        user_message="hi",
        assistant_content="",
        thinking_blocks=[],
        tool_activity=[],
        error="",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        completed_at=None,
    )
    _ACTIVE_RUNS[run_id] = _ActiveRun(fake_row)
    assert _get_active_run_id_for_session(other_session) is None


# ---------------------------------------------------------------------------
# _get_oldest_queued_run
# ---------------------------------------------------------------------------


async def test_get_oldest_queued_run_returns_none_when_no_queued(queue_db):
    session_id = uuid4()
    result = await _get_oldest_queued_run(session_id)
    assert result is None


async def test_get_oldest_queued_run_returns_first_by_created_at(queue_db):
    session_id = uuid4()
    first_id = await _insert_run(queue_db, session_id, "queued")
    await asyncio.sleep(0.01)  # ensure distinct created_at
    await _insert_run(queue_db, session_id, "queued")

    result = await _get_oldest_queued_run(session_id)
    assert result is not None
    assert result.id == first_id


async def test_get_oldest_queued_run_ignores_other_statuses(queue_db):
    session_id = uuid4()
    await _insert_run(queue_db, session_id, "done")
    await _insert_run(queue_db, session_id, "error")

    result = await _get_oldest_queued_run(session_id)
    assert result is None


# ---------------------------------------------------------------------------
# _dequeue_next_run_for_session
# ---------------------------------------------------------------------------


async def test_dequeue_promotes_queued_run_to_running(queue_db, monkeypatch):
    session_id = uuid4()
    queued_id = await _insert_run(queue_db, session_id, "queued")

    started_runs: list[str] = []

    async def _fake_run_in_background(*, run_id, **_kwargs):
        started_runs.append(run_id)

    monkeypatch.setattr(chat, "_run_chat_in_background", _fake_run_in_background)
    monkeypatch.setattr(chat, "build_tool_adapter", lambda: object())
    monkeypatch.setattr(chat, "ChatRequest", lambda **kw: SimpleNamespace(**kw))

    await _dequeue_next_run_for_session(session_id)

    assert queued_id in _ACTIVE_RUNS
    assert started_runs == [queued_id]

    async with get_session(queue_db) as db:
        row = await db.get(ChatRunRow, queued_id)
    assert row is not None
    assert row.status == "running"


async def test_dequeue_uses_stored_tool_adapter(queue_db, monkeypatch):
    session_id = uuid4()
    queued_id = await _insert_run(queue_db, session_id, "queued")

    sentinel = object()
    _QUEUED_TOOL_ADAPTERS[queued_id] = sentinel

    captured: list[object] = []

    async def _fake_run_in_background(*, tool_adapter, **_kwargs):
        captured.append(tool_adapter)

    monkeypatch.setattr(chat, "_run_chat_in_background", _fake_run_in_background)
    monkeypatch.setattr(chat, "ChatRequest", lambda **kw: SimpleNamespace(**kw))

    await _dequeue_next_run_for_session(session_id)

    assert captured == [sentinel]
    assert queued_id not in _QUEUED_TOOL_ADAPTERS


async def test_dequeue_noop_when_no_queued_run(queue_db):
    session_id = uuid4()
    await _dequeue_next_run_for_session(session_id)
    assert len(_ACTIVE_RUNS) == 0


# ---------------------------------------------------------------------------
# create_chat_run — queuing behaviour
# ---------------------------------------------------------------------------


async def test_create_chat_run_queues_when_session_active(queue_db, monkeypatch):
    session_id = uuid4()
    active_run_id = str(uuid4())

    # Simulate an in-process run for this session.
    fake_row = SimpleNamespace(
        id=active_run_id,
        session_id=str(session_id),
        status="running",
        user_message="first",
        assistant_content="",
        thinking_blocks=[],
        tool_activity=[],
        error="",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        completed_at=None,
    )
    _ACTIVE_RUNS[active_run_id] = _ActiveRun(fake_row)

    fake_adapter = object()
    monkeypatch.setattr(chat, "_resolve_tool_adapter", lambda _req: fake_adapter)

    request = ChatRequest(message="second message", session_id=session_id)
    response: ChatRunResponse = await chat.create_chat_run(request, SimpleNamespace())

    assert response.status == "queued"
    assert response.run_id in _QUEUED_TOOL_ADAPTERS
    assert _QUEUED_TOOL_ADAPTERS[response.run_id] is fake_adapter

    async with get_session(queue_db) as db:
        row = await db.get(ChatRunRow, response.run_id)
    assert row is not None
    assert row.status == "queued"


async def test_create_chat_run_starts_immediately_when_no_active(queue_db, monkeypatch):
    session_id = uuid4()

    async def _noop(**_kw):
        pass

    monkeypatch.setattr(chat, "_run_chat_in_background", _noop)
    monkeypatch.setattr(chat, "_resolve_tool_adapter", lambda _req: object())
    monkeypatch.setattr(chat, "_get_active_run_row", lambda _sid: None)

    request = ChatRequest(message="first message", session_id=session_id)
    response: ChatRunResponse = await chat.create_chat_run(request, SimpleNamespace())

    assert response.status == "running"
    assert response.run_id in _ACTIVE_RUNS


# ---------------------------------------------------------------------------
# cancel_chat_run — queued runs
# ---------------------------------------------------------------------------


async def test_cancel_removes_queued_tool_adapter(queue_db, monkeypatch):
    session_id = uuid4()
    queued_id = await _insert_run(queue_db, session_id, "queued")
    _QUEUED_TOOL_ADAPTERS[queued_id] = object()

    from uuid import UUID

    await chat.cancel_chat_run(UUID(queued_id))

    assert queued_id not in _QUEUED_TOOL_ADAPTERS

    async with get_session(queue_db) as db:
        row = await db.get(ChatRunRow, queued_id)
    assert row is not None
    assert row.status == "cancelled"
```

- [ ] **Step 2: Run new tests**

```
make test
```

Expected: all tests pass (155 + new tests).

- [ ] **Step 3: Commit**

```bash
git add tests/service/test_queued_runs.py
git commit -m "test(chat): queued run lifecycle — enqueue, dequeue, cancel, SSE wait"
```

---

## Self-Review

**Spec coverage:**
| Requirement | Covered by |
|---|---|
| Queue message without interrupting active run | Task 3 (`create_chat_run` check) |
| Auto-start next queued run on completion | Task 4 (`_run_chat_in_background.finally`) |
| SSE subscriber waits for queued run | Task 5 (`stream_chat_run` poll loop) |
| SSE timeout after 5 min | Task 5 (`_QUEUE_POLL_MAX = 600`) |
| Cancel queued run cleans up adapter | Task 6 |
| MCP tool adapter preserved for queued run | Task 2 (`_QUEUED_TOOL_ADAPTERS`) |
| Stale restart rows still handled | Task 3 (unchanged stale-row path) |

**Placeholder scan:** None found. All code blocks are complete.

**Type consistency:**
- `_dequeue_next_run_for_session(session_id: UUID)` — matches call in Task 4 (`session_id` is `UUID` in `_run_chat_in_background`)
- `_get_active_run_id_for_session(session_id: str)` — call in Task 3 passes `str(session_id)` ✓
- `_create_run_row(..., status="queued")` — keyword-only param matches Task 1 signature ✓
- `_QUEUED_TOOL_ADAPTERS: dict[str, ToolAdapter]` — all access sites use `str` run_id ✓
