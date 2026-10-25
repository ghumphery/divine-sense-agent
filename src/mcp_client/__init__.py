"""MCP client + host package (v0.8.0+).

Public surface:

* ``McpManager``     — top-level orchestrator
* ``McpClient``      — abstract base; concrete subclasses in stdio.py
                       and http_sse.py
* ``McpTool``        — wire-agnostic tool descriptor
* ``McpCallResult``  — wire-agnostic call result
* ``LocalTool``      — wraps any Python callable as an ``McpTool``
* ``QdrantLocalHost`` /
  ``WebSearchLocalHost`` /
  ``MetaLocalHost``    — pre-baked local hosts
* ``FilesystemLocalHost`` (v0.9.3+, expanded v0.9.4+) —
  15 file / directory / search / copy / diff / patch tools
  under ``filesystem-local``.
"""
from .base import McpCallResult, McpClient, McpTool
from .host import (
    FilesystemLocalHost,
    LocalTool,
    QdrantLocalHost,
    WebSearchLocalHost,
    MetaLocalHost,
)
from .manager import McpManager, ToolSpec

__all__ = [
    "McpCallResult",
    "McpClient",
    "McpTool",
    "McpManager",
    "ToolSpec",
    "LocalTool",
    "QdrantLocalHost",
    "WebSearchLocalHost",
    "MetaLocalHost",
    "FilesystemLocalHost",
]
