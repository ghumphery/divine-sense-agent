"""mcp_server package — MCP servers exposed to external clients.

Two servers ship in v0.8.x:

* ``server.QdrantMCPServer``  — Qdrant-backed (search / ingest /
  list / stats), runs as ``python -m src.mcp_server.server``.
* ``telegram_server.TelegramMCPServer`` — Telegram Bot gateway with
  an allowlist of chat IDs, runs as
  ``python -m src.mcp_server.telegram_server``.

Both are stdio MCP servers. They are **not** consumed internally by
the divine-sense-agent CLI; the in-process ``McpManager`` calls the
local Qdrant / Web Search hosts directly. These modules exist so
external MCP clients (Claude Desktop, Cline, …) can connect to the
agent's tools over stdio.
"""
from .server import QdrantMCPServer
from .telegram_server import TelegramMCPServer

__all__ = ["QdrantMCPServer", "TelegramMCPServer"]
