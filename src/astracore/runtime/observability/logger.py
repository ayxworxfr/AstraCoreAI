"""Structured logging implementation."""

import json
import logging
from datetime import datetime
from typing import Any

from astracore.core.ports.audit import AuditEvent, AuditEventType, AuditLogger


class StructuredLogger(AuditLogger):
    """Structured JSON logger for audit events."""

    def __init__(self, logger_name: str = "astracore"):
        self.logger = logging.getLogger(logger_name)
        self.logger.setLevel(logging.INFO)

        if not self.logger.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter("%(message)s"))
            self.logger.addHandler(handler)

    async def log_event(self, event: AuditEvent) -> None:
        """Log an audit event as structured JSON."""
        log_data = {
            "event_id": str(event.event_id),
            "event_type": event.event_type.value,
            "session_id": str(event.session_id) if event.session_id else None,
            "user_id": event.user_id,
            "action": event.action,
            "details": event.details,
            "timestamp": event.timestamp.isoformat(),
        }

        self.logger.info(json.dumps(log_data))

    async def query_events(
        self,
        session_id: Any = None,
        user_id: str | None = None,
        event_type: AuditEventType | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        limit: int = 100,
    ) -> list[AuditEvent]:
        """Query audit events (not implemented in simple logger)."""
        return []
