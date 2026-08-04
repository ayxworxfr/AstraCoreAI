"""Schema validation — errors return to model, never crash the loop."""

from astracore.modules.tools.application.validate import validate_tool_arguments
from astracore.modules.tools.ports.tool import (
    ToolDefinition,
    ToolParameter,
    ToolParameterType,
)


def _def() -> ToolDefinition:
    return ToolDefinition(
        name="search",
        description="search",
        parameters=[
            ToolParameter(
                name="query",
                type=ToolParameterType.STRING,
                description="q",
                required=True,
            ),
            ToolParameter(
                name="top_k",
                type=ToolParameterType.NUMBER,
                description="k",
                required=False,
            ),
        ],
    )


def test_missing_required_fails():
    result = validate_tool_arguments(_def(), {})
    assert result.ok is False
    assert "query" in result.error_message()
    assert "required" in result.error_message()


def test_valid_args_pass():
    result = validate_tool_arguments(_def(), {"query": "hello", "top_k": 3})
    assert result.ok is True


def test_wrong_type_fails():
    result = validate_tool_arguments(_def(), {"query": 123})
    assert result.ok is False
    assert "string" in result.error_message()


def test_extra_fields_allowed():
    """MCP 常带额外字段，不应拒绝。"""
    result = validate_tool_arguments(_def(), {"query": "x", "extra": True})
    assert result.ok is True


def test_bool_not_accepted_as_number():
    result = validate_tool_arguments(_def(), {"query": "x", "top_k": True})
    assert result.ok is False
