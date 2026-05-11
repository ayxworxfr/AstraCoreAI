"""Shared argument-coercion utilities for tool adapters."""

import json
from typing import Any

from astracore.core.ports.tool import ToolParameter, ToolParameterType

_STRUCTURED_TYPES = {ToolParameterType.ARRAY, ToolParameterType.OBJECT}


def coerce_tool_arguments(
    arguments: dict[str, Any],
    param_types: dict[str, ToolParameterType],
) -> dict[str, Any]:
    """JSON-parse string-valued args whose schema type is ARRAY or OBJECT.

    LLMs sometimes serialize complex arguments as JSON strings instead of
    structured values.  This coercion step ensures the tool adapter receives
    the correct Python type regardless of how the LLM serialized the value.
    """
    result: dict[str, Any] = {}
    for key, val in arguments.items():
        expected = param_types.get(key)
        if expected in _STRUCTURED_TYPES and isinstance(val, str):
            try:
                result[key] = json.loads(val)
            except (json.JSONDecodeError, ValueError):
                result[key] = val
        else:
            result[key] = val
    return result


def build_param_type_map(parameters: list[ToolParameter]) -> dict[str, ToolParameterType]:
    """Build a ``{param_name: type}`` lookup from a list of ToolParameter."""
    return {p.name: p.type for p in parameters}
