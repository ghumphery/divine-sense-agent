"""MCP server: filesystem operations (v0.9.3+, expanded v0.9.4+).

Exposes 15 filesystem tools to an external MCP client:

READ class (sandbox: ``QDRANT_FILESYSTEM_READ_ROOT``,
fallback ``WATCH_DIR`` → ``./documents``):

* ``list_dir(path, pattern?, recursive?, include_dirs?, include_files?)``
* ``stat_file(path)``
* ``read_file(path, offset_bytes?, max_bytes?, encoding?)``
* ``read_multiple(paths[], max_bytes_each?)``
* ``search_files(path, pattern, recursive?, max_results?)``
* ``get_tree(path, max_depth?, max_entries?, include_dirs?, include_files?)``
* ``search_content(path, query, is_regex?, case_sensitive?, file_pattern?,
  max_results?, context_chars?)``
* ``diff_files(a, b, context?, max_output_bytes?)`` — unified diff
  between two text files. ``context`` defaults to 3. Output is
  capped at 4 MB; oversized diffs return ``truncated=true``.

WRITE class (sandbox: ``QDRANT_FILESYSTEM_WRITE_ROOT``, fallback
same as read):

* ``write_file(path, content, encoding?, overwrite?)``
* ``append_file(path, content, encoding?)``
* ``delete_file(path)``
* ``move_file(src, dst, overwrite?)``
* ``mkdir(path, exist_ok?)``
* ``copy_file(src, dst, overwrite?, follow_symlinks?)`` — atomic
  copy preserving the source. Same sandbox + dangerous-ext rules
  as ``write_file``.
* ``patch_file(path, patch, dry_run?)`` — apply a unified-diff
  patch to a text file in place. Atomic via mkstemp + os.replace.
  Strict ``difflib.unified_diff`` format only (no ``diff --git``
  extensions, no binary hunks, no ``rename from``). Dangerous
  extensions refused.

Transport
---------
Run as a stdio MCP server::

    python -m src.mcp_server.filesystem_server

Add to ``config/mcp.json``::

    {
      "name": "filesystem",
      "transport": "stdio",
      "command": ["python", "-m", "src.mcp_server.filesystem_server"]
    }

Note: the **in-process** ``FilesystemLocalHost`` in
``src/mcp_client/host.py`` registers the *same* tool surface
automatically — most users won't need this standalone server
at all. This stdio server exists for external MCP clients
(Claude Desktop, Cline, etc.) that want filesystem access
without the Divine Sense agent in the loop.

All operations are sandboxed by ``_fs_resolve_path`` (see
``src/mcp_client/host.py``) — paths must resolve under the
configured read / write root or the call returns a clean
``{"error": "..."}`` dict instead of touching the disk.
Symlinks that escape the sandbox are refused at walk
boundaries. ``write_file`` / ``append_file`` reject a closed
list of dangerous executable extensions regardless of sandbox.
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

from src.mcp_client.host import FilesystemLocalHost

logger = logging.getLogger(__name__)


def _ok(payload: dict[str, Any]) -> list[TextContent]:
    """JSON-encode with compact separators — filesystem results
    can include large ``content`` blobs and we don't want indent
    to 4× the TextContent payload."""
    return [TextContent(
        type="text",
        text=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
    )]


def _err(message: str) -> list[TextContent]:
    return [TextContent(type="text", text=f"Error: {message}")]


# ── tool schemas (must mirror FilesystemLocalHost's LocalTool
#    schemas so a stdio client sees the same surface as the
#    in-process host) ────────────────────────────────────────


def _build_tool_specs() -> list[Tool]:
    return [
        Tool(
            name="list_dir",
            description=(
                "List entries in a directory inside the filesystem "
                "read sandbox. Returns "
                "{entries: [...], truncated: bool}. "
                "Each entry is {name, path, relative_path, type, "
                "size_bytes, mtime}. Bare relative paths are joined "
                "to the sandbox root; absolute paths must resolve "
                "inside it."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "pattern": {"type": "string"},
                    "include_dirs": {"type": "boolean", "default": True},
                    "include_files": {"type": "boolean", "default": True},
                },
            },
        ),
        Tool(
            name="stat_file",
            description=(
                "Stat a single path. Returns "
                "{exists, type, size_bytes, mtime, mode, is_symlink, "
                "path, relative_path}."
            ),
            inputSchema={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        ),
        Tool(
            name="read_file",
            description=(
                "Read a text file inside the read sandbox. Returns "
                "{content, encoding, size_bytes, truncated, "
                "offset_bytes, next_offset}. Use offset_bytes / "
                "max_bytes to page through large files. Binary "
                "extensions are rejected."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "offset_bytes": {"type": "integer", "minimum": 0},
                    "max_bytes": {"type": "integer", "minimum": 1},
                    "encoding": {"type": "string"},
                },
                "required": ["path"],
            },
        ),
        Tool(
            name="read_multiple",
            description=(
                "Read several files in one round-trip. Returns "
                "{files: [{path, content, size_bytes, error?}], "
                "total_bytes}. Binary extensions produce per-file "
                "errors without aborting the batch."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "paths": {"type": "array", "items": {"type": "string"}},
                    "max_bytes_each": {"type": "integer", "minimum": 1},
                },
                "required": ["paths"],
            },
        ),
        Tool(
            name="search_files",
            description=(
                "Glob-match file names inside the read sandbox. "
                "Returns {matches: [{path, relative_path, "
                "size_bytes}], truncated: bool, match_count}."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "pattern": {"type": "string"},
                    "recursive": {"type": "boolean", "default": True},
                    "max_results": {"type": "integer", "minimum": 1},
                },
                "required": ["pattern"],
            },
        ),
        Tool(
            name="get_tree",
            description=(
                "Recursive tree dump of a directory. Returns "
                "{entries: [...], truncated: bool, total_count, "
                "max_depth}."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "max_depth": {"type": "integer", "minimum": 1, "default": 8},
                    "max_entries": {"type": "integer", "minimum": 1},
                    "include_dirs": {"type": "boolean", "default": True},
                    "include_files": {"type": "boolean", "default": True},
                },
            },
        ),
        Tool(
            name="search_content",
            description=(
                "Grep text files for query (plain or regex). "
                "Returns {matches: [{path, line_number, line}], "
                "truncated: bool, files_scanned, match_count}. "
                "Skips binary files by extension."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "query": {"type": "string"},
                    "is_regex": {"type": "boolean", "default": False},
                    "case_sensitive": {"type": "boolean", "default": False},
                    "file_pattern": {"type": "string"},
                    "max_results": {"type": "integer", "minimum": 1, "default": 100},
                    "context_chars": {"type": "integer", "minimum": 0, "default": 60},
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="write_file",
            description=(
                "Create or overwrite a UTF-8 text file inside the "
                "filesystem write sandbox. Returns "
                "{ok, path, size_bytes} or {error}. Refuses "
                "dangerous executable extensions. Atomic write."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                    "encoding": {"type": "string"},
                    "overwrite": {"type": "boolean", "default": False},
                },
                "required": ["path", "content"],
            },
        ),
        Tool(
            name="append_file",
            description=(
                "Append text to a UTF-8 file inside the write "
                "sandbox. Creates the file if missing. Returns "
                "{ok, path, appended_bytes, size_bytes} or {error}."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                    "encoding": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        ),
        Tool(
            name="delete_file",
            description=(
                "Delete a single file inside the write sandbox. "
                "Returns {ok, path} or {error}. DESTRUCTIVE — "
                "refuses to remove directories."
            ),
            inputSchema={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        ),
        Tool(
            name="move_file",
            description=(
                "Rename or move a file inside the write sandbox. "
                "Returns {ok, src, dst} or {error}."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "src": {"type": "string"},
                    "dst": {"type": "string"},
                    "overwrite": {"type": "boolean", "default": False},
                },
                "required": ["src", "dst"],
            },
        ),
        Tool(
            name="mkdir",
            description=(
                "Create a directory (and parents if needed) inside "
                "the write sandbox. Returns "
                "{ok, path, created} or {error}."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "exist_ok": {"type": "boolean", "default": True},
                },
                "required": ["path"],
            },
        ),
        Tool(
            name="copy_file",
            description=(
                "Copy a file (or overwrite in place) inside the "
                "write sandbox; the source is preserved. Atomic "
                "via mkstemp + os.replace. Returns "
                "{ok, src, dst, bytes_copied, overwrite, "
                "follow_symlinks} or {error}. Dangerous extensions "
                "(.exe/.bat/.sh/.ps1/.js/.vbs/.scr/...) are "
                "refused on either side of the copy."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "src": {"type": "string"},
                    "dst": {"type": "string"},
                    "overwrite": {"type": "boolean", "default": False},
                    "follow_symlinks": {
                        "type": "boolean", "default": True,
                    },
                },
                "required": ["src", "dst"],
            },
        ),
        Tool(
            name="diff_files",
            description=(
                "Return a unified-diff between two text files "
                "inside the read sandbox. Both files must be text "
                "(extension whitelist). Returns {ok, diff, "
                "identical, hunks, truncated, a_path, b_path, "
                "read_root} or {error}. context=N adjusts the "
                "hunk context (default 3). max_output_bytes caps "
                "the diff string (default 4 MB); oversized diffs "
                "set truncated=true."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "a": {"type": "string"},
                    "b": {"type": "string"},
                    "context": {"type": "integer", "default": 3},
                    "max_output_bytes": {
                        "type": "integer", "default": 4194304,
                    },
                },
                "required": ["a", "b"],
            },
        ),
        Tool(
            name="patch_file",
            description=(
                "Apply a unified-diff patch to a text file inside "
                "the write sandbox. Strict difflib.unified_diff "
                "format only — context / '-' / '+' lines, "
                "@@ -a,b +c,d @@ headers. Returns {ok, path, "
                "hunks_applied, bytes_written, dry_run} or "
                "{error}. Pass dry_run=true to preview without "
                "writing. Dangerous extensions refused; atomic "
                "write via mkstemp + os.replace."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "patch": {"type": "string"},
                    "dry_run": {"type": "boolean", "default": False},
                },
                "required": ["path", "patch"],
            },
        ),
    ]


_TOOL_SPECS = _build_tool_specs()


class FilesystemMCPServer:
    """stdio MCP server wrapping ``FilesystemLocalHost``."""

    def __init__(self) -> None:
        self._local = FilesystemLocalHost()
        self.server = Server("qdrant-fs-agent")
        self._register_handlers()

    def _register_handlers(self) -> None:
        local = self._local

        @self.server.list_tools()
        async def _list() -> list[Tool]:
            return _TOOL_SPECS

        @self.server.call_tool()
        async def _call(name: str, arguments: dict) -> list[TextContent]:
            args = arguments or {}
            method = getattr(local, f"_{name}", None)
            if method is None:
                return _err(f"Unknown tool: {name}")
            try:
                out = await asyncio.to_thread(method, args)
            except Exception as e:
                logger.warning(
                    "[fs-server] %s crashed: %r", name, e,
                )
                return _err(f"{type(e).__name__}: {e}")
            if not isinstance(out, dict):
                out = {"value": out}
            if "error" in out:
                return _err(str(out["error"]))
            return _ok(out)

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
    server = FilesystemMCPServer()
    asyncio.run(server.run_stdio())


if __name__ == "__main__":
    main()

