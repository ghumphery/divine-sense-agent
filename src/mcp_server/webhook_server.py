"""MCP server: webhook trigger tools (v0.9.25.0+).

Tools: add_webhook_trigger / list_webhook_triggers / get_webhook_trigger /
get_webhook_trigger_url / delete_webhook_trigger / enable_webhook_trigger /
disable_webhook_trigger / run_webhook_trigger_now / list_webhook_trigger_runs /
get_webhook_trigger_errors.

Each trigger has its OWN URL (``/trigger/<id>``) + random access key; an
HTTP call to that URL fires the pre-configured prompt through
``Agent.chat_turn`` — the event-driven counterpart to the scheduler.

Transport
---------
Run as a stdio MCP server:

    python -m src.mcp_server.webhook_server

Add to ``config/mcp.json``::

    {
      "name": "webhook",
      "transport": "stdio",
      "command": ["python", "-m", "src.mcp_server.webhook_server"]
    }

Note: the **in-process** LocalHost (``WebhookLocalHost`` in
``src/mcp_client/host.py``) registers the *same* tool surface
automatically; the HTTP listener itself (``WebhookServer``) runs
inside the divine-sense-agent app process and is NOT part of this
stdio surface (external clients can only manage triggers here —
the listener lives with the Agent process).
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


def _ok(payload: dict) -> list[TextContent]:
    return [TextContent(
        type="text", text=json.dumps(payload, ensure_ascii=False),
    )]


_TOOL_NAMES = (
    "add_webhook_trigger",
    "list_webhook_triggers",
    "get_webhook_trigger",
    "get_webhook_trigger_url",
    "delete_webhook_trigger",
    "enable_webhook_trigger",
    "disable_webhook_trigger",
    "run_webhook_trigger_now",
    "list_webhook_trigger_runs",
    "get_webhook_trigger_errors",
)


def _build_tool_specs() -> list[Tool]:
    from src.mcp_client.host import WebhookLocalHost

    specs: list[Tool] = []
    for t in WebhookLocalHost().list_tools():
        specs.append(Tool(
            name=t.name, description=t.description,
            inputSchema=t.input_schema,
        ))
    return specs


_TOOL_SPECS = _build_tool_specs()


class WebhookMCPServer:
    """MCP server exposing the webhook tools to an LLM agent."""

    def __init__(self):
        from src.mcp_client.host import WebhookLocalHost

        self.host = WebhookLocalHost()
        self.server = Server("divine-sense-agent-webhook")
        self._register_tools()

    def _tool_specs(self) -> list[Tool]:
        return list(_TOOL_SPECS)

    def _register_tools(self) -> None:
        @self.server.list_tools()
        async def _list() -> list[Tool]:
            return list(_TOOL_SPECS)

        @self.server.call_tool()
        async def _call(name: str, arguments: dict) -> list[TextContent]:
            handler = {
                "add_webhook_trigger": self.host._add,
                "list_webhook_triggers": self.host._list,
                "get_webhook_trigger": self.host._get,
                "get_webhook_trigger_url": self.host._url,
                "delete_webhook_trigger": self.host._delete,
                "enable_webhook_trigger": self.host._enable,
                "disable_webhook_trigger": self.host._disable,
                "run_webhook_trigger_now": self.host._run_now,
                "list_webhook_trigger_runs": self.host._runs,
                "get_webhook_trigger_errors": self.host._errors,
            }.get(name)
            if handler is None:
                return _err(f"Unknown tool: {name}")
            try:
                return await asyncio.to_thread(handler, arguments or {})
            except Exception as e:
                logger.exception(
                    "[webhook-mcp] tool %r failed: %r", name, e,
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
    server = WebhookMCPServer()
    asyncio.run(server.run_stdio())


if __name__ == "__main__":
    main()