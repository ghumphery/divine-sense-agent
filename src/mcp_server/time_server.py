"""MCP server: current date / time query (v0.9.11.0+).

Exposes ``get_current_time`` — the LOCAL time (ISO 8601 with UTC
offset + timezone name) and UTC+0 time, plus Unix epoch seconds and
weekday — so an external MCP client (Claude Desktop, Cline, ...) can
answer '現在幾點' / 'what time is it' / 'today's date' without
guessing the clock.

Transport
---------
Run as a stdio MCP server so it composes with the existing fleet:

    python -m src.mcp_server.time_server

Add to ``config/mcp.json``::

    {
      "name": "time",
      "transport": "stdio",
      "command": ["python", "-m", "src.mcp_server.time_server"]
    }

Note: the **in-process** LocalHost (``DateTimeLocalHost`` in
``src/mcp_client/host.py``) registers the *same* tool surface
automatically - most users won't need this standalone server at
all. The stdio server exists for external MCP clients.

Read-only, zero arguments, no sandbox concerns.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

logger = logging.getLogger(__name__)


def _err(message: str) -> list[TextContent]:
    return [TextContent(type="text", text=f"Error: {message}")]


def _ok(payload: dict[str, Any]) -> list[TextContent]:
    return [TextContent(
        type="text", text=json.dumps(payload, ensure_ascii=False),
    )]


def _build_tool_specs() -> list[Tool]:
    return [
        Tool(
            name="get_current_time",
            description=(
                "Current date and time — LOCAL time (with UTC offset "
                "and timezone name) and UTC+0 time, plus Unix epoch "
                "seconds and weekday. Call this whenever the user "
                "asks '現在幾點' / '今天幾號' / 'what time is it' / "
                "'today's date' — never guess from memory."
            ),
            inputSchema={"type": "object", "properties": {}},
        ),
    ]


_TOOL_SPECS = _build_tool_specs()


class TimeMCPServer:
    """MCP server exposing the date/time host to an LLM agent."""

    def __init__(self):
        from src.mcp_client.host import DateTimeLocalHost

        self.host = DateTimeLocalHost()
        self.server = Server("divine-sense-agent-time")
        self._register_tools()

    # Tool list lives in a method so unit tests can call it
    # without spinning up the full Server.
    def _tool_specs(self) -> list[Tool]:
        return list(_TOOL_SPECS)

    def _register_tools(self) -> None:
        @self.server.list_tools()
        async def _list() -> list[Tool]:
            return list(_TOOL_SPECS)

        @self.server.call_tool()
        async def _call(name: str, arguments: dict) -> list[TextContent]:
            try:
                if name == "get_current_time":
                    return await asyncio.to_thread(
                        self._do_now, arguments,
                    )
            except Exception as e:
                logger.exception(
                    "[time-mcp] tool %r failed: %r", name, e,
                )
                return _err(f"{type(e).__name__}: {e}")
            return _err(f"Unknown tool: {name}")

    # ── handlers (sync, wrapped in to_thread) ──────────────────
    def _do_now(self, args: dict) -> list[TextContent]:
        return _ok(self.host._now(args))

    # ── stdio entry point ─────────────────────────────────────
    async def run_stdio(self) -> None:
        """Run the MCP server over stdio."""
        async with stdio_server() as (r, w):
            await self.server.run(
                r, w, self.server.create_initialization_options(),
            )


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,  # stdout is reserved for JSON-RPC
    )
    server = TimeMCPServer()
    asyncio.run(server.run_stdio())


if __name__ == "__main__":
    main()