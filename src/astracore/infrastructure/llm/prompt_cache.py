"""Anthropic prompt-cache breakpoint helpers.

Anthropic caches a request prefix in order: tools → system → messages.
Each ``cache_control`` marker is a breakpoint (max 4 per request). Lookback
from a breakpoint walks at most 20 content blocks, so long agent turns need
intermediate markers roughly every 15 blocks.
"""

from __future__ import annotations

from typing import Any

CACHE_CONTROL_EPHEMERAL: dict[str, str] = {"type": "ephemeral"}
_MAX_BREAKPOINTS = 4
# 留出余量：官方 lookback 上限 20 blocks
_LOOKBACK_SAFE_INTERVAL = 15
_CACHEABLE_BLOCK_TYPES = frozenset({"text", "tool_use", "tool_result", "image", "document"})


def allocate_message_cache_slots(*, has_tools: bool, has_cached_system: bool) -> int:
    """Return how many breakpoints remain for the messages array."""
    used = int(has_tools) + int(has_cached_system)
    return max(0, _MAX_BREAKPOINTS - used)


def mark_tools_cache_breakpoint(
    tools: list[dict[str, Any]] | None,
) -> list[dict[str, Any]] | None:
    """Clone *tools* and put ``cache_control`` on the last definition."""
    if not tools:
        return tools
    out = [dict(t) for t in tools]
    last = dict(out[-1])
    last["cache_control"] = dict(CACHE_CONTROL_EPHEMERAL)
    out[-1] = last
    return out


def mark_messages_cache_breakpoints(
    messages: list[dict[str, Any]],
    *,
    remaining_slots: int,
) -> list[dict[str, Any]]:
    """Place up to *remaining_slots* breakpoints on message content blocks.

    Always marks the final cacheable block so each agent round can reuse the
    growing conversation prefix. When enough slots remain, also marks every
    ``_LOOKBACK_SAFE_INTERVAL``-th block so lookback can find a prior write.
    """
    if remaining_slots <= 0 or not messages:
        return messages

    out = [_normalize_message(m) for m in messages]
    refs = _collect_cacheable_refs(out)
    if not refs:
        return out

    mark_indices = _select_mark_indices(len(refs), remaining_slots)
    for ref_i in mark_indices:
        mi, bi = refs[ref_i]
        block = dict(out[mi]["content"][bi])
        block["cache_control"] = dict(CACHE_CONTROL_EPHEMERAL)
        out[mi]["content"][bi] = block
    return out


def _normalize_message(msg: dict[str, Any]) -> dict[str, Any]:
    cloned = dict(msg)
    content = cloned.get("content")
    if isinstance(content, str):
        cloned["content"] = [{"type": "text", "text": content}] if content else []
    elif isinstance(content, list):
        cloned["content"] = [dict(b) if isinstance(b, dict) else b for b in content]
    else:
        cloned["content"] = []
    return cloned


def _is_cacheable_block(block: Any) -> bool:
    if not isinstance(block, dict):
        return False
    btype = block.get("type")
    if btype not in _CACHEABLE_BLOCK_TYPES:
        return False
    if btype == "text" and not str(block.get("text") or "").strip():
        return False
    return True


def _collect_cacheable_refs(messages: list[dict[str, Any]]) -> list[tuple[int, int]]:
    refs: list[tuple[int, int]] = []
    for mi, msg in enumerate(messages):
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for bi, block in enumerate(content):
            if _is_cacheable_block(block):
                refs.append((mi, bi))
    return refs


def _select_mark_indices(n_refs: int, slots: int) -> list[int]:
    """Pick ascending ref indices to mark; always includes the last ref."""
    if n_refs <= 0 or slots <= 0:
        return []
    if slots == 1 or n_refs == 1:
        return [n_refs - 1]

    chosen: set[int] = {n_refs - 1}
    # 从前往后每隔 LOOKBACK_SAFE_INTERVAL 放一个中间断点
    for i in range(_LOOKBACK_SAFE_INTERVAL - 1, n_refs - 1, _LOOKBACK_SAFE_INTERVAL):
        chosen.add(i)
        if len(chosen) >= slots:
            break

    # 若中间断点不够填满 slots，从尾部往前均匀补点（仍保留最后一块）
    if len(chosen) < slots and n_refs > 1:
        step = max(1, n_refs // slots)
        for i in range(n_refs - 1 - step, -1, -step):
            chosen.add(i)
            if len(chosen) >= slots:
                break

    return sorted(chosen)[-slots:]
