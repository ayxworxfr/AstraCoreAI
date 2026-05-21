"""Message domain models."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class MessageRole(StrEnum):
    """Message role enumeration."""

    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class ToolCall(BaseModel):
    """Tool call request from LLM."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    arguments: dict[str, Any]
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ToolResult(BaseModel):
    """Tool execution result."""

    tool_call_id: str
    name: str
    content: str
    is_error: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Message(BaseModel):
    """Unified message model across the framework."""

    id: UUID = Field(default_factory=uuid4)
    role: MessageRole
    content: str
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_results: list[ToolResult] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def has_tool_calls(self) -> bool:
        """Check if message contains tool calls."""
        return len(self.tool_calls) > 0

    def has_tool_results(self) -> bool:
        """Check if message contains tool results."""
        return len(self.tool_results) > 0

    def token_estimate(self) -> int:
        """Rough token estimation (4 chars per token average)."""
        base_tokens = len(self.content) // 4
        tool_tokens = sum(len(str(tc.arguments)) // 4 for tc in self.tool_calls)
        result_tokens = sum(len(tr.content) // 4 for tr in self.tool_results)
        return base_tokens + tool_tokens + result_tokens
