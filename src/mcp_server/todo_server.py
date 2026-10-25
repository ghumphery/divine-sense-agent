"""MCP stdio server exposing the multi-level todo list to external
LLM clients (v0.8.8+).

Mirrors the in-process ``TodoLocalHost`` so an external MCP client
(Claude Desktop, Cline, etc.) gets the same 16-tool surface via
the official stdio transport. The runtime dispatcher here
**reuses** the LocalHost handlers - so stdio and in-process
behaviour stay in lockstep by construction. New tools only need
to be added in one place (host.py); the server pulls them in
lazily on the first list_tools / call_tool request.

Entry point: ``python -m src.mcp_server.todo_server``
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from typing import Any

logger = logging.getLogger("mcp_server.todo_server")

# `mcp` is an optional dep - guard the import so importing this
# module under tooling that lacks mcp (lint, docs) doesn't blow up.
try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import Tool, TextContent
except Exception:  # pragma: no cover - exercised only when mcp is missing
    Server = None  # type: ignore
    stdio_server = None  # type: ignore
    Tool = None  # type: ignore
    TextContent = None  # type: ignore


def _err(message: str) -> list["TextContent"]:
    return [TextContent(type="text", text=f"ERROR: {message}")]


def _ok(payload: dict[str, Any]) -> list["TextContent"]:
    import json
    return [TextContent(type="text", text=json.dumps(payload, ensure_ascii=False, indent=2))]



class TodoMCPServer:
    """MCP server exposing ``TodoManager`` to an LLM agent.

    The dispatcher maps incoming ``call_tool`` names straight
    onto the in-process ``TodoLocalHost`` handlers - one source
    of truth, zero drift between stdio and in-process paths.
    """

    def __init__(self, todos_root: str | None = None) -> None:
        if Server is None:
            raise RuntimeError(
                "mcp package is not installed; install with "
                "`pip install mcp` to use the todo stdio server."
            )
        from src.core.todo_manager import TodoManager
        from src.mcp_client.host import TodoLocalHost
        if todos_root:
            self.mgr = TodoManager(root=todos_root)
        else:
            self.mgr = TodoManager()
        # Build the in-process host and attach our manager.
        self.host = TodoLocalHost()
        self.host.attach_manager(self.mgr)
        self._tools_by_name: dict[str, Any] = {
            t.name: t for t in self.host.list_tools()
        }
        self.server = Server("divine-sense-agent-todo")
        self._register_tools()

    def _register_tools(self) -> None:
        @self.server.list_tools()
        async def _list() -> list[Tool]:
            # Re-fetch the LocalTool list (defensive in case the
            # host changes after construction) and convert to the
            # MCP ``Tool`` type. ``inputSchema`` is already a dict
            # shaped the MCP SDK accepts.
            out: list[Tool] = []
            for lt in self.host.list_tools():
                out.append(Tool(
                    name=lt.name,
                    description=lt.description,
                    inputSchema=lt.input_schema,
                ))
            return out

        @self.server.call_tool()
        async def _call(name: str, arguments: dict) -> list[TextContent]:
            lt = self._tools_by_name.get(name)
            if lt is None:
                return _err(f"Unknown tool: {name}")
            # LocalTool.__call__ is sync and returns McpCallResult.
            # We offload to a thread so the event loop stays free
            # if the handler does heavy I/O.
            def _invoke() -> Any:
                return lt(arguments or {})
            res = await asyncio.to_thread(_invoke)
            # LocalTool returns McpCallResult(text=..., is_error=...)
            return [TextContent(
                type="text",
                text=res.text,
            )]

    # ── stdio entry point ──────────────────────────────────
    async def run_stdio(self) -> None:
        """Run the MCP server over stdio."""
        async with stdio_server() as (r, w):
            await self.server.run(
                r, w, self.server.create_initialization_options(),
            )


def _build_tool_specs() -> list[Tool]:
    """Construct the 16 ``Tool()`` objects describing the public
    surface. Only used for introspection / unit tests; the
    runtime dispatcher goes through TodoLocalHost directly."""
    from src.mcp_client.host import TodoLocalHost
    if Server is None or Tool is None:
        return []
    host = TodoLocalHost()
    return [
        Tool(
            name=lt.name,
            description=lt.description,
            inputSchema=lt.input_schema,
        )
        for lt in host.list_tools()
    ]


_TOOL_SPECS = _build_tool_specs()


def main() -> None:
    """``python -m src.mcp_server.todo_server`` entry point."""
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,  # stdout is reserved for JSON-RPC
    )
    if Server is None:
        print("ERROR: mcp package not installed; pip install mcp",
              file=sys.stderr)
        sys.exit(2)
    try:
        asyncio.run(TodoMCPServer().run_stdio())
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
