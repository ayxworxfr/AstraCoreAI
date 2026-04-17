"""Multi-agent orchestration use case."""

from typing import Any
from uuid import UUID

from astracore.core.domain.agent import AgentDecision, AgentRole, AgentTask
from astracore.core.ports.workflow import WorkflowOrchestrator, WorkflowState


class AgentOrchestrationUseCase:
    """Multi-agent collaboration orchestration."""

    def __init__(self, orchestrator: WorkflowOrchestrator):
        self.orchestrator = orchestrator

    async def create_multi_agent_workflow(
        self,
        objective: str,
        context: dict[str, Any] | None = None,
    ) -> WorkflowState:
        """Create a multi-agent workflow with planner, executor, reviewer."""
        planning_task = AgentTask(
            role=AgentRole.PLANNER,
            description=f"Create a plan to achieve: {objective}",
            context=context or {},
        )

        execution_task = AgentTask(
            role=AgentRole.EXECUTOR,
            description=f"Execute the plan for: {objective}",
            context=context or {},
            parent_task_id=planning_task.task_id,
        )

        review_task = AgentTask(
            role=AgentRole.REVIEWER,
            description=f"Review and validate results for: {objective}",
            context=context or {},
            parent_task_id=execution_task.task_id,
        )

        workflow = await self.orchestrator.create_workflow(
            name=f"Multi-Agent: {objective}",
            tasks=[planning_task, execution_task, review_task],
            context=context or {},
        )

        return workflow

    async def execute_workflow(self, workflow_id: UUID) -> WorkflowState:
        """Execute the multi-agent workflow."""
        return await self.orchestrator.execute_workflow(workflow_id)

    async def pause_for_approval(self, workflow_id: UUID, task_id: UUID) -> WorkflowState:
        """Pause workflow for human approval."""
        workflow = await self.orchestrator.get_workflow_state(workflow_id)

        for task in workflow.tasks:
            if task.task_id == task_id:
                task.require_approval()
                break

        return await self.orchestrator.pause_workflow(workflow_id)

    async def approve_and_continue(self, workflow_id: UUID, task_id: UUID) -> WorkflowState:
        """Approve a task and continue workflow."""
        workflow = await self.orchestrator.get_workflow_state(workflow_id)

        for task in workflow.tasks:
            if task.task_id == task_id:
                task.mark_completed("Approved by human reviewer")
                break

        return await self.orchestrator.resume_workflow(workflow_id)

    async def get_workflow_status(self, workflow_id: UUID) -> WorkflowState:
        """Get current workflow status."""
        return await self.orchestrator.get_workflow_state(workflow_id)

    def create_agent_decision(
        self,
        task: AgentTask,
        action: str,
        reasoning: str,
        next_steps: list[str] | None = None,
        confidence: float = 1.0,
    ) -> AgentDecision:
        """Create an agent decision."""
        return AgentDecision(
            task_id=task.task_id,
            role=task.role,
            action=action,
            reasoning=reasoning,
            next_steps=next_steps or [],
            confidence=confidence,
        )
