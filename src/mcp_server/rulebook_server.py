"""MCP server: agent RULE BOOK tools (v0.9.124.0).

The agent-maintained rule file borrowed from Memento 3 (arXiv
2610.11794, the rulebook工件) per the self-evolving-agent 譜系 report:
the LLM revises its own reusable rules WHEN THE ENVIRONMENT BREAKS ITS
EXPECTATIONS — a failed tool call is treated as a prediction error
(observe→reflect→revise), and every REVISE must cite evidence
(fail-closed, the Evo² evidence gate). Rollback = a zero-dep snapshot
ring (no git required):

    rules_list       id / title / text / evidence / updated
    rules_add        new rule (confirm=true required; evidence encouraged)
    rules_revise     replace a rule — evidence REQUIRED (fail-closed)
    rules_rollback   restore from the Nth most recent snapshot
                     (confirm=true required)
    rules_history    snapshot ring + latest unified diff

Transport
---------
Run as a stdio MCP server:

    python -m src.mcp_server.rulebook_server

Add to ``config/mcp.json``::

    {
      "name": "rulebook",
      "transport": "stdio",
      "command": ["python", "-m", "src.mcp_server.rulebook_server"]
    }

The in-process LocalHost (``RulebookLocalHost`` in ``src/mcp_client/
host.py``, server ``rulebook-local``) registers the SAME tool surface
inside the GUI/agent process.
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

_TOOL_NAMES = (
    "rules_list", "rules_add", "rules_revise",
    "rules_rollback", "rules_history",
    "toolroute_override_add", "toolroute_override_list",
)

_DISPATCH_ATTRS = {
    "rules_list": "_op_list",
    "rules_add": "_op_add",
    "rules_revise": "_op_revise",
    "rules_rollback": "_op_rollback",
    "rules_history": "_op_history",
    "toolroute_override_add": "_op_tro_add",
    "toolroute_override_list": "_op_tro_list",
}


def _err(message: str) -> list[TextContent]:
    return [TextContent(type="text", text=f"Error: {message}")]


def _ok(payload: dict[str, Any]) -> list[TextContent]:
    return [TextContent(
        type="text", text=json.dumps(payload, ensure_ascii=False),
    )]


def _build_tool_specs() -> list[Tool]:
    from src.mcp_client.host import RulebookLocalHost

    specs: list[Tool] = []
    for t in RulebookLocalHost().list_tools():
        specs.append(Tool(
            name=t.name, description=t.description,
            inputSchema=t.input_schema,
        ))
    return specs


_TOOL_SPECS = _build_tool_specs()


class RulebookMCPServer:
    """MCP server exposing the agent rule book host."""

    def __init__(self):
        from src.mcp_client.host import RulebookLocalHost

        self.host = RulebookLocalHost()
        self.server = Server("divine-sense-agent-rulebook")
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
                attr = _DISPATCH_ATTRS.get(name)
                handler = (
                    getattr(self.host, attr) if attr else None
                )
                if handler is None:
                    return _err(f"Unknown tool: {name}")
                return await asyncio.to_thread(handler, arguments or {})
            except Exception as e:
                logger.exception(
                    "[rulebook-mcp] tool %r failed: %r", name, e,
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
    server = RulebookMCPServer()
    asyncio.run(server.run_stdio())