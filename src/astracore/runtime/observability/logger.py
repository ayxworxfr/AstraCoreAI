"""Structured logging implementation."""

import json
import logging
import sys
from contextvars import ContextVar
from datetime import datetime
from typing import Any

from astracore.core.ports.audit import AuditEvent, AuditEventType, AuditLogger

_PACKAGE = "astracore"
_AUDIT_LOGGER_NAME = "astracore.audit"

# 当前请求的 ID，由 RequestLoggingMiddleware 在每次请求开始时写入，
# 请求结束后自动还原（ContextVar token reset）。
# 所有日志记录通过 _ContextFilter 自动附带此字段，无需手动传递。
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)


class _ContextFilter(logging.Filter):
    """将 contextvars 中的 request_id 注入每条 LogRecord。

    在没有请求上下文时（如启动阶段、后台任务）输出 "-" 作为占位符。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get() or "-"
        return True


def setup_logging(level: str | int = "INFO") -> None:
    """Configure the astracore package logger.

    Call once at application startup (e.g., in the FastAPI lifespan).
    Safe to call multiple times — re-calling only adjusts the log level.
    """
    pkg_logger = logging.getLogger(_PACKAGE)
    pkg_logger.setLevel(level)

    if not pkg_logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.addFilter(_ContextFilter())
        handler.setFormatter(
            logging.Formatter(
                # [request_id] 字段由 _ContextFilter 注入，无请求时显示 "-"
                fmt="%(asctime)s %(levelname)-8s [%(request_id)s] %(name)s  %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        pkg_logger.addHandler(handler)
        pkg_logger.propagate = False

    # 审计日志使用独立 logger，输出裸 JSON，不经过应用日志的 formatter，
    # 两种格式彻底分离，方便日志采集工具（fluentd/logstash 等）独立解析。
    audit_logger = logging.getLogger(_AUDIT_LOGGER_NAME)
    if not audit_logger.handlers:
        audit_handler = logging.StreamHandler(sys.stdout)
        audit_handler.setFormatter(logging.Formatter("%(message)s"))
        audit_logger.addHandler(audit_handler)
        audit_logger.propagate = False  # 不向上传播到 astracore logger，避免重复输出


def get_logger(name: str) -> logging.Logger:
    """Return a logger scoped under the astracore package hierarchy."""
    return logging.getLogger(name)


class StructuredLogger(AuditLogger):
    """Structured JSON logger for audit events.

    输出到独立的 astracore.audit logger（裸 JSON），与应用日志格式彻底分离。
    每条审计记录自动附带当前 request_id，便于跨日志关联同一次请求。
    """

    def __init__(self) -> None:
        # 直接使用已由 setup_logging 配置好的 audit logger，不自行添加 handler
        self.logger = logging.getLogger(_AUDIT_LOGGER_NAME)

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
            "request_id": request_id_var.get(),  # 关联同一请求的所有审计事件
        }
        self.logger.info(json.dumps(log_data, ensure_ascii=False))

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
