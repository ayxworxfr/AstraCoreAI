"""Tests for MCP server configuration helpers."""

import importlib
import sys
from pathlib import Path

from astracore.adapters.tools.mcp import build_server_configs
from astracore.sdk.config import FilesystemServerConfig, ShellServerConfig


def test_build_server_configs_expands_home_paths(monkeypatch, tmp_path) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))

    configs = build_server_configs([
        FilesystemServerConfig(paths=["~/develope"]),
        ShellServerConfig(allow_dirs=["~/develope"]),
    ])

    expected = str(home / "develope")
    assert configs[0].args[-1] == expected
    assert configs[1].args[configs[1].args.index("--allow-dir") + 1] == expected


def test_shell_server_normalizes_cli_home_paths(monkeypatch, tmp_path) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(sys, "argv", ["shell_server.py", "--allow-dir", "~/develope"])
    sys.modules.pop("astracore.mcp_servers.shell_server", None)

    module = importlib.import_module("astracore.mcp_servers.shell_server")

    assert module._ALLOWED_DIRS == [Path(home / "develope").resolve()]
