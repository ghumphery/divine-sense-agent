"""Remote MCP transports (v0.8.0+).

Supports both transports:

* **Streamable HTTP** (MCP 2025-03-26+ spec) — single HTTP endpoint
  that speaks JSON or SSE in either direction. Default for new
  servers; preferred in v0.8.0.
* **Legacy SSE** (MCP 2024-11-05 spec) — separate ``GET /sse`` for
  server-to-client push + ``POST /messages`` for client-to-server.
  Many third-party servers (Notion / Linear / Cloudflare) still ship
  with this transport.

Use ``HttpSseMcpClient(transport="streamable_http"|"sse")`` from
``McpManager.from_config()`` based on the config's ``"transport"``
field. Both transports share the same ``McpClient`` interface so the
rest of the stack does not need to care which wire it is on.
"""
from __future__ import annotations

import asyncio
import datetime
import logging
import threading
from typing import Any

from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamablehttp_client

from .base import McpCallResult, McpClient, McpTool
from .stdio import _content_to_result  # content -> string helper
from .stdio import _serialized  # v0.9.45.0 shared loop-serialization lock

logger = logging.getLogger(__name__)


VALID_TRANSPORTS = ("streamable_http", "sse")


class HttpSseMcpClient(McpClient):
    """Remote MCP client over Streamable HTTP or legacy SSE.

    Parameters
    ----------
    server_name : str
        Logical name (e.g. ``"notion"``). Used only for logs.
    url : str
        Endpoint URL. For streamable-http this is the single ``/mcp``
        endpoint. For legacy SSE this is the ``GET /sse`` URL.
    headers : dict[str, str] | None
        Optional HTTP headers (auth tokens, etc).
    transport : str
        ``"streamable_http"`` (default) or ``"sse"``.
    timeout : float
        Per-request HTTP timeout in seconds (default 30).
    sse_read_timeout : float
        SSE keepalive timeout in seconds (default 300).
    """

    def __init__(
        self,
        server_name: str,
        url: str,
        headers: dict[str, str] | None = None,
        transport: str = "streamable_http",
        timeout: float = 30.0,
        sse_read_timeout: float = 300.0,
    ):
        super().__init__(server_name)
        if transport not in VALID_TRANSPORTS:
            raise ValueError(
                f"unknown transport {transport!r}; "
                "use one of " + ", ".join(VALID_TRANSPORTS)
            )
        self.transport = transport
        self.url = url
        self.headers = headers
        self.timeout = timeout
        self.sse_read_timeout = sse_read_timeout
        self._cm = None
        self._session: ClientSession | None = None
        self._tools: list[McpTool] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        # v0.9.45.0: see stdio._serialized — one event loop, one lock.
        self._call_lock = threading.RLock()

    @_serialized
    def connect(self) -> None:
        if self._connected:
            return
        self._loop = asyncio.new_event_loop()
        try:
            if self.transport == "streamable_http":
                to = datetime.timedelta(seconds=self.timeout)
                sse_to = datetime.timedelta(seconds=self.sse_read_timeout)
                self._cm = streamablehttp_client(
                    url=self.url,
                    headers=self.headers,
                    timeout=to,
                    sse_read_timeout=sse_to,
                )
                # yields (read, write, get_session_id)
                read, write, _get_sid = self._loop.run_until_complete(
                    self._cm.__aenter__()
                )
            else:  # legacy SSE
                self._cm = sse_client(
                    url=self.url,
                    headers=self.headers,
                    timeout=self.timeout,
                    sse_read_timeout=self.sse_read_timeout,
                )
                read, write = self._loop.run_until_complete(
                    self._cm.__aenter__()
                )

            self._session = ClientSession(read, write)
            self._loop.run_until_complete(self._session.__aenter__())
            self._loop.run_until_complete(self._session.initialize())
            self._connected = True
            logger.info(
                "[mcp:%s:%s] connected: %s",
                self.transport,
                self.server_name,
                self.url,
            )
        except Exception as e:
            logger.error(
                "[mcp:%s:%s] connect failed: %r",
                self.transport, self.server_name, e,
            )
            self.close()
            raise RuntimeError(
                f"MCP {self.transport} connect failed for "
                f"{self.server_name!r}: {e!r}"
            ) from e

    def _require_connected(self) -> None:
        if not self._connected or self._session is None or self._loop is None:
            raise RuntimeError(
                f"MCP {self.transport} client {self.server_name!r} "
                "is not connected"
            )

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
                        "[mcp:%s:%s] session close: %r",
                        self.transport, self.server_name, e,
                    )
            if self._loop is not None and self._cm is not None:
                try:
                    self._loop.run_until_complete(
                        self._cm.__aexit__(None, None, None)
                    )
                except Exception as e:
                    logger.debug(
                        "[mcp:%s:%s] transport close: %r",
                        self.transport, self.server_name, e,
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
