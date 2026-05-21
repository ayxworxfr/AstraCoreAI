"""Agent domain models."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class AgentRole(StrEnum):
    """Agent role types."""

    PLANNER = "planner"
    EXECUTOR = "executor"
    REVIEWER = "reviewer"


class AgentTaskStatus(StrEnum):
    """Agent task status."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    REQUIRES_APPROVAL = "requires_approval"


class AgentTask(BaseModel):
    """Agent task definition."""

    task_id: UUID = Field(default_factory=uuid4)
    role: AgentRole
    description: str
    context: dict[str, Any] = Field(default_factory=dict)
    status: AgentTaskStatus = AgentTaskStatus.PENDING
    assigned_to: str | None = None
    result: str | None = None
    error: str | None = None
    parent_task_id: UUID | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None

    def mark_in_progress(self) -> None:
        """Mark task as in progress."""
        self.status = AgentTaskStatus.IN_PROGRESS
        self.updated_at = datetime.now(UTC)

    def mark_completed(self, result: str) -> None:
        """Mark task as completed."""
        self.status = AgentTaskStatus.COMPLETED
        self.result = result
        self.completed_at = datetime.now(UTC)
        self.updated_at = datetime.now(UTC)

    def mark_failed(self, error: str) -> None:
        """Mark task as failed."""
        self.status = AgentTaskStatus.FAILED
        self.error = error
        self.updated_at = datetime.now(UTC)

    def require_approval(self) -> None:
        """Mark task as requiring approval."""
        self.status = AgentTaskStatus.REQUIRES_APPROVAL
        self.updated_at = datetime.now(UTC)


class AgentDecision(BaseModel):
    """Agent decision result."""

    decision_id: UUID = Field(default_factory=uuid4)
    task_id: UUID
    role: AgentRole
    action: str
    reasoning: str
    next_steps: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0, default=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
