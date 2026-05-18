"""LLM adapter port interface."""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from astracore.core.domain.message import Message, ToolCall


class StreamEventType(StrEnum):
    """Stream event types."""

    TEXT_DELTA = "text_delta"
    THINKING_DELTA = "thinking_delta"
    ROUND_START = "round_start"    # 工具循环每轮开始，前端以此分隔思考块
    THINKING_STOP = "thinking_stop"  # 思考轮结束，携带 duration_ms
    TOOL_CALL = "tool_call"          # LLM 决定调用工具（含 arguments）
    TOOL_RESULT = "tool_result"      # 工具执行完毕，携带结果
    SKILL_MATCH = "skill_match"   # 技能路由命中，metadata: anchor, routed
    ERROR = "error"
    DONE = "done"

    # 子 Agent 事件（metadata 包含 agent_id 字段）
    AGENT_START          = "agent_start"           # 子 Agent 启动
    AGENT_TEXT_DELTA     = "agent_text_delta"       # 子 Agent 文本增量
    AGENT_THINKING_DELTA = "agent_thinking_delta"   # 子 Agent 思考增量
    AGENT_TOOL_CALL      = "agent_tool_call"        # 子 Agent 工具调用
    AGENT_TOOL_RESULT    = "agent_tool_result"      # 子 Agent 工具结果
    AGENT_DONE           = "agent_done"             # 子 Agent 完成


class StreamEvent(BaseModel):
    """Streaming event from LLM."""

    event_id: UUID = Field(default_factory=uuid4)
    event_type: StreamEventType
    content: str = ""
    tool_call: ToolCall | None = None
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class LLMResponse(BaseModel):
    """Complete LLM response."""

    response_id: UUID = Field(default_factory=uuid4)
    content: str
    tool_calls: list[ToolCall] = Field(default_factory=list)
    model: str
    usage: dict[str, int] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class LLMAdapter(ABC):
    """Abstract LLM adapter interface."""

    @abstractmethod
    async def generate(
        self,
        messages: list[Message],
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.7,
        **kwargs: Any,
    ) -> LLMResponse:
        """Generate a complete response."""
        pass

    @abstractmethod
    async def generate_stream(
        self,
        messages: list[Message],
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.7,
        **kwargs: Any,
    ) -> AsyncIterator[StreamEvent]:
        """Generate a streaming response."""
        pass

    @abstractmethod
    async def count_tokens(self, messages: list[Message]) -> int:
        """Count tokens in messages."""
        pass

    @abstractmethod
    def supports_tools(self) -> bool:
        """Check if provider supports tool calling."""
        pass
