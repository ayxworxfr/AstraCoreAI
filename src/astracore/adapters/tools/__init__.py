"""Tool execution adapters."""

from astracore.adapters.tools.composite import CompositeToolAdapter
from astracore.adapters.tools.mcp import MCPToolAdapter, build_server_configs
from astracore.adapters.tools.native import NativeToolAdapter
from astracore.core.ports.tool import MutableToolAdapter

__all__ = [
    "NativeToolAdapter",
    "MutableToolAdapter",
    "MCPToolAdapter",
    "build_server_configs",
    "CompositeToolAdapter",
]
