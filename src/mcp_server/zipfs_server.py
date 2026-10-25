"""MCP server: archive-as-filesystem member surgery (v0.9.133.13).

Exposes the local ``ZipFsLocalHost`` as MCP tools so an external LLM
agent can operate an uploaded ZIP/TAR like a filesystem — LIST, READ,
WRITE (create/overwrite), DELETE, RENAME, extract-one — WITHOUT
extracting the whole archive. zip / tar / tar.gz / tar.bz2 are
read-write (copy-on-write atomic rebuild); 7z is read-only (optional
py7zr). Mutations require the archive inside
``QDRANT_FILESYSTEM_WRITE_ROOT``; deletes/renames are confirm-gated.

Run as a stdio MCP server::

    python -m src.mcp_server.zipfs_server

The **in-process** LocalHost (``ZipFsLocalHost`` in
``src/mcp_client/host.py``) registers the same surface automatically —
this standalone server is only for external MCP clients.
"""
from __future__ import annotations

import asyncio
import json
import logging
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


_HOST = None


def _host():
    global _HOST
    if _HOST is None:
        from src.mcp_client.host import ZipFsLocalHost

        _HOST = ZipFsLocalHost()
    return _HOST


_TOOLS: tuple[tuple[str, str, dict], ...] = (
    ("zipfs_list",
     "Folder tree of a ZIP/TAR/7z archive (no extraction)", {
         "type": "object", "properties": {
             "path": {"type": "string"},
             "subdir": {"type": "string"},
             "depth": {"type": "integer", "minimum": 1, "maximum": 8},
         }, "required": ["path"],
     }),
    ("zipfs_read",
     "Read ONE archive member (text decoded / binary base64)", {
         "type": "object", "properties": {
             "path": {"type": "string"},
             "member": {"type": "string"},
             "max_bytes": {"type": "integer", "minimum": 200,
                           "maximum": 5000000},
         }, "required": ["path", "member"],
     }),
    ("zipfs_write",
     "Create/overwrite ONE member (atomic rebuild; encoding text|base64)", {
         "type": "object", "properties": {
             "path": {"type": "string"},
             "member": {"type": "string"},
             "content": {"type": "string"},
             "encoding": {"type": "string", "enum": ["text", "base64"]},
         }, "required": ["path", "member", "content"],
     }),
    ("zipfs_delete",
     "Delete members ('x/' = whole subtree); confirm=true", {
         "type": "object", "properties": {
             "path": {"type": "string"},
             "members": {"type": "array", "items": {"type": "string"}},
             "confirm": {"type": "boolean"},
         }, "required": ["path", "members", "confirm"],
     }),
    ("zipfs_rename", "Rename/move a member or subtree; confirm=true", {
        "type": "object", "properties": {
            "path": {"type": "string"},
            "src": {"type": "string"},
            "dst": {"type": "string"},
            "confirm": {"type": "boolean"},
        }, "required": ["path", "src", "dst", "confirm"],
    }),
    ("zipfs_extract_one", "Extract ONE member into the write sandbox", {
        "type": "object", "properties": {
            "path": {"type": "string"},
            "member": {"type": "string"},
            "dest_path": {"type": "string"},
            "allow_dangerous": {"type": "boolean"},
        }, "required": ["path", "member"],
    }),
    ("zipfs_info", "Archive metadata + writability + budgets", {
        "type": "object", "properties": {"path": {"type": "string"}},
        "required": ["path"],
    }),
    ("zipfs_status", "zipfs capability introspection", {
        "type": "object", "properties": {},
    }),
)


def _build_tool_specs() -> list[Tool]:
    return [
        Tool(name=name, description=desc, inputSchema=schema)
        for name, desc, schema in _TOOLS
    ]


async def _maybe_await(result: Any) -> Any:
    if asyncio.iscoroutine(result):
        return await result
    return result


async def serve() -> None:
    server = Server("zipfs-local")
    host = _host()

    @server.list_tools()
    async def _handle_list_tools() -> list[Tool]:
        return _build_tool_specs()

    @server.call_tool()
    async def _handle_call_tool(name: str, arguments: dict) -> list:
        methods = {
            "zipfs_list": host._list,
            "zipfs_read": host._read,
            "zipfs_write": host._write,
            "zipfs_delete": host._delete,
            "zipfs_rename": host._rename,
            "zipfs_extract_one": host._extract_one,
            "zipfs_info": host._info,
            "zipfs_status": host._status,
        }
        fn = methods.get(name)
        if fn is None:
            return _err(f"unknown tool: {name}")
        try:
            payload = await _maybe_await(fn(arguments or {}))
            return _ok(payload if isinstance(payload, dict) else
                       {"ok": False, "result": payload})
        except Exception as e:  # noqa: BLE001
            logger.exception("zipfs tool %s failed", name)
            return _err(repr(e)[:300])

    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream,
                         server.create_initialization_options())


if __name__ == "__main__":
    print("zipfs-local MCP server on stdio", file=sys.stderr)
    asyncio.run(serve())