"""Scheduled task runner — the APScheduler callback and concurrency guard.

When a trigger fires, APScheduler calls the importable async job function. That function:

1. Acquires the global concurrency semaphore (``_RUN_SEMAPHORE``).
2. Skips if the same task is already running (per-task lock via ``_RUNNING_TASKS``).
3. Creates a ``ConversationRow`` (title = "task name (datetime)") + ``ChatRunRow``.
4. Delegates execution to ``run_pipeline_background`` from the shared executor.
5. Updates task stats (last_run_id, run_count, error_count, next_run_at).
6. Marks one-shot date-trigger tasks as finished.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from astracore.infrastructure.db.models import ConversationRow
from astracore.infrastructure.db.session import get_session
from astracore.modules.chat.application.run_executor import run_pipeline_background
from astracore.modules.chat.application.run_factory import create_chat_run_row
from astracore.shared.observability.logger import get_logger

logger = get_logger(__name__)

# Per-task asyncio.Task — prevents overlapping runs of the same task.
_RUNNING_TASKS: dict[str, asyncio.Task[None]] = {}

# Global concurrency cap — initialised in init_runner().
_RUN_SEMAPHORE: asyncio.Semaphore | None = None

# Lazily-resolved references to avoid import cycles at module load time.
_pipeline_factory: Callable[..., Any] | None = None  # () -> ChatPipeline
_config_factory: Callable[..., Any] | None = None  # () -> AstraCoreConfig


def init_runner(
    pipeline_factory: Callable[..., Any],
    config_factory: Callable[..., Any],
    max_concurrent_runs: int = 5,
) -> None:
    """Initialise module globals; called once in the FastAPI lifespan."""
    global _RUN_SEMAPHORE, _pipeline_factory, _config_factory  # noqa: PLW0603
    _RUN_SEMAPHORE = asyncio.Semaphore(max_concurrent_runs)
    _pipeline_factory = pipeline_factory
    _config_factory = config_factory


async def _create_trigger_conversation(
    db_url: str, user_id: str, task_name: str, task_id: str
) -> str:
    """Create a new ConversationRow for this trigger; return its id (= session_id)."""
    session_id = str(uuid4())
    now = datetime.now(UTC)
    ts = now.strftime("%Y-%m-%d %H:%M")
    title = f"{task_name} ({ts})"
    row = ConversationRow(
        id=session_id,
        user_id=user_id,
        title=title,
        created_at=now,
        updated_at=now,
    )
    async with get_session(db_url) as db:
        db.add(row)
        await db.commit()
    return session_id


async def _fire(task_id: str) -> None:
    """Core async callback executed when a scheduled task trigger fires."""
    from uuid import UUID  # noqa: PLC0415

    from astracore.modules.scheduling.application.task_service import (  # noqa: PLC0415
        ScheduledTaskService,
        _compute_next_run,
    )
    from astracore.modules.scheduling.domain.task import (  # noqa: PLC0415
        TaskStatus,
        TriggerType,
    )

    if _pipeline_factory is None or _config_factory is None:
        logger.error("Runner not initialised — call init_runner() in lifespan")
        return

    # Skip if previous run still in progress for this task
    if task_id in _RUNNING_TASKS and not _RUNNING_TASKS[task_id].done():
        logger.info("Skipping trigger for task %s: previous run still active", task_id)
        return

    cfg = _config_factory()
    db_url: str = cfg.storage.db_url

    svc = ScheduledTaskService(db_url, cfg.scheduling.default_timezone)
    task = await svc.get_task(task_id, user_id="__system__")

    # Allow __system__ bypass for internal lookup
    if task is None:
        from astracore.infrastructure.db.models import ScheduledTaskRow  # noqa: PLC0415

        async with get_session(db_url) as db:
            row = await db.get(ScheduledTaskRow, task_id)
            if row is None:
                logger.warning("Task %s not found in DB; skipping", task_id)
                return
            from astracore.modules.scheduling.application.task_service import (  # noqa: PLC0415
                _row_to_domain,
            )

            task = _row_to_domain(row)

    if task.status != TaskStatus.ACTIVE:
        logger.info("Task %s is not active (status=%s); skipping", task_id, task.status)
        return

    semaphore = _RUN_SEMAPHORE
    if semaphore is None:
        logger.error("_RUN_SEMAPHORE not initialised")
        return

    async def _run() -> None:
        async with semaphore:
            session_id_str = await _create_trigger_conversation(
                db_url, task.user_id, task.name, task_id
            )
            session_id = UUID(session_id_str)
            run_row = await create_chat_run_row(
                db_url=db_url,
                session_id=session_id,
                prompt=task.prompt,
                user_id=task.user_id,
                trigger_source="schedule",
                request_payload={
                    "message": task.prompt,
                    "trigger_source": "schedule",
                    "task_id": task_id,
                    "use_tools": task.use_tools,
                    "enable_web": task.use_tools,
                    "model_profile": task.model_profile,
                },
            )

            pipeline = _pipeline_factory()
            result = await run_pipeline_background(
                run_id=run_row.id,
                prompt=task.prompt,
                session_id=session_id,
                pipeline=pipeline,
                db_url=db_url,
                user_id=task.user_id,
                model_profile=task.model_profile,
                use_tools=task.use_tools,
                enable_web=task.use_tools,
            )

            final_status = "done" if result is not None and result.status == "done" else "error"
            error_msg = (result.error if result is not None else "Pipeline returned None") or ""

            next_run = None
            if task.trigger_type != TriggerType.DATE:
                next_run = _compute_next_run(task.trigger_type, task.trigger_config, task.timezone)

            await svc.update_run_stats(
                task_id,
                run_id=run_row.id,
                status=final_status,
                error=error_msg,
                next_run_at=next_run,
                conversation_id=session_id_str,
            )

            if task.trigger_type == TriggerType.DATE:
                await svc.mark_finished(task_id)

            logger.info(
                "Scheduled task %s fired: run_id=%s status=%s",
                task_id,
                run_row.id,
                final_status,
            )

    _RUNNING_TASKS[task_id] = asyncio.create_task(_run())
    try:
        await _RUNNING_TASKS[task_id]
    except Exception:
        logger.exception("Scheduled task %s run raised an exception", task_id)
    finally:
        _RUNNING_TASKS.pop(task_id, None)


async def _sync_fire_job(task_id: str) -> None:
    """Top-level APScheduler callback — importable by pickle for SQLAlchemyJobStore.

    AsyncIOScheduler runs coroutine functions on its own event loop. Keeping this
    as an async top-level function avoids relying on a worker thread event loop.
    """
    await _fire(task_id)


def make_job_func(task_id: str) -> Callable[[], Coroutine[Any, Any, None]]:
    """Kept for backward compatibility; prefer registering _sync_fire_job with kwargs."""
    import functools  # noqa: PLC0415

    return functools.partial(_sync_fire_job, task_id)
