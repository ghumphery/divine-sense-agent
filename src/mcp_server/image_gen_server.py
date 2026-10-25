"""MCP server: ImageGen 產圖 (v0.9.104.0+).

Exposes the ``imagegen-local`` tool surface (image_generate /
image_gen_list / image_gen_models / image_gen_status) to external MCP
clients (Claude Desktop, Cline, ...).

Mock-first: ``IMAGEGEN_MODE=mock`` (the default) renders a deterministic
Pillow placeholder with NO network; live mode calls the OpenRouter
Image API (``POST /api/v1/images/generations``) reusing
``OPENROUTER_API_KEY`` — every image bills REAL money (usage.cost).
Writes are gated by per-call ``confirm=true``; live additionally needs
``IMAGEGEN_WRITES_ENABLED=on``.

Transport
---------
Run as a stdio MCP server:

    python -m src.mcp_server.image_gen_server

Note: the **in-process** LocalHost (``ImageGenLocalHost`` in
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
    from src.mcp_client.host import ImageGenLocalHost

    return [
        Tool(
            name=t.name, description=t.description,
            inputSchema=t.input_schema,
        )
        for t in ImageGenLocalHost().list_tools()
    ]


_TOOL_SPECS = _build_tool_specs()


class ImageGenMCPServer:
    """MCP server exposing the OpenRouter Image API tools to an LLM agent."""

    def __init__(self):
        from src.mcp_client.host import ImageGenLocalHost

        self.host = ImageGenLocalHost()
        self.server = Server("divine-sense-agent-imagegen")
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
                "image_generate": self.host._generate,
                "image_gen_list": self.host._list,
                "image_gen_models": self.host._models,
                "image_gen_status": self.host._status,
            }.get(name)
            if handler is None:
                return _err(f"Unknown tool: {name}")
            try:
                return _ok(handler(arguments or {}))
            except Exception as e:
                logger.exception(
                    "[imagegen-mcp] tool %r failed: %r", name, e,
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
    server = ImageGenMCPServer()
    asyncio.run(server.run_stdio())


if __name__ == "__main__":
    main()