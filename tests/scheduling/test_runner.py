"""Runner tests — pipeline failure handling, concurrency semaphore."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

import astracore.modules.scheduling.runner as runner_module
from astracore.infrastructure.db.session import get_engine
from astracore.modules.scheduling.application.task_service import ScheduledTaskService
from astracore.modules.scheduling.domain.task import CreateTaskRequest, TriggerType
from astracore.modules.scheduling.runner import _fire, _sync_fire_job, init_runner
from tests.support.db import prepare_test_db


@pytest.fixture
async def db_url(tmp_path):
    url = await prepare_test_db(tmp_path)
    yield url
    get_engine.cache_clear()


@pytest.fixture
async def active_task(db_url):
    svc = ScheduledTaskService(db_url, "Asia/Shanghai")
    task = await svc.create_task(
        CreateTaskRequest(
            user_id="u1",
            prompt="test prompt",
            trigger_type=TriggerType.CRON,
            trigger_config={"expr": "0 9 * * *"},
            name="Test Task",
        )
    )
    return task, svc, db_url


def _make_fake_pipeline(*, raise_exc=None, status="done"):
    """Return a fake ChatPipeline whose run_pipeline_background returns a mock result."""
    from types import SimpleNamespace

    fake_result = SimpleNamespace(
        status=status, error=None if status == "done" else "pipeline error"
    )

    async def fake_run_pipeline_background(**kwargs):
        if raise_exc:
            raise raise_exc
        return fake_result

    return fake_run_pipeline_background, fake_result


def _make_config(db_url: str) -> MagicMock:
    return MagicMock(
        storage=MagicMock(db_url=db_url),
        memory=MagicMock(db_url=db_url),
        scheduling=MagicMock(default_timezone="Asia/Shanghai"),
    )


async def test_fire_pipeline_error_records_error_status(manual_test, active_task) -> None:
    """F2: pipeline exception → task stats record status='error'."""
    task, svc, db_url = active_task

    mock_pipeline_factory = MagicMock()

    fake_run, _ = _make_fake_pipeline(status="error")

    init_runner(
        pipeline_factory=mock_pipeline_factory,
        config_factory=lambda: _make_config(db_url),
        max_concurrent_runs=5,
    )

    with (
        patch("astracore.modules.scheduling.runner.run_pipeline_background", side_effect=fake_run),
        patch("astracore.modules.scheduling.runner._pipeline_factory", mock_pipeline_factory),
        patch(
            "astracore.modules.scheduling.runner._config_factory",
            lambda: _make_config(db_url),
        ),
    ):
        await _fire(task.id)

    fetched = await svc.get_task(task.id, "u1")
    assert fetched is not None
    assert fetched.run_count == 1
    assert fetched.error_count == 1


async def test_fire_skips_when_previous_run_still_active(manual_test, active_task) -> None:
    """F3: overlapping trigger — second _fire is skipped while first run is still active."""
    task, svc, db_url = active_task

    barrier = asyncio.Event()
    released = asyncio.Event()

    async def slow_run(**kwargs):
        await barrier.wait()
        released.set()
        from types import SimpleNamespace

        return SimpleNamespace(status="done", error=None)

    init_runner(
        pipeline_factory=MagicMock(),
        config_factory=lambda: _make_config(db_url),
        max_concurrent_runs=5,
    )

    first_task = None
    with (
        patch("astracore.modules.scheduling.runner.run_pipeline_background", side_effect=slow_run),
        patch("astracore.modules.scheduling.runner._pipeline_factory", MagicMock()),
        patch(
            "astracore.modules.scheduling.runner._config_factory",
            lambda: _make_config(db_url),
        ),
    ):
        # Start first run and keep it running
        first_task = asyncio.create_task(_fire(task.id))
        await asyncio.sleep(0.05)  # let it start

        # Inject a fake in-progress entry to simulate overlap
        runner_module._RUNNING_TASKS[task.id] = first_task

        # Second fire should skip because task is running
        with patch(
            "astracore.modules.scheduling.runner.run_pipeline_background", side_effect=slow_run
        ) as mock_run:
            await _fire(task.id)
            # The second _fire should not have called run_pipeline_background
            mock_run.assert_not_called()

        # Release the first run
        barrier.set()
        await first_task


async def test_semaphore_limits_concurrency(manual_test, tmp_path) -> None:
    """F6: semaphore caps concurrent runs — excess tasks wait, not fail."""
    db_url = await prepare_test_db(tmp_path, name="sem.db")

    svc = ScheduledTaskService(db_url, "Asia/Shanghai")
    tasks = []
    for i in range(3):
        t = await svc.create_task(
            CreateTaskRequest(
                user_id=f"u{i}",
                prompt=f"task {i}",
                trigger_type=TriggerType.CRON,
                trigger_config={"expr": "0 9 * * *"},
            )
        )
        tasks.append(t)

    completed = []
    barrier = asyncio.Event()

    async def fake_run(**kwargs):
        await barrier.wait()
        completed.append(1)
        from types import SimpleNamespace

        return SimpleNamespace(status="done", error=None)

    # Semaphore of 2 means only 2 run concurrently
    init_runner(
        pipeline_factory=MagicMock(),
        config_factory=lambda: _make_config(db_url),
        max_concurrent_runs=2,
    )

    with (
        patch("astracore.modules.scheduling.runner.run_pipeline_background", side_effect=fake_run),
        patch("astracore.modules.scheduling.runner._pipeline_factory", MagicMock()),
        patch(
            "astracore.modules.scheduling.runner._config_factory",
            lambda: _make_config(db_url),
        ),
    ):
        fires = [asyncio.create_task(_fire(t.id)) for t in tasks]

        await asyncio.sleep(0.05)
        # Only 2 should be holding the semaphore; let them all through
        barrier.set()
        await asyncio.gather(*fires, return_exceptions=True)

    # All 3 eventually complete (semaphore serialises, not rejects)
    assert len(completed) == 3

    get_engine.cache_clear()


async def test_scheduler_job_callback_runs_fire_and_records_conversation(
    manual_test, active_task
) -> None:
    task, svc, db_url = active_task

    async def fake_run(**kwargs):
        from types import SimpleNamespace

        return SimpleNamespace(status="done", error=None)

    init_runner(
        pipeline_factory=MagicMock(),
        config_factory=lambda: _make_config(db_url),
        max_concurrent_runs=5,
    )

    with (
        patch(
            "astracore.modules.scheduling.runner.run_pipeline_background", side_effect=fake_run
        ) as run_mock,
        patch("astracore.modules.scheduling.runner._pipeline_factory", MagicMock()),
        patch("astracore.modules.scheduling.runner._config_factory", lambda: _make_config(db_url)),
    ):
        await _sync_fire_job(task.id)

    fetched = await svc.get_task(task.id, "u1")
    assert fetched is not None
    assert fetched.run_count == 1
    assert fetched.last_run_status == "done"
    assert fetched.conversation_id is not None
    assert run_mock.call_args.kwargs["use_tools"] is True
    assert run_mock.call_args.kwargs["enable_web"] is True
