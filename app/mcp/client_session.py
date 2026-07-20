from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
import os
import tempfile
from typing import Any

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import McpError

from app.core.config import Settings
from app.mcp.configuration import MCPServerConfig
from app.mcp.errors import MCPIntegrationError
from app.security.redaction import safe_text_preview

_BASE_ENVIRONMENT = (
    "SYSTEMROOT",
    "WINDIR",
    "PATH",
    "PATHEXT",
    "COMSPEC",
    "TEMP",
    "TMP",
    "LANG",
    "LC_ALL",
)


class SanitizedStderrCapture:
    def __init__(self, max_chars: int, directory) -> None:
        self.max_chars = max_chars
        directory.mkdir(parents=True, exist_ok=True)
        self._file = tempfile.TemporaryFile(mode="w+t", encoding="utf-8", errors="replace", dir=directory)
        self._stored_preview = ""

    def fileno(self) -> int:
        return self._file.fileno()

    def flush(self) -> None:
        self._file.flush()

    @property
    def preview(self) -> str:
        if self._file.closed:
            return self._stored_preview
        self._file.flush()
        position = self._file.tell()
        self._file.seek(0)
        value = self._file.read(self.max_chars * 2)
        self._file.seek(position)
        return safe_text_preview(value, self.max_chars)

    def close(self) -> None:
        if not self._file.closed:
            self._stored_preview = self.preview
            self._file.close()


class MCPClientSession:
    """Owns an SDK session on one dedicated task so context managers exit safely."""

    def __init__(self, config: MCPServerConfig, settings: Settings) -> None:
        self.config = config
        self.settings = settings
        self.stderr = SanitizedStderrCapture(settings.mcp_stderr_preview_chars, settings.temp_dir)
        self.initialization_result: Any | None = None
        self._commands: asyncio.Queue[tuple[str, Any, asyncio.Future[Any]]] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None
        self._ready: asyncio.Future[Any] | None = None

    async def start(self) -> Any:
        if self._task and not self._task.done():
            return self.initialization_result
        loop = asyncio.get_running_loop()
        self._ready = loop.create_future()
        self._task = asyncio.create_task(self._run(), name=f"mcp-session-{self.config.name}")
        try:
            return await asyncio.shield(self._ready)
        except Exception:
            await self.stop()
            raise

    async def list_tools(self, *, limit: int) -> list[Any]:
        return await self._request("list_tools", limit)

    async def call_tool(self, name: str, arguments: dict[str, Any], *, timeout_seconds: int) -> Any:
        return await self._request("call_tool", (name, arguments, timeout_seconds))

    async def stop(self) -> None:
        task = self._task
        if task is None:
            return
        if task.done():
            await asyncio.gather(task, return_exceptions=True)
            self._task = None
            return
        loop = asyncio.get_running_loop()
        stopped = loop.create_future()
        await self._commands.put(("disconnect", None, stopped))
        try:
            await asyncio.wait_for(asyncio.shield(stopped), timeout=5)
            await asyncio.wait_for(task, timeout=5)
        except TimeoutError:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        finally:
            self._task = None

    @property
    def running(self) -> bool:
        return bool(self._task and not self._task.done())

    async def _request(self, name: str, payload: Any) -> Any:
        if not self.running:
            raise MCPIntegrationError("mcp_server_not_connected", "MCP server is not connected")
        future = asyncio.get_running_loop().create_future()
        await self._commands.put((name, payload, future))
        return await future

    async def _run(self) -> None:
        disconnect_future: asyncio.Future[Any] | None = None
        try:
            async with self._transport() as (read_stream, write_stream):
                async with ClientSession(
                    read_stream,
                    write_stream,
                    read_timeout_seconds=timedelta(seconds=self.config.timeout_seconds),
                ) as session:
                    try:
                        self.initialization_result = await session.initialize()
                    except Exception as exc:
                        raise MCPIntegrationError("mcp_handshake_failed", "MCP initialization handshake failed") from exc
                    if self._ready and not self._ready.done():
                        self._ready.set_result(self.initialization_result)

                    while True:
                        name, payload, future = await self._commands.get()
                        if name == "disconnect":
                            disconnect_future = future
                            break
                        try:
                            if name == "list_tools":
                                result = await self._list_tools(session, int(payload))
                                if not future.done():
                                    future.set_result(result)
                            elif name == "call_tool":
                                tool_name, arguments, timeout_seconds = payload
                                protocol_request_id = str(getattr(session, "_request_id", ""))[:100]
                                result = await session.call_tool(
                                    tool_name,
                                    arguments=arguments,
                                    read_timeout_seconds=timedelta(seconds=timeout_seconds),
                                )
                                if not future.done():
                                    future.set_result((result, protocol_request_id))
                            else:  # pragma: no cover - internal invariant
                                if not future.done():
                                    future.set_exception(MCPIntegrationError("mcp_protocol_error", "Unknown MCP command"))
                        except TimeoutError as exc:
                            if not future.done():
                                future.set_exception(MCPIntegrationError("mcp_timeout", "MCP request timed out"))
                        except MCPIntegrationError as exc:
                            if not future.done():
                                future.set_exception(exc)
                        except McpError as exc:
                            code = "mcp_timeout" if int(exc.error.code) == int(httpx.codes.REQUEST_TIMEOUT) else "mcp_protocol_error"
                            if not future.done():
                                future.set_exception(MCPIntegrationError(code, "MCP request failed"))
                        except Exception as exc:
                            if not future.done():
                                future.set_exception(MCPIntegrationError("mcp_protocol_error", "MCP request failed"))
        except MCPIntegrationError as exc:
            if self._ready and not self._ready.done():
                self._ready.set_exception(exc)
        except Exception as exc:
            if self._ready and not self._ready.done():
                self._ready.set_exception(
                    MCPIntegrationError("mcp_connection_failed", "MCP transport connection failed")
                )
        finally:
            while not self._commands.empty():
                _, _, future = self._commands.get_nowait()
                if not future.done():
                    future.set_exception(MCPIntegrationError("mcp_server_not_connected", "MCP server disconnected"))
            if disconnect_future and not disconnect_future.done():
                disconnect_future.set_result(None)
            self.stderr.close()

    @staticmethod
    async def _list_tools(session: ClientSession, limit: int) -> list[Any]:
        tools: list[Any] = []
        cursor: str | None = None
        while True:
            result = await session.list_tools(cursor=cursor)
            tools.extend(result.tools)
            if len(tools) > limit:
                raise MCPIntegrationError("mcp_protocol_error", "MCP server exposed too many tools")
            cursor = result.nextCursor
            if not cursor:
                return tools

    @asynccontextmanager
    async def _transport(self) -> AsyncIterator[tuple[Any, Any]]:
        if self.config.transport == "stdio":
            params = StdioServerParameters(
                command=self.config.resolved_command,
                args=list(self.config.args),
                cwd=self.config.resolved_cwd,
                env=self._child_environment(),
                encoding="utf-8",
                encoding_error_handler="replace",
            )
            async with stdio_client(params, errlog=self.stderr) as streams:
                yield streams
            return

        headers: dict[str, str] = {}
        if self.config.auth_env_var:
            token = os.environ.get(self.config.auth_env_var)
            if token:
                headers["Authorization"] = f"Bearer {token}"
        timeout = httpx.Timeout(self.config.timeout_seconds, read=self.config.timeout_seconds)
        async with httpx.AsyncClient(headers=headers, timeout=timeout, follow_redirects=False) as client:
            assert self.config.url is not None
            async with streamable_http_client(self.config.url, http_client=client) as streams:
                yield streams[0], streams[1]

    def _child_environment(self) -> dict[str, str]:
        names = {*_BASE_ENVIRONMENT, *self.config.environment_allowlist}
        environment = {name: os.environ[name] for name in names if name in os.environ}
        environment["PYTHONIOENCODING"] = "utf-8"
        environment["PYTHONUTF8"] = "1"
        environment["PYTHONUNBUFFERED"] = "1"
        return environment
