"""Memory adapter port interface."""

from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from astracore.core.domain.message import Message


class MemoryEntry(BaseModel):
    """Memory entry model."""

    entry_id: UUID = Field(default_factory=uuid4)
    session_id: UUID
    content: str
    memory_type: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime | None = None


class MemoryAdapter(ABC):
    """Abstract memory adapter interface."""

    @abstractmethod
    async def save_short_term(
        self,
        session_id: UUID,
        messages: list[Message],
        ttl_seconds: int = 3600,
    ) -> None:
        """Save short-term memory (Redis)."""
        pass

    @abstractmethod
    async def load_short_term(
        self,
        session_id: UUID,
    ) -> list[Message]:
        """Load short-term memory."""
        pass

    @abstractmethod
    async def save_long_term(
        self,
        session_id: UUID,
        summary: str,
        metadata: dict[str, Any] | None = None,
    ) -> MemoryEntry:
        """Save long-term memory (PostgreSQL)."""
        pass

    @abstractmethod
    async def load_long_term(
        self,
        session_id: UUID,
        limit: int = 10,
    ) -> list[MemoryEntry]:
        """Load long-term memory entries."""
        pass

    @abstractmethod
    async def search_memory(
        self,
        query: str,
        session_id: UUID | None = None,
        limit: int = 5,
    ) -> list[MemoryEntry]:
        """Search memory entries."""
        pass

    @abstractmethod
    async def delete_session_memory(
        self,
        session_id: UUID,
    ) -> None:
        """Delete all memory for a session."""
        pass
