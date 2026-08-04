"""ToolLoop 运行参数 —— 参数对象，避免构造器散落一堆标量。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ToolLoopConfig:
    """工具循环的可配置预算与身份。"""

    max_iterations: int = 10
    max_tool_result_chars: int = 20_000
    tool_timeout_s: float = 120.0
    profile_id: str | None = None
    extra_context: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # 读路径追踪集合：ReadTrackedToolAdapter 依赖此可变集合
        self.extra_context.setdefault("_read_files", set())
