"""工具参数业务校验 —— Schema 层，失败回流给模型而非崩溃。"""

from __future__ import annotations

from dataclasses import dataclass

from astracore.modules.tools.ports.tool import ToolDefinition, ToolParameterType


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    """单条参数校验问题。"""

    field: str
    message: str


@dataclass(frozen=True, slots=True)
class ValidationResult:
    """校验结果；``ok=False`` 时 ``issues`` 非空。"""

    ok: bool
    issues: tuple[ValidationIssue, ...] = ()

    def error_message(self) -> str:
        if self.ok:
            return ""
        lines = ["Error: tool argument validation failed:"]
        for issue in self.issues:
            lines.append(f"  - parameter '{issue.field}': {issue.message}")
        return "\n".join(lines)


_TYPE_CHECKS: dict[ToolParameterType, type | tuple[type, ...]] = {
    ToolParameterType.STRING: str,
    ToolParameterType.NUMBER: (int, float),
    ToolParameterType.BOOLEAN: bool,
    ToolParameterType.OBJECT: dict,
    ToolParameterType.ARRAY: list,
}


def validate_tool_arguments(
    definition: ToolDefinition,
    arguments: dict[str, object],
) -> ValidationResult:
    """校验必填与基础类型；允许额外字段（兼容 MCP 宽松 schema）。"""
    issues: list[ValidationIssue] = []
    args = arguments or {}

    for param in definition.parameters:
        if param.required and param.name not in args:
            issues.append(ValidationIssue(param.name, "is required"))
            continue
        if param.name not in args:
            continue
        value = args[param.name]
        if value is None:
            if param.required:
                issues.append(ValidationIssue(param.name, "must not be null"))
            continue
        expected = _TYPE_CHECKS.get(param.type)
        if expected is None:
            continue
        # JSON 数字经常以 int 出现；bool 是 int 子类，排除假阳性
        if param.type == ToolParameterType.NUMBER:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                issues.append(
                    ValidationIssue(param.name, f"expected number, got {type(value).__name__}")
                )
        elif param.type == ToolParameterType.BOOLEAN:
            if not isinstance(value, bool):
                issues.append(
                    ValidationIssue(param.name, f"expected boolean, got {type(value).__name__}")
                )
        elif not isinstance(value, expected):
            issues.append(
                ValidationIssue(
                    param.name,
                    f"expected {param.type.value}, got {type(value).__name__}",
                )
            )

    if issues:
        return ValidationResult(ok=False, issues=tuple(issues))
    return ValidationResult(ok=True)
