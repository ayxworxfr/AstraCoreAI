"""JSON 工具：序列化边界清洗 + AI 输出畸形 JSON 修复。"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from astracore.shared.observability.logger import get_logger

_logger = get_logger(__name__)


def json_safe(value: Any) -> Any:
    """把任意对象收成 JSON 可序列化形态（DB JSON 列 / Redis / transcript 边界）。"""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (frozenset, set)):
        # 稳定顺序便于审计 diff；元素可能不可比，统一按 str 排序
        return sorted((json_safe(v) for v in value), key=lambda x: str(x))
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, BaseModel):
        return json_safe(value.model_dump(mode="json"))
    return str(value)


def repair_json(tool_name: str, raw: str, original_exc: json.JSONDecodeError) -> dict[str, Any]:
    """尝试用 json-repair 修复畸形 JSON；修复失败则抛出 ValueError。"""
    try:
        from json_repair import loads as repair_loads  # noqa: PLC0415

        repaired = repair_loads(raw)
        if isinstance(repaired, dict):
            _logger.warning(
                "Tool '%s' JSON repaired (原始错误: %s, char %d/%d): %s",
                tool_name,
                original_exc.msg,
                original_exc.pos,
                len(raw),
                raw[:200],
            )
            return repaired
    except ImportError:
        pass
    raise ValueError(
        f"Tool '{tool_name}' 的参数 JSON 解析失败。"
        f" 原始错误: {original_exc}（截断位置: char {original_exc.pos}，原始长度: {len(raw)}）。"
        f" 原始参数字符串: {raw!r}"
    ) from original_exc
