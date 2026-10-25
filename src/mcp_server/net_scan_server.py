"""MCP server: LAN IP scanner + TCP port scanner (v0.9.40.0+).

Tools: net_scan_hosts / net_scan_ports / net_my_interfaces.

**Safety**: private-network targets only by default; public/internet
targets are refused unless QDRANT_NETSCAN_ALLOW_PUBLIC=on. TCP-connect
probes only — no payloads, no exploitation.

Transport
---------
Run as a stdio MCP server:

    python -m src.mcp_server.net_scan_server
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
    from src.mcp_client.host import NetScanLocalHost

    specs: list[Tool] = []
    for t in NetScanLocalHost().list_tools():
        specs.append(Tool(
            name=t.name, description=t.description,
            inputSchema=t.input_schema,
        ))
    return specs


_TOOL_SPECS = _build_tool_specs()


class NetScanMCPServer:
    """MCP server exposing the network scanner tools to an LLM agent."""

    def __init__(self):
        from src.mcp_client.host import NetScanLocalHost

        self.host = NetScanLocalHost()
        self.server = Server("divine-sense-agent-net-scan")
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
                "net_scan_hosts": self.host._scan_hosts,
                "net_scan_ports": self.host._scan_ports,
                "net_my_interfaces": self.host._my_interfaces,
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
                    "[net-scan-mcp] tool %r failed: %r", name, e,
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
    server = NetScanMCPServer()
    asyncio.run(server.run_stdio())


if __name__ == "__main__":
    main()