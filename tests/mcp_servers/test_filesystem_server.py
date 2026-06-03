"""Tests for the Python filesystem MCP server."""

import importlib
import sys
from pathlib import Path

import pytest


def _load_server(monkeypatch, tmp_path: Path, allow_paths: list[str] | None = None):
    """Import filesystem_server with patched argv."""
    args = ["filesystem_server.py"]
    for p in allow_paths or [str(tmp_path)]:
        args += ["--allow-path", p]
    monkeypatch.setattr(sys, "argv", args)
    sys.modules.pop("astracore.mcp_servers.filesystem_server", None)
    return importlib.import_module("astracore.mcp_servers.filesystem_server")


# ---------------------------------------------------------------------------
# F-1: 路径遍历拦截
# ---------------------------------------------------------------------------


def test_check_allowed_path_rejects_traversal(monkeypatch, tmp_path):
    """F-1: '../' traversal outside allowed dir must be rejected."""
    server = _load_server(monkeypatch, tmp_path)

    outside = tmp_path.parent / "outside.txt"
    assert server._check_allowed_path(outside) is False


def test_check_allowed_path_accepts_subpath(monkeypatch, tmp_path):
    """F-1: Paths inside allowed dir must pass."""
    server = _load_server(monkeypatch, tmp_path)

    inside = tmp_path / "subdir" / "file.txt"
    assert server._check_allowed_path(inside) is True


def test_check_allowed_path_accepts_exact_root(monkeypatch, tmp_path):
    """F-1: Allowed root itself must pass."""
    server = _load_server(monkeypatch, tmp_path)

    assert server._check_allowed_path(tmp_path) is True


# ---------------------------------------------------------------------------
# F-3: 大文件截断
# ---------------------------------------------------------------------------


def test_truncate_returns_full_when_under_limit(monkeypatch, tmp_path):
    """F-3: Content under limit must not be truncated."""
    server = _load_server(monkeypatch, tmp_path)

    content = "a" * 100
    result = server._truncate(content)
    assert result == content


def test_truncate_cuts_and_appends_notice(monkeypatch, tmp_path):
    """F-3: Oversized content must be truncated with notice."""
    server = _load_server(monkeypatch, tmp_path)

    big = "x" * (server.MAX_OUTPUT_CHARS + 1000)
    result = server._truncate(big)

    assert len(result) <= server.MAX_OUTPUT_CHARS + 200  # notice adds a bit
    assert "[输出已截断" in result
    assert str(len(big)) in result  # total size shown in notice


# ---------------------------------------------------------------------------
# F-1 integration: read_file rejects path outside allowed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_file_rejects_path_traversal(monkeypatch, tmp_path):
    """F-1 integration: read_file returns [拒绝] for paths outside allowed."""
    server = _load_server(monkeypatch, tmp_path)

    outside = str(tmp_path.parent / "secret.txt")
    result = await server.read_file(path=outside)
    assert "[拒绝]" in result


@pytest.mark.asyncio
async def test_read_file_returns_content(monkeypatch, tmp_path):
    """Happy path: read_file returns file content."""
    server = _load_server(monkeypatch, tmp_path)

    test_file = tmp_path / "hello.txt"
    test_file.write_text("hello world", encoding="utf-8")

    result = await server.read_file(path=str(test_file))
    assert "hello world" in result


@pytest.mark.asyncio
async def test_read_file_missing_returns_error(monkeypatch, tmp_path):
    """read_file on nonexistent path must return [错误], not raise."""
    server = _load_server(monkeypatch, tmp_path)

    result = await server.read_file(path=str(tmp_path / "nonexistent.txt"))
    assert "[错误]" in result


# ---------------------------------------------------------------------------
# F-1 integration: write_file / edit_file also check path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_file_rejects_path_traversal(monkeypatch, tmp_path):
    """F-1 integration: write_file returns [拒绝] for outside paths."""
    server = _load_server(monkeypatch, tmp_path)

    result = await server.write_file(path=str(tmp_path.parent / "evil.txt"), content="x")
    assert "[拒绝]" in result


@pytest.mark.asyncio
async def test_edit_file_rejects_path_traversal(monkeypatch, tmp_path):
    """F-1 integration: edit_file returns [拒绝] for outside paths."""
    server = _load_server(monkeypatch, tmp_path)

    result = await server.edit_file(
        path=str(tmp_path.parent / "evil.txt"),
        old_string="a",
        new_string="b",
    )
    assert "[拒绝]" in result
