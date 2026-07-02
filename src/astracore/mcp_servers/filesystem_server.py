"""Python filesystem MCP server — 替代 @modelcontextprotocol/server-filesystem。

启动方式（由 MCPToolAdapter 通过 StdioTransport 自动调用）：
    python filesystem_server.py --allow-path D:/project [--allow-path D:/other] [--max-chars 8000]

工具（工具名与 read_tracked.py 完全对齐）：
    read_file            — 读取单个文件内容
    read_multiple_files  — 批量读取多个文件
    write_file           — 创建或覆写文件
    edit_file            — 替换文件内指定字符串片段（单处）
    batch_edit_file      — 原子替换文件内多处字符串片段
    list_directory       — 列出目录内容
    create_directory     — 创建目录（含父级）
    move_file            — 移动或重命名文件
    delete_file          — 删除文件
    search_files         — 按 glob 模式搜索文件
    get_file_info        — 获取文件/目录元数据
"""

import argparse
import datetime
import fnmatch
import os
import re
import sys
from pathlib import Path

from astracore.mcp_servers._base import FastMCP, normalize_path, truncate_output

MAX_OUTPUT_CHARS = 8000
_CONTENT_MAX_FILE_SIZE = 1 * 1024 * 1024  # skip files larger than 1 MB in content search
_CONTENT_MAX_MATCHES_PER_FILE = 3  # max matching lines shown per file
_DIFF_CONTEXT_LINES = 2  # lines of context shown before/after each edit
_WRITE_PREVIEW_LINES = 3  # lines shown in write_file success preview


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AstraCore MCP Filesystem Server")
    parser.add_argument(
        "--allow-path",
        dest="allow_paths",
        action="append",
        metavar="PATH",
        default=[],
        help="允许访问的路径（可重复指定多个）",
    )
    parser.add_argument(
        "--max-chars",
        dest="max_chars",
        type=int,
        default=MAX_OUTPUT_CHARS,
        help=f"单次输出字符上限（默认 {MAX_OUTPUT_CHARS}）",
    )
    known, _ = parser.parse_known_args()
    return known


_args = _parse_args()
_ALLOWED_PATHS: list[Path] = [normalize_path(p) for p in _args.allow_paths]
MAX_OUTPUT_CHARS = _args.max_chars


def _check_allowed_path(path: Path) -> bool:
    """Return True if path is inside at least one allowed root. No limits when list is empty."""
    if not _ALLOWED_PATHS:
        return True
    resolved = path.resolve()
    for root in _ALLOWED_PATHS:
        try:
            resolved.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def _deny(path: str) -> str:
    roots = ", ".join(str(r) for r in _ALLOWED_PATHS) or "（未设置限制）"
    return f"[拒绝] 路径 '{path}' 不在允许列表内。允许路径: {roots}"


def _truncate(content: str) -> str:
    return truncate_output(content, MAX_OUTPUT_CHARS)


mcp = FastMCP(
    name="filesystem",
    instructions=(
        "提供受控的文件系统操作能力。访问路径必须在允许列表内。"
        "编辑文件前请先用 read_file 读取原始内容。"
    ),
)


@mcp.tool(
    description=(
        "读取文件文本内容（UTF-8，附行号）。"
        "结果包含总行数；内容超限时提示下一段的 offset，用 offset+limit 分页读完整文件。"
    )
)
async def read_file(path: str, offset: int = 1, limit: int | None = None) -> str:
    """Args:
    path: 文件绝对路径或相对路径。
    offset: 起始行号（从 1 开始，默认 1）。
    limit: 最多读取的行数（不传则读到文件末尾或字符上限）。
    """
    file_path = Path(path).resolve()
    if not _check_allowed_path(file_path):
        return _deny(path)
    if not file_path.exists():
        return f"[错误] 文件不存在: {path}"
    if not file_path.is_file():
        return f"[错误] 不是文件: {path}"

    lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
    total = len(lines)

    if total == 0:
        return f"[{file_path.name} | 共 0 行]"

    start = max(0, offset - 1)  # 1-based → 0-based
    if start >= total:
        return f"[错误] offset={offset} 超出文件总行数 {total}"

    end = min(start + limit, total) if limit is not None else total
    width = len(str(total))

    # Accumulate lines within the character budget
    budget = MAX_OUTPUT_CHARS - 120  # reserve room for header + notice
    output_lines: list[str] = []
    actual_end = start
    for i, line in enumerate(lines[start:end]):
        entry = f"{start + i + 1:{width}} {line}"
        budget -= len(entry) + 1
        if budget < 0:
            break
        output_lines.append(entry)
        actual_end = start + i + 1

    header = f"[{file_path.name} | 共 {total} 行 | 第 {start + 1}–{actual_end} 行]"
    body = "\n".join(output_lines)
    notice = (
        f"\n... [字符超限已截断，使用 offset={actual_end + 1} 继续读取]" if actual_end < end else ""
    )
    return header + "\n" + body + notice


@mcp.tool(description="批量读取多个文件，每个文件内容用分隔线分隔（超长自动截断）。")
async def read_multiple_files(paths: list[str]) -> str:
    """Args:
    paths: 文件路径列表。
    """
    parts: list[str] = []
    for p in paths:
        file_path = Path(p).resolve()
        if not _check_allowed_path(file_path):
            parts.append(f"=== {p} ===\n{_deny(p)}")
            continue
        if not file_path.exists():
            parts.append(f"=== {p} ===\n[错误] 文件不存在")
            continue
        if not file_path.is_file():
            parts.append(f"=== {p} ===\n[错误] 不是文件")
            continue
        content = file_path.read_text(encoding="utf-8", errors="replace")
        parts.append(f"=== {p} ===\n{_truncate(content)}")
    return "\n\n".join(parts)


def _find_match_lines(content: str, search: str) -> list[int]:
    """Return 1-based line numbers of every occurrence of search in content."""
    result: list[int] = []
    start = 0
    while True:
        pos = content.find(search, start)
        if pos == -1:
            break
        result.append(content[:pos].count("\n") + 1)
        start = pos + 1
    return result


def _edit_diff_context(content: str, old_string: str, new_string: str, match_line: int) -> str:
    """Return a compact diff snippet around match_line (content before the edit is applied)."""
    all_lines = content.splitlines()
    old_lines = old_string.splitlines() or [""]
    new_lines = new_string.splitlines() or [""]

    zero = match_line - 1  # 0-based index of first changed line
    before = all_lines[max(0, zero - _DIFF_CONTEXT_LINES) : zero]
    after = all_lines[zero + len(old_lines) : zero + len(old_lines) + _DIFF_CONTEXT_LINES]

    parts = [f"  {line}" for line in before]
    parts += [f"- {line}" for line in old_lines]
    parts += [f"+ {line}" for line in new_lines]
    parts += [f"  {line}" for line in after]
    return "\n".join(parts)


def _write_preview(content: str) -> str:
    """Return the first _WRITE_PREVIEW_LINES lines of content, each prefixed with '>'."""
    lines = content.splitlines()
    preview = [f"> {line}" for line in lines[:_WRITE_PREVIEW_LINES]]
    remaining = len(lines) - _WRITE_PREVIEW_LINES
    if remaining > 0:
        preview.append(f"> ... (还有 {remaining} 行)")
    return "\n".join(preview)


@mcp.tool(description="创建或覆写文件内容（父目录不存在时自动创建）。")
async def write_file(path: str, content: str) -> str:
    """Args:
    path: 目标文件路径。
    content: 写入的完整文本内容。
    """
    file_path = Path(path).resolve()
    if not _check_allowed_path(file_path):
        return _deny(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")
    line_count = len(content.splitlines())
    preview = _write_preview(content)
    return f"[成功] 已写入 {line_count} 行到: {file_path}\n{preview}"


@mcp.tool(
    description=(
        "在文件中精确替换一处字符串片段（old_string → new_string）。"
        "调用前必须先用 read_file 读取文件；old_string 取自文件原始内容，不含 read_file 输出的行号前缀。"
        "匹配到多处时返回所有行号，扩展 old_string 上下文后重试。"
        "需要同时修改多处时，优先使用 batch_edit_file。"
    )
)
async def edit_file(path: str, old_string: str, new_string: str) -> str:
    """Args:
    path: 目标文件路径。
    old_string: 要替换的原始字符串（必须在文件中唯一存在，不含行号前缀）。
    new_string: 替换后的字符串。
    """
    file_path = Path(path).resolve()
    if not _check_allowed_path(file_path):
        return _deny(path)
    if not file_path.exists():
        return f"[错误] 文件不存在: {path}"
    if not file_path.is_file():
        return f"[错误] 不是文件: {path}"

    content = file_path.read_text(encoding="utf-8", errors="replace")
    count = content.count(old_string)
    if count == 0:
        return f"[错误] old_string 在文件中不存在: {path}"
    if count > 1:
        lines = _find_match_lines(content, old_string)
        line_str = "、".join(str(n) for n in lines)
        return f"[错误] old_string 出现 {count} 次（第 {line_str} 行），请扩展上下文使其唯一"

    match_line = _find_match_lines(content, old_string)[0]
    diff = _edit_diff_context(content, old_string, new_string, match_line)
    new_content = content.replace(old_string, new_string, 1)
    file_path.write_text(new_content, encoding="utf-8")
    total_lines = len(new_content.splitlines())
    return f"[成功] 已编辑第 {match_line} 行（共 {total_lines} 行）\n{diff}"


@mcp.tool(
    description=(
        "对同一文件一次性做多处替换，原子执行（全部校验通过才写盘）。"
        "old_strings 与 new_strings 按索引对应，长度必须相等。"
        "适用于重构、批量改名等需要同时修改多处的场景。"
    )
)
async def batch_edit_file(path: str, old_strings: list[str], new_strings: list[str]) -> str:
    """Args:
    path: 目标文件路径。
    old_strings: 要替换的原始字符串列表（各项必须在文件中唯一存在，不含行号前缀）。
    new_strings: 替换后的字符串列表（与 old_strings 按索引对应）。
    """
    file_path = Path(path).resolve()
    if not _check_allowed_path(file_path):
        return _deny(path)
    if not file_path.exists():
        return f"[错误] 文件不存在: {path}"
    if not file_path.is_file():
        return f"[错误] 不是文件: {path}"
    if len(old_strings) != len(new_strings):
        return (
            f"[错误] old_strings ({len(old_strings)} 项) 与"
            f" new_strings ({len(new_strings)} 项) 长度不一致"
        )
    if not old_strings:
        return "[错误] 编辑列表为空"

    content = file_path.read_text(encoding="utf-8", errors="replace")

    # Validate all edits against the original content before touching the file.
    # Any uniqueness violation causes a full abort so the file is never partially modified.
    errors: list[str] = []
    for i, old in enumerate(old_strings):
        count = content.count(old)
        if count == 0:
            errors.append(f"  编辑 {i + 1}: old_string 在文件中不存在")
        elif count > 1:
            lines = _find_match_lines(content, old)
            line_str = "、".join(str(n) for n in lines)
            errors.append(
                f"  编辑 {i + 1}: old_string 出现 {count} 次（第 {line_str} 行），请扩展上下文使其唯一"
            )

    if errors:
        return "[错误] 校验失败，文件未修改\n" + "\n".join(errors)

    # Apply edits sequentially on the accumulating content.
    result_content = content
    summary: list[str] = []
    for i, (old, new) in enumerate(zip(old_strings, new_strings, strict=True)):
        match_line = _find_match_lines(result_content, old)[0]
        result_content = result_content.replace(old, new, 1)
        old_head = (old.splitlines()[0] if old else "")[:50]
        new_head = (new.splitlines()[0] if new else "")[:50]
        summary.append(f"  {i + 1}. 第 {match_line} 行: {old_head!r} → {new_head!r}")

    file_path.write_text(result_content, encoding="utf-8")
    total_lines = len(result_content.splitlines())
    header = f"[成功] 完成 {len(old_strings)} 处替换（共 {total_lines} 行）"
    return header + "\n" + "\n".join(summary)


@mcp.tool(description="列出目录内容，显示文件名、类型和大小。")
async def list_directory(path: str) -> str:
    """Args:
    path: 目录路径。
    """
    dir_path = Path(path).resolve()
    if not _check_allowed_path(dir_path):
        return _deny(path)
    if not dir_path.exists():
        return f"[错误] 路径不存在: {path}"
    if not dir_path.is_dir():
        return f"[错误] 不是目录: {path}"

    entries: list[str] = []
    for entry in sorted(dir_path.iterdir(), key=lambda e: (e.is_file(), e.name)):
        if entry.is_dir():
            entries.append(f"[目录] {entry.name}/")
        else:
            size = entry.stat().st_size
            entries.append(f"[文件] {entry.name} ({size} 字节)")

    if not entries:
        return f"[空目录] {dir_path}"
    return f"{dir_path}\n" + "\n".join(entries)


@mcp.tool(description="创建目录（含所有父级目录）。")
async def create_directory(path: str) -> str:
    """Args:
    path: 要创建的目录路径。
    """
    dir_path = Path(path).resolve()
    if not _check_allowed_path(dir_path):
        return _deny(path)
    dir_path.mkdir(parents=True, exist_ok=True)
    return f"[成功] 目录已创建: {dir_path}"


@mcp.tool(description="移动或重命名文件/目录。")
async def move_file(source: str, destination: str) -> str:
    """Args:
    source: 源路径。
    destination: 目标路径。
    """
    src = Path(source).resolve()
    dst = Path(destination).resolve()
    if not _check_allowed_path(src):
        return _deny(source)
    if not _check_allowed_path(dst):
        return _deny(destination)
    if not src.exists():
        return f"[错误] 源路径不存在: {source}"
    dst.parent.mkdir(parents=True, exist_ok=True)
    src.rename(dst)
    return f"[成功] 已移动: {src} → {dst}"


@mcp.tool(description="删除单个文件（不删目录）。")
async def delete_file(path: str) -> str:
    """Args:
    path: 要删除的文件路径。
    """
    file_path = Path(path).resolve()
    if not _check_allowed_path(file_path):
        return _deny(path)
    if not file_path.exists():
        return f"[错误] 文件不存在: {path}"
    if not file_path.is_file():
        return f"[错误] 不是文件（删目录请用其他命令）: {path}"
    file_path.unlink()
    return f"[成功] 已删除: {file_path}"


def _should_exclude(name: str, patterns: list[str]) -> bool:
    """Return True if name matches any exclude glob pattern."""
    return any(fnmatch.fnmatch(name, p) for p in patterns)


def _file_matches_pattern(file_path: Path, pattern: str) -> bool:
    """Check if file_path matches a glob pattern, Python 3.11 compatible.

    Path.match() in 3.11 returns False for Path("file.py").match("**/*.py")
    when there are zero directory components before the file.  The fallback
    extracts the tail after the last '**/' and retries with fnmatch on the
    filename alone (fixed natively in 3.12).
    """
    if file_path.match(pattern):
        return True
    if "**" in pattern:
        tail = pattern.split("**/")[-1]
        if tail and fnmatch.fnmatch(file_path.name, tail):
            return True
    return False


def _format_file_entry(path: Path, size: int, mtime: float, include_info: bool) -> str:
    """Format one search result line, optionally with size and modification time."""
    if not include_info:
        return str(path)
    if size >= 1024 * 1024:
        size_str = f"{size / 1024 / 1024:.1f} MB"
    elif size >= 1024:
        size_str = f"{size / 1024:.1f} KB"
    else:
        size_str = f"{size} B"
    mtime_str = datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
    return f"{path}  ({size_str}, {mtime_str})"


def _match_content(path: Path, pattern: str) -> list[tuple[int, str]]:
    """Search file content for pattern (regex, case-insensitive).

    Falls back to plain substring search when pattern is not valid regex.
    Skips files larger than _CONTENT_MAX_FILE_SIZE to avoid blocking on
    large binaries or generated bundles.
    Returns at most _CONTENT_MAX_MATCHES_PER_FILE (line_number, line) pairs.
    """
    try:
        if path.stat().st_size > _CONTENT_MAX_FILE_SIZE:
            return []
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []

    try:
        regex = re.compile(pattern, re.IGNORECASE)
        matches = [
            (i, line.strip()) for i, line in enumerate(text.splitlines(), 1) if regex.search(line)
        ]
    except re.error:
        low = pattern.lower()
        matches = [
            (i, line.strip()) for i, line in enumerate(text.splitlines(), 1) if low in line.lower()
        ]

    return matches[:_CONTENT_MAX_MATCHES_PER_FILE]


@mcp.tool(
    description=(
        "在允许路径内按 glob 模式搜索文件。"
        "【必须】通过 exclude 排除噪音目录，接受列表或逗号字符串。"
        "前端：'node_modules,dist,.next,coverage'；"
        "Python：'.venv,__pycache__,.pytest_cache,dist,build'；"
        "通用加：'.git,.idea'。"
        "content 搜索文件内容；sort_by='name'/'modified'/'size'；include_info 附带元数据。"
    )
)
async def search_files(
    pattern: str,
    path: str | None = None,
    exclude: list[str] | str | None = None,
    content: str | None = None,
    sort_by: str = "name",
    max_results: int = 50,
    include_info: bool = True,
) -> str:
    """Args:
    pattern: glob 模式（如 '**/*.py' / '*.txt'）。
    path: 搜索根目录，不传则使用第一个允许路径。
    exclude: 要跳过的目录/文件名 glob 模式，接受列表或逗号分隔字符串（如 'node_modules,.venv,__pycache__'）。
    content: 在文件内容中搜索的关键词或正则表达式（仅返回内容匹配的文件）。
    sort_by: 排序方式：'name'（默认）/ 'modified'（最近修改在前）/ 'size'（最大在前）。
    max_results: 最多返回的结果数（默认 50）。
    include_info: 是否附带文件大小和修改时间（默认 True）。
    """
    if path is not None:
        root = Path(path).resolve()
    elif _ALLOWED_PATHS:
        root = _ALLOWED_PATHS[0]
    else:
        root = Path.cwd()

    if not _check_allowed_path(root):
        return _deny(str(root))
    if not root.exists():
        return f"[错误] 路径不存在: {root}"
    if not root.is_dir():
        return f"[错误] 不是目录: {root}"
    if sort_by not in ("name", "modified", "size"):
        return "[错误] sort_by 仅支持 'name' / 'modified' / 'size'"

    if isinstance(exclude, str):
        exclude_patterns = [p.strip() for p in exclude.split(",") if p.strip()]
    else:
        exclude_patterns = exclude or []
    # collected: (path, size_bytes, mtime_epoch, content_matches)
    collected: list[tuple[Path, int, float, list[tuple[int, str]]]] = []
    truncated = False

    for dirpath, dirs, files in os.walk(root, topdown=True):
        # Prune excluded directories before descending — this is the key perf win
        # over pathlib.glob which walks the full tree before filtering.
        dirs[:] = [d for d in dirs if not _should_exclude(d, exclude_patterns)]

        for filename in files:
            if _should_exclude(filename, exclude_patterns):
                continue

            file_path = Path(dirpath) / filename
            if not _check_allowed_path(file_path):
                continue
            if not _file_matches_pattern(file_path, pattern):
                continue

            try:
                stat = file_path.stat()
            except OSError:
                continue

            content_matches: list[tuple[int, str]] = []
            if content is not None:
                content_matches = _match_content(file_path, content)
                if not content_matches:
                    continue  # content filter active: skip files with no match

            collected.append((file_path, stat.st_size, stat.st_mtime, content_matches))
            if len(collected) >= max_results:
                truncated = True
                break

        if truncated:
            break

    if not collected:
        desc = f"匹配 '{pattern}'" + (f" 且包含 '{content}'" if content else "")
        return f"[无结果] 在 {root} 中未找到{desc}的文件"

    if sort_by == "modified":
        collected.sort(key=lambda t: t[2], reverse=True)
    elif sort_by == "size":
        collected.sort(key=lambda t: t[1], reverse=True)
    else:
        collected.sort(key=lambda t: str(t[0]))

    lines: list[str] = []
    for file_path, size, mtime, content_matches in collected:
        lines.append(_format_file_entry(file_path, size, mtime, include_info))
        for line_num, line_text in content_matches:
            lines.append(f"  L{line_num}: {line_text}")

    if truncated:
        lines.append(f"... [结果已截断，仅显示前 {max_results} 条]")

    return _truncate("\n".join(lines))


@mcp.tool(description="获取文件或目录的元数据（大小、修改时间、类型）。")
async def get_file_info(path: str) -> str:
    """Args:
    path: 文件或目录路径。
    """
    target = Path(path).resolve()
    if not _check_allowed_path(target):
        return _deny(path)
    if not target.exists():
        return f"[错误] 路径不存在: {path}"

    stat = target.stat()
    mtime = datetime.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    kind = "目录" if target.is_dir() else "文件"
    size_info = f"{stat.st_size} 字节" if target.is_file() else "—"
    return f"路径: {target}\n类型: {kind}\n大小: {size_info}\n修改时间: {mtime}"


if __name__ == "__main__":
    if not _ALLOWED_PATHS:
        print(
            "[警告] 未指定 --allow-path，filesystem 服务器将允许访问任意路径。",
            file=sys.stderr,
        )
    mcp.run(transport="stdio", show_banner=False)
