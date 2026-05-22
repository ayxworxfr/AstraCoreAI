"""Workflow orchestrator port interface."""

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from astracore.modules.agent.domain import AgentTask

# A TaskExecutor receives the task and the accumulated results from already
# completed tasks (task_id str → result str) and returns the task result.
TaskExecutor = Callable[[AgentTask, dict[str, str]], Awaitable[str]]


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
    task_results: dict[str, str] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)
    result: Any = None
    error: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None

    def add_task(self, task: AgentTask) -> None:
        self.tasks.append(task)
        self.updated_at = datetime.now(UTC)

    def mark_running(self) -> None:
        self.status = WorkflowStatus.RUNNING
        self.updated_at = datetime.now(UTC)

    def mark_paused(self) -> None:
        self.status = WorkflowStatus.PAUSED
        self.updated_at = datetime.now(UTC)

    def mark_completed(self, result: Any) -> None:
        self.status = WorkflowStatus.COMPLETED
        self.result = result
        self.completed_at = datetime.now(UTC)
        self.updated_at = datetime.now(UTC)

    def mark_failed(self, error: str) -> None:
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
        executor: TaskExecutor | None = None,
    ) -> WorkflowState:
        """Create a new workflow in PENDING state without executing it.

        ``executor`` can be stored for use by ``execute_workflow`` / ``resume_workflow``.
        """

    @abstractmethod
    async def execute_workflow(
        self,
        workflow_id: UUID,
        executor: TaskExecutor | None = None,
    ) -> WorkflowState:
        """Execute a previously created workflow.

        ``executor`` overrides any stored executor; falls back to a default
        no-op that returns the task description.
        """

    @abstractmethod
    async def get_workflow_state(self, workflow_id: UUID) -> WorkflowState:
        """Return the current (or final) workflow state."""

    @abstractmethod
    async def pause_workflow(self, workflow_id: UUID) -> WorkflowState:
        """Pause a running workflow."""

    @abstractmethod
    async def resume_workflow(self, workflow_id: UUID) -> WorkflowState:
        """Resume a paused workflow, executing remaining pending tasks."""

    @abstractmethod
    async def save_checkpoint(self, workflow_id: UUID) -> None:
        """Persist current workflow state to durable storage (no-op without Redis)."""

    @abstractmethod
    async def load_checkpoint(self, workflow_id: UUID) -> WorkflowState:
        """Load workflow state from durable storage, falling back to in-memory."""
