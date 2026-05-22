"""Native in-memory DAG workflow orchestrator.

Execution model:
1. Build a DAG from ``AgentTask.depends_on`` edges.
2. Compute topological layers (Kahn's algorithm) — tasks within the same
   layer have no dependency on each other and are executed in parallel via
   ``asyncio.gather``.
3. Before executing a task, evaluate its optional ``condition`` expression
   against the accumulated ``task_results``; falsy → mark SKIPPED.
4. Already-completed tasks are skipped so that ``resume_workflow`` can re-use
   the same layer structure without re-running finished work.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from typing import Any
from uuid import UUID

from astracore.modules.agent.domain import AgentTask, AgentTaskStatus
from astracore.modules.agent.ports.workflow import (
    TaskExecutor,
    WorkflowOrchestrator,
    WorkflowState,
    WorkflowStatus,
)
from astracore.shared.observability.logger import get_logger

_logger = get_logger(__name__)


async def _default_executor(task: AgentTask, task_results: dict[str, str]) -> str:  # noqa: ARG001
    return task.description


def _eval_condition(expr: str, task_results: dict[str, str], context: dict[str, Any]) -> bool:
    """Evaluate a condition expression in a restricted namespace.

    Returns True on any exception so that misconfigured conditions don't
    silently skip tasks.
    """
    namespace = {"task_results": task_results, "context": context, "__builtins__": {}}
    try:
        return bool(eval(expr, namespace))  # noqa: S307
    except Exception:
        _logger.warning("Condition expression %r raised an exception; treating as True", expr)
        return True


def _topo_layers(tasks: list[AgentTask]) -> list[list[AgentTask]]:
    """Return tasks grouped into dependency layers via Kahn's algorithm.

    Raises ``ValueError`` if a cycle is detected.
    """
    id_to_task: dict[UUID, AgentTask] = {t.task_id: t for t in tasks}
    in_degree: dict[UUID, int] = {t.task_id: 0 for t in tasks}
    successors: dict[UUID, list[UUID]] = defaultdict(list)

    for task in tasks:
        for dep_id in task.depends_on:
            if dep_id not in id_to_task:
                raise ValueError(f"Task {task.task_id} depends on unknown task {dep_id}")
            in_degree[task.task_id] += 1
            successors[dep_id].append(task.task_id)

    queue: deque[UUID] = deque(tid for tid, deg in in_degree.items() if deg == 0)
    layers: list[list[AgentTask]] = []
    processed = 0

    while queue:
        layer_ids = list(queue)
        queue.clear()
        layers.append([id_to_task[tid] for tid in layer_ids])
        for tid in layer_ids:
            processed += 1
            for succ_id in successors[tid]:
                in_degree[succ_id] -= 1
                if in_degree[succ_id] == 0:
                    queue.append(succ_id)

    if processed != len(tasks):
        raise ValueError("Workflow DAG contains a cycle")

    return layers


class NativeWorkflowOrchestrator(WorkflowOrchestrator):
    """In-memory DAG workflow orchestrator."""

    def __init__(self) -> None:
        self._workflows: dict[UUID, WorkflowState] = {}
        self._executors: dict[UUID, TaskExecutor] = {}

    # ------------------------------------------------------------------
    # WorkflowOrchestrator interface
    # ------------------------------------------------------------------

    async def create_workflow(
        self,
        name: str,
        tasks: list[AgentTask],
        context: dict[str, Any] | None = None,
        executor: TaskExecutor | None = None,
    ) -> WorkflowState:
        """Create a workflow in PENDING state without executing it."""
        workflow = WorkflowState(name=name, tasks=tasks, context=context or {})
        self._workflows[workflow.workflow_id] = workflow
        self._executors[workflow.workflow_id] = executor or _default_executor
        return workflow

    async def execute_workflow(
        self,
        workflow_id: UUID,
        executor: TaskExecutor | None = None,
    ) -> WorkflowState:
        """Execute a previously created workflow."""
        if workflow_id not in self._workflows:
            raise ValueError(f"Workflow {workflow_id} not found")
        workflow = self._workflows[workflow_id]
        effective = executor or self._executors.get(workflow_id) or _default_executor
        return await self._run_workflow(workflow, effective)

    async def get_workflow_state(self, workflow_id: UUID) -> WorkflowState:
        if workflow_id not in self._workflows:
            raise ValueError(f"Workflow {workflow_id} not found")
        return self._workflows[workflow_id]

    async def pause_workflow(self, workflow_id: UUID) -> WorkflowState:
        if workflow_id not in self._workflows:
            raise ValueError(f"Workflow {workflow_id} not found")
        workflow = self._workflows[workflow_id]
        workflow.mark_paused()
        return workflow

    async def resume_workflow(self, workflow_id: UUID) -> WorkflowState:
        if workflow_id not in self._workflows:
            raise ValueError(f"Workflow {workflow_id} not found")
        workflow = self._workflows[workflow_id]
        if workflow.status != WorkflowStatus.PAUSED:
            raise ValueError(f"Workflow {workflow_id} is not paused")
        executor = self._executors.get(workflow_id) or _default_executor
        return await self._run_workflow(workflow, executor)

    async def save_checkpoint(self, workflow_id: UUID) -> None:
        """No-op: Redis is not configured."""

    async def load_checkpoint(self, workflow_id: UUID) -> WorkflowState:
        """Return in-memory state; raise if not found."""
        if workflow_id not in self._workflows:
            raise ValueError(f"checkpoint not found for workflow {workflow_id}")
        return self._workflows[workflow_id]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _run_workflow(
        self,
        workflow: WorkflowState,
        executor: TaskExecutor,
    ) -> WorkflowState:
        workflow.mark_running()
        try:
            layers = _topo_layers(workflow.tasks)
        except ValueError as exc:
            workflow.mark_failed(str(exc))
            return workflow

        try:
            for layer in layers:
                await self._run_layer(layer, workflow, executor)
                if workflow.status in (WorkflowStatus.FAILED, WorkflowStatus.PAUSED):
                    return workflow
        except Exception as exc:
            _logger.exception("Workflow %s raised an unexpected error", workflow.workflow_id)
            workflow.mark_failed(str(exc))
            return workflow

        completed = sum(1 for t in workflow.tasks if t.status == AgentTaskStatus.COMPLETED)
        skipped = sum(1 for t in workflow.tasks if t.status == AgentTaskStatus.SKIPPED)
        workflow.mark_completed({"completed_tasks": completed, "skipped_tasks": skipped})
        return workflow

    async def _run_layer(
        self,
        layer: list[AgentTask],
        workflow: WorkflowState,
        executor: TaskExecutor,
    ) -> None:
        await asyncio.gather(*[self._run_task(task, workflow, executor) for task in layer])

    async def _run_task(
        self,
        task: AgentTask,
        workflow: WorkflowState,
        executor: TaskExecutor,
    ) -> None:
        if task.status == AgentTaskStatus.COMPLETED:
            return

        if task.condition is not None:
            should_run = _eval_condition(task.condition, workflow.task_results, workflow.context)
            if not should_run:
                _logger.debug("Task %s skipped (condition %r)", task.task_id, task.condition)
                task.mark_skipped()
                return

        task.mark_in_progress()
        try:
            result = await executor(task, workflow.task_results)
            task.mark_completed(result)
            workflow.task_results[str(task.task_id)] = result
            workflow.updated_at = task.updated_at
        except Exception as exc:
            _logger.exception("Task %s failed", task.task_id)
            task.mark_failed(str(exc))
            workflow.mark_failed(f"Task {task.task_id} failed: {exc}")
