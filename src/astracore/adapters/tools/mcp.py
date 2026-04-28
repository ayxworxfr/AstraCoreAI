"""MCP (Model Context Protocol) tool adapter using fastmcp."""

import asyncio
import os
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from astracore.core.ports.tool import (
    ToolAdapter,
    ToolDefinition,
    ToolExecutionResult,
    ToolParameter,
    ToolParameterType,
)
from astracore.runtime.observability.logger import get_logger

if TYPE_CHECKING:
    from astracore.sdk.config import MCPServerEntry

logger = get_logger(__name__)

_TYPE_MAP: dict[str, ToolParameterType] = {
    "string": ToolParameterType.STRING,
    "number": ToolParameterType.NUMBER,
    "integer": ToolParameterType.NUMBER,
    "boolean": ToolParameterType.BOOLEAN,
    "object": ToolParameterType.OBJECT,
    "array": ToolParameterType.ARRAY,
}


def _parse_parameters(input_schema: dict[str, Any]) -> list[ToolParameter]:
    """Parse a JSON Schema ``input_schema`` into a list of ToolParameter."""
    properties: dict[str, Any] = input_schema.get("properties") or {}
    required: set[str] = set(input_schema.get("required") or [])
    return [
        ToolParameter(
            name=name,
            type=_TYPE_MAP.get(str(schema.get("type", "string")), ToolParameterType.STRING),
            description=str(schema.get("description") or ""),
            required=name in required,
        )
        for name, schema in properties.items()
    ]


class MCPServerConfig(BaseModel):
    """Internal configuration for a single MCP server process."""

    name: str
    command: str
    args: list[str] = []
    env: dict[str, str] = {}


_SHELL_SERVER_SCRIPT = Path(__file__).parent.parent.parent / "mcp_servers" / "shell_server.py"


def _normalize_path(path: str) -> str:
    return str(Path(path).expanduser().resolve())


def build_server_configs(entries: "list[MCPServerEntry]") -> list[MCPServerConfig]:
    """Convert SDK server configuration entries to internal MCPServerConfig objects."""
    from astracore.sdk.config import FilesystemServerConfig, ShellServerConfig  # noqa: PLC0415

    result: list[MCPServerConfig] = []
    for entry in entries:
        if isinstance(entry, FilesystemServerConfig):
            result.append(MCPServerConfig(
                name=entry.name,
                command="npx",
                args=["-y", "@modelcontextprotocol/server-filesystem"]
                + [_normalize_path(path) for path in entry.paths],
            ))
        elif isinstance(entry, ShellServerConfig):
            args = [str(_SHELL_SERVER_SCRIPT)]
            for d in entry.allow_dirs:
                args += ["--allow-dir", _normalize_path(d)]
            args += ["--timeout", str(entry.timeout)]
            result.append(MCPServerConfig(name=entry.name, command=sys.executable, args=args))
        else:
            result.append(MCPServerConfig(
                name=entry.name,
                command=entry.command,
                args=entry.args,
                env=entry.env,
            ))
    return result


class MCPToolAdapter(ToolAdapter):
    """Tool adapter that bridges one or more MCP servers using fastmcp Client.

    fastmcp's Client manages the session in a background asyncio.create_task(),
    which means tool calls from any asyncio task (including FastAPI route handlers)
    work correctly — no anyio cross-task boundary issues.

    Lifecycle::

        adapter = MCPToolAdapter([MCPServerConfig(...)])
        await adapter.start()      # connect to servers, discover tools
        ...
        await adapter.stop()       # graceful shutdown
    """

    def __init__(self, server_configs: list[MCPServerConfig]) -> None:
        self._server_configs = server_configs
        # server_name -> fastmcp.Client (populated after connect)
        self._clients: dict[str, Any] = {}
        # tool_name -> server_name
        self._tool_server_map: dict[str, str] = {}
        self._definitions: list[ToolDefinition] = []
        self._background_tasks: list[asyncio.Task[None]] = []
        self._stop_events: dict[str, asyncio.Event] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start connections to all configured MCP servers."""
        ready_events: list[asyncio.Event] = []

        for config in self._server_configs:
            ready = asyncio.Event()
            stop = asyncio.Event()
            ready_events.append(ready)
            self._stop_events[config.name] = stop
            task: asyncio.Task[None] = asyncio.create_task(
                self._run_server(config, ready, stop),
                name=f"mcp-{config.name}",
            )
            self._background_tasks.append(task)

        for event in ready_events:
            await event.wait()

    async def _run_server(
        self,
        config: MCPServerConfig,
        ready: asyncio.Event,
        stop: asyncio.Event,
    ) -> None:
        """Hold a fastmcp Client connection open until stop is signalled."""
        from fastmcp import Client  # noqa: PLC0415
        from fastmcp.client.transports import StdioTransport  # noqa: PLC0415

        # Always include the current process environment so that commands like
        # 'npx' can find Node.js. User-specified env vars are merged on top.
        effective_env = {**os.environ, **config.env}

        for attempt in range(1, 4):
            if stop.is_set():
                ready.set()
                return

            try:
                transport = StdioTransport(
                    command=config.command,
                    args=config.args,
                    env=effective_env,
                    # 显式传入 log_file 可稳定 stdio 握手；写入空设备避免生成本地日志文件。
                    log_file=Path(os.devnull),
                )
                async with Client(transport) as client:
                    tools = await client.list_tools()

                    for tool in tools:
                        schema: dict[str, Any] = {}
                        raw_schema = getattr(tool, "inputSchema", None) or getattr(
                            tool, "input_schema", None
                        )
                        if isinstance(raw_schema, dict):
                            schema = raw_schema

                        definition = ToolDefinition(
                            name=tool.name,
                            description=str(tool.description or ""),
                            parameters=_parse_parameters(schema),
                        )
                        self._definitions.append(definition)
                        self._tool_server_map[tool.name] = config.name

                    self._clients[config.name] = client
                    logger.info(
                        "Connected to MCP server '%s', loaded %d tools",
                        config.name,
                        len(tools),
                    )
                    ready.set()

                    # Hold the connection open until stop() is called.
                    await stop.wait()
                    return  # clean exit → context manager closes connection

            except asyncio.CancelledError:
                raise
            except Exception:
                if attempt < 3:
                    logger.warning(
                        "MCP server '%s' connection attempt %d/3 failed, retrying in 1s...",
                        config.name,
                        attempt,
                    )
                    await asyncio.sleep(1.0)
                else:
                    logger.exception(
                        "Failed to connect to MCP server '%s' after 3 attempts",
                        config.name,
                    )
                    ready.set()

    async def stop(self) -> None:
        """Gracefully shut down all MCP server connections."""
        for stop_event in self._stop_events.values():
            stop_event.set()

        for task in self._background_tasks:
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=5.0)
            except (TimeoutError, Exception):
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

        self._background_tasks.clear()
        self._clients.clear()
        self._stop_events.clear()
        self._tool_server_map.clear()
        self._definitions.clear()

    # ------------------------------------------------------------------
    # ToolAdapter interface
    # ------------------------------------------------------------------

    async def execute(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> ToolExecutionResult:
        server_name = self._tool_server_map.get(tool_name)
        if not server_name:
            return ToolExecutionResult(
                tool_name=tool_name,
                success=False,
                output="",
                error=f"Tool '{tool_name}' not found in any connected MCP server",
                execution_time_ms=0.0,
            )

        client = self._clients.get(server_name)
        if client is None:
            return ToolExecutionResult(
                tool_name=tool_name,
                success=False,
                output="",
                error=f"MCP server '{server_name}' is not connected",
                execution_time_ms=0.0,
            )

        start_time = time.time()
        logger.info("Calling MCP tool '%s' with arguments: %s", tool_name, arguments)

        try:
            raw_result = await client.call_tool(tool_name, arguments)
            execution_time = (time.time() - start_time) * 1000

            parts: list[str] = []
            is_error = raw_result.is_error

            for block in raw_result.content:
                if hasattr(block, "text"):
                    parts.append(block.text)
                else:
                    parts.append(str(block))

            output = "\n".join(parts)
            logger.info(
                "MCP tool '%s' result: isError=%s, output=%r",
                tool_name,
                is_error,
                output[:500],
            )
            return ToolExecutionResult(
                tool_name=tool_name,
                success=not is_error,
                output=output,
                error=output if is_error else None,
                execution_time_ms=execution_time,
            )
        except Exception as e:
            execution_time = (time.time() - start_time) * 1000
            logger.exception("MCP tool '%s' raised exception (args=%s)", tool_name, arguments)
            return ToolExecutionResult(
                tool_name=tool_name,
                success=False,
                output="",
                error=str(e),
                execution_time_ms=execution_time,
            )

    async def execute_parallel(
        self,
        tool_calls: list[tuple[str, dict[str, Any]]],
        context: dict[str, Any] | None = None,
    ) -> list[ToolExecutionResult]:
        tasks = [self.execute(name, args, context) for name, args in tool_calls]
        return list(await asyncio.gather(*tasks))

    def get_definitions(self) -> list[ToolDefinition]:
        return list(self._definitions)

    def register_tool(
        self,
        name: str,
        func: Any,
        description: str,
        parameters: list[ToolParameter],
        requires_confirmation: bool = False,
    ) -> None:
        raise NotImplementedError(
            "MCPToolAdapter does not support manual tool registration. "
            "Tools are discovered from MCP servers at connection time."
        )
