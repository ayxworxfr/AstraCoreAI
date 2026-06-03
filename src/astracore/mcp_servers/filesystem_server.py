"""Python filesystem MCP server — 替代 @modelcontextprotocol/server-filesystem。

启动方式（由 MCPToolAdapter 通过 StdioTransport 自动调用）：
    python filesystem_server.py --allow-path D:/project [--allow-path D:/other] [--max-chars 8000]

工具（工具名与 read_tracked.py 完全对齐）：
    read_file            — 读取单个文件内容
    read_multiple_files  — 批量读取多个文件
    write_file           — 创建或覆写文件
    edit_file            — 替换文件内指定字符串片段
    list_directory       — 列出目录内容
    create_directory     — 创建目录（含父级）
    move_file            — 移动或重命名文件
    delete_file          — 删除文件
    search_files         — 按 glob 模式搜索文件
    get_file_info        — 获取文件/目录元数据
"""

import argparse
import sys
from pathlib import Path

from astracore.mcp_servers._base import FastMCP, normalize_path, truncate_output

MAX_OUTPUT_CHARS = 8000
_SEARCH_MAX_RESULTS = 200


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


@mcp.tool(description="读取单个文件的文本内容，返回 UTF-8 字符串（超长自动截断）。")
async def read_file(path: str) -> str:
    """Args:
    path: 文件绝对路径或相对路径。
    """
    file_path = Path(path).resolve()
    if not _check_allowed_path(file_path):
        return _deny(path)
    if not file_path.exists():
        return f"[错误] 文件不存在: {path}"
    if not file_path.is_file():
        return f"[错误] 不是文件: {path}"
    content = file_path.read_text(encoding="utf-8", errors="replace")
    return _truncate(content)


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
    return f"[成功] 已写入 {len(content)} 字符到: {file_path}"


@mcp.tool(
    description=(
        "在文件中精确替换一处字符串片段（old_string → new_string）。"
        "调用前必须先用 read_file 读取文件，将返回内容完整复制为 old_string。"
    )
)
async def edit_file(path: str, old_string: str, new_string: str) -> str:
    """Args:
    path: 目标文件路径。
    old_string: 要替换的原始字符串（必须在文件中唯一存在）。
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
        return f"[错误] old_string 在文件中出现 {count} 次，必须唯一。 请提供更多上下文使其唯一。"

    new_content = content.replace(old_string, new_string, 1)
    file_path.write_text(new_content, encoding="utf-8")
    return f"[成功] 已编辑: {file_path}"


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


@mcp.tool(description="在允许路径内按 glob 模式搜索文件。")
async def search_files(pattern: str, path: str | None = None) -> str:
    """Args:
    pattern: glob 模式（如 '**/*.py' / '*.txt'）。
    path: 搜索根目录，不传则使用第一个允许路径。
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

    matches: list[str] = []
    for match in root.glob(pattern):
        if _check_allowed_path(match):
            matches.append(str(match))
        if len(matches) >= _SEARCH_MAX_RESULTS:
            matches.append(f"... [结果已截断，超过 {_SEARCH_MAX_RESULTS} 条]")
            break

    if not matches:
        return f"[无结果] 在 {root} 中未找到匹配 '{pattern}' 的文件"
    return "\n".join(matches)


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
    import datetime

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
