"""Session and context management domain models."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from astracore.core.domain.message import Message


class TokenBudget(BaseModel):
    """Token budget allocation and tracking."""

    max_input_tokens: int = 100_000
    max_output_tokens: int = 4_096
    max_tool_tokens: int = 10_000
    max_memory_tokens: int = 5_000

    current_input_tokens: int = 0
    current_output_tokens: int = 0
    current_tool_tokens: int = 0
    current_memory_tokens: int = 0

    def total_max(self) -> int:
        """Get total max tokens."""
        return (
            self.max_input_tokens
            + self.max_output_tokens
            + self.max_tool_tokens
            + self.max_memory_tokens
        )

    def total_current(self) -> int:
        """Get current total tokens used."""
        return (
            self.current_input_tokens
            + self.current_output_tokens
            + self.current_tool_tokens
            + self.current_memory_tokens
        )

    def available_input_tokens(self) -> int:
        """Get available input tokens."""
        return self.max_input_tokens - self.current_input_tokens

    def is_input_budget_exceeded(self) -> bool:
        """Check if input budget is exceeded."""
        return self.current_input_tokens >= self.max_input_tokens

    def add_input_tokens(self, tokens: int) -> None:
        """Add input tokens to current count."""
        self.current_input_tokens += tokens

    def add_output_tokens(self, tokens: int) -> None:
        """Add output tokens to current count."""
        self.current_output_tokens += tokens


class ContextWindow(BaseModel):
    """Context window management."""

    messages: list[Message] = Field(default_factory=list)
    max_messages: int = 50
    summary: str | None = None
    last_summarized_at: datetime | None = None

    def add_message(self, message: Message) -> None:
        """Add a message to the context window."""
        self.messages.append(message)

    def get_recent_messages(self, count: int) -> list[Message]:
        """Get the most recent N messages."""
        return self.messages[-count:] if count > 0 else []

    def total_tokens(self) -> int:
        """Calculate total tokens in the context window."""
        return sum(msg.token_estimate() for msg in self.messages)

    def should_summarize(self, threshold: int = 80_000) -> bool:
        """Check if summarization is needed."""
        return self.total_tokens() >= threshold or len(self.messages) >= self.max_messages

    def truncate_to_budget(self, max_tokens: int) -> None:
        """Truncate oldest messages to fit within token budget. O(n) — no pop(0) loop."""
        if self.total_tokens() <= max_tokens:
            return

        tokens = self.total_tokens()
        cutoff = 0
        while cutoff < len(self.messages) and tokens > max_tokens:
            tokens -= self.messages[cutoff].token_estimate()
            cutoff += 1
        self.messages = self.messages[cutoff:]


class SessionState(BaseModel):
    """Session state management."""

    session_id: UUID = Field(default_factory=uuid4)
    user_id: str | None = None
    context_window: ContextWindow = Field(default_factory=ContextWindow)
    token_budget: TokenBudget = Field(default_factory=TokenBudget)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def add_message(self, message: Message) -> None:
        """Add a message to the session."""
        self.context_window.add_message(message)
        self.token_budget.add_input_tokens(message.token_estimate())
        self.updated_at = datetime.now(UTC)

    def get_messages(self) -> list[Message]:
        """Get all messages in the session."""
        return self.context_window.messages

    def restore_messages(self, messages: list[Message]) -> None:
        """Restore messages from storage without double-counting tokens.

        Use this when rehydrating a session from Redis/DB.
        Token budget is recalculated from the actual messages, not accumulated.
        """
        self.context_window.messages = list(messages)
        self.token_budget.current_input_tokens = sum(
            m.token_estimate() for m in messages
        )
        self.updated_at = datetime.now(UTC)

    def clear_context(self) -> None:
        """Clear the context window."""
        self.context_window = ContextWindow()
        self.updated_at = datetime.now(UTC)
