"""Tests for the Python filesystem MCP server."""

import importlib
import sys
import time
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


@pytest.mark.asyncio
async def test_read_file_includes_line_numbers_and_total(monkeypatch, tmp_path):
    """read_file output must include line numbers and total line count."""
    server = _load_server(monkeypatch, tmp_path)
    f = tmp_path / "f.txt"
    f.write_text("line1\nline2\nline3\n", encoding="utf-8")

    result = await server.read_file(path=str(f))
    assert "共 3 行" in result
    assert "1 line1" in result
    assert "3 line3" in result


@pytest.mark.asyncio
async def test_read_file_offset_and_limit(monkeypatch, tmp_path):
    """offset+limit selects the correct line range."""
    server = _load_server(monkeypatch, tmp_path)
    f = tmp_path / "f.txt"
    f.write_text("\n".join(f"line{i}" for i in range(1, 11)), encoding="utf-8")

    result = await server.read_file(path=str(f), offset=3, limit=3)
    assert "line3" in result
    assert "line5" in result
    assert "line1" not in result
    assert "line6" not in result
    assert "第 3–5 行" in result


@pytest.mark.asyncio
async def test_read_file_offset_beyond_eof_returns_error(monkeypatch, tmp_path):
    server = _load_server(monkeypatch, tmp_path)
    f = tmp_path / "f.txt"
    f.write_text("one\ntwo\n", encoding="utf-8")

    result = await server.read_file(path=str(f), offset=999)
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
async def test_write_file_returns_line_count_and_preview(monkeypatch, tmp_path):
    """write_file success must show line count and first lines."""
    server = _load_server(monkeypatch, tmp_path)
    content = "import os\nimport sys\n\ndef main():\n    pass\n"

    result = await server.write_file(path=str(tmp_path / "a.py"), content=content)
    assert "[成功]" in result
    assert "5 行" in result
    assert "> import os" in result


@pytest.mark.asyncio
async def test_write_file_preview_shows_overflow_notice(monkeypatch, tmp_path):
    """Preview notice appears when file has more than _WRITE_PREVIEW_LINES lines."""
    server = _load_server(monkeypatch, tmp_path)
    content = "\n".join(f"line{i}" for i in range(10))

    result = await server.write_file(path=str(tmp_path / "b.py"), content=content)
    assert "还有" in result


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


@pytest.mark.asyncio
async def test_edit_file_success_returns_line_and_diff(monkeypatch, tmp_path):
    """edit_file success must report the line number and a diff snippet."""
    server = _load_server(monkeypatch, tmp_path)
    f = tmp_path / "f.py"
    f.write_text("line1\ndef old_func():\n    pass\nline4\n", encoding="utf-8")

    result = await server.edit_file(
        path=str(f), old_string="def old_func():", new_string="def new_func():"
    )
    assert "[成功]" in result
    assert "第 2 行" in result
    assert "- def old_func():" in result
    assert "+ def new_func():" in result


@pytest.mark.asyncio
async def test_edit_file_duplicate_returns_line_numbers(monkeypatch, tmp_path):
    """count > 1 error must include every matching line number."""
    server = _load_server(monkeypatch, tmp_path)
    f = tmp_path / "f.py"
    f.write_text("x = 1\nx = 1\nx = 1\n", encoding="utf-8")

    result = await server.edit_file(path=str(f), old_string="x = 1", new_string="x = 2")
    assert "[错误]" in result
    assert "3 次" in result
    # All three line numbers must be present
    assert "1" in result
    assert "2" in result
    assert "3" in result


# ---------------------------------------------------------------------------
# _find_match_lines / _edit_diff_context / _write_preview — unit helpers
# ---------------------------------------------------------------------------


def test_find_match_lines_single(monkeypatch, tmp_path):
    server = _load_server(monkeypatch, tmp_path)
    assert server._find_match_lines("a\nfoo\nb\n", "foo") == [2]


def test_find_match_lines_multiple(monkeypatch, tmp_path):
    server = _load_server(monkeypatch, tmp_path)
    assert server._find_match_lines("foo\nbar\nfoo\n", "foo") == [1, 3]


def test_find_match_lines_not_found(monkeypatch, tmp_path):
    server = _load_server(monkeypatch, tmp_path)
    assert server._find_match_lines("hello\nworld\n", "xyz") == []


def test_edit_diff_context_shows_changed_lines(monkeypatch, tmp_path):
    server = _load_server(monkeypatch, tmp_path)
    content = "a\nb\nold\nd\ne\n"
    diff = server._edit_diff_context(content, "old", "new", match_line=3)
    assert "- old" in diff
    assert "+ new" in diff
    assert "  b" in diff  # context before
    assert "  d" in diff  # context after


def test_write_preview_truncates_at_limit(monkeypatch, tmp_path):
    server = _load_server(monkeypatch, tmp_path)
    content = "\n".join(f"line{i}" for i in range(10))
    preview = server._write_preview(content)
    assert "> line0" in preview
    assert "> line2" in preview
    assert "line3" not in preview
    assert "还有 7 行" in preview


# ---------------------------------------------------------------------------
# batch_edit_file
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_batch_edit_file_applies_all_edits(monkeypatch, tmp_path):
    """All replacements must be applied and summary must list each change."""
    server = _load_server(monkeypatch, tmp_path)
    f = tmp_path / "f.py"
    f.write_text("foo = 1\nbar = 2\nbaz = 3\n", encoding="utf-8")

    result = await server.batch_edit_file(
        path=str(f),
        old_strings=["foo = 1", "bar = 2"],
        new_strings=["foo = 10", "bar = 20"],
    )
    assert "[成功]" in result
    assert "2 处替换" in result
    updated = f.read_text(encoding="utf-8")
    assert "foo = 10" in updated
    assert "bar = 20" in updated


@pytest.mark.asyncio
async def test_batch_edit_file_aborts_on_any_validation_error(monkeypatch, tmp_path):
    """If any old_string is invalid, file must not be modified at all."""
    server = _load_server(monkeypatch, tmp_path)
    f = tmp_path / "f.py"
    original = "foo = 1\nbar = 2\n"
    f.write_text(original, encoding="utf-8")

    result = await server.batch_edit_file(
        path=str(f),
        old_strings=["foo = 1", "DOES_NOT_EXIST"],
        new_strings=["foo = 99", "x"],
    )
    assert "[错误]" in result
    assert "校验失败" in result
    assert f.read_text(encoding="utf-8") == original  # file untouched


@pytest.mark.asyncio
async def test_batch_edit_file_aborts_on_duplicate(monkeypatch, tmp_path):
    """Duplicate old_string must be reported with line numbers; file unchanged."""
    server = _load_server(monkeypatch, tmp_path)
    f = tmp_path / "f.py"
    f.write_text("x = 1\nx = 1\n", encoding="utf-8")

    result = await server.batch_edit_file(
        path=str(f),
        old_strings=["x = 1"],
        new_strings=["x = 2"],
    )
    assert "[错误]" in result
    assert "2 次" in result


@pytest.mark.asyncio
async def test_batch_edit_file_rejects_mismatched_lengths(monkeypatch, tmp_path):
    server = _load_server(monkeypatch, tmp_path)
    f = tmp_path / "f.py"
    f.write_text("x = 1\n", encoding="utf-8")

    result = await server.batch_edit_file(path=str(f), old_strings=["x = 1"], new_strings=[])
    assert "[错误]" in result
    assert "长度不一致" in result


@pytest.mark.asyncio
async def test_batch_edit_file_rejects_empty_list(monkeypatch, tmp_path):
    server = _load_server(monkeypatch, tmp_path)
    f = tmp_path / "f.py"
    f.write_text("x = 1\n", encoding="utf-8")

    result = await server.batch_edit_file(path=str(f), old_strings=[], new_strings=[])
    assert "[错误]" in result


@pytest.mark.asyncio
async def test_batch_edit_file_rejects_path_traversal(monkeypatch, tmp_path):
    server = _load_server(monkeypatch, tmp_path)
    result = await server.batch_edit_file(
        path=str(tmp_path.parent / "evil.txt"),
        old_strings=["a"],
        new_strings=["b"],
    )
    assert "[拒绝]" in result


# ---------------------------------------------------------------------------
# search_files — unit helpers
# ---------------------------------------------------------------------------


def test_should_exclude_matches_exact(monkeypatch, tmp_path):
    server = _load_server(monkeypatch, tmp_path)
    assert server._should_exclude("node_modules", ["node_modules", ".venv"]) is True


def test_should_exclude_matches_glob(monkeypatch, tmp_path):
    server = _load_server(monkeypatch, tmp_path)
    assert server._should_exclude("dist-bundle", ["dist-*"]) is True


def test_should_exclude_no_match(monkeypatch, tmp_path):
    server = _load_server(monkeypatch, tmp_path)
    assert server._should_exclude("src", ["node_modules", ".venv"]) is False


def test_file_matches_pattern_simple_ext(monkeypatch, tmp_path):
    server = _load_server(monkeypatch, tmp_path)
    assert server._file_matches_pattern(tmp_path / "main.py", "*.py") is True


def test_file_matches_pattern_double_star(monkeypatch, tmp_path):
    """Python 3.11 fallback: file at root with **/*.py pattern must match."""
    server = _load_server(monkeypatch, tmp_path)
    # File directly under root (zero intermediate dirs) — exercises the 3.11 fallback path.
    assert server._file_matches_pattern(tmp_path / "main.py", "**/*.py") is True


def test_file_matches_pattern_double_star_nested(monkeypatch, tmp_path):
    server = _load_server(monkeypatch, tmp_path)
    assert server._file_matches_pattern(tmp_path / "src" / "utils.py", "**/*.py") is True


def test_file_matches_pattern_wrong_ext(monkeypatch, tmp_path):
    server = _load_server(monkeypatch, tmp_path)
    assert server._file_matches_pattern(tmp_path / "main.js", "**/*.py") is False


def test_match_content_regex(monkeypatch, tmp_path):
    server = _load_server(monkeypatch, tmp_path)
    f = tmp_path / "a.py"
    f.write_text("def foo():\n    return 42\n", encoding="utf-8")
    result = server._match_content(f, r"def \w+")
    assert len(result) == 1
    assert result[0][0] == 1
    assert "def foo" in result[0][1]


def test_match_content_invalid_regex_falls_back_to_substring(monkeypatch, tmp_path):
    server = _load_server(monkeypatch, tmp_path)
    f = tmp_path / "a.py"
    f.write_text("hello world\nfoo bar\n", encoding="utf-8")
    # "[unclosed" is an invalid regex pattern
    result = server._match_content(f, "[unclosed")
    assert len(result) == 0  # "[unclosed" not found as substring either


def test_match_content_case_insensitive(monkeypatch, tmp_path):
    server = _load_server(monkeypatch, tmp_path)
    f = tmp_path / "a.py"
    f.write_text("Hello World\n", encoding="utf-8")
    result = server._match_content(f, "hello")
    assert len(result) == 1


def test_match_content_skips_large_file(monkeypatch, tmp_path):
    server = _load_server(monkeypatch, tmp_path)
    f = tmp_path / "big.bin"
    # Write a file just over the 1 MB threshold
    f.write_bytes(b"x" * (server._CONTENT_MAX_FILE_SIZE + 1))
    result = server._match_content(f, "x")
    assert result == []


def test_format_file_entry_with_info(monkeypatch, tmp_path):
    server = _load_server(monkeypatch, tmp_path)
    f = tmp_path / "file.txt"
    f.write_text("hi", encoding="utf-8")
    stat = f.stat()
    entry = server._format_file_entry(f, stat.st_size, stat.st_mtime, include_info=True)
    assert str(f) in entry
    assert "B" in entry  # size unit present


def test_format_file_entry_without_info(monkeypatch, tmp_path):
    server = _load_server(monkeypatch, tmp_path)
    f = tmp_path / "file.txt"
    f.write_text("hi", encoding="utf-8")
    stat = f.stat()
    entry = server._format_file_entry(f, stat.st_size, stat.st_mtime, include_info=False)
    assert entry == str(f)


# ---------------------------------------------------------------------------
# search_files — integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_files_basic_glob(monkeypatch, tmp_path):
    """Happy path: glob matches files in subdirectory."""
    server = _load_server(monkeypatch, tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("pass", encoding="utf-8")
    (tmp_path / "src" / "util.ts").write_text("", encoding="utf-8")

    result = await server.search_files(pattern="**/*.py", path=str(tmp_path))
    assert "main.py" in result
    assert "util.ts" not in result


@pytest.mark.asyncio
async def test_search_files_exclude_directory(monkeypatch, tmp_path):
    """Excluded directories must not appear in results."""
    server = _load_server(monkeypatch, tmp_path)
    nm = tmp_path / "node_modules"
    nm.mkdir()
    (nm / "index.js").write_text("", encoding="utf-8")
    (tmp_path / "app.js").write_text("", encoding="utf-8")

    result = await server.search_files(
        pattern="**/*.js",
        path=str(tmp_path),
        exclude=["node_modules"],
    )
    assert "app.js" in result
    assert "node_modules" not in result


@pytest.mark.asyncio
async def test_search_files_exclude_file_pattern(monkeypatch, tmp_path):
    """Exclude patterns also filter individual files."""
    server = _load_server(monkeypatch, tmp_path)
    (tmp_path / "main.py").write_text("", encoding="utf-8")
    (tmp_path / "main.min.js").write_text("", encoding="utf-8")

    result = await server.search_files(
        pattern="*.*",
        path=str(tmp_path),
        exclude=["*.min.js"],
    )
    assert "main.py" in result
    assert "main.min.js" not in result


@pytest.mark.asyncio
async def test_search_files_content_filter(monkeypatch, tmp_path):
    """content filter returns only files containing the keyword."""
    server = _load_server(monkeypatch, tmp_path)
    (tmp_path / "hit.py").write_text("class MyModel:\n    pass\n", encoding="utf-8")
    (tmp_path / "miss.py").write_text("x = 1\n", encoding="utf-8")

    result = await server.search_files(
        pattern="**/*.py",
        path=str(tmp_path),
        content="class",
    )
    assert "hit.py" in result
    assert "miss.py" not in result
    assert "L1:" in result  # matching line shown


@pytest.mark.asyncio
async def test_search_files_content_no_match_returns_no_results(monkeypatch, tmp_path):
    server = _load_server(monkeypatch, tmp_path)
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")

    result = await server.search_files(
        pattern="**/*.py",
        path=str(tmp_path),
        content="DEFINITELY_NOT_HERE_XYZ",
    )
    assert "[无结果]" in result


@pytest.mark.asyncio
async def test_search_files_sort_by_size(monkeypatch, tmp_path):
    """sort_by='size' puts the largest file first."""
    server = _load_server(monkeypatch, tmp_path)
    small = tmp_path / "small.py"
    big = tmp_path / "big.py"
    small.write_text("x" * 10, encoding="utf-8")
    big.write_text("x" * 500, encoding="utf-8")

    result = await server.search_files(
        pattern="**/*.py",
        path=str(tmp_path),
        sort_by="size",
    )
    assert result.index("big.py") < result.index("small.py")


@pytest.mark.asyncio
async def test_search_files_sort_by_modified(monkeypatch, tmp_path):
    """sort_by='modified' puts the most recently modified file first."""
    server = _load_server(monkeypatch, tmp_path)
    old_f = tmp_path / "old.py"
    new_f = tmp_path / "new.py"
    old_f.write_text("a", encoding="utf-8")
    # Advance mtime so new_f is strictly newer
    time.sleep(0.02)
    new_f.write_text("b", encoding="utf-8")

    result = await server.search_files(
        pattern="**/*.py",
        path=str(tmp_path),
        sort_by="modified",
    )
    assert result.index("new.py") < result.index("old.py")


@pytest.mark.asyncio
async def test_search_files_max_results_truncates(monkeypatch, tmp_path):
    """max_results limits output and appends a truncation notice."""
    server = _load_server(monkeypatch, tmp_path)
    for i in range(5):
        (tmp_path / f"file_{i}.py").write_text("", encoding="utf-8")

    result = await server.search_files(
        pattern="**/*.py",
        path=str(tmp_path),
        max_results=3,
    )
    assert "截断" in result
    assert "3" in result


@pytest.mark.asyncio
async def test_search_files_include_info_false(monkeypatch, tmp_path):
    """include_info=False returns bare paths without size/date metadata."""
    server = _load_server(monkeypatch, tmp_path)
    (tmp_path / "a.py").write_text("", encoding="utf-8")

    result = await server.search_files(
        pattern="**/*.py",
        path=str(tmp_path),
        include_info=False,
    )
    # Should not contain metadata markers like KB/MB or date digits
    lines = [ln for ln in result.splitlines() if "a.py" in ln]
    assert len(lines) == 1
    assert "(" not in lines[0]


@pytest.mark.asyncio
async def test_search_files_invalid_sort_by(monkeypatch, tmp_path):
    server = _load_server(monkeypatch, tmp_path)
    result = await server.search_files(pattern="*.py", path=str(tmp_path), sort_by="random")
    assert "[错误]" in result


@pytest.mark.asyncio
async def test_search_files_no_match_returns_no_results(monkeypatch, tmp_path):
    server = _load_server(monkeypatch, tmp_path)
    result = await server.search_files(pattern="**/*.rs", path=str(tmp_path))
    assert "[无结果]" in result


@pytest.mark.asyncio
async def test_search_files_exclude_as_comma_string(monkeypatch, tmp_path):
    """exclude 可以传逗号分隔字符串，效果与列表相同。"""
    server = _load_server(monkeypatch, tmp_path)
    nm = tmp_path / "node_modules"
    nm.mkdir()
    (nm / "index.js").write_text("", encoding="utf-8")
    (tmp_path / "app.js").write_text("", encoding="utf-8")

    result = await server.search_files(
        pattern="**/*.js",
        path=str(tmp_path),
        exclude="node_modules,dist,.git",
    )
    assert "app.js" in result
    assert "node_modules" not in result


@pytest.mark.asyncio
async def test_search_files_rejects_path_outside_allowed(monkeypatch, tmp_path):
    """F-1: search root outside allowed path must be rejected."""
    server = _load_server(monkeypatch, tmp_path)
    result = await server.search_files(
        pattern="**/*.py",
        path=str(tmp_path.parent / "outside"),
    )
    assert "[拒绝]" in result
