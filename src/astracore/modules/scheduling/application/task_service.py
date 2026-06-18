"""Scheduled task CRUD and APScheduler synchronisation."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import delete, func, select

from astracore.infrastructure.db.models import ScheduledTaskRow
from astracore.infrastructure.db.session import get_session
from astracore.modules.scheduling.domain.task import (
    CreateTaskRequest,
    ScheduledTask,
    TaskFilter,
    TaskStatus,
    TriggerType,
    UpdateTaskRequest,
)
from astracore.shared.observability.logger import get_logger

logger = get_logger(__name__)


def _to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _parse_date_run_at(run_at_raw: object, timezone: str) -> datetime:
    run_at = datetime.fromisoformat(str(run_at_raw))
    if run_at.tzinfo is None:
        run_at = run_at.replace(tzinfo=ZoneInfo(timezone))
    return run_at.astimezone(UTC)


def _row_to_domain(row: ScheduledTaskRow) -> ScheduledTask:
    return ScheduledTask(
        id=row.id,
        user_id=row.user_id,
        name=row.name,
        prompt=row.prompt,
        trigger_type=TriggerType(row.trigger_type),
        trigger_config=dict(row.trigger_config),
        timezone=row.timezone,
        status=TaskStatus(row.status),
        model_profile=row.model_profile,
        use_tools=row.use_tools,
        conversation_id=row.conversation_id,
        last_run_id=row.last_run_id,
        last_run_at=row.last_run_at,
        last_run_status=row.last_run_status,
        next_run_at=row.next_run_at,
        run_count=row.run_count,
        error_count=row.error_count,
        last_error=row.last_error,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _build_apscheduler_trigger(
    trigger_type: TriggerType,
    trigger_config: dict[str, Any],
    timezone: str,
) -> tuple[str, dict[str, Any]]:
    """Return (trigger_kind, kwargs) for apscheduler add_job()."""
    from apscheduler.triggers.cron import CronTrigger  # noqa: PLC0415
    from apscheduler.triggers.date import DateTrigger  # noqa: PLC0415

    if trigger_type == TriggerType.CRON:
        expr = str(trigger_config.get("expr", ""))
        # Validates the expression; raises ValueError on invalid crontab
        CronTrigger.from_crontab(expr, timezone=timezone)
        return "cron", {"trigger": CronTrigger.from_crontab(expr, timezone=timezone)}

    if trigger_type == TriggerType.INTERVAL:
        seconds = int(trigger_config.get("seconds", 0))
        if seconds <= 0:
            raise ValueError("interval trigger requires seconds > 0")
        return "interval", {"seconds": seconds}

    if trigger_type == TriggerType.DATE:
        run_at_raw = trigger_config.get("run_at")
        if not run_at_raw:
            raise ValueError("date trigger requires run_at (ISO8601)")
        run_at = _parse_date_run_at(run_at_raw, timezone)
        DateTrigger(run_date=run_at)  # validates the datetime
        return "date", {"run_date": run_at}

    raise ValueError(f"Unknown trigger_type: {trigger_type}")


def _compute_next_run(
    trigger_type: TriggerType,
    trigger_config: dict[str, Any],
    timezone: str,
) -> datetime | None:
    """Compute the next fire time without touching APScheduler's internal state."""
    try:
        from apscheduler.triggers.cron import CronTrigger  # noqa: PLC0415
        from apscheduler.triggers.interval import IntervalTrigger  # noqa: PLC0415

        now = datetime.now(UTC)
        if trigger_type == TriggerType.CRON:
            t = CronTrigger.from_crontab(trigger_config["expr"], timezone=timezone)
            next_run = cast(datetime | None, t.get_next_fire_time(None, now))
            return _to_utc(next_run) if next_run else None
        if trigger_type == TriggerType.INTERVAL:
            t = IntervalTrigger(seconds=int(trigger_config["seconds"]))
            next_run = cast(datetime | None, t.get_next_fire_time(None, now))
            return _to_utc(next_run) if next_run else None
        if trigger_type == TriggerType.DATE:
            return _parse_date_run_at(trigger_config["run_at"], timezone)
    except Exception:
        return None
    return None


class ScheduledTaskService:
    """Application-layer use-cases for scheduled tasks."""

    def __init__(self, db_url: str, default_timezone: str = "Asia/Shanghai") -> None:
        self._db_url = db_url
        self._default_timezone = default_timezone

    async def count_user_tasks(self, user_id: str) -> int:
        async with get_session(self._db_url) as db:
            result = await db.execute(
                select(func.count())
                .select_from(ScheduledTaskRow)
                .where(
                    ScheduledTaskRow.user_id == user_id,
                    ScheduledTaskRow.status != TaskStatus.FINISHED.value,
                )
            )
            return result.scalar_one()

    async def list_tasks(self, f: TaskFilter) -> tuple[list[ScheduledTask], int]:
        async with get_session(self._db_url) as db:
            q = select(ScheduledTaskRow).where(ScheduledTaskRow.user_id == f.user_id)
            if f.status is not None:
                q = q.where(ScheduledTaskRow.status == f.status.value)
            if f.q:
                pattern = f"%{f.q.lower()}%"
                q = q.where(
                    func.lower(ScheduledTaskRow.name).like(pattern)
                    | func.lower(ScheduledTaskRow.prompt).like(pattern)
                )
            total_result = await db.execute(select(func.count()).select_from(q.subquery()))
            total = total_result.scalar_one()
            rows = (
                (
                    await db.execute(
                        q.order_by(ScheduledTaskRow.created_at.desc())
                        .offset((f.page - 1) * f.page_size)
                        .limit(f.page_size)
                    )
                )
                .scalars()
                .all()
            )
            return [_row_to_domain(r) for r in rows], total

    async def delete_tasks_batch(self, ids: list[str], user_id: str) -> int:
        """Delete multiple tasks in one SQL statement and remove their APScheduler jobs."""
        if not ids:
            return 0
        async with get_session(self._db_url) as db:
            stmt = (
                delete(ScheduledTaskRow)
                .where(
                    ScheduledTaskRow.id.in_(ids),
                    ScheduledTaskRow.user_id == user_id,
                )
                .returning(ScheduledTaskRow.id)
            )
            result = await db.execute(stmt)
            deleted_ids = [row[0] for row in result.fetchall()]
            await db.commit()
        for task_id in deleted_ids:
            self._remove_from_scheduler(task_id)
        return len(deleted_ids)

    async def get_task(self, task_id: str, user_id: str) -> ScheduledTask | None:
        async with get_session(self._db_url) as db:
            row = await db.get(ScheduledTaskRow, task_id)
            if row is None or row.user_id != user_id:
                return None
            return _row_to_domain(row)

    async def create_task(self, req: CreateTaskRequest, max_per_user: int = 50) -> ScheduledTask:
        """Create a task, validate trigger, and register with APScheduler."""
        tz = req.timezone or self._default_timezone
        try:
            import zoneinfo  # noqa: PLC0415

            zoneinfo.ZoneInfo(tz)
        except (ZoneInfoNotFoundError, KeyError) as exc:
            raise ValueError(f"Unknown timezone: {tz}") from exc

        _build_apscheduler_trigger(req.trigger_type, req.trigger_config, tz)  # validate

        active_count = await self.count_user_tasks(req.user_id)
        if active_count >= max_per_user:
            raise ValueError(
                f"max_tasks_per_user limit reached ({max_per_user}); "
                "delete or finish existing tasks first"
            )

        name = req.name or (req.prompt[:24] + ("…" if len(req.prompt) > 24 else ""))
        next_run = _compute_next_run(req.trigger_type, req.trigger_config, tz)
        task_id = str(uuid4())
        now = datetime.now(UTC)
        row = ScheduledTaskRow(
            id=task_id,
            user_id=req.user_id,
            name=name,
            prompt=req.prompt,
            trigger_type=req.trigger_type.value,
            trigger_config=req.trigger_config,
            timezone=tz,
            status=TaskStatus.ACTIVE.value,
            model_profile=req.model_profile,
            use_tools=req.use_tools,
            conversation_id=req.conversation_id,
            next_run_at=next_run,
            created_at=now,
            updated_at=now,
        )
        async with get_session(self._db_url) as db:
            db.add(row)
            await db.commit()
            await db.refresh(row)

        task = _row_to_domain(row)
        self._register_with_scheduler(task)
        return task

    async def update_task(
        self, task_id: str, user_id: str, req: UpdateTaskRequest
    ) -> ScheduledTask | None:
        async with get_session(self._db_url) as db:
            row = await db.get(ScheduledTaskRow, task_id)
            if row is None or row.user_id != user_id:
                return None

            if req.name is not None:
                row.name = req.name
            if req.prompt is not None:
                row.prompt = req.prompt
            if req.model_profile is not None:
                row.model_profile = req.model_profile
            if req.use_tools is not None:
                row.use_tools = req.use_tools

            if req.trigger_type is not None or req.trigger_config is not None:
                new_type = req.trigger_type or TriggerType(row.trigger_type)
                new_cfg = req.trigger_config or dict(row.trigger_config)
                tz = req.timezone or row.timezone
                _build_apscheduler_trigger(new_type, new_cfg, tz)  # validate
                row.trigger_type = new_type.value
                row.trigger_config = new_cfg
                if req.timezone:
                    row.timezone = req.timezone
                row.next_run_at = _compute_next_run(new_type, new_cfg, row.timezone)

            row.updated_at = datetime.now(UTC)
            await db.commit()
            await db.refresh(row)

        task = _row_to_domain(row)
        self._remove_from_scheduler(task_id)
        if task.status == TaskStatus.ACTIVE:
            self._register_with_scheduler(task)
        return task

    async def pause_task(self, task_id: str, user_id: str) -> ScheduledTask | None:
        return await self._set_status(task_id, user_id, TaskStatus.PAUSED)

    async def resume_task(self, task_id: str, user_id: str) -> ScheduledTask | None:
        task = await self._set_status(task_id, user_id, TaskStatus.ACTIVE)
        if task is not None:
            self._register_with_scheduler(task)
        return task

    async def delete_task(self, task_id: str, user_id: str) -> bool:
        async with get_session(self._db_url) as db:
            row = await db.get(ScheduledTaskRow, task_id)
            if row is None or row.user_id != user_id:
                return False
            await db.delete(row)
            await db.commit()
        self._remove_from_scheduler(task_id)
        return True

    async def update_run_stats(
        self,
        task_id: str,
        *,
        run_id: str,
        status: str,
        error: str = "",
        next_run_at: datetime | None = None,
        conversation_id: str | None = None,
    ) -> None:
        """Update last-run metadata after a trigger fires."""
        async with get_session(self._db_url) as db:
            row = await db.get(ScheduledTaskRow, task_id)
            if row is None:
                return
            row.last_run_id = run_id
            row.last_run_at = datetime.now(UTC)
            row.last_run_status = status
            if conversation_id:
                row.conversation_id = conversation_id
            if next_run_at:
                row.next_run_at = next_run_at
            row.run_count += 1
            if status == "error":
                row.error_count += 1
                row.last_error = error
            else:
                row.last_error = ""
            row.updated_at = datetime.now(UTC)
            await db.commit()

    async def mark_finished(self, task_id: str) -> None:
        """Mark a one-shot date-trigger task as finished after it fires."""
        await self._set_status(
            task_id, user_id="__system__", status=TaskStatus.FINISHED, skip_owner_check=True
        )

    async def load_all_active_tasks(self) -> list[ScheduledTask]:
        """Load all active tasks — used on startup to restore APScheduler jobs."""
        async with get_session(self._db_url) as db:
            result = await db.execute(
                select(ScheduledTaskRow).where(ScheduledTaskRow.status == TaskStatus.ACTIVE.value)
            )
            return [_row_to_domain(r) for r in result.scalars().all()]

    # ------------------------------------------------------------------
    # APScheduler helpers
    # ------------------------------------------------------------------

    def _register_with_scheduler(self, task: ScheduledTask) -> None:
        """Add or replace the APScheduler job for this task."""
        try:
            from astracore.modules.scheduling.runner import _sync_fire_job  # noqa: PLC0415
            from astracore.modules.scheduling.scheduler import get_scheduler  # noqa: PLC0415

            scheduler = get_scheduler()
            trigger_kind, trigger_kwargs = _build_apscheduler_trigger(
                task.trigger_type, task.trigger_config, task.timezone
            )
            trigger = trigger_kwargs.get("trigger")

            # _sync_fire_job is a top-level module function, so pickle can resolve it
            # by its fully-qualified name — required for SQLAlchemyJobStore serialization.
            job_kwargs: dict[str, Any] = {
                "id": task.id,
                "replace_existing": True,
                "name": task.name,
                "kwargs": {"task_id": task.id},
            }
            if trigger is not None:
                scheduler.add_job(_sync_fire_job, trigger=trigger, **job_kwargs)
            elif trigger_kind == "interval":
                scheduler.add_job(
                    _sync_fire_job,
                    trigger="interval",
                    seconds=trigger_kwargs["seconds"],
                    **job_kwargs,
                )
            elif trigger_kind == "date":
                scheduler.add_job(
                    _sync_fire_job,
                    trigger="date",
                    run_date=trigger_kwargs["run_date"],
                    **job_kwargs,
                )
        except RuntimeError:
            # Scheduler not yet initialised (e.g. during tests) — skip silently
            logger.debug("Scheduler not initialised; skipping job registration for %s", task.id)
        except Exception:
            logger.exception("Failed to register APScheduler job for task %s", task.id)

    def _remove_from_scheduler(self, task_id: str) -> None:
        try:
            from astracore.modules.scheduling.scheduler import get_scheduler  # noqa: PLC0415

            get_scheduler().remove_job(task_id)
        except Exception:
            pass  # job may not exist; that's fine

    async def _set_status(
        self,
        task_id: str,
        user_id: str,
        status: TaskStatus,
        skip_owner_check: bool = False,
    ) -> ScheduledTask | None:
        async with get_session(self._db_url) as db:
            row = await db.get(ScheduledTaskRow, task_id)
            if row is None:
                return None
            if not skip_owner_check and row.user_id != user_id:
                return None
            row.status = status.value
            if status in {TaskStatus.PAUSED, TaskStatus.FINISHED}:
                row.next_run_at = None
            row.updated_at = datetime.now(UTC)
            await db.commit()
            await db.refresh(row)

        task = _row_to_domain(row)
        if status == TaskStatus.PAUSED:
            self._remove_from_scheduler(task_id)
        return task
