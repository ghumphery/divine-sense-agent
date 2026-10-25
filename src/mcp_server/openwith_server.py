"""MCP server: open-with-associated-application (v0.9.6.0+).

Exposes the local ``OpenWithLocalHost`` as MCP tools so an LLM agent
can open chat artifacts the way Windows Explorer does — by file
association:

* ``open_file(path)``  -  open a file from the filesystem write
  sandbox with its associated application. ``.drawio`` / ``.dio``
  files (and drawio-shaped ``.xml``) are routed to the in-GUI Drawio
  Viewer when one is wired, otherwise the OS file association
  (``os.startfile`` on Windows) handles them. Executables are
  refused.
* ``open_content(name?, content)``  -  save content the model just
  generated (drawio XML, markdown, csv, plain text) into the
  sandbox AND open it in one step. Drawio-shaped content is
  force-routed to a ``.drawio`` file + the Drawio Viewer window.
* ``list_openable_types()``  -  what can be opened, how, and what
  is refused.

Transport
---------
Run as a stdio MCP server so it composes with the existing fleet:

    python -m src.mcp_server.openwith_server

Add to ``config/mcp.json``::

    {
      "name": "openwith",
      "transport": "stdio",
      "command": ["python", "-m", "src.mcp_server.openwith_server"]
    }

Note: the **in-process** LocalHost (``OpenWithLocalHost`` in
``src/mcp_client/host.py``) registers the *same* tool surface
automatically - most users won't need this standalone server at
all. The stdio server exists for external MCP clients (Claude
Desktop, Cline, etc.).

Security: paths are sandbox-scoped to
``QDRANT_FILESYSTEM_WRITE_ROOT`` (same root the
FilesystemLocalHost writes to); dangerous executables (``.exe`` /
``.bat`` / ``.sh`` / ...) are refused. ``open_content`` caps at
4 MB (same as the filesystem read cap).
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


_TOOL_SPECS: list[Tool] = []


def _build_tool_specs() -> list[Tool]:
    return [
        Tool(
            name="open_file",
            description=(
                "Open a file from the filesystem write sandbox with "
                "its associated application (Windows Explorer "
                "double-click semantics). '.drawio' / '.dio' files "
                "(and drawio-shaped '.xml') are routed to the in-GUI "
                "Drawio Viewer when available, otherwise the OS file "
                "association is used. Executables are refused."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                },
                "required": ["path"],
            },
        ),
        Tool(
            name="open_content",
            description=(
                "Save content you just generated (drawio XML, "
                "markdown, csv, plain text) into the filesystem "
                "write sandbox and open it with the associated "
                "application in one step. Drawio-shaped content is "
                "auto-routed to a .drawio file + the Drawio Viewer "
                "window."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["content"],
            },
        ),
        Tool(
            name="list_openable_types",
            description=(
                "What can be opened, how, and what is refused."
            ),
            inputSchema={"type": "object", "properties": {}},
        ),
    ]


class OpenWithMCPServer:
    """MCP server exposing the open-with host to an LLM agent."""

    def __init__(self, write_root: str | None = None):
        from src.mcp_client.host import OpenWithLocalHost
        self.host = OpenWithLocalHost()
        if write_root:
            self.host.attach_write_root(write_root)
        self.server = Server("divine-sense-agent-openwith")
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
                if name == "open_file":
                    return await asyncio.to_thread(
                        self._do_open_file, arguments,
                    )
                if name == "open_content":
                    return await asyncio.to_thread(
                        self._do_open_content, arguments,
                    )
                if name == "list_openable_types":
                    return await asyncio.to_thread(
                        self._do_list_openable, arguments,
                    )
            except Exception as e:
                logger.exception(
                    "[openwith-mcp] tool %r failed: %r", name, e,
                )
                return _err(f"{type(e).__name__}: {e}")
            return _err(f"Unknown tool: {name}")

    # ── handlers (sync, wrapped in to_thread) ──────────────────
    def _do_open_file(self, args: dict) -> list[TextContent]:
        return _ok(self.host._open_file(args))

    def _do_open_content(self, args: dict) -> list[TextContent]:
        return _ok(self.host._open_content(args))

    def _do_list_openable(self, args: dict) -> list[TextContent]:
        return _ok(self.host._list_openable(args))

    # ── stdio entry point ─────────────────────────────────────
    async def run_stdio(self) -> None:
        """Run the MCP server over stdio."""
        async with stdio_server() as (r, w):
            await self.server.run(
                r, w, self.server.create_initialization_options(),
            )


_TOOL_SPECS = _build_tool_specs()


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,  # stdout is reserved for JSON-RPC
    )
    server = OpenWithMCPServer()
    asyncio.run(server.run_stdio())


if __name__ == "__main__":
    main()