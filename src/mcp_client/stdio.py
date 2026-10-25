"""Stdio Transport MCP client.

Spawns a child process over stdin/stdout using the MCP JSON-RPC
protocol. Use this for *local* MCP servers (the classic pattern
Claude Desktop / Cline / Cursor use):
    - stdio.qdrant  -> python -m src.mcp_server.server
    - stdio.github  -> github-mcp-server binary
    - stdio.fetch   -> uvx mcp-server-fetch
"""
from __future__ import annotations

import asyncio
import datetime
import functools
import json
import logging
import shlex
import threading
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .base import McpCallResult, McpClient, McpTool

logger = logging.getLogger(__name__)


def _serialized(fn):
    """v0.9.45.0 — serialize calls that drive the client's ONE asyncio
    event loop. ``loop.run_until_complete`` is not thread-safe: two
    threads racing it raise ``RuntimeError: This event loop is already
    running`` (or worse, interleave JSON-RPC traffic). Parallel turn
    sources (GUI chat + scheduler + webhook + telegram) share the same
    ``McpManager``, so every client method that touches the loop holds
    a per-client ``RLock`` — re-entrant so the connect()-failure
    ``close()`` path cannot self-deadlock."""
    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        with self._call_lock:
            return fn(self, *args, **kwargs)
    return wrapper


class StdioMcpClient(McpClient):
    """Spawn a child process, talk MCP over its stdin/stdout.

    Parameters
    ----------
    server_name : str
        Logical name (e.g. ``"qdrant"``). Used for logs only.
    command : list[str]
        The argv to launch. Example: ``["python", "-m",
        "src.mcp_server.server"]``.
    env : dict[str, str] | None
        Extra env vars merged onto ``os.environ`` for the child.
        Use ``None`` to inherit the parent environment.
    cwd : str | None
        Optional working directory for the child.
    """

    def __init__(
        self,
        server_name: str,
        command: list[str],
        env: dict[str, str] | None = None,
        cwd: str | None = None,
    ):
        super().__init__(server_name)
        self.command = list(command)
        self.env = env
        self.cwd = cwd
        self._cm = None
        self._session: ClientSession | None = None
        self._tools: list[McpTool] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        # v0.9.45.0: see _serialized — one event loop, one lock.
        self._call_lock = threading.RLock()

    @_serialized
    def connect(self) -> None:
        if self._connected:
            return
        self._loop = asyncio.new_event_loop()
        try:
            self._cm = stdio_client(
                StdioServerParameters(
                    command=self.command[0],
                    args=self.command[1:],
                    env=self.env,
                    cwd=self.cwd,
                )
            )
            read, write = self._loop.run_until_complete(self._cm.__aenter__())
            self._session = ClientSession(read, write)
            self._loop.run_until_complete(self._session.__aenter__())
            self._loop.run_until_complete(self._session.initialize())
            self._connected = True
            logger.info(
                "[mcp:stdio:%s] connected: %s",
                self.server_name,
                shlex.join(self.command),
            )
        except Exception as e:
            logger.error("[mcp:stdio:%s] connect failed: %r", self.server_name, e)
            self.close()
            raise RuntimeError(
                f"MCP stdio connect failed for {self.server_name!r}: {e!r}"
            ) from e

    @_serialized
    def close(self) -> None:
        if not self._connected and self._cm is None and self._session is None:
            return
        try:
            if self._loop is not None and self._session is not None:
                try:
                    self._loop.run_until_complete(
                        self._session.__aexit__(None, None, None)
                    )
                except Exception as e:
                    logger.debug(
                        "[mcp:stdio:%s] session close: %r",
                        self.server_name,
                        e,
                    )
            if self._loop is not None and self._cm is not None:
                try:
                    self._loop.run_until_complete(
                        self._cm.__aexit__(None, None, None)
                    )
                except Exception as e:
                    logger.debug(
                        "[mcp:stdio:%s] transport close: %r",
                        self.server_name,
                        e,
                    )
        finally:
            if self._loop is not None:
                try:
                    self._loop.close()
                except Exception:
                    pass
            self._session = None
            self._cm = None
            self._loop = None
            self._tools = None
            self._connected = False

    # ─── tool surface ───────────────────────────────────────────

    @_serialized
    def list_tools(self) -> list[McpTool]:
        self._require_connected()
        if self._tools is not None:
            return self._tools
        try:
            result = self._loop.run_until_complete(self._session.list_tools())
        except Exception as e:
            raise RuntimeError(f"list_tools failed: {e!r}") from e
        out: list[McpTool] = []
        for t in result.tools:
            out.append(
                McpTool(
                    name=t.name,
                    description=t.description or "",
                    input_schema=t.inputSchema
                    or {"type": "object", "properties": {}},
                    server_name=self.server_name,
                )
            )
        self._tools = out
        return out

    @_serialized
    def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        timeout: float | None = None,
    ) -> McpCallResult:
        self._require_connected()
        try:
            to = datetime.timedelta(seconds=timeout) if timeout else None
            res = self._loop.run_until_complete(
                self._session.call_tool(
                    name=name,
                    arguments=arguments or {},
                    read_timeout_seconds=to,
                )
            )
        except Exception as e:
            raise RuntimeError(f"call_tool({name}) failed: {e!r}") from e
        return _content_to_result(
            getattr(res, "content", []) or [],
            bool(getattr(res, "isError", False)),
        )

    def _require_connected(self) -> None:
        if (
            not self._connected
            or self._session is None
            or self._loop is None
        ):
            raise RuntimeError(
                f"MCP stdio client {self.server_name!r} is not connected"
            )


def _content_to_result(content: list, is_error: bool) -> McpCallResult:
    """Collapse ``list[TextContent|ImageContent|...]`` to one string."""
    chunks: list[str] = []
    for block in content or []:
        btype = getattr(block, "type", None)
        if btype == "text":
            chunks.append(getattr(block, "text", "") or "")
        elif btype == "image":
            mime = getattr(block, "mimeType", "image")
            chunks.append(f"[image: {mime}]")
        elif btype == "audio":
            mime = getattr(block, "mimeType", "audio")
            chunks.append(f"[audio: {mime}]")
        elif btype in ("resource", "resource_link"):
            uri = (
                getattr(block, "uri", "")
                or getattr(block, "name", "?")
            )
            chunks.append(f"[resource: {uri}]")
        else:
            try:
                dumped = (
                    block.model_dump()
                    if hasattr(block, "model_dump")
                    else str(block)
                )
            except Exception:
                dumped = str(block)
            chunks.append(json.dumps(dumped, ensure_ascii=False))
    text = "\n".join(c for c in chunks if c).strip()
    return McpCallResult(text=text, is_error=bool(is_error))
