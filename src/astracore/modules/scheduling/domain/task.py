"""Scheduling domain types — pure data, no I/O."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class TriggerType(StrEnum):
    CRON = "cron"
    INTERVAL = "interval"
    DATE = "date"


class TaskStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    FINISHED = "finished"


@dataclass
class ScheduledTask:
    id: str
    user_id: str
    name: str
    prompt: str
    trigger_type: TriggerType
    trigger_config: dict[str, Any]
    timezone: str
    status: TaskStatus
    model_profile: str | None
    use_tools: bool
    conversation_id: str | None
    last_run_id: str | None
    last_run_at: datetime | None
    last_run_status: str | None
    next_run_at: datetime | None
    run_count: int
    error_count: int
    last_error: str
    created_at: datetime
    updated_at: datetime


@dataclass
class CreateTaskRequest:
    user_id: str
    prompt: str
    trigger_type: TriggerType
    trigger_config: dict[str, Any]
    name: str | None = None
    timezone: str | None = None
    model_profile: str | None = None
    use_tools: bool = True
    conversation_id: str | None = None


@dataclass
class UpdateTaskRequest:
    name: str | None = None
    prompt: str | None = None
    trigger_type: TriggerType | None = None
    trigger_config: dict[str, Any] | None = None
    timezone: str | None = None
    model_profile: str | None = None
    use_tools: bool | None = None


@dataclass
class TaskFilter:
    user_id: str
    status: TaskStatus | None = None
    q: str | None = None
    page: int = 1
    page_size: int = 20
    fields: list[str] = field(default_factory=list)
