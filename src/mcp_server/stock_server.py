"""MCP server: 兆豐證券 stock query + order tools (v0.9.24.0+).

Tools: stock_get_quote / stock_get_kline / stock_get_account_summary /
stock_list_orders / stock_place_order / stock_cancel_order;
v0.9.35.0+ adds stock_search_history / stock_list_history — the
auto-saved Qdrant snapshots (stock_docs).

**Mock-first safety**: ``MEGA_STOCK_MODE=mock`` (the default) runs a
fully simulated in-memory book — place_order NEVER touches the real
MegaAPI. live mode additionally requires
``MEGA_STOCK_TRADING_ENABLED=on`` AND a per-call ``confirm=True``.

Transport
---------
Run as a stdio MCP server:

    python -m src.mcp_server.stock_server

Add to ``config/mcp.json``::

    {
      "name": "stock",
      "transport": "stdio",
      "command": ["python", "-m", "src.mcp_server.stock_server"]
    }

Note: the **in-process** LocalHost (``MegaStockLocalHost`` in
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

logger = logging.getLogger(__name__)


def _err(message: str) -> list[TextContent]:
    return [TextContent(type="text", text=f"Error: {message}")]


def _ok(payload: dict[str, Any]) -> list[TextContent]:
    return [TextContent(
        type="text", text=json.dumps(payload, ensure_ascii=False),
    )]


def _build_tool_specs() -> list[Tool]:
    from src.mcp_client.host import MegaStockLocalHost

    specs: list[Tool] = []
    for t in MegaStockLocalHost().list_tools():
        specs.append(Tool(
            name=t.name, description=t.description,
            inputSchema=t.input_schema,
        ))
    return specs


_TOOL_SPECS = _build_tool_specs()


class StockMCPServer:
    """MCP server exposing the stock tools to an LLM agent."""

    def __init__(self):
        from src.mcp_client.host import MegaStockLocalHost

        self.host = MegaStockLocalHost()
        self.server = Server("divine-sense-agent-stock")
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
                "stock_get_quote": self.host._quote,
                "stock_get_quotes": self.host._quotes_batch,
                "stock_get_kline": self.host._kline,
                "stock_get_account_summary": self.host._account,
                "stock_list_orders": self.host._orders,
                "stock_place_order": self.host._place,
                "stock_cancel_order": self.host._cancel,
                # v0.9.35.0+ — Qdrant snapshot history
                "stock_search_history": self.host._search_history,
                "stock_list_history": self.host._list_history,
            }.get(name)
            if handler is None:
                return _err(f"Unknown tool: {name}")
            try:
                return await asyncio.to_thread(handler, arguments or {})
            except Exception as e:
                logger.exception(
                    "[stock-mcp] tool %r failed: %r", name, e,
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
    server = StockMCPServer()
    asyncio.run(server.run_stdio())


if __name__ == "__main__":
    main()