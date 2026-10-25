"""MCP server: log anomaly analysis over Qdrant (v0.9.7+).

Exposes the local ``LogAnalysisLocalHost`` as MCP tools so an LLM
agent can ingest and analyse web-server access logs (Apache/Nginx
combined) and system logs (syslog RFC3164/RFC5424):

* ``ingest_log(path, log_type?, max_lines?, batch_size?)`` —
  parse + embed + upsert one log file (one point per line) into the
  dedicated ``QDRANT_LOG_COLLECTION`` collection.
* ``analyze_anomalies(source?, log_type?, top_n?)`` — rule-based
  statistics (HTTP error rate, 404 scanning, high-frequency IPs,
  suspicious paths / user-agents, 401/403 bursts, SSH brute force,
  OOM / kernel events, log floods) + semantic outliers (lines whose
  embedding is isolated from every other line).
* ``search_logs(query, ...filters)`` — semantic search over lines.
* ``get_log_stats(source?)`` — distributions + top IPs / paths.
* ``list_log_sources()`` — ingested files + point counts.
* ``remove_logs(source)`` — DESTRUCTIVE per-source delete.

Transport
---------
Run as a stdio MCP server so it composes with the existing fleet::

    python -m src.mcp_server.log_analyzer_server

Note: the **in-process** LocalHost (``LogAnalysisLocalHost`` in
``src/mcp_client/host.py``) registers the *same* tool surface
automatically - most users won't need this standalone server at
all. The stdio server exists for external MCP clients (Claude
Desktop, Cline, etc.).

Security: log file paths are sandbox-scoped to
``QDRANT_LOG_ALLOWED_ROOT`` (fallback: filesystem read root →
WATCH_DIR → ./documents); ``..`` traversal and symlinks outside the
root are refused.
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
            name="ingest_log",
            description=(
                "Parse and index a web-server access log (combined) "
                "or system log (syslog RFC3164/RFC5424) into the log "
                "analysis collection — one vector per line. Paths "
                "are sandboxed to QDRANT_LOG_ALLOWED_ROOT. "
                "Compressed archives are accepted directly: .zip / "
                ".7z / .tar / .tar.gz / .tgz / .tar.bz2 / .gz are "
                "extracted and every parsable member ingested as "
                "its own source '<archive>!<member>'."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            "Log file or archive (.zip / .7z / .tar / "
                            ".tar.gz / .tgz / .tar.bz2 / .gz) path."
                        ),
                    },
                    "log_type": {
                        "type": "string",
                        "enum": ["auto", "access", "syslog"],
                    },
                    "members": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Archives only: member-name allowlist."
                        ),
                    },
                    "max_lines": {"type": "integer"},
                    "batch_size": {"type": "integer"},
                },
                "required": ["path"],
            },
        ),
        Tool(
            name="analyze_anomalies",
            description=(
                "Run anomaly analysis over ingested log lines: "
                "rule-based statistics plus semantic outliers. "
                "Returns structured findings with severity + "
                "evidence."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "source": {"type": "string"},
                    "log_type": {
                        "type": "string", "enum": ["access", "syslog"],
                    },
                    "top_n": {"type": "integer"},
                },
            },
        ),
        Tool(
            name="search_logs",
            description=(
                "Semantic search over ingested log lines with "
                "optional filters (source / log_type / level / "
                "min_status)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "source": {"type": "string"},
                    "log_type": {
                        "type": "string", "enum": ["access", "syslog"],
                    },
                    "level": {"type": "string"},
                    "min_status": {"type": "integer"},
                    "top_k": {"type": "integer"},
                    "min_score": {"type": "number"},
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="get_log_stats",
            description=(
                "Aggregate statistics over ingested logs: counts by "
                "log_type / level / status, top IPs and paths, time "
                "range."
            ),
            inputSchema={
                "type": "object",
                "properties": {"source": {"type": "string"}},
            },
        ),
        Tool(
            name="list_log_sources",
            description=(
                "List the log files already ingested with their "
                "point counts."
            ),
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="remove_logs",
            description=(
                "Remove ALL points ingested from one log file (by "
                "source path). DESTRUCTIVE — confirm with the user "
                "before calling."
            ),
            inputSchema={
                "type": "object",
                "properties": {"source": {"type": "string"}},
                "required": ["source"],
            },
        ),
    ]


class LogAnalyzerMCPServer:
    """MCP server exposing the log-analysis host to an LLM agent."""

    def __init__(self):
        from src.mcp_client.host import LogAnalysisLocalHost

        self.host = LogAnalysisLocalHost()
        self.server = Server("divine-sense-agent-log-analyzer")
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
                if name == "ingest_log":
                    return await asyncio.to_thread(
                        self._do_ingest_log, arguments,
                    )
                if name == "analyze_anomalies":
                    return await asyncio.to_thread(
                        self._do_analyze, arguments,
                    )
                if name == "search_logs":
                    return await asyncio.to_thread(
                        self._do_search, arguments,
                    )
                if name == "get_log_stats":
                    return await asyncio.to_thread(
                        self._do_stats, arguments,
                    )
                if name == "list_log_sources":
                    return await asyncio.to_thread(
                        self._do_list_sources, arguments,
                    )
                if name == "remove_logs":
                    return await asyncio.to_thread(
                        self._do_remove, arguments,
                    )
            except Exception as e:
                logger.exception(
                    "[log-analyzer-mcp] tool %r failed: %r", name, e,
                )
                return _err(f"{type(e).__name__}: {e}")
            return _err(f"Unknown tool: {name}")

    # ── handlers (sync, wrapped in to_thread) ──────────────────
    def _do_ingest_log(self, args: dict) -> list[TextContent]:
        return _ok(self.host._ingest_log(args or {}))

    def _do_analyze(self, args: dict) -> list[TextContent]:
        return _ok(self.host._analyze(args or {}))

    def _do_search(self, args: dict) -> list[TextContent]:
        return _ok(self.host._search(args or {}))

    def _do_stats(self, args: dict) -> list[TextContent]:
        return _ok(self.host._stats(args or {}))

    def _do_list_sources(self, args: dict) -> list[TextContent]:
        return _ok(self.host._list_sources(args or {}))

    def _do_remove(self, args: dict) -> list[TextContent]:
        return _ok(self.host._remove(args or {}))

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
    server = LogAnalyzerMCPServer()
    asyncio.run(server.run_stdio())


if __name__ == "__main__":
    main()