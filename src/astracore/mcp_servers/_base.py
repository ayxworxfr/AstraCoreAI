"""Shared utilities for AstraCore MCP server scripts.

New server authors need only:

    from astracore.mcp_servers._base import FastMCP, normalize_path, truncate_output

Then define tools with @mcp.tool() and call mcp.run(transport="stdio", show_banner=False).
"""

import os
from pathlib import Path

__all__ = ["FastMCP", "normalize_path", "truncate_output"]

try:
    from fastmcp import FastMCP
except ModuleNotFoundError:

    class FastMCP:  # type: ignore[no-redef]
        """Import-time fallback so server scripts can be imported in tests without fastmcp."""

        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def tool(self, *args: object, **kwargs: object) -> object:
            def decorator(func: object) -> object:
                return func

            return decorator

        def run(self, *args: object, **kwargs: object) -> None:
            raise RuntimeError("fastmcp is required to run an MCP server")


def normalize_path(path: str) -> Path:
    """Resolve a path, expanding '~' using $HOME before Path.expanduser() as fallback."""
    if path == "~" or path.startswith("~/") or path.startswith("~\\"):
        home = os.environ.get("HOME") or str(Path.home())
        suffix = path[2:] if len(path) > 1 else ""
        return (Path(home) / suffix).resolve()
    return Path(path).expanduser().resolve()


def truncate_output(content: str, max_chars: int) -> str:
    """Return content truncated to max_chars with a notice appended when cut."""
    if len(content) <= max_chars:
        return content
    total = len(content)
    return content[:max_chars] + f"\n... [输出已截断，共 {total} 字符]"
