"""Tool adapter port interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

__all__ = [
    "ToolParameterType",
    "ToolParameter",
    "ToolDefinition",
    "ToolExecutionResult",
    "ToolAdapter",
    "MutableToolAdapter",
    "ToolError",
    "ToolErrorCode",
]

from pydantic import BaseModel, Field

from astracore.modules.tools.ports.tool_errors import ToolError, ToolErrorCode

if TYPE_CHECKING:
    from astracore.shared.ports.llm import StreamEvent


class ToolParameterType(StrEnum):
    """Tool parameter types."""

    STRING = "string"
    NUMBER = "number"
    BOOLEAN = "boolean"
    OBJECT = "object"
    ARRAY = "array"


class ToolParameter(BaseModel):
    """Tool parameter definition."""

    name: str
    type: ToolParameterType
    description: str
    required: bool = False
    default: Any = None


class ToolDefinition(BaseModel):
    """Tool definition for LLM."""

    name: str
    description: str
    parameters: list[ToolParameter] = Field(default_factory=list)
    requires_confirmation: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolExecutionResult(BaseModel):
    """Result of tool execution.

    On success: ``ok=True``, ``data`` holds the output (str for most builtin tools).
    On failure: ``ok=False``, ``error`` holds the structured ToolError.
    """

    execution_id: UUID = Field(default_factory=uuid4)
    tool_name: str
    ok: bool
    data: Any = None
    error: ToolError | None = None
    execution_time_ms: float
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ToolAdapter(ABC):
    """Abstract tool adapter interface."""

    @abstractmethod
    async def execute(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> ToolExecutionResult:
        """Execute a tool."""
        pass

    @abstractmethod
    async def execute_parallel(
        self,
        tool_calls: list[tuple[str, dict[str, Any]]],
        context: dict[str, Any] | None = None,
    ) -> list[ToolExecutionResult]:
        """Execute multiple tools in parallel."""
        pass

    @abstractmethod
    def get_definitions(self) -> list[ToolDefinition]:
        """Get all tool definitions."""
        pass

    async def execute_streaming(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> AsyncIterator[StreamEvent | ToolExecutionResult]:
        """Execute a tool, optionally yielding intermediate StreamEvents.

        Default implementation wraps execute() — fully backward compatible.
        Override (without abstractmethod) to emit AGENT_* events during execution.
        """
        yield await self.execute(tool_name, arguments, context)

    def is_timeout_managed(self, tool_name: str) -> bool:
        """Return True if this tool manages its own timeout internally.

        When True, tool_loop skips the outer asyncio.timeout wrapper for this tool,
        preventing double-timeout interference. Override in adapters like ParallelAgentTool
        that have per-worker timeouts.
        """
        return False


class MutableToolAdapter(ToolAdapter):
    """ToolAdapter that supports dynamic tool registration at runtime."""

    @abstractmethod
    def register_tool(
        self,
        name: str,
        func: Any,
        description: str,
        parameters: list[ToolParameter],
        requires_confirmation: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Register a new tool.

        ``metadata`` 用于挂载工具级别的运行时约束，常用键：
        - ``max_output_chars``: 单次工具结果截断上限（覆盖全局 ``agent.max_tool_result_chars``）
        - ``timeout_s``: 工具自身超时（覆盖全局 ``policy.timeout.tool_timeout_s``）
        """
        pass
