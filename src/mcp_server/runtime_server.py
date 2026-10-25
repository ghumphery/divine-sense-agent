"""MCP server: runtime control (v0.9.43.0+).

Tools: reload_config / restart_services / restart_mcp_servers /
get_runtime_status — the MCP-facing core functions of the v0.9.42.0
config hot-reload + service-restart mechanism.

Note: reload_config / restart_services / get_runtime_status delegate
to the Agent (in-process). This stdio mirror runs WITHOUT an Agent,
so those three return a "not wired" envelope; restart_mcp_servers
works fully (it talks to the manager only). External MCP clients
should drive the reload via `python cli.py reload` instead.

Transport
---------
    python -m src.mcp_server.runtime_server
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

logger = logging.getLogger(__name__)


def _err(message: str) -> list[TextContent]:
    return [TextContent(type="text", text=f"Error: {message}")]


def _build_tool_specs() -> list[Tool]:
    from src.mcp_client.host import RuntimeLocalHost

    specs: list[Tool] = []
    for t in RuntimeLocalHost().list_tools():
        specs.append(Tool(
            name=t.name, description=t.description,
            inputSchema=t.input_schema,
        ))
    return specs


_TOOL_SPECS = _build_tool_specs()


class RuntimeMCPServer:
    """MCP server exposing the runtime control tools."""

    def __init__(self):
        from src.mcp_client.host import RuntimeLocalHost

        self.host = RuntimeLocalHost()
        self.server = Server("divine-sense-agent-runtime")
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
                "reload_config": self.host._reload,
                "restart_services": self.host._restart_services,
                "restart_mcp_servers": self.host._restart_mcp,
                "get_runtime_status": self.host._status,
            }.get(name)
            if handler is None:
                return _err(f"Unknown tool: {name}")
            try:
                return [
                    TextContent(type="text",
                                text=str(handler(arguments or {})))
                ]
            except Exception as e:
                logger.exception(
                    "[runtime-mcp] tool %r failed: %r", name, e,
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
    server = RuntimeMCPServer()
    asyncio.run(server.run_stdio())


if __name__ == "__main__":
    main()