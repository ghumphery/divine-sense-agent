"""MCP server: tacti (tactical plan) / workflow compiler tools
(v0.9.109.0; tacti-local since v0.9.120.3 — formerly ``skill-local``).

Exposes the tacti / workflow COMPILER + plan
executor so an external MCP client (Claude Desktop, Cline, ...) can
compile multi-turn work into a wave-plan and run it:

    compile_plan      validate + optimize (deps/waves/caps/verify)
                      → plan_id
    run_plan          execute / resume (wave driver in the background)
    get_plan_status   per-stage status / verify results / errors
    list_plans        the compiled-plan library (NOT the SKILL.md
                      file library — that is skill-library-local)
    run_plan_stage    retry ONE failed/skipped stage
    stop_plan         abort a running plan
    delete_plan       delete a compiled plan (confirm-gated; force =
                      stop+delete a RUNNING plan in one call)

Transport
---------
Run as a stdio MCP server:

    python -m src.mcp_server.tacti_server

Add to ``config/mcp.json``::

    {
      "name": "tacti",
      "transport": "stdio",
      "command": ["python", "-m", "src.mcp_server.tacti_server"]
    }

Note: the in-process LocalHost (``TactiLocalHost`` in
``src/mcp_client/host.py``) registers the SAME tool surface inside the
GUI/agent process — there the background stage agents share that
process's ``McpManager`` (no ~15 s fleet rebuild) and plan/stage
notifications ride the app's notify wiring. This standalone server
builds its own fresh Agents per stage.
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
    "compile_plan", "run_plan", "get_plan_status",
    "list_plans", "run_plan_stage", "stop_plan",
)


def _err(message: str) -> list[TextContent]:
    return [TextContent(type="text", text=f"Error: {message}")]


def _ok(payload: dict[str, Any]) -> list[TextContent]:
    return [TextContent(
        type="text", text=json.dumps(payload, ensure_ascii=False),
    )]


def _build_tool_specs() -> list[Tool]:
    from src.mcp_client.host import TactiLocalHost

    specs: list[Tool] = []
    for t in TactiLocalHost().list_tools():
        specs.append(Tool(
            name=t.name, description=t.description,
            inputSchema=t.input_schema,
        ))
    return specs


_TOOL_SPECS = _build_tool_specs()


class TactiMCPServer:
    """MCP server exposing the tacti/workflow compiler host."""

    def __init__(self):
        from src.mcp_client.host import TactiLocalHost

        self.host = TactiLocalHost()
        self.server = Server("divine-sense-agent-tacti")
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
                    "compile_plan": self.host._compile,
                    "run_plan": self.host._run,
                    "get_plan_status": self.host._status,
                    "list_plans": self.host._list,
                    "run_plan_stage": self.host._retry_stage,
                    "stop_plan": self.host._stop,
                    "delete_plan": self.host._delete,
                }.get(name)
                if handler is None:
                    return _err(f"Unknown tool: {name}")
                return await asyncio.to_thread(handler, arguments or {})
            except Exception as e:
                logger.exception(
                    "[tacti-mcp] tool %r failed: %r", name, e,
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
    server = TactiMCPServer()
    asyncio.run(server.run_stdio())


if __name__ == "__main__":
    main()