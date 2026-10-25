"""MCP server: skill FILE library tools (v0.9.116.0).

Import / query / remove / reference (引用) manager for Claude-style
``SKILL.md`` skill bundles — the STORAGE half of the skill surfaces
(the compiler plan tools live in ``tacti_server``):

    skill_import     local folder (SKILL.md + companions) / single
                     file / URL (github blob → raw) → the library
                     (QDRANT_SKILLS_DIR); confirm=true required
    skill_list       the catalog
    skill_get        frontmatter + raw SKILL.md + file map + provenance
    skill_search     keyword scan over name + description + body
    skill_remove     delete one skill folder (confirm=true required)
    skill_use        the 引用 path — SKILL.md body INLINE into the
                     current tool result + one-time usage note

Transport
---------
Run as a stdio MCP server:

    python -m src.mcp_server.skill_library_server

Add to ``config/mcp.json``::

    {
      "name": "skill-library",
      "transport": "stdio",
      "command": ["python", "-m", "src.mcp_server.skill_library_server"]
    }

The in-process LocalHost (``SkillLibraryLocalHost`` in
``src/mcp_client/host.py``, server ``skill-library-local``) registers
the SAME tool surface inside the GUI/agent process.
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
    "skill_import", "skill_list", "skill_get",
    "skill_search", "skill_remove", "skill_use",
)


def _err(message: str) -> list[TextContent]:
    return [TextContent(type="text", text=f"Error: {message}")]


def _ok(payload: dict[str, Any]) -> list[TextContent]:
    return [TextContent(
        type="text", text=json.dumps(payload, ensure_ascii=False),
    )]


def _build_tool_specs() -> list[Tool]:
    from src.mcp_client.host import SkillLibraryLocalHost

    specs: list[Tool] = []
    for t in SkillLibraryLocalHost().list_tools():
        specs.append(Tool(
            name=t.name, description=t.description,
            inputSchema=t.input_schema,
        ))
    return specs


_TOOL_SPECS = _build_tool_specs()


class SkillLibraryMCPServer:
    """MCP server exposing the skill FILE library host."""

    def __init__(self):
        from src.mcp_client.host import SkillLibraryLocalHost

        self.host = SkillLibraryLocalHost()
        self.server = Server("divine-sense-agent-skill-library")
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
                    "skill_import": self.host._import,
                    "skill_list": self.host._list,
                    "skill_get": self.host._get,
                    "skill_search": self.host._search,
                    "skill_remove": self.host._remove,
                    "skill_use": self.host._use,
                }.get(name)
                if handler is None:
                    return _err(f"Unknown tool: {name}")
                return await asyncio.to_thread(handler, arguments or {})
            except Exception as e:
                logger.exception(
                    "[skill-library-mcp] tool %r failed: %r", name, e,
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
    server = SkillLibraryMCPServer()
    asyncio.run(server.run_stdio())


if __name__ == "__main__":
    main()