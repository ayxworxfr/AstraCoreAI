"""Structured tool error types for the tool contract."""

from enum import StrEnum

from pydantic import BaseModel


class ToolErrorCode(StrEnum):
    """Canonical error codes for tool execution failures."""

    INVALID_ARGUMENT = "INVALID_ARGUMENT"
    TIMEOUT = "TIMEOUT"
    POLICY_BLOCKED = "POLICY_BLOCKED"
    TOOL_NOT_FOUND = "TOOL_NOT_FOUND"
    EXECUTION_ERROR = "EXECUTION_ERROR"
    UPSTREAM_UNAVAILABLE = "UPSTREAM_UNAVAILABLE"


class ToolError(BaseModel):
    """Structured error returned by a failed tool execution."""

    code: ToolErrorCode
    message: str
    retryable: bool = False
    hint: str | None = None
