"""Toolset —— 按角色组合工具子集（Registry / Toolset / Model Tools 的中间层）。

Registry（Native/MCP/Composite）定义「有哪些工具」；
Toolset 定义「这个 Agent 能用哪些」；
Model Tools 投影由 ToolLoopUseCase._build_tool_definitions 完成。
"""

from __future__ import annotations

from dataclasses import dataclass

from astracore.modules.tools.ports.tool import ToolAdapter, ToolDefinition


@dataclass(frozen=True, slots=True)
class Toolset:
    """命名工具子集。``names`` 为空表示不裁剪（暴露适配器全部工具）。"""

    name: str
    tool_names: frozenset[str] = frozenset()
    description: str = ""

    def resolve(self, adapter: ToolAdapter) -> frozenset[str]:
        """相对适配器可用工具求交集；空 tool_names = 全部。"""
        available = {d.name for d in adapter.get_definitions()}
        if not self.tool_names:
            return frozenset(available)
        return frozenset(self.tool_names & available)

    def filter_definitions(self, definitions: list[ToolDefinition]) -> list[ToolDefinition]:
        if not self.tool_names:
            return list(definitions)
        return [d for d in definitions if d.name in self.tool_names]


# 预设：按场景裁剪，避免「工具超市迷路」
READONLY = Toolset(
    name="readonly",
    description="只读检索与计算，禁止写记忆/调度/脚本",
    tool_names=frozenset(
        {
            "get_current_time",
            "calculate",
            "search_knowledge_base",
            "web_search",
            "fetch_page",
            "recall_memory",
            "list_scheduled_tasks",
            "load_skill",
            "get_skill_reference",
            "ask_user",
        }
    ),
)

DEFAULT = Toolset(
    name="default",
    description="主对话默认工具集（不含 spawn_agents 时由配置决定）",
    tool_names=frozenset(),  # 空 = 全部
)

WORKER = Toolset(
    name="worker",
    description="spawn_agents 子 Agent：继承父工具但禁止递归 spawn",
    tool_names=frozenset(),  # 运行时用 resolve 后再减 spawn_agents
)

MEMORY_OPS = Toolset(
    name="memory_ops",
    description="记忆读写专用",
    tool_names=frozenset(
        {
            "recall_memory",
            "save_memory",
            "delete_memory",
            "compact_memory",
            "ask_user",
        }
    ),
)

TOOLSETS: dict[str, Toolset] = {
    DEFAULT.name: DEFAULT,
    READONLY.name: READONLY,
    WORKER.name: WORKER,
    MEMORY_OPS.name: MEMORY_OPS,
}


def get_toolset(name: str | None) -> Toolset:
    """按名称取 Toolset；未知名称回退 DEFAULT。"""
    if not name:
        return DEFAULT
    return TOOLSETS.get(name, DEFAULT)
