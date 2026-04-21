"""Structured logging implementation."""

import json
import logging
import sys
from datetime import datetime
from typing import Any

from astracore.core.ports.audit import AuditEvent, AuditEventType, AuditLogger

_PACKAGE = "astracore"


def setup_logging(level: str | int = "INFO") -> None:
    """Configure the astracore package logger.

    Call once at application startup (e.g., in the FastAPI lifespan).
    Safe to call multiple times — re-calling only adjusts the log level.
    """
    pkg_logger = logging.getLogger(_PACKAGE)
    pkg_logger.setLevel(level)

    if pkg_logger.handlers:
        return

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)-8s %(name)s  %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    pkg_logger.addHandler(handler)
    pkg_logger.propagate = False


def get_logger(name: str) -> logging.Logger:
    """Return a logger scoped under the astracore package hierarchy."""
    return logging.getLogger(name)


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
