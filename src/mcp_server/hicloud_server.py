"""MCP server: 中華電信 hicloud CaaS/CVPC 雲服務 tools (v0.9.64.0+).

Tools: hicloud_list_instances / hicloud_describe_images /
hicloud_list_instance_types / hicloud_run_instances /
hicloud_start_instances / hicloud_stop_instances /
hicloud_reboot_instances / hicloud_terminate_instances.

**Mock-first safety**: ``HICLOUD_MODE=mock`` (the default) runs a fully
simulated in-memory VM book — no HTTP call ever leaves the box. live
mode requires ``HICLOUD_ACCESS_KEY`` + ``HICLOUD_SECRET_KEY``; lifecycle
writes additionally require a per-call ``confirm=true`` and
``HICLOUD_WRITES_ENABLED=on``.

Transport
---------
Run as a stdio MCP server:

    python -m src.mcp_server.hicloud_server

Add to ``config/mcp.json``::

    {
      "name": "hicloud",
      "transport": "stdio",
      "command": ["python", "-m", "src.mcp_server.hicloud_server"]
    }

Note: the **in-process** LocalHost (``HicloudLocalHost`` in
``src/mcp_client/host.py``) registers the *same* tool surface
automatically.
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

from src.mcp_client.host import _HICLOUD_TOOL_SPECS

logger = logging.getLogger(__name__)


def _err(message: str) -> list[TextContent]:
    return [TextContent(type="text", text=f"Error: {message}")]


def _ok(payload: dict[str, Any]) -> list[TextContent]:
    return [TextContent(
        type="text", text=json.dumps(payload, ensure_ascii=False),
    )]


def _build_tool_specs() -> list[Tool]:
    from src.mcp_client.host import HicloudLocalHost

    specs: list[Tool] = []
    for t in HicloudLocalHost().list_tools():
        specs.append(Tool(
            name=t.name, description=t.description,
            inputSchema=t.input_schema,
        ))
    return specs


_TOOL_SPECS = _build_tool_specs()

# v0.9.66.0 — name → engine method map for the data-driven tools
_TOOL_NAMES = {t["name"] for t in _HICLOUD_TOOL_SPECS}
_TOOL_METHODS = {t["name"]: t["method"] for t in _HICLOUD_TOOL_SPECS}


class HicloudMCPServer:
    """MCP server exposing the hicloud cloud tools to an LLM agent."""

    def __init__(self):
        from src.mcp_client.host import HicloudLocalHost

        self.host = HicloudLocalHost()
        self.server = Server("divine-sense-agent-hicloud")
        self._register_tools()

    def _tool_specs(self) -> list[Tool]:
        return list(_TOOL_SPECS)

    def _register_tools(self) -> None:
        @self.server.list_tools()
        async def _list() -> list[Tool]:
            return list(_TOOL_SPECS)

        @self.server.call_tool()
        async def _call(name: str, arguments: dict) -> list[TextContent]:
            # v0.9.64 8 tools keep their hand-written handlers;
            # v0.9.66 adds the data-driven dispatch for the remaining
            # 48 tools (args map 1:1 to the engine kwargs).
            handler = {
                "hicloud_list_instances": self.host._list_instances,
                "hicloud_describe_images": self.host._describe_images,
                "hicloud_list_instance_types":
                    self.host._list_instance_types,
                "hicloud_run_instances": self.host._run_instances,
                "hicloud_start_instances": self.host._start_instances,
                "hicloud_stop_instances": self.host._stop_instances,
                "hicloud_reboot_instances": self.host._reboot_instances,
                "hicloud_terminate_instances":
                    self.host._terminate_instances,
            }.get(name)
            try:
                if handler is not None:
                    return await asyncio.to_thread(handler, arguments or {})
                if name in _TOOL_NAMES:
                    return await asyncio.to_thread(
                        self.host._engine_call,
                        _TOOL_METHODS[name], arguments or {})
                return _err(f"Unknown tool: {name}")
            except Exception as e:
                logger.exception(
                    "[hicloud-mcp] tool %r failed: %r", name, e,
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
    server = HicloudMCPServer()
    asyncio.run(server.run_stdio())


if __name__ == "__main__":
    main()