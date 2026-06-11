"""Structured memory domain models."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class MemoryScope(StrEnum):
    SESSION = "session"
    PROJECT = "project"
    USER = "user"
    GLOBAL = "global"


class MemoryType(StrEnum):
    FACT = "fact"
    PREFERENCE = "preference"
    DECISION = "decision"
    CONSTRAINT = "constraint"
    STATE = "state"
    PLAN = "plan"
    SUMMARY = "summary"
    LESSON = "lesson"
    PROCEDURE = "procedure"


class MemoryStatus(StrEnum):
    ACTIVE = "active"
    STALE = "stale"
    ARCHIVED = "archived"
    REJECTED = "rejected"


class Project(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    root_paths: list[str] = Field(default_factory=list)
    description: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ConversationProjectBinding(BaseModel):
    conversation_id: UUID
    project_id: str
    locked: bool = False
    source: str = "manual"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class StructuredMemory(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    scope: MemoryScope
    type: MemoryType
    content: str
    subject: str = ""
    summary: str = ""
    session_id: UUID | None = None
    conversation_id: UUID | None = None
    project_id: str | None = None
    user_id: str = "default"
    source_run_id: str | None = None
    importance: int = Field(default=3, ge=1, le=5)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    status: MemoryStatus = MemoryStatus.ACTIVE
    locked: bool = False
    use_count: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_used_at: datetime | None = None
