"""Audit logger port interface."""

from abc import ABC, abstractmethod
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class AuditEventType(StrEnum):
    """Audit event types."""

    REQUEST_START = "request_start"
    REQUEST_END = "request_end"
    TOOL_EXECUTION = "tool_execution"
    POLICY_APPLIED = "policy_applied"
    ERROR = "error"
    SECURITY_EVENT = "security_event"


class AuditEvent(BaseModel):
    """Audit event model."""

    event_id: UUID = Field(default_factory=uuid4)
    event_type: AuditEventType
    session_id: UUID | None = None
    user_id: str | None = None
    action: str
    details: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AuditLogger(ABC):
    """Abstract audit logger interface."""

    @abstractmethod
    async def log_event(
        self,
        event: AuditEvent,
    ) -> None:
        """Log an audit event."""
        pass

    @abstractmethod
    async def query_events(
        self,
        session_id: UUID | None = None,
        user_id: str | None = None,
        event_type: AuditEventType | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        limit: int = 100,
    ) -> list[AuditEvent]:
        """Query audit events."""
        pass
