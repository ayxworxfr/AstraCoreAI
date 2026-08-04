"""Toolset resolve / filter."""

from unittest.mock import MagicMock

from astracore.modules.tools.application.toolset import READONLY, get_toolset
from astracore.modules.tools.ports.tool import ToolDefinition


def test_get_toolset_unknown_falls_back_to_default():
    ts = get_toolset("nope")
    assert ts.name == "default"
    assert not ts.tool_names


def test_readonly_excludes_destructive_tools():
    adapter = MagicMock()
    adapter.get_definitions.return_value = [
        ToolDefinition(name="recall_memory", description="r"),
        ToolDefinition(name="delete_memory", description="d"),
        ToolDefinition(name="web_search", description="w"),
        ToolDefinition(name="ask_user", description="a"),
    ]
    resolved = READONLY.resolve(adapter)
    assert "recall_memory" in resolved
    assert "web_search" in resolved
    assert "ask_user" in resolved
    assert "delete_memory" not in resolved
