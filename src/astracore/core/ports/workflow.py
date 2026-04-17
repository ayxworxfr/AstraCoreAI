"""Workflow orchestrator port interface."""

from abc import ABC, abstractmethod
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from astracore.core.domain.agent import AgentTask


class WorkflowStatus(StrEnum):
    """Workflow status."""

    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


class WorkflowState(BaseModel):
    """Workflow execution state."""

    workflow_id: UUID = Field(default_factory=uuid4)
    name: str
    status: WorkflowStatus = WorkflowStatus.PENDING
    tasks: list[AgentTask] = Field(default_factory=list)
    current_task_id: UUID | None = None
    context: dict[str, Any] = Field(default_factory=dict)
    result: Any = None
    error: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None

    def add_task(self, task: AgentTask) -> None:
        """Add a task to the workflow."""
        self.tasks.append(task)
        self.updated_at = datetime.now(UTC)

    def mark_running(self) -> None:
        """Mark workflow as running."""
        self.status = WorkflowStatus.RUNNING
        self.updated_at = datetime.now(UTC)

    def mark_completed(self, result: Any) -> None:
        """Mark workflow as completed."""
        self.status = WorkflowStatus.COMPLETED
        self.result = result
        self.completed_at = datetime.now(UTC)
        self.updated_at = datetime.now(UTC)

    def mark_failed(self, error: str) -> None:
        """Mark workflow as failed."""
        self.status = WorkflowStatus.FAILED
        self.error = error
        self.updated_at = datetime.now(UTC)


class WorkflowOrchestrator(ABC):
    """Abstract workflow orchestrator interface."""

    @abstractmethod
    async def create_workflow(
        self,
        name: str,
        tasks: list[AgentTask],
        context: dict[str, Any] | None = None,
    ) -> WorkflowState:
        """Create a new workflow."""
        pass

    @abstractmethod
    async def execute_workflow(
        self,
        workflow_id: UUID,
    ) -> WorkflowState:
        """Execute a workflow."""
        pass

    @abstractmethod
    async def pause_workflow(
        self,
        workflow_id: UUID,
    ) -> WorkflowState:
        """Pause a workflow."""
        pass

    @abstractmethod
    async def resume_workflow(
        self,
        workflow_id: UUID,
    ) -> WorkflowState:
        """Resume a paused workflow."""
        pass

    @abstractmethod
    async def get_workflow_state(
        self,
        workflow_id: UUID,
    ) -> WorkflowState:
        """Get current workflow state."""
        pass

    @abstractmethod
    async def save_checkpoint(
        self,
        workflow_id: UUID,
    ) -> None:
        """Save workflow checkpoint for recovery."""
        pass

    @abstractmethod
    async def load_checkpoint(
        self,
        workflow_id: UUID,
    ) -> WorkflowState:
        """Load workflow from checkpoint."""
        pass
