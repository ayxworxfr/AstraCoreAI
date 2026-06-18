"""Scheduled tasks REST API.

Routes (all prefixed by /api/v1/scheduled-tasks in factory.py):
  GET    /                       list tasks (paginated)
  POST   /                       create task
  GET    /_health                 scheduler health
  GET    /{id}                   get task
  PUT    /{id}                   update task
  DELETE /{id}                   delete task
  POST   /{id}/pause             pause task
  POST   /{id}/resume            resume task
  POST   /{id}/run-now           fire immediately
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from astracore.infrastructure.db.models import UserRow
from astracore.modules.auth.dependencies import get_current_user
from astracore.modules.scheduling.application.task_service import (
    ScheduledTaskService,
)
from astracore.modules.scheduling.domain.task import (
    CreateTaskRequest,
    ScheduledTask,
    TaskFilter,
    TaskStatus,
    TriggerType,
    UpdateTaskRequest,
)
from astracore.modules.scheduling.runner import _fire
from astracore.modules.scheduling.scheduler import get_scheduler
from astracore.sdk.config import AstraCoreConfig
from astracore.shared.observability.logger import get_logger
from astracore.shared.security.validator import InputValidator as SecurityValidator

router = APIRouter()
logger = get_logger(__name__)


# ------------------------------------------------------------------
# HTTP I/O schemas
# ------------------------------------------------------------------


class TriggerConfigSchema(BaseModel):
    expr: str | None = None  # cron
    seconds: int | None = None  # interval
    run_at: str | None = None  # date (ISO8601)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        if self.expr is not None:
            d["expr"] = self.expr
        if self.seconds is not None:
            d["seconds"] = self.seconds
        if self.run_at is not None:
            d["run_at"] = self.run_at
        return d


class CreateTaskBody(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=4000)
    trigger_type: TriggerType
    trigger_config: TriggerConfigSchema
    name: str | None = Field(default=None, max_length=128)
    timezone: str | None = None
    model_profile: str | None = None
    use_tools: bool = True
    conversation_id: str | None = None


class UpdateTaskBody(BaseModel):
    name: str | None = Field(default=None, max_length=128)
    prompt: str | None = Field(default=None, min_length=1, max_length=4000)
    trigger_type: TriggerType | None = None
    trigger_config: TriggerConfigSchema | None = None
    timezone: str | None = None
    model_profile: str | None = None
    use_tools: bool | None = None


class ScheduledTaskOut(BaseModel):
    id: str
    user_id: str
    name: str
    prompt: str
    trigger_type: str
    trigger_config: dict[str, Any]
    timezone: str
    status: str
    model_profile: str | None
    use_tools: bool
    conversation_id: str | None
    last_run_id: str | None
    last_run_at: str | None
    last_run_status: str | None
    next_run_at: str | None
    run_count: int
    error_count: int
    last_error: str
    created_at: str
    updated_at: str


class TaskListOut(BaseModel):
    items: list[ScheduledTaskOut]
    total: int
    page: int
    page_size: int


class SchedulerHealthOut(BaseModel):
    scheduler_running: bool


def _task_to_out(t: ScheduledTask) -> ScheduledTaskOut:
    def _fmt(dt: datetime | None) -> str | None:
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC).isoformat()

    return ScheduledTaskOut(
        id=t.id,
        user_id=t.user_id,
        name=t.name,
        prompt=t.prompt,
        trigger_type=t.trigger_type.value,
        trigger_config=t.trigger_config,
        timezone=t.timezone,
        status=t.status.value,
        model_profile=t.model_profile,
        use_tools=t.use_tools,
        conversation_id=t.conversation_id,
        last_run_id=t.last_run_id,
        last_run_at=_fmt(t.last_run_at),
        last_run_status=t.last_run_status,
        next_run_at=_fmt(t.next_run_at),
        run_count=t.run_count,
        error_count=t.error_count,
        last_error=t.last_error,
        created_at=_fmt(t.created_at) or "",
        updated_at=_fmt(t.updated_at) or "",
    )


# ------------------------------------------------------------------
# Dependency helpers
# ------------------------------------------------------------------


def _get_cfg() -> AstraCoreConfig:

    from astracore.sdk.config import AstraCoreConfig  # noqa: PLC0415

    return AstraCoreConfig()


def _get_svc() -> ScheduledTaskService:
    cfg = _get_cfg()
    return ScheduledTaskService(cfg.storage.db_url, cfg.scheduling.default_timezone)


# ------------------------------------------------------------------
# Routes
# ------------------------------------------------------------------


@router.get("/_health", response_model=SchedulerHealthOut)
async def scheduler_health() -> SchedulerHealthOut:
    try:
        scheduler = get_scheduler()
        return SchedulerHealthOut(scheduler_running=scheduler.running)
    except RuntimeError:
        return SchedulerHealthOut(scheduler_running=False)


@router.get("/", response_model=TaskListOut)
async def list_tasks(
    page: int = 1,
    page_size: int = 20,
    status: str | None = None,
    q: str | None = None,
    current_user: UserRow = Depends(get_current_user),
    svc: ScheduledTaskService = Depends(_get_svc),
) -> TaskListOut:
    status_filter = TaskStatus(status) if status in {s.value for s in TaskStatus} else None
    tasks, total = await svc.list_tasks(
        TaskFilter(
            user_id=current_user.id,
            status=status_filter,
            q=q or None,
            page=max(1, page),
            page_size=min(100, max(1, page_size)),
        )
    )
    return TaskListOut(
        items=[_task_to_out(t) for t in tasks],
        total=total,
        page=page,
        page_size=page_size,
    )


class BatchDeleteTasksBody(BaseModel):
    ids: list[str] = Field(min_length=1)


class BatchDeleteTasksOut(BaseModel):
    deleted: int


@router.post("/batch-delete", response_model=BatchDeleteTasksOut)
async def batch_delete_tasks(
    body: BatchDeleteTasksBody,
    current_user: UserRow = Depends(get_current_user),
    svc: ScheduledTaskService = Depends(_get_svc),
) -> BatchDeleteTasksOut:
    deleted = await svc.delete_tasks_batch(body.ids, current_user.id)
    return BatchDeleteTasksOut(deleted=deleted)


@router.post("/", response_model=ScheduledTaskOut, status_code=201)
async def create_task(
    body: CreateTaskBody,
    current_user: UserRow = Depends(get_current_user),
    svc: ScheduledTaskService = Depends(_get_svc),
    cfg: AstraCoreConfig = Depends(_get_cfg),
) -> ScheduledTaskOut:
    is_valid, err_msg = SecurityValidator().validate_user_input(body.prompt)
    if not is_valid:
        raise HTTPException(status_code=400, detail=err_msg or "Invalid prompt")
    try:
        task = await svc.create_task(
            CreateTaskRequest(
                user_id=current_user.id,
                prompt=body.prompt,
                trigger_type=body.trigger_type,
                trigger_config=body.trigger_config.to_dict(),
                name=body.name,
                timezone=body.timezone,
                model_profile=body.model_profile,
                use_tools=body.use_tools,
                conversation_id=body.conversation_id,
            ),
            max_per_user=cfg.scheduling.max_tasks_per_user,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _task_to_out(task)


@router.get("/{task_id}", response_model=ScheduledTaskOut)
async def get_task(
    task_id: str,
    current_user: UserRow = Depends(get_current_user),
    svc: ScheduledTaskService = Depends(_get_svc),
) -> ScheduledTaskOut:
    task = await svc.get_task(task_id, current_user.id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return _task_to_out(task)


@router.put("/{task_id}", response_model=ScheduledTaskOut)
async def update_task(
    task_id: str,
    body: UpdateTaskBody,
    current_user: UserRow = Depends(get_current_user),
    svc: ScheduledTaskService = Depends(_get_svc),
) -> ScheduledTaskOut:
    prompt: str | None = None
    if body.prompt is not None:
        is_valid, err_msg = SecurityValidator().validate_user_input(body.prompt)
        if not is_valid:
            raise HTTPException(status_code=400, detail=err_msg or "Invalid prompt")
        prompt = body.prompt
    try:
        task = await svc.update_task(
            task_id,
            current_user.id,
            UpdateTaskRequest(
                name=body.name,
                prompt=prompt,
                trigger_type=body.trigger_type,
                trigger_config=body.trigger_config.to_dict() if body.trigger_config else None,
                timezone=body.timezone,
                model_profile=body.model_profile,
                use_tools=body.use_tools,
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return _task_to_out(task)


@router.delete("/{task_id}", status_code=204)
async def delete_task(
    task_id: str,
    current_user: UserRow = Depends(get_current_user),
    svc: ScheduledTaskService = Depends(_get_svc),
) -> None:
    deleted = await svc.delete_task(task_id, current_user.id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Task not found")


@router.post("/{task_id}/pause", response_model=ScheduledTaskOut)
async def pause_task(
    task_id: str,
    current_user: UserRow = Depends(get_current_user),
    svc: ScheduledTaskService = Depends(_get_svc),
) -> ScheduledTaskOut:
    task = await svc.pause_task(task_id, current_user.id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return _task_to_out(task)


@router.post("/{task_id}/resume", response_model=ScheduledTaskOut)
async def resume_task(
    task_id: str,
    current_user: UserRow = Depends(get_current_user),
    svc: ScheduledTaskService = Depends(_get_svc),
) -> ScheduledTaskOut:
    task = await svc.resume_task(task_id, current_user.id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return _task_to_out(task)


@router.post("/{task_id}/run-now", response_model=ScheduledTaskOut)
async def run_now(
    task_id: str,
    current_user: UserRow = Depends(get_current_user),
    svc: ScheduledTaskService = Depends(_get_svc),
) -> ScheduledTaskOut:
    task = await svc.get_task(task_id, current_user.id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    # Fire asynchronously; do not block the HTTP response
    asyncio.create_task(_fire(task_id))
    return _task_to_out(task)
