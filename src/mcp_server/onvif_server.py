"""MCP server: ONVIF camera tools (v0.9.37.0+, Phase A — READ-ONLY).

Tools: onvif_discover / onvif_favorite_add / onvif_favorite_list /
onvif_favorite_remove / onvif_test_credentials / onvif_get_profiles /
onvif_get_snapshot.

**Phase A scope**: WS-Discovery + favorites with credentials
(``password_env`` recommended) + media profiles + HTTP snapshot.
No RTSP recording (Phase B) and no settings writes (Phase C).

Transport
---------
Run as a stdio MCP server:

    python -m src.mcp_server.onvif_server
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


def _build_tool_specs() -> list[Tool]:
    from src.mcp_client.host import OnvifLocalHost

    specs: list[Tool] = []
    for t in OnvifLocalHost().list_tools():
        specs.append(Tool(
            name=t.name, description=t.description,
            inputSchema=t.input_schema,
        ))
    return specs


_TOOL_SPECS = _build_tool_specs()


class OnvifMCPServer:
    """MCP server exposing the ONVIF tools to an LLM agent."""

    def __init__(self):
        from src.mcp_client.host import OnvifLocalHost

        self.host = OnvifLocalHost()
        self.server = Server("divine-sense-agent-onvif")
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
                "onvif_discover": self.host._discover,
                "onvif_favorite_add": self.host._favorite_add,
                "onvif_favorite_list": self.host._favorite_list,
                "onvif_favorite_remove": self.host._favorite_remove,
                "onvif_test_credentials": self.host._test_credentials,
                "onvif_get_profiles": self.host._get_profiles,
                "onvif_get_snapshot": self.host._get_snapshot,
                "onvif_rtsp_snapshot": self.host._rtsp_snapshot,
                "onvif_record": self.host._record,
                "onvif_get_resources": self.host._get_resources,
                "onvif_ptz_move": self.host._ptz_move,
                "onvif_ptz_stop": self.host._ptz_stop,
                "onvif_get_presets": self.host._get_presets,
                "onvif_goto_preset": self.host._goto_preset,
                "onvif_get_events": self.host._get_events,
                "onvif_watch_start": self.host._watch_start,
                "onvif_watch_list": self.host._watch_list,
                "onvif_watch_stop": self.host._watch_stop,
            }.get(name)
            if handler is None:
                return _err(f"Unknown tool: {name}")
            try:
                return await asyncio.to_thread(handler, arguments or {})
            except Exception as e:
                logger.exception(
                    "[onvif-mcp] tool %r failed: %r", name, e,
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
    server = OnvifMCPServer()
    asyncio.run(server.run_stdio())


if __name__ == "__main__":
    main()