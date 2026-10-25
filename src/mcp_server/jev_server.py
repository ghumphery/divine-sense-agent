"""MCP server: Jev 分類器 (v0.9.93.0+).

Exposes the ``jev-classifier-local`` tool surface (batch classify /
schema / status) to external MCP clients (Claude Desktop, Cline, ...).
Mock-first: ``JEV_MODE=mock`` (the default) runs a deterministic hash
pick; live mode runs real Jev decisions via the rerank backend
(OPENROUTER_API_KEY shared).

Transport
---------
Run as a stdio MCP server:

    python -m src.mcp_server.jev_server
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
    from src.mcp_client.host import JevLocalHost

    return [
        Tool(
            name=t.name, description=t.description,
            inputSchema=t.input_schema,
        )
        for t in JevLocalHost().list_tools()
    ]


_TOOL_SPECS = _build_tool_specs()


class JevMCPServer:
    """MCP server exposing the Jev classifier tools to an LLM agent."""

    def __init__(self):
        from src.mcp_client.host import JevLocalHost

        self.host = JevLocalHost()
        self.server = Server("divine-sense-agent-jev")
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
                "jev_classify": self.host._classify,
                "jev_schema": self.host._schema,
                "jev_classify_status": self.host._status,
            }.get(name)
            if handler is None:
                return _err(f"Unknown tool: {name}")
            try:
                return _ok(handler(arguments or {}))
            except Exception as e:
                logger.exception(
                    "[jev-mcp] tool %r failed: %r", name, e,
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
    server = JevMCPServer()
    asyncio.run(server.run_stdio())


if __name__ == "__main__":
    main()