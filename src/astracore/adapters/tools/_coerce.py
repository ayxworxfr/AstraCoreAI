"""Shared argument-coercion utilities for tool adapters."""

import json
from typing import Any

from astracore.core.ports.tool import ToolParameter, ToolParameterType
from astracore.runtime.observability.logger import get_logger

_logger = get_logger(__name__)
_STRUCTURED_TYPES = {ToolParameterType.ARRAY, ToolParameterType.OBJECT}


def coerce_tool_arguments(
    arguments: dict[str, Any],
    param_types: dict[str, ToolParameterType],
) -> dict[str, Any]:
    """JSON-parse string-valued args whose schema type is ARRAY or OBJECT.

    LLMs sometimes serialize complex arguments as JSON strings instead of
    structured values.  This coercion step ensures the tool adapter receives
    the correct Python type regardless of how the LLM serialized the value.
    Malformed JSON (e.g. unescaped quotes in LLM-generated content) is
    repaired via json-repair before falling back to the raw string.
    """
    result: dict[str, Any] = {}
    for key, val in arguments.items():
        expected = param_types.get(key)
        if expected in _STRUCTURED_TYPES and isinstance(val, str):
            try:
                result[key] = json.loads(val)
            except (json.JSONDecodeError, ValueError) as exc:
                repaired = _try_repair(key, val, exc)
                result[key] = repaired if repaired is not None else val
        else:
            result[key] = val
    return result


def _try_repair(key: str, raw: str, original_exc: Exception) -> Any | None:
    """尝试用 json-repair 修复畸形 JSON 字符串；修复失败返回 None。"""
    try:
        from json_repair import loads as repair_loads  # noqa: PLC0415

        repaired = repair_loads(raw)
        if isinstance(repaired, (list, dict)):
            _logger.warning(
                "Coerce '%s' JSON repaired (原始错误: %s): %s",
                key, original_exc, raw[:200],
            )
            return repaired
    except ImportError:
        pass
    return None


def build_param_type_map(parameters: list[ToolParameter]) -> dict[str, ToolParameterType]:
    """Build a ``{param_name: type}`` lookup from a list of ToolParameter."""
    return {p.name: p.type for p in parameters}
