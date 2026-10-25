"""MCP server: Google Keep 記事本工具 (v0.9.88.0+).

Exposes the ``keep-local`` tool surface (list/create/get/delete +
status) to external MCP clients (Claude Desktop, Cline, ...).
Mock-first: ``KEEP_MODE=mock`` (the default) runs a fully simulated
in-memory note book; live mode uses the official Keep API v1 REST
(keep.googleapis.com) with OAuth2 (token from ``KEEP_TOKEN_PATH``).

Transport
---------
Run as a stdio MCP server:

    python -m src.mcp_server.keep_server

Add to ``config/mcp.json``::

    {
      "name": "keep",
      "transport": "stdio",
      "command": ["python", "-m", "src.mcp_server.keep_server"]
    }

Note: the **in-process** LocalHost (``KeepLocalHost`` in
``src/mcp_client/host.py``) registers the *same* tool surface
automatically.
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
    from src.mcp_client.host import KeepLocalHost

    specs: list[Tool] = []
    for t in KeepLocalHost().list_tools():
        specs.append(Tool(
            name=t.name, description=t.description,
            inputSchema=t.input_schema,
        ))
    return specs


_TOOL_SPECS = _build_tool_specs()


class KeepMCPServer:
    """MCP server exposing the Keep tools to an LLM agent."""

    def __init__(self):
        from src.mcp_client.host import KeepLocalHost

        self.host = KeepLocalHost()
        self.server = Server("divine-sense-agent-keep")
        self._register_tools()

    def _tool_specs(self) -> list[Tool]:
        return list(_TOOL_SPECS)

    def _register_tools(self) -> None:
        @self.server.list_tools()
        async def _list() -> list[Tool]:
            return list(_TOOL_SPECS)

        @self.server.call_tool()
        async def _call(name: str, arguments: dict) -> list[TextContent]:
            handler = {
                "keep_list_notes": self.host._list_notes,
                "keep_create_note": self.host._create_note,
                "keep_update_note": self.host._update_note,
                "keep_preview_note": self.host._preview_note,
                "keep_get_note": self.host._get_note,
                "keep_delete_note": self.host._delete_note,
                "keep_status": self.host._status,
            }.get(name)
            if handler is None:
                return _err(f"Unknown tool: {name}")
            try:
                return _ok(handler(arguments or {}))
            except Exception as e:
                logger.exception(
                    "[keep-mcp] tool %r failed: %r", name, e,
                )
                return _err(f"{type(e).__name__}: {e}")

    async def run_stdio(self) -> None:
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
    server = KeepMCPServer()
    asyncio.run(server.run_stdio())


if __name__ == "__main__":
    main()