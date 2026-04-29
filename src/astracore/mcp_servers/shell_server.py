"""轻量 MCP Shell Server — 通过 fastmcp 暴露受控的 shell 命令执行能力。

启动方式（由 MCPToolAdapter 通过 StdioTransport 自动调用）：
    python shell_server.py --allow-dir D:/project [--allow-dir D:/other] [--timeout 30]

工具：
    run_command(command, cwd?)  — 在允许目录内执行 shell 命令
    list_allowed_dirs()         — 返回当前允许的目录列表
"""

import argparse
import asyncio
import os
import re
import sys
from pathlib import Path

from fastmcp import FastMCP

# ---------------------------------------------------------------------------
# 危险命令黑名单（正则，case-insensitive），匹配即拒绝
# ---------------------------------------------------------------------------
_DANGEROUS_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"rm\s+-[a-z]*r[a-z]*f\s+/",       # rm -rf /
        r"rm\s+-[a-z]*f[a-z]*r\s+/",       # rm -fr /
        r"format\s+[a-zA-Z]:",              # format C:
        r"del\s+/[fFsS].*\s+[a-zA-Z]:",    # del /f /s C:
        r"rd\s+/[sS]\s+/[qQ]\s+[a-zA-Z]:", # rd /s /q C:
        r"mkfs",                             # mkfs.*
        r"dd\s+if=",                         # dd if= (覆写磁盘)
        r"shutdown",                         # shutdown / halt
        r"reboot",
        r"halt",
        r">\s*/dev/sd",                      # > /dev/sdX
        r"chmod\s+-[rR]\s+777\s+/",         # chmod -R 777 /
    ]
]

MAX_OUTPUT_CHARS = 8000
_WINDOWS_UNIX_HINTS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bps\s+aux\b", re.IGNORECASE), "Get-Process | Sort-Object WorkingSet -Descending"),
    (re.compile(r"\bgrep\b", re.IGNORECASE), "findstr <关键词> 或 Select-String"),
    (re.compile(r"\blsof\b", re.IGNORECASE), "netstat -ano"),
    (re.compile(r"\bpkill\b", re.IGNORECASE), "taskkill /F /PID <pid>"),
]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AstraCore MCP Shell Server")
    parser.add_argument(
        "--allow-dir",
        dest="allow_dirs",
        action="append",
        metavar="DIR",
        default=[],
        help="允许执行命令的目录（可重复指定多个）",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="命令执行超时秒数（默认 30）",
    )
    # 过滤掉 fastmcp 内部可能注入的参数
    known, _ = parser.parse_known_args()
    return known


_args = _parse_args()


def _normalize_path(path: str) -> Path:
    if path == "~" or path.startswith("~/") or path.startswith("~\\"):
        home = os.environ.get("HOME") or str(Path.home())
        suffix = path[2:] if len(path) > 1 else ""
        return (Path(home) / suffix).resolve()
    return Path(path).expanduser().resolve()


# 将允许目录规范化为绝对路径
_ALLOWED_DIRS: list[Path] = [_normalize_path(d) for d in _args.allow_dirs]
_TIMEOUT: float = _args.timeout

mcp = FastMCP(
    name="shell",
    instructions=(
        "提供受控的 shell 命令执行能力。"
        "执行前请先用 list_allowed_dirs() 确认可操作目录。"
    ),
)


def _check_dangerous(command: str) -> str | None:
    """若命令匹配黑名单返回匹配到的 pattern 描述，否则返回 None。"""
    for pattern in _DANGEROUS_PATTERNS:
        if pattern.search(command):
            return pattern.pattern
    return None


def _check_cwd(cwd_path: Path) -> bool:
    """检查 cwd 是否是某个允许目录的子路径（或本身）。无限制时放行。"""
    if not _ALLOWED_DIRS:
        return True
    for allowed in _ALLOWED_DIRS:
        try:
            cwd_path.resolve().relative_to(allowed)
            return True
        except ValueError:
            continue
    return False


def _check_windows_incompatible_command(command: str) -> str | None:
    """Windows 下识别常见 Unix 风格命令并返回替代建议。"""
    if os.name != "nt":
        return None
    for pattern, suggestion in _WINDOWS_UNIX_HINTS:
        if pattern.search(command):
            return (
                "[提示] 当前为 Windows 环境，检测到 Unix 风格命令，可能导致工具等待超时。"
                f"建议改用: {suggestion}"
            )
    return None


@mcp.tool(description="在允许的目录内执行 shell 命令，返回 stdout+stderr 合并输出。")
async def run_command(command: str, cwd: str | None = None) -> str:
    """执行一条 shell 命令。

    Args:
        command: 要执行的完整命令字符串（如 "git status" / "ls -la"）
        cwd: 执行目录，必须在允许目录列表内；不传则使用第一个允许目录
    """
    # 1. 黑名单检查
    matched = _check_dangerous(command)
    if matched is not None:
        return f"[拒绝] 命令匹配危险模式: {matched}"
    windows_hint = _check_windows_incompatible_command(command)
    if windows_hint is not None:
        return windows_hint

    # 2. 确定工作目录
    if cwd is not None:
        work_dir = Path(cwd).resolve()
    elif _ALLOWED_DIRS:
        work_dir = _ALLOWED_DIRS[0]
    else:
        work_dir = Path.cwd()

    if not _check_cwd(work_dir):
        allowed_list = ", ".join(str(d) for d in _ALLOWED_DIRS)
        return f"[拒绝] 目录 '{work_dir}' 不在允许列表内。允许目录: {allowed_list}"

    if not work_dir.exists():
        return f"[错误] 目录不存在: {work_dir}"

    # 3. 执行命令
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdin=asyncio.subprocess.DEVNULL,   # 断开 stdin：避免子进程等待终端输入导致永久阻塞
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,   # stderr 合并到 stdout，统一捕获
            cwd=str(work_dir),
        )

        async def _read() -> str:
            stdout, _ = await proc.communicate()
            return stdout.decode("utf-8", errors="replace") if stdout else ""

        output = await asyncio.wait_for(_read(), timeout=_TIMEOUT)
        exit_code = proc.returncode

        result = output[:MAX_OUTPUT_CHARS]
        if len(output) > MAX_OUTPUT_CHARS:
            result += f"\n... [输出已截断，共 {len(output)} 字符]"

        output_line = f"[退出码: {exit_code}]\n{result}" if result else f"[退出码: {exit_code}] (无输出)"
        # 非零退出码视为命令失败：抛出异常让 fastmcp 将结果标记为 isError=True，
        # LLM 收到明确的错误信号而非模糊的"成功"响应，能更准确地判断后续操作。
        if exit_code != 0:
            raise RuntimeError(output_line)
        return output_line

    except TimeoutError:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
        return f"[超时] 命令超过 {_TIMEOUT}s 已被终止: {command}"
    except Exception as e:
        return f"[异常] {type(e).__name__}: {e}"


@mcp.tool(description="返回当前 shell 服务器允许操作的目录列表。")
async def list_allowed_dirs() -> str:
    """查看当前允许执行命令的目录列表。"""
    if not _ALLOWED_DIRS:
        return "未设置目录限制，允许在任意目录执行。"
    lines = [str(d) for d in _ALLOWED_DIRS]
    return "允许目录:\n" + "\n".join(f"  - {line}" for line in lines)


if __name__ == "__main__":
    if not _ALLOWED_DIRS:
        print(
            "[警告] 未指定 --allow-dir，shell 服务器将允许在任意目录执行命令。",
            file=sys.stderr,
        )
    mcp.run(transport="stdio", show_banner=False)
