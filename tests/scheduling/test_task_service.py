"""ScheduledTaskService unit tests — CRUD, limits, and trigger validation."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from astracore.infrastructure.db.session import get_engine, init_db
from astracore.modules.scheduling.application.task_service import ScheduledTaskService
from astracore.modules.scheduling.domain.task import (
    CreateTaskRequest,
    TriggerType,
    UpdateTaskRequest,
)


@pytest.fixture
async def svc(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"
    get_engine.cache_clear()
    await init_db(db_url)
    yield ScheduledTaskService(db_url, default_timezone="Asia/Shanghai")
    get_engine.cache_clear()


async def _make_cron_task(
    svc: ScheduledTaskService, user_id: str = "u1", expr: str = "0 9 * * *"
) -> None:
    await svc.create_task(
        CreateTaskRequest(
            user_id=user_id,
            prompt="hello",
            trigger_type=TriggerType.CRON,
            trigger_config={"expr": expr},
        )
    )


async def test_create_and_get_task(manual_test, svc) -> None:
    task = await svc.create_task(
        CreateTaskRequest(
            user_id="u1",
            prompt="run daily report",
            trigger_type=TriggerType.CRON,
            trigger_config={"expr": "0 9 * * *"},
            name="Daily Report",
        )
    )

    assert task.id
    assert task.user_id == "u1"
    assert task.name == "Daily Report"
    assert task.prompt == "run daily report"
    assert task.trigger_type == TriggerType.CRON
    assert task.status.value == "active"

    fetched = await svc.get_task(task.id, "u1")
    assert fetched is not None
    assert fetched.id == task.id


async def test_create_task_auto_names_from_prompt(manual_test, svc) -> None:
    task = await svc.create_task(
        CreateTaskRequest(
            user_id="u1",
            prompt="a" * 30,
            trigger_type=TriggerType.INTERVAL,
            trigger_config={"seconds": 3600},
        )
    )
    assert task.name == "a" * 24 + "…"


async def test_get_task_cross_user_returns_none(manual_test, svc) -> None:
    task = await svc.create_task(
        CreateTaskRequest(
            user_id="u1",
            prompt="private",
            trigger_type=TriggerType.CRON,
            trigger_config={"expr": "0 9 * * *"},
        )
    )

    result = await svc.get_task(task.id, user_id="u2")
    assert result is None


async def test_list_tasks_filtered_by_user(manual_test, svc) -> None:
    await _make_cron_task(svc, user_id="u1")
    await _make_cron_task(svc, user_id="u2")

    from astracore.modules.scheduling.domain.task import TaskFilter

    tasks, total = await svc.list_tasks(TaskFilter(user_id="u1", page=1, page_size=20))
    assert total == 1
    assert tasks[0].user_id == "u1"


async def test_max_per_user_raises_on_limit(manual_test, svc) -> None:
    for _ in range(3):
        await _make_cron_task(svc, user_id="u1")

    with pytest.raises(ValueError, match="max_tasks_per_user limit reached"):
        await svc.create_task(
            CreateTaskRequest(
                user_id="u1",
                prompt="overflow",
                trigger_type=TriggerType.CRON,
                trigger_config={"expr": "0 9 * * *"},
            ),
            max_per_user=3,
        )


async def test_invalid_cron_expression_raises(manual_test, svc) -> None:
    with pytest.raises(ValueError):
        await svc.create_task(
            CreateTaskRequest(
                user_id="u1",
                prompt="bad cron",
                trigger_type=TriggerType.CRON,
                trigger_config={"expr": "not-a-cron"},
            )
        )


async def test_interval_requires_positive_seconds(manual_test, svc) -> None:
    with pytest.raises(ValueError, match="seconds > 0"):
        await svc.create_task(
            CreateTaskRequest(
                user_id="u1",
                prompt="zero interval",
                trigger_type=TriggerType.INTERVAL,
                trigger_config={"seconds": 0},
            )
        )


async def test_pause_and_resume_task(manual_test, svc) -> None:
    task = await svc.create_task(
        CreateTaskRequest(
            user_id="u1",
            prompt="pauseable",
            trigger_type=TriggerType.CRON,
            trigger_config={"expr": "0 * * * *"},
        )
    )

    paused = await svc.pause_task(task.id, "u1")
    assert paused is not None
    assert paused.status.value == "paused"
    assert paused.next_run_at is None

    resumed = await svc.resume_task(task.id, "u1")
    assert resumed is not None
    assert resumed.status.value == "active"


async def test_delete_task(manual_test, svc) -> None:
    task = await svc.create_task(
        CreateTaskRequest(
            user_id="u1",
            prompt="to be deleted",
            trigger_type=TriggerType.CRON,
            trigger_config={"expr": "0 0 * * *"},
        )
    )

    deleted = await svc.delete_task(task.id, "u1")
    assert deleted is True

    gone = await svc.get_task(task.id, "u1")
    assert gone is None


async def test_delete_task_cross_user_returns_false(manual_test, svc) -> None:
    task = await svc.create_task(
        CreateTaskRequest(
            user_id="u1",
            prompt="protected",
            trigger_type=TriggerType.CRON,
            trigger_config={"expr": "0 0 * * *"},
        )
    )

    result = await svc.delete_task(task.id, user_id="u2")
    assert result is False

    still_there = await svc.get_task(task.id, "u1")
    assert still_there is not None


async def test_update_run_stats(manual_test, svc) -> None:
    task = await svc.create_task(
        CreateTaskRequest(
            user_id="u1",
            prompt="track stats",
            trigger_type=TriggerType.CRON,
            trigger_config={"expr": "0 0 * * *"},
        )
    )

    await svc.update_run_stats(task.id, run_id="run-1", status="done")
    await svc.update_run_stats(task.id, run_id="run-2", status="error", error="boom")

    fetched = await svc.get_task(task.id, "u1")
    assert fetched is not None
    assert fetched.run_count == 2
    assert fetched.error_count == 1
    assert fetched.last_error == "boom"
    assert fetched.last_run_id == "run-2"


async def test_update_task_timezone_only_recomputes_next_run(manual_test, svc) -> None:
    task = await svc.create_task(
        CreateTaskRequest(
            user_id="u1",
            prompt="timezone update",
            trigger_type=TriggerType.DATE,
            trigger_config={"run_at": "2099-01-01T09:00:00"},
            timezone="Asia/Shanghai",
        )
    )
    assert task.next_run_at is not None
    original_next_run = task.next_run_at
    if original_next_run.tzinfo is None:
        original_next_run = original_next_run.replace(tzinfo=UTC)
    assert original_next_run == datetime(2099, 1, 1, 1, 0, 0, tzinfo=UTC)

    updated = await svc.update_task(
        task.id,
        "u1",
        UpdateTaskRequest(timezone="UTC"),
    )

    assert updated is not None
    assert updated.timezone == "UTC"
    assert updated.next_run_at is not None
    next_run = updated.next_run_at
    if next_run.tzinfo is None:
        next_run = next_run.replace(tzinfo=UTC)
    assert next_run == datetime(2099, 1, 1, 9, 0, 0, tzinfo=UTC)


async def test_update_finished_date_task_reactivates_when_schedule_changes(
    manual_test, svc
) -> None:
    task = await svc.create_task(
        CreateTaskRequest(
            user_id="u1",
            prompt="one-shot",
            trigger_type=TriggerType.DATE,
            trigger_config={"run_at": "2099-01-01T00:00:00+00:00"},
        )
    )
    await svc.mark_finished(task.id)

    updated = await svc.update_task(
        task.id,
        "u1",
        UpdateTaskRequest(
            trigger_config={"run_at": "2099-01-02T00:00:00+00:00"},
        ),
    )

    assert updated is not None
    assert updated.status.value == "active"
    assert updated.next_run_at is not None
    next_run = updated.next_run_at
    if next_run.tzinfo is None:
        next_run = next_run.replace(tzinfo=UTC)
    assert next_run == datetime(2099, 1, 2, 0, 0, 0, tzinfo=UTC)


async def test_mark_finished(manual_test, svc) -> None:
    task = await svc.create_task(
        CreateTaskRequest(
            user_id="u1",
            prompt="one-shot",
            trigger_type=TriggerType.DATE,
            trigger_config={"run_at": "2099-01-01T00:00:00+00:00"},
        )
    )

    await svc.mark_finished(task.id)
    fetched = await svc.get_task(task.id, "u1")
    assert fetched is not None
    assert fetched.status.value == "finished"
    assert fetched.next_run_at is None


async def test_date_trigger_without_offset_uses_task_timezone(manual_test, svc) -> None:
    task = await svc.create_task(
        CreateTaskRequest(
            user_id="u1",
            prompt="one-shot local time",
            trigger_type=TriggerType.DATE,
            trigger_config={"run_at": "2099-01-01T11:45:36"},
            timezone="Asia/Shanghai",
        )
    )

    assert task.next_run_at is not None
    next_run = task.next_run_at
    if next_run.tzinfo is None:
        next_run = next_run.replace(tzinfo=UTC)
    assert next_run == datetime(2099, 1, 1, 3, 45, 36, tzinfo=UTC)


async def test_load_all_active_tasks(manual_test, svc) -> None:
    task1 = await svc.create_task(
        CreateTaskRequest(
            user_id="u1",
            prompt="active1",
            trigger_type=TriggerType.CRON,
            trigger_config={"expr": "0 9 * * *"},
        )
    )
    task2 = await svc.create_task(
        CreateTaskRequest(
            user_id="u2",
            prompt="active2",
            trigger_type=TriggerType.CRON,
            trigger_config={"expr": "0 10 * * *"},
        )
    )
    await svc.pause_task(task2.id, "u2")

    active = await svc.load_all_active_tasks()
    ids = [t.id for t in active]
    assert task1.id in ids
    assert task2.id not in ids
