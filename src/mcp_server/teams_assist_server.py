"""MCP server: Teams desktop-assist tools (v0.9.69.0+).

Tools: teams_assist_status / teams_assist_check /
teams_assist_start_watch / teams_assist_stop_watch /
teams_assist_reply / teams_assist_pause / teams_assist_resume.

**Zero-Microsoft-permission design**: the assistant operates the
user's OWN logged-in Teams DESKTOP client via the windows-mcp fleet —
no Teams API, no OAuth consent, no Graph permissions. The watch loop
is a cheap screenshot-hash poll (mock-first:
``QDRANT_TEAMS_ASSIST_MODE=mock`` default never grabs the real
screen); ``teams_assist_reply`` is the write gate (AUTO switch or
``manual=true``; ``confirm=false`` records a draft only).

Note: this stdio mirror exposes the SAME tool surface as the
in-process ``TeamsAssistLocalHost``. For the assistant's AUTO-fire
loop to run inside the Agent process, use the in-process host
(registered automatically by ``McpManager.from_config``) — an
external stdio client gets the tools but its watch thread lives in
that client's process.

Transport
---------
Run as a stdio MCP server:

    python -m src.mcp_server.teams_assist_server

Add to ``config/mcp.json``::

    {
      "name": "teams-assist",
      "transport": "stdio",
      "command": ["python", "-m", "src.mcp_server.teams_assist_server"]
    }
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
    from src.mcp_client.host import TeamsAssistLocalHost

    specs: list[Tool] = []
    for t in TeamsAssistLocalHost().list_tools():
        specs.append(Tool(
            name=t.name, description=t.description,
            inputSchema=t.input_schema,
        ))
    return specs


_TOOL_SPECS = _build_tool_specs()


class TeamsAssistMCPServer:
    """MCP server exposing the Teams desktop-assist tools."""

    def __init__(self):
        from src.mcp_client.host import TeamsAssistLocalHost

        self.host = TeamsAssistLocalHost()
        self.server = Server("divine-sense-agent-teams-assist")
        self._register_tools()

    def _register_tools(self) -> None:
        @self.server.list_tools()
        async def _list() -> list[Tool]:
            return list(_TOOL_SPECS)

        @self.server.call_tool()
        async def _call(name: str, arguments: dict) -> list[TextContent]:
            handler = {
                "teams_assist_status": self.host._status,
                "teams_assist_config": self.host._config,
                "teams_assist_list_chats": self.host._list_chats,
                "teams_assist_drafts": self.host._drafts,
                "teams_assist_check": self.host._check,
                "teams_assist_start_watch": self.host._start_watch,
                "teams_assist_stop_watch": self.host._stop_watch,
                "teams_assist_reply": self.host._reply,
                "teams_assist_send_uia": self.host._send_uia,
                "teams_assist_read_uia": self.host._read_uia,
                "teams_assist_pause": self.host._pause,
                "teams_assist_resume": self.host._resume,
            }.get(name)
            try:
                if handler is None:
                    return _err(f"Unknown tool: {name}")
                return await asyncio.to_thread(
                    handler, arguments or {},
                )
            except Exception as e:
                logger.exception(
                    "[teams-assist-mcp] tool %r failed: %r", name, e,
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
    server = TeamsAssistMCPServer()
    logger.info("[teams-assist-mcp] starting (stdio)")
    asyncio.run(server.run_stdio())


if __name__ == "__main__":
    main()