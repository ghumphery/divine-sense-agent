"""MCP server: filesystem archive tools — zip / 7z / tar / gzip (v0.9.8+).

Exposes the local ``ArchiveLocalHost`` as MCP tools so an LLM agent
can compress and decompress files inside the filesystem sandboxes:

* ``archive_create(files, out_path?, format?)`` — compress files
  and/or directories (walked recursively) into zip / 7z / tar /
  tar.gz / tar.bz2 / gz. Dangerous executables are skipped from the
  bundle.
* ``archive_extract(path, to_dir?, members?, overwrite?)`` — extract
  with Zip-Slip traversal blocking, dangerous-executable skipping,
  and total-uncompressed-size / member-count caps (zip-bomb bounds).
* ``archive_list(path)`` — member listing without extraction.
* ``list_archive_formats()`` — introspection (formats, py7zr
  availability, budgets, sandbox roots).

Transport
---------
Run as a stdio MCP server so it composes with the existing fleet::

    python -m src.mcp_server.archive_server

Note: the **in-process** LocalHost (``ArchiveLocalHost`` in
``src/mcp_client/host.py``) registers the *same* tool surface
automatically — you only need this standalone server for external
MCP clients (Claude Desktop, Cline, etc.).

Security: all paths are sandbox-scoped — inputs to
``QDRANT_FILESYSTEM_READ_ROOT``, outputs to
``QDRANT_FILESYSTEM_WRITE_ROOT`` (both default to WATCH_DIR, then
./documents).
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
            name="archive_create",
            description=(
                "Compress files (and/or directories, walked "
                "recursively) into an archive — zip, 7z, tar, "
                "tar.gz, tar.bz2, or gz (gz = ONE file only). "
                "Dangerous executables are skipped."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "files": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "out_path": {"type": "string"},
                    "format": {
                        "type": "string",
                        "enum": [
                            "auto", "zip", "7z", "tar",
                            "tar.gz", "tar.bz2", "gz",
                        ],
                    },
                },
                "required": ["files"],
            },
        ),
        Tool(
            name="archive_extract",
            description=(
                "Extract a .zip / .7z / .tar / .tar.gz / .tgz / "
                ".tar.bz2 / .gz archive into the write sandbox. "
                "Zip-Slip traversal members are blocked, dangerous "
                "executable members are skipped, and zip-bomb "
                "bounds are enforced."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "to_dir": {"type": "string"},
                    "members": {
                        "type": "array", "items": {"type": "string"},
                    },
                    "overwrite": {"type": "boolean"},
                },
                "required": ["path"],
            },
        ),
        Tool(
            name="archive_list",
            description=(
                "List the members of an archive without extracting: "
                "name, uncompressed / compressed size, is_dir, "
                "modified time."
            ),
            inputSchema={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        ),
        Tool(
            name="list_archive_formats",
            description=(
                "Introspection: formats, py7zr availability, size / "
                "member-count budgets, sandbox roots."
            ),
            inputSchema={"type": "object", "properties": {}},
        ),
    ]


class ArchiveMCPServer:
    """MCP server exposing the archive host to an LLM agent."""

    def __init__(self):
        from src.mcp_client.host import ArchiveLocalHost

        self.host = ArchiveLocalHost()
        self.server = Server("divine-sense-agent-archive")
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
                if name == "archive_create":
                    return await asyncio.to_thread(
                        self._do_create, arguments,
                    )
                if name == "archive_extract":
                    return await asyncio.to_thread(
                        self._do_extract, arguments,
                    )
                if name == "archive_list":
                    return await asyncio.to_thread(
                        self._do_list, arguments,
                    )
                if name == "list_archive_formats":
                    return await asyncio.to_thread(
                        self._do_formats, arguments,
                    )
            except Exception as e:
                logger.exception(
                    "[archive-mcp] tool %r failed: %r", name, e,
                )
                return _err(f"{type(e).__name__}: {e}")
            return _err(f"Unknown tool: {name}")

    # ── handlers (sync, wrapped in to_thread) ──────────────────
    def _do_create(self, args: dict) -> list[TextContent]:
        return _ok(self.host._create(args or {}))

    def _do_extract(self, args: dict) -> list[TextContent]:
        return _ok(self.host._extract(args or {}))

    def _do_list(self, args: dict) -> list[TextContent]:
        return _ok(self.host._list(args or {}))

    def _do_formats(self, args: dict) -> list[TextContent]:
        return _ok(self.host._formats(args or {}))

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
    server = ArchiveMCPServer()
    asyncio.run(server.run_stdio())


if __name__ == "__main__":
    main()