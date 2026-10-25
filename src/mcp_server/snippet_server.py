"""MCP server: ⚡ snippets (quick phrases) management (v0.9.122.0).

Exposes the GLOBAL multi-line snippet store so an external MCP client
(Claude Desktop, Cline, ...) can manage the SAME file the chat ⚡
panel uses ($QDRANT_RAG_HOME/snippets.json; entries are plain
multiline strings — inner newlines kept verbatim):

    snippet_list       index + preview + multi flag + full text
    snippet_add        add one (text may be multi-line; at = position)
    snippet_edit       replace one (multi-line verbatim)
    snippet_delete     remove one by index
    snippet_move       reorder (index → to)

Transport
---------
Run as a stdio MCP server:

    python -m src.mcp_server.snippet_server

Add to ``config/mcp.json``::

    {
      "name": "snippets",
      "transport": "stdio",
      "command": ["python", "-m", "src.mcp_server.snippet_server"]
    }

The in-process host (``SnippetsLocalHost`` in src/mcp_client/host.py,
server ``snippets-local``) shares the identical surface inside the
GUI/agent process.
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
    from src.mcp_client.host import SnippetsLocalHost

    return [
        Tool(name=t.name, description=t.description,
             inputSchema=t.input_schema)
        for t in SnippetsLocalHost().list_tools()
    ]


_TOOL_SPECS = _build_tool_specs()


class SnippetMCPServer:
    """MCP server exposing the snippets host."""

    def __init__(self):
        from src.mcp_client.host import SnippetsLocalHost

        self.host = SnippetsLocalHost()
        self.server = Server("divine-sense-agent-snippets")
        self._register_tools()

    def _tool_specs(self) -> list[Tool]:
        return list(_TOOL_SPECS)

    def _register_tools(self) -> None:
        @self.server.list_tools()
        async def _list() -> list[Tool]:
            return list(_TOOL_SPECS)

        @self.server.call_tool()
        async def _call(name: str, arguments: dict) -> list[TextContent]:
            try:
                handler = {
                    "snippet_list": self.host._op_list,
                    "snippet_add": self.host._op_add,
                    "snippet_edit": self.host._op_edit,
                    "snippet_delete": self.host._op_delete,
                    "snippet_move": self.host._op_move,
                }.get(name)
                if handler is None:
                    return _err(f"Unknown tool: {name}")
                return await asyncio.to_thread(handler, arguments or {})
            except Exception as e:
                logger.exception(
                    "[snippets-mcp] tool %r failed: %r", name, e,
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
    server = SnippetMCPServer()
    asyncio.run(server.run_stdio())


if __name__ == "__main__":
    main()