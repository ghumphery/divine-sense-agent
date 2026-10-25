"""MCP client abstraction (v0.8.0+).

Defines a transport-agnostic interface so Agent.py stays free of MCP
wire details. Both the in-process Stdio transport (child subprocess)
and the remote Streamable HTTP / SSE transport implement the same
``McpClient`` interface.

The older v0.3.x client-side McpRegistry / FirefoxClient was deleted
in v0.4.0 because we decided the local Qdrant retriever could be
called in-process (no MCP round-trip). v0.8.0 re-introduces the MCP
client architecture for a different purpose:

    * expose our own Qdrant tools to *external* hosts (server role,
      still in ``src/mcp_server/server.py``), AND
    * consume *third-party* MCP servers as Agent tools
      (client / host role, this file's children).

Common usage:

    from src.mcp_client.manager import McpManager

    manager = McpManager.from_config("config/mcp.json")
    tools   = manager.list_tools()        # list[McpTool]
    result  = manager.call("fetch_url",   # tool_name
                            {"url": "..."},
                            timeout=10)
    manager.close()
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any


@dataclass
class McpTool:
    """A single MCP tool that the Agent can call.

    ``input_schema`` is the JSON schema dict that the server returned
    in ``list_tools``. We pass it back to the LLM verbatim so the
    model knows what arguments to emit.

    ``server_name`` is the logical server this tool came from
    (e.g. ``"qdrant"``, ``"github"``). Used only for debugging /
    attribution — the LLM sees the bare ``name`` plus our
    prefix-prefixed description.
    """

    name: str
    description: str
    input_schema: dict[str, Any]
    server_name: str
    call_format: str = "openai"   # always openai in v0.8.0
    annotations: dict[str, Any] = field(default_factory=dict)


@dataclass
class McpCallResult:
    """Normalised call_tool() response.

    MCP servers may return a list of ``content`` items (text / image /
    resource link / …). We collapse everything to one JSON-serialisable
    string so we can shove it into the OpenAI ``tool`` message without
    the LLM SDK complaining about exotic payload types.
    """

    text: str
    is_error: bool = False

    def to_message_text(self) -> str:
        """Format for the LLM ``tool`` role message."""
        # The OpenAI tool-result message has a single ``content`` string.
        # We prefix errors so the model can reason about failures.
        if self.is_error:
            return f"[tool error] {self.text}"
        return self.text or ""


class McpClient(abc.ABC):
    """Abstract MCP client: connect → list_tools → call_tool → close.

    Implementations:
      * ``StdioMcpClient``  — spawn child subprocess
      * ``SseMcpClient``    — HTTP + Server-Sent Events (legacy)
      * ``StreamableHttpMcpClient`` — Streamable HTTP transport (modern)

    All implementations MUST be safe to construct with ``async with``
    semantics — but the actual connect / disconnect flow is exposed
    as plain methods so the synchronous CLI doesn't need an event
    loop. Internally we use ``asyncio.run`` to drive the protocol.
    """

    def __init__(self, server_name: str):
        self.server_name = server_name
        self._connected = False

    @abc.abstractmethod
    def connect(self) -> None:
        """Establish the MCP session (handshake + initialize).

        Raises ``RuntimeError`` on transport failure. After this
        returns, ``list_tools`` is callable.
        """

    @abc.abstractmethod
    def list_tools(self) -> list[McpTool]:
        """Return the server's tool inventory.

        Implementations may cache internally — call ``invalidate()``
        if you need a fresh fetch.
        """

    @abc.abstractmethod
    def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        timeout: float | None = None,
    ) -> McpCallResult:
        """Call a tool and return the normalised result.

        Raises ``RuntimeError`` on transport-level failure (network
        down, session closed, etc). Tool-level failures come back as
        ``McpCallResult(is_error=True, text=...)``.
        """

    @abc.abstractmethod
    def close(self) -> None:
        """Tear down the session + transport.

        Idempotent — calling more than once is fine.
        """

    @property
    def connected(self) -> bool:
        return self._connected
