"""Multi-agent workflow example using AstraCore SDK."""

import asyncio
import os

from astracore.core.domain.agent import AgentRole, AgentTask
from astracore.sdk import AstraCoreClient, AstraCoreConfig
from astracore.sdk.config import LLMConfig


async def main() -> None:
    """Run multi-agent workflow example."""
    config = AstraCoreConfig(
        llm=LLMConfig(
            provider="anthropic",
            api_key=os.getenv("ANTHROPIC_API_KEY", "test-key"),
        )
    )

    client = AstraCoreClient(config)

    print("=== Multi-Agent Workflow Example ===\n")

    objective = "Create a comprehensive report on AI framework design patterns"

    print(f"Objective: {objective}\n")

    workflow = await client.create_agent_workflow(objective=objective)

    print(f"Workflow created: {workflow.workflow_id}")
    print(f"Tasks: {len(workflow.tasks)}\n")

    for i, task in enumerate(workflow.tasks, 1):
        print(f"{i}. {task.role.value}: {task.description}")

    print("\nExecuting workflow...\n")

    result = await client.execute_workflow(workflow.workflow_id)

    print(f"Workflow status: {result.status.value}")
    print(f"Completed tasks: {sum(1 for t in result.tasks if t.status.value == 'completed')}")

    if result.status.value == "completed":
        print("\n=== Results ===")
        for task in result.tasks:
            if task.result:
                print(f"\n{task.role.value}:")
                print(f"  {task.result}")


if __name__ == "__main__":
    asyncio.run(main())
