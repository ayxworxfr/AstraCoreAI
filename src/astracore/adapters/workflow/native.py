"""Native workflow orchestrator with Redis-backed checkpoint persistence."""

import asyncio
from typing import Any
from uuid import UUID

from astracore.core.domain.agent import AgentTask, AgentTaskStatus
from astracore.core.ports.workflow import WorkflowOrchestrator, WorkflowState, WorkflowStatus


class NativeWorkflowOrchestrator(WorkflowOrchestrator):
    """Native Python-based workflow orchestrator.

    Workflow state is kept in memory and optionally persisted to Redis as JSON
    snapshots. On restart, pass redis_url to re-hydrate paused workflows.
    """

    def __init__(self, redis_url: str | None = None):
        self._workflows: dict[UUID, WorkflowState] = {}
        self._redis_url = redis_url
        self._redis: Any = None

    def _get_redis(self) -> Any | None:
        """Lazy load Redis client. Returns None if Redis not configured or unavailable."""
        if self._redis is not None:
            return self._redis
        if self._redis_url is None:
            return None
        try:
            from redis.asyncio import Redis

            self._redis = Redis.from_url(self._redis_url, decode_responses=True)
        except ImportError:
            return None
        return self._redis

    def _checkpoint_key(self, workflow_id: UUID) -> str:
        return f"workflow:{workflow_id}:checkpoint"

    async def create_workflow(
        self,
        name: str,
        tasks: list[AgentTask],
        context: dict[str, Any] | None = None,
    ) -> WorkflowState:
        """Create a new workflow."""
        workflow = WorkflowState(
            name=name,
            tasks=tasks,
            context=context or {},
        )
        self._workflows[workflow.workflow_id] = workflow
        return workflow

    async def execute_workflow(
        self,
        workflow_id: UUID,
    ) -> WorkflowState:
        """Execute a workflow sequentially, saving checkpoints on pause."""
        if workflow_id not in self._workflows:
            raise ValueError(f"Workflow {workflow_id} not found")

        workflow = self._workflows[workflow_id]
        workflow.mark_running()

        try:
            for task in workflow.tasks:
                if task.status == AgentTaskStatus.COMPLETED:
                    continue

                workflow.current_task_id = task.task_id
                task.mark_in_progress()

                await self._execute_task(task, workflow.context)

                if task.status == AgentTaskStatus.REQUIRES_APPROVAL:
                    workflow.status = WorkflowStatus.PAUSED
                    await self.save_checkpoint(workflow_id)
                    return workflow

                if task.status == AgentTaskStatus.FAILED:
                    workflow.mark_failed(task.error or "Task failed")
                    return workflow

            workflow.mark_completed({"completed_tasks": len(workflow.tasks)})

        except Exception as e:
            workflow.mark_failed(str(e))

        return workflow

    async def _execute_task(self, task: AgentTask, context: dict[str, Any]) -> None:
        """Placeholder task execution. Override in subclasses for real LLM dispatch."""
        await asyncio.sleep(0.1)
        task.mark_completed(f"Completed: {task.description}")

    async def pause_workflow(
        self,
        workflow_id: UUID,
    ) -> WorkflowState:
        """Pause a workflow."""
        if workflow_id not in self._workflows:
            raise ValueError(f"Workflow {workflow_id} not found")

        workflow = self._workflows[workflow_id]
        workflow.status = WorkflowStatus.PAUSED
        return workflow

    async def resume_workflow(
        self,
        workflow_id: UUID,
    ) -> WorkflowState:
        """Resume a paused workflow, loading from checkpoint if not in memory."""
        if workflow_id not in self._workflows:
            try:
                await self.load_checkpoint(workflow_id)
            except Exception as exc:
                raise ValueError(f"Workflow {workflow_id} not found") from exc

        workflow = self._workflows[workflow_id]
        if workflow.status != WorkflowStatus.PAUSED:
            raise ValueError("Workflow is not paused")

        return await self.execute_workflow(workflow_id)

    async def get_workflow_state(
        self,
        workflow_id: UUID,
    ) -> WorkflowState:
        """Get current workflow state."""
        if workflow_id not in self._workflows:
            raise ValueError(f"Workflow {workflow_id} not found")

        return self._workflows[workflow_id]

    async def save_checkpoint(
        self,
        workflow_id: UUID,
    ) -> None:
        """Persist workflow state to Redis as a JSON snapshot.

        7-day TTL matches typical workflow lifespan.
        No-op if Redis is not configured or unavailable.
        """
        if workflow_id not in self._workflows:
            return

        workflow = self._workflows[workflow_id]
        redis = self._get_redis()
        if redis is None:
            return

        try:
            key = self._checkpoint_key(workflow_id)
            await redis.set(key, workflow.model_dump_json(), ex=604_800)
        except Exception:
            pass  # Checkpoint is best-effort — don't fail the caller

    async def load_checkpoint(
        self,
        workflow_id: UUID,
    ) -> WorkflowState:
        """Load workflow from Redis checkpoint. Falls back to in-memory state.

        Restores the workflow into the in-memory store so subsequent operations work.
        """
        redis = self._get_redis()
        if redis is not None:
            try:
                key = self._checkpoint_key(workflow_id)
                data = await redis.get(key)
                if data:
                    workflow = WorkflowState.model_validate_json(data)
                    self._workflows[workflow.workflow_id] = workflow
                    return workflow
            except Exception:
                pass

        if workflow_id in self._workflows:
            return self._workflows[workflow_id]

        raise ValueError(f"Workflow {workflow_id} checkpoint not found")
