"""Lightweight span-based tracer wired into the HookRegistry.

No OTel dependency.  Spans are emitted as structured JSON via the standard
logger at DEBUG level so they can be collected by any log aggregator.

Usage::

    from astracore.shared.observability.tracing import Tracer
    from astracore.shared.observability.hooks import HookRegistry

    registry = HookRegistry()
    tracer = Tracer(session_id="abc-123")
    tracer.register_hooks(registry)
    # Now hooks automatically record spans for every LLM/tool call.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from astracore.shared.observability.hooks import (
    HookRegistry,
    LLMCallInput,
    LLMCallOutput,
    ToolCallInput,
    ToolCallOutput,
)
from astracore.shared.observability.logger import get_logger

_logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Span
# ---------------------------------------------------------------------------


@dataclass
class Span:
    span_id: str
    operation: str
    parent_span_id: str | None
    start_time: float  # monotonic
    end_time: float | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    status: str = "ok"  # ok | error
    error: str | None = None

    @property
    def duration_ms(self) -> int | None:
        if self.end_time is None:
            return None
        return int((self.end_time - self.start_time) * 1000)

    def finish(self, *, status: str = "ok", error: str | None = None) -> None:
        self.end_time = time.monotonic()
        self.status = status
        self.error = error

    def to_dict(self) -> dict[str, Any]:
        return {
            "span_id": self.span_id,
            "operation": self.operation,
            "parent_span_id": self.parent_span_id,
            "duration_ms": self.duration_ms,
            "status": self.status,
            "error": self.error,
            "attributes": self.attributes,
        }


# ---------------------------------------------------------------------------
# Tracer
# ---------------------------------------------------------------------------


class Tracer:
    """Session-scoped tracer.  One instance per chat session."""

    def __init__(self, session_id: str) -> None:
        self._session_id = session_id
        # In-flight spans keyed by a correlation id
        self._llm_span: Span | None = None
        self._tool_spans: dict[str, Span] = {}

    # ------------------------------------------------------------------
    # Hook implementations
    # ------------------------------------------------------------------

    def _before_llm(self, payload: LLMCallInput) -> None:
        self._llm_span = Span(
            span_id=str(uuid.uuid4()),
            operation="llm.call",
            parent_span_id=None,
            start_time=time.monotonic(),
            attributes={
                "session_id": self._session_id,
                "model": payload.model,
                "message_count": len(payload.messages),
                "tool_count": len(payload.tools) if payload.tools else 0,
            },
        )

    def _after_llm(self, payload: LLMCallOutput) -> None:
        if self._llm_span is None:
            return
        self._llm_span.finish(status="error" if payload.metadata.get("error") else "ok")
        self._llm_span.attributes.update(
            {
                "content_length": len(payload.content),
                "tool_call_count": len(payload.tool_calls),
                "duration_ms": payload.duration_ms,
            }
        )
        self._emit(self._llm_span)
        self._llm_span = None

    def _before_tool(self, payload: ToolCallInput) -> None:
        parent = self._llm_span.span_id if self._llm_span else None
        span = Span(
            span_id=str(uuid.uuid4()),
            operation="tool.call",
            parent_span_id=parent,
            start_time=time.monotonic(),
            attributes={
                "session_id": self._session_id,
                "tool_name": payload.tool_name,
                "tool_call_id": payload.tool_call_id,
            },
        )
        self._tool_spans[payload.tool_call_id] = span

    def _after_tool(self, payload: ToolCallOutput) -> None:
        span = self._tool_spans.pop(payload.tool_call_id, None)
        if span is None:
            return
        span.finish(status="error" if payload.is_error else "ok")
        span.attributes.update(
            {
                "is_error": payload.is_error,
                "content_length": len(payload.content),
                "duration_ms": payload.duration_ms,
            }
        )
        self._emit(span)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _emit(self, span: Span) -> None:
        _logger.debug("SPAN %s", json.dumps(span.to_dict()))

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def register_hooks(self, registry: HookRegistry) -> None:
        """Wire this tracer's hooks into a HookRegistry."""
        registry.before_llm.append(self._before_llm)
        registry.after_llm.append(self._after_llm)
        registry.before_tool.append(self._before_tool)
        registry.after_tool.append(self._after_tool)
