"""Tool execution adapters."""

from astracore.infrastructure.tools.composite import CompositeToolAdapter
from astracore.infrastructure.tools.mcp import MCPToolAdapter, build_server_configs
from astracore.infrastructure.tools.native import NativeToolAdapter
from astracore.modules.tools.ports.tool import MutableToolAdapter

__all__ = [
    "NativeToolAdapter",
    "MutableToolAdapter",
    "MCPToolAdapter",
    "build_server_configs",
    "CompositeToolAdapter",
]
