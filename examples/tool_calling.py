"""Tool calling example using AstraCore SDK."""

import asyncio
import os
from datetime import datetime

from astracore.core.ports.tool import ToolParameter, ToolParameterType
from astracore.sdk import AstraCoreClient, AstraCoreConfig
from astracore.sdk.config import LLMConfig


def get_current_time(timezone: str = "UTC") -> str:
    """Get current time in specified timezone."""
    return f"Current time in {timezone}: {datetime.utcnow().isoformat()}"


def calculate(operation: str, a: float, b: float) -> str:
    """Perform basic arithmetic operations."""
    operations = {
        "add": lambda x, y: x + y,
        "subtract": lambda x, y: x - y,
        "multiply": lambda x, y: x * y,
        "divide": lambda x, y: x / y if y != 0 else "Error: Division by zero",
    }

    if operation not in operations:
        return f"Error: Unknown operation '{operation}'"

    result = operations[operation](a, b)
    return f"{a} {operation} {b} = {result}"


async def main() -> None:
    """Run tool calling example."""
    config = AstraCoreConfig(
        llm=LLMConfig(
            provider="anthropic",
            api_key=os.getenv("ANTHROPIC_API_KEY", "test-key"),
        )
    )

    client = AstraCoreClient(config)

    client.register_tool(
        name="get_current_time",
        func=get_current_time,
        description="Get the current time in a specified timezone",
        parameters=[
            ToolParameter(
                name="timezone",
                type=ToolParameterType.STRING,
                description="The timezone to get time for",
                required=False,
                default="UTC",
            )
        ],
    )

    client.register_tool(
        name="calculate",
        func=calculate,
        description="Perform basic arithmetic operations",
        parameters=[
            ToolParameter(
                name="operation",
                type=ToolParameterType.STRING,
                description="The operation: add, subtract, multiply, divide",
                required=True,
            ),
            ToolParameter(
                name="a",
                type=ToolParameterType.NUMBER,
                description="First number",
                required=True,
            ),
            ToolParameter(
                name="b",
                type=ToolParameterType.NUMBER,
                description="Second number",
                required=True,
            ),
        ],
    )

    print("=== Tool Calling Example ===\n")

    response = await client.chat(
        message="What time is it now? Also, can you calculate 15 multiplied by 7?",
    )

    print(f"Assistant: {response.content}\n")


if __name__ == "__main__":
    asyncio.run(main())
