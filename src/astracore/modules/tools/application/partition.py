"""声明式工具并发分区。

1. ``is_concurrency_safe=False`` → 独占串行 batch
2. 路径冲突（读/写同一 path）→ 强制串行
3. 连续无冲突的 safe 工具 → 同一并行 batch
"""

from __future__ import annotations

from dataclasses import dataclass

from astracore.modules.chat.domain.message import ToolCall
from astracore.modules.tools.ports.tool import ToolDefinition

# 参数名里常见的路径字段
_PATH_KEYS = ("path", "file", "filepath", "file_path", "filename", "target", "src", "dst")


@dataclass(frozen=True, slots=True)
class ToolBatch:
    """一组同调度策略的工具调用。"""

    calls: tuple[ToolCall, ...]
    concurrent: bool

    def __iter__(self):
        return iter(self.calls)

    def __len__(self) -> int:
        return len(self.calls)


def extract_paths(call: ToolCall) -> frozenset[str]:
    """从工具参数里抽出规范化路径集合（小写、反斜杠统一）。"""
    found: set[str] = set()
    args = call.arguments or {}
    for key, value in args.items():
        key_l = key.lower()
        if key_l in _PATH_KEYS or key_l.endswith("_path") or key_l.endswith("_file"):
            if isinstance(value, str) and value.strip():
                found.add(_norm_path(value))
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, str) and item.strip():
                        found.add(_norm_path(item))
    return frozenset(found)


def _norm_path(path: str) -> str:
    return path.replace("\\", "/").rstrip("/").lower()


def _is_safe(call: ToolCall, definitions: dict[str, ToolDefinition]) -> bool:
    """未知工具视为不安全（fail-closed）。"""
    defn = definitions.get(call.name)
    if defn is None:
        return False
    return defn.is_concurrency_safe


def _paths_conflict(a: frozenset[str], b: frozenset[str]) -> bool:
    return bool(a & b)


def partition_tool_calls(
    calls: list[ToolCall],
    definitions: dict[str, ToolDefinition],
) -> list[ToolBatch]:
    """按 concurrency_safe + 路径冲突将工具调用切分为调度批次。"""
    if not calls:
        return []

    batches: list[ToolBatch] = []
    pending_safe: list[ToolCall] = []
    pending_paths: set[str] = set()

    def flush_safe() -> None:
        nonlocal pending_paths
        if pending_safe:
            batches.append(ToolBatch(calls=tuple(pending_safe), concurrent=True))
            pending_safe.clear()
            pending_paths = set()

    for call in calls:
        call_paths = extract_paths(call)
        if not _is_safe(call, definitions):
            flush_safe()
            batches.append(ToolBatch(calls=(call,), concurrent=False))
            continue

        # 与当前并行批内路径冲突 → 先收束再开新批
        if pending_safe and _paths_conflict(frozenset(pending_paths), call_paths):
            flush_safe()

        pending_safe.append(call)
        pending_paths |= call_paths

    flush_safe()
    return batches
