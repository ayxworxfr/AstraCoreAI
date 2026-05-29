"""JSON 解析工具：解析 AI 输出的 JSON，支持自动修复畸形格式。"""

import json
from typing import Any

from astracore.shared.observability.logger import get_logger

_logger = get_logger(__name__)


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
