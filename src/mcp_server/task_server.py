"""MCP server: background-task registry tools (v0.9.45.0 Phase 2).

Exposes the process-wide background-task tools so an external MCP
client (Claude Desktop, Cline, ...) can submit a one-shot background
LLM turn and observe running/finished tasks:

    run_task_once    submit ONE background LLM turn (returns immediately)
    list_tasks       running + recent background turns (all sources)
    get_task_result  status + session id + answer preview
    list_task_models openrouter / ollama model catalog for llm pins
    stop_task        force-stop a running/zombie task (cooperative)

Transport
---------
Run as a stdio MCP server:

    python -m src.mcp_server.task_server

Add to ``config/mcp.json``::

    {
      "name": "task",
      "transport": "stdio",
      "command": ["python", "-m", "src.mcp_server.task_server"]
    }

Note: the **in-process** LocalHost (``TaskLocalHost`` in
``src/mcp_client/host.py``) registers the *same* tool surface
automatically — inside the GUI/agent process the tasks share THAT
process's registry and session store. This standalone server keeps
its OWN in-memory registry (a separate Python process), so
``run_task_once`` results persist only in the session files it
writes (``task-<id>.json``). Prefer the in-process surface for the
agent chat.
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
    from src.mcp_client.host import TaskLocalHost

    specs: list[Tool] = []
    for t in TaskLocalHost().list_tools():
        specs.append(Tool(
            name=t.name, description=t.description,
            inputSchema=t.input_schema,
        ))
    return specs


_TOOL_SPECS = _build_tool_specs()


class TaskMCPServer:
    """MCP server exposing the task host to an LLM agent."""

    def __init__(self):
        from src.mcp_client.host import TaskLocalHost

        self.host = TaskLocalHost()
        self.server = Server("divine-sense-agent-task")
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
                    "run_task_once": self._do_run_once,
                    "list_tasks": self._do_list,
                    "get_task_result": self._do_get,
                    "list_task_models": self._do_models,
                    "stop_task": self._do_stop,
                }.get(name)
                if handler is None:
                    return _err(f"Unknown tool: {name}")
                return await asyncio.to_thread(handler, arguments or {})
            except Exception as e:
                logger.exception(
                    "[task-mcp] tool %r failed: %r", name, e,
                )
                return _err(f"{type(e).__name__}: {e}")

    # ── handlers (sync, wrapped in to_thread) ──────────────────
    def _do_run_once(self, args: dict) -> list[TextContent]:
        return _ok(self.host._run_once(args))

    def _do_list(self, args: dict) -> list[TextContent]:
        return _ok(self.host._list(args))

    def _do_get(self, args: dict) -> list[TextContent]:
        return _ok(self.host._get(args))

    def _do_models(self, args: dict) -> list[TextContent]:
        return _ok(self.host._models(args))

    def _do_stop(self, args: dict) -> list[TextContent]:
        return _ok(self.host._stop(args))

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
    server = TaskMCPServer()
    asyncio.run(server.run_stdio())


if __name__ == "__main__":
    main()