"""Stdio MCP server — Qdrant long-term memory (v0.9.59.0+).

Exposes the SEVEN tools (v0.9.127.0 adds the DRY-RUN maintenance
report; v0.9.133.3 adds the T+1 session-summary compactor) as the
in-process ``MemoryLocalHost`` (``remember_info`` / ``search_memory`` /
``list_recent_memories`` / ``forget_memory`` / ``purge_memories`` /
``suggest_memory_cleanup`` / ``summarize_sessions``) to EXTERNAL MCP
clients (Claude Desktop, Cline, ...). Run:

    python -m src.mcp_server.memory_server

stdout is reserved for the MCP JSON-RPC channel; logs go to stderr.
"""
from __future__ import annotations

import asyncio
import os
import sys

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from src.mcp_client.host import MemoryLocalHost

_host = MemoryLocalHost()
_server = Server("divine-sense-agent-memory")


@_server.list_tools()
async def _list() -> list[Tool]:
    out: list[Tool] = []
    for t in _host.list_tools():
        out.append(Tool(
            name=t.name, description=t.description,
            inputSchema=t.input_schema,
        ))
    return out


_DISPATCH = {
    "remember_info": _host._remember_info,
    "search_memory": _host._search_memory,
    "list_recent_memories": _host._list_recent,
    "forget_memory": _host._forget,
    "purge_memories": _host._purge,
    "suggest_memory_cleanup": _host._suggest_cleanup,
    # v0.9.133.3 — T+1 session-summary compactor (UniiChat-inspired)
    "summarize_sessions": _host._summarize_sessions,
}


@_server.call_tool()
async def _call(name: str, arguments: dict) -> list[TextContent]:
    handler = _DISPATCH.get(name)
    if handler is None:
        return [TextContent(
            type="text", text=f"Unknown tool: {name}",
        )]
    try:
        result = handler(arguments or {})
    except Exception as e:
        return [TextContent(type="text", text=f"Error: {e}")]
    import json as _json

    return [TextContent(
        type="text", text=_json.dumps(result, ensure_ascii=False),
    )]


async def main() -> None:
    async with stdio_server() as (r, w):
        await _server.run(r, w, _server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())