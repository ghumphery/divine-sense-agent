"""MCP server: sandboxed local compute — bash / awk / grep / python
(v0.9.9+).

Exposes the local ``ExecLocalHost`` as MCP tools so an LLM agent can
run local compute instead of burning its context window on bulk
text work:

* ``run_shell(command, timeout?)`` — platform-native shell
  (Windows ``cmd /c``, POSIX ``/bin/sh -c``).
* ``run_bash(script, timeout?)`` — GNU bash (Git-for-Windows bundles
  awk/grep/sed; full Unix pipelines).
* ``run_grep(pattern, paths?, ...flags)`` — pure-Python grep over
  sandbox files (regex/fixed, -i/-v/-n/-c/-l, context lines).
* ``run_awk(script, file, field_separator?)`` — system awk column
  surgery over one sandbox file.
* ``run_python(code, timeout?)`` — ``sys.executable -c <code>``;
  the most flexible converter.

Safety: every subprocess runs with cwd = the filesystem write
sandbox and an env exposing ``QDRANT_READ_ROOT`` / ``QDRANT_WRITE_ROOT``;
per-call timeouts (default 30 s, cap 120 s), stdout/stderr caps
(64 KB), and a machine-level disaster blocklist (``rm -rf /``,
``mkfs``, fork bombs, ``del /f /s``, ``shutdown``... — sandbox file
operations are allowed, OS/disk destruction is refused).
* ``script_save(code, name?, lang?, description?, conda_env?, docker_image?, network?)`` - save a reusable script (v0.9.102.5).  
* ``script_list()`` - list the saved script library.  
* ``script_run(name, args?, timeout?)`` - replay a saved script (direct / conda / docker backend). 

Transport
---------
Run as a stdio MCP server so it composes with the existing fleet::

    python -m src.mcp_server.exec_server

Note: the **in-process** LocalHost (``ExecLocalHost`` in
``src/mcp_client/host.py``) registers the *same* tool surface
automatically — you only need this standalone server for external
MCP clients (Claude Desktop, Cline, etc.).
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
            name="run_shell",
            description=(
                "Run a platform-native shell command (Windows cmd /c; "
                "POSIX /bin/sh -c) with the write sandbox as cwd. "
                "Machine-level destructive commands are refused."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "timeout": {"type": "integer"},
                },
                "required": ["command"],
            },
        ),
        Tool(
            name="run_bash",
            description=(
                "Run a GNU bash script (bash -c) with the write "
                "sandbox as cwd; full Unix pipelines (grep|awk|sed|"
                "sort|uniq) for format conversion and bulk text work."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "script": {"type": "string"},
                    "timeout": {"type": "integer"},
                },
                "required": ["script"],
            },
        ),
        Tool(
            name="run_grep",
            description=(
                "grep over sandbox files — pure Python, always "
                "available (regex/fixed, -i/-v/-n/-c/-l, context "
                "lines). Returns {matches|counts|files, "
                "total_matches, truncated}."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "paths": {
                        "type": "array", "items": {"type": "string"},
                    },
                    "fixed": {"type": "boolean"},
                    "ignore_case": {"type": "boolean"},
                    "invert": {"type": "boolean"},
                    "count": {"type": "boolean"},
                    "filenames_only": {"type": "boolean"},
                    "line_numbers": {"type": "boolean"},
                    "after_context": {"type": "integer"},
                    "before_context": {"type": "integer"},
                    "max_results": {"type": "integer"},
                },
                "required": ["pattern"],
            },
        ),
        Tool(
            name="run_awk",
            description=(
                "Run the system awk over ONE sandbox file — column "
                "surgery (awk -F, '{sum += $3} END {print sum}' "
                "data.csv)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "script": {"type": "string"},
                    "file": {"type": "string"},
                    "field_separator": {"type": "string"},
                    "timeout": {"type": "integer"},
                },
                "required": ["script", "file"],
            },
        ),
        Tool(
            name="run_python",
            description=(
                "Run Python code (sys.executable -c <code>) with the "
                "write sandbox as cwd — json/csv transforms, regex "
                "extraction, computed reports. Write outputs into "
                "the sandbox; stdout is capped."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "code": {"type": "string"},
                    "timeout": {"type": "integer"},
                },
                "required": ["code"],
            },
        ),
       Tool(  
           name='script_save',  
           description=(  
               'Save a reusable script into the exec script '  
               'library (write-sandbox scripts dir, sha1 dedup, '  
               'optional conda or docker isolation backend).'  
           ),  
           inputSchema={  
               'type': 'object',  
               'properties': {  
                   'name': {'type': 'string'},  
                   'code': {'type': 'string'},  
                   'lang': {'type': 'string'},  
                   'description': {'type': 'string'},  
                   'conda_env': {'type': 'string'},  
                   'docker_image': {'type': 'string'},  
                   'network': {'type': 'string'},  
               },  
               'required': ['code'],  
           },  
       ),  
       Tool(  
           name='script_list',  
           description='List the exec script library.',  
           inputSchema={'type': 'object', 'properties': {}},  
       ),  
       Tool(  
           name='script_run',  
           description=(  
               'Replay a saved library script direct / conda / docker.'  
           ),  
           inputSchema={  
               'type': 'object',  
               'properties': {  
                   'name': {'type': 'string'},  
                   'args': {'type': 'string'},  
                   'timeout': {'type': 'integer'},  
               },  
               'required': ['name'],  
           },  
       ), 
        Tool(
            name="os_input_status",
            description=(
                "OS real-input capability report (read-only): gate "
                "status (QDRANT_OS_INPUT_ALLOW), cursor, foreground "
                "window title, screen size, pynput availability."
            ),
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="os_windows_list",
            description=(
                "List visible top-level windows (title/hwnd/pid) "
                "filtered by an optional regex — read-only."
            ),
            inputSchema={"type": "object", "properties": {
                "regex": {"type": "string"},
                "limit": {"type": "integer"},
            }},
        ),
        Tool(
            name="os_focus_window",
            description=(
                "Bring a window matching title_regex to the foreground "
                "(REAL focus steal) — needs QDRANT_OS_INPUT_ALLOW=on "
                "+ confirm=true."
            ),
            inputSchema={"type": "object", "properties": {
                "title_regex": {"type": "string"},
                "confirm": {"type": "boolean"},
            }, "required": ["title_regex", "confirm"]},
        ),
        Tool(
            name="os_click_point",
            description=(
                "REAL OS mouse click at PHYSICAL screen (x,y) — pair "
                "with browser_element_point; title_regex anchor; "
                "preview=true = passive crop for describe_image; "
                "real click needs confirm=true."
            ),
            inputSchema={"type": "object", "properties": {
                "x": {"type": "integer"},
                "y": {"type": "integer"},
                "title_regex": {"type": "string"},
                "confirm": {"type": "boolean"},
                "preview": {"type": "boolean"},
                "half": {"type": "integer"},
                "restore_cursor": {"type": "boolean"},
            }, "required": ["x", "y"]},
        ),
        Tool(
            name="os_type_text",
            description=(
                "Type REAL text (SendInput) into the currently focused "
                "element — trusted-only fields (Teams/Outlook rich "
                "compose); title_regex anchor + confirm=true."
            ),
            inputSchema={"type": "object", "properties": {
                "text": {"type": "string"},
                "title_regex": {"type": "string"},
                "confirm": {"type": "boolean"},
            }, "required": ["text", "title_regex", "confirm"]},
        ),
        Tool(
            name="os_key_press",
            description=(
                "Press ONE whitelisted key as REAL input; title_regex "
                "anchor + confirm=true; submit combos refused."
            ),
            inputSchema={"type": "object", "properties": {
                "key": {"type": "string"},
                "title_regex": {"type": "string"},
                "confirm": {"type": "boolean"},
            }, "required": ["key", "title_regex", "confirm"]},
        ),
    ]


class ExecMCPServer:
    """MCP server exposing the exec host to an LLM agent."""

    def __init__(self):
        from src.mcp_client.host import ExecLocalHost

        self.host = ExecLocalHost()
        self.server = Server("divine-sense-agent-exec")
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
                if name == "run_shell":
                    return await asyncio.to_thread(
                        self._do_shell, arguments,
                    )
                if name == "run_bash":
                    return await asyncio.to_thread(
                        self._do_bash, arguments,
                    )
                if name == "run_grep":
                    return await asyncio.to_thread(
                        self._do_grep, arguments,
                    )
                if name == "run_awk":
                    return await asyncio.to_thread(
                        self._do_awk, arguments,
                    )
                if name == "run_python":
                    return await asyncio.to_thread(
                        self._do_python, arguments,
                    )
                if name == 'script_save':
                    return await asyncio.to_thread(
                        self._do_script_save, arguments)
                if name == 'script_list':
                    return await asyncio.to_thread(
                        self._do_script_list, arguments)
                if name == 'script_run':
                    return await asyncio.to_thread(
                        self._do_script_run, arguments)
                if name == 'os_input_status':
                    return await asyncio.to_thread(
                        self._do_os_status, arguments)
                if name == 'os_windows_list':
                    return await asyncio.to_thread(
                        self._do_os_windows, arguments)
                if name == 'os_focus_window':
                    return await asyncio.to_thread(
                        self._do_os_focus, arguments)
                if name == 'os_click_point':
                    return await asyncio.to_thread(
                        self._do_os_click, arguments)
                if name == 'os_type_text':
                    return await asyncio.to_thread(
                        self._do_os_type, arguments)
                if name == 'os_key_press':
                    return await asyncio.to_thread(
                        self._do_os_key, arguments)


            except Exception as e:
                logger.exception(
                    "[exec-mcp] tool %r failed: %r", name, e,
                )
                return _err(f"{type(e).__name__}: {e}")
            return _err(f"Unknown tool: {name}")

    # ── handlers (sync, wrapped in to_thread) ──────────────────
    def _do_shell(self, args: dict) -> list[TextContent]:
        return _ok(self.host._run_shell(args or {}))

    def _do_bash(self, args: dict) -> list[TextContent]:
        return _ok(self.host._run_bash(args or {}))

    def _do_grep(self, args: dict) -> list[TextContent]:
        return _ok(self.host._run_grep(args or {}))

    def _do_awk(self, args: dict) -> list[TextContent]:
        return _ok(self.host._run_awk(args or {}))

    def _do_python(self, args: dict) -> list[TextContent]:
        return _ok(self.host._run_python(args or {}))

    # ── stdio entry point ─────────────────────────────────────
    # script-library mirror (v0.9.102.5)
    def _do_script_save(self, args: dict) -> list[TextContent]:
        return _ok(self.host._script_save(args or {}))
    def _do_script_list(self, args: dict) -> list[TextContent]:
        return _ok(self.host._script_list(args or {}))
    def _do_script_run(self, args: dict) -> list[TextContent]:
        return _ok(self.host._script_run(args or {}))

    # ── os-input mirror (v0.9.105.0) ─────────────────────────────
    def _do_os_status(self, args: dict) -> list[TextContent]:
        return _ok(self.host._os_status(args or {}))

    def _do_os_windows(self, args: dict) -> list[TextContent]:
        return _ok(self.host._os_windows(args or {}))

    def _do_os_focus(self, args: dict) -> list[TextContent]:
        return _ok(self.host._os_focus(args or {}))

    def _do_os_click(self, args: dict) -> list[TextContent]:
        return _ok(self.host._os_click(args or {}))

    def _do_os_type(self, args: dict) -> list[TextContent]:
        return _ok(self.host._os_type(args or {}))

    def _do_os_key(self, args: dict) -> list[TextContent]:
        return _ok(self.host._os_key(args or {}))
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
    server = ExecMCPServer()
    asyncio.run(server.run_stdio())


if __name__ == "__main__":
    main()