"""MCP server: chat-history query tools (v0.9.21.0+).

Exposes three READ-ONLY tools over the persisted chat sessions
(``$QDRANT_RAG_HOME/sessions/<sid>.json`` via
``src.agent.session.SessionManager``) so an external MCP client
(Claude Desktop, Cline, ...) can query historical conversations:

    list_chat_sessions    enumerate sessions, newest first
    search_chat_history   keyword search across message content
    get_chat_session      read one session's messages (bounded)

Transport
---------
Run as a stdio MCP server:

    python -m src.mcp_server.chat_history_server

Add to ``config/mcp.json``::

    {
      "name": "chat-history",
      "transport": "stdio",
      "command": ["python", "-m", "src.mcp_server.chat_history_server"]
    }

Note: the **in-process** LocalHost (``ChatHistoryLocalHost`` in
``src/mcp_client/host.py``) registers the *same* tool surface
automatically - most users won't need this standalone server at
all. The stdio server exists for external MCP clients.

Read-only. Built-in caps (result counts, snippet lengths) keep a
runaway history dump from blowing the client's context window.
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
    from src.mcp_client.host import ChatHistoryLocalHost

    specs: list[Tool] = []
    for t in ChatHistoryLocalHost().list_tools():
        specs.append(Tool(
            name=t.name, description=t.description,
            inputSchema=t.input_schema,
        ))
    return specs


_TOOL_SPECS = _build_tool_specs()


class ChatHistoryMCPServer:
    """MCP server exposing the chat-history host to an LLM agent."""

    def __init__(self):
        from src.mcp_client.host import ChatHistoryLocalHost

        self.host = ChatHistoryLocalHost()
        self.server = Server("divine-sense-agent-chat-history")
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
                handler = {
                    "list_chat_sessions": self._do_list,
                    "search_chat_history": self._do_search,
                    "get_chat_session": self._do_get,
                }.get(name)
                if handler is None:
                    return _err(f"Unknown tool: {name}")
                return await asyncio.to_thread(handler, arguments or {})
            except Exception as e:
                logger.exception(
                    "[chat-history-mcp] tool %r failed: %r", name, e,
                )
                return _err(f"{type(e).__name__}: {e}")

    # ── handlers (sync, wrapped in to_thread) ──────────────────
    def _do_list(self, args: dict) -> list[TextContent]:
        return _ok(self.host._list(args))

    def _do_search(self, args: dict) -> list[TextContent]:
        return _ok(self.host._search(args))

    def _do_get(self, args: dict) -> list[TextContent]:
        return _ok(self.host._get(args))

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
    server = ChatHistoryMCPServer()
    asyncio.run(server.run_stdio())


if __name__ == "__main__":
    main()