"""Tests for MetaLocalHost (v0.8.2+).

The MetaLocalHost exposes ``list_mcp_tools`` as a regular Agent
tool so the LLM can introspect the live ``McpManager`` catalog
mid-conversation. Without this, the LLM falls back to
``web_search("MCP 功能")`` and gives a generic answer.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from src.mcp_client.host import LocalTool, MetaLocalHost


class _FakeTool:
    """Stand-in for ``McpTool`` with real string attrs (NOT a
    ``MagicMock(name=...)``, whose ``.name`` becomes the mock id)."""

    def __init__(self, name, server_name, description=""):
        self.name = name
        self.server_name = server_name
        self.description = description


class TestMetaLocalHost:
    def test_list_tools_advertises_list_mcp_tools(self):
        meta = MetaLocalHost()
        tools = meta.list_tools()
        assert len(tools) == 1
        t = tools[0]
        assert t.name == "list_mcp_tools"
        assert t.server_name == "meta-local"
        assert "list" in t.description.lower()
        assert t.input_schema == {
            "type": "object",
            "properties": {},
        }

    def test_call_without_manager_returns_note(self):
        meta = MetaLocalHost()
        result = meta._list()
        assert result["tool_count"] == 0
        assert result["tools"] == []
        assert "not attached" in result["note"]

    def test_call_with_manager_lists_live_catalog(self):
        # Simulate a populated McpManager.
        fake_mgr = MagicMock()
        fake_mgr.list_tools.return_value = [
            _FakeTool(
                "search_documents", "qdrant-local",
                "semantic search",
            ),
            _FakeTool(
                "web_search", "web-local", "web search",
            ),
            _FakeTool(
                "list_mcp_tools", "meta-local",
                "introspect catalog",
            ),
        ]
        fake_mgr.failed = {}
        fake_mgr.active_tools.return_value = fake_mgr.list_tools.return_value
        fake_mgr.last_hidden_servers = []
        meta = MetaLocalHost()
        meta.attach_manager(fake_mgr)
        result = meta._list()

        assert result["tool_count"] == 3
        assert result["server_count"] == 3
        assert sorted(result["servers"]) == [
            "meta-local", "qdrant-local", "web-local",
        ]
        assert result["failed"] == {}
        # Flat list contains each tool with name + server.
        flat = {t["name"]: t["server"] for t in result["tools"]}
        assert flat["search_documents"] == "qdrant-local"
        assert flat["web_search"] == "web-local"
        assert flat["list_mcp_tools"] == "meta-local"
        # Grouped view exists too.
        assert "qdrant-local" in result["tools_by_server"]
        assert result["tools_by_server"]["meta-local"][0]["name"] == (
            "list_mcp_tools"
        )

    def test_failed_servers_surface_in_result(self):
        """If the manager has tracked a failed server (e.g.
        telegram stdio crashed on connect), it must appear in
        the returned ``failed`` field so the LLM knows."""
        fake_mgr = MagicMock()
        fake_mgr.list_tools.return_value = [
            _FakeTool("search_documents", "qdrant-local", "x"),
        ]
        fake_mgr.failed = {
            "telegram": "RuntimeError('spawn failed')",
        }
        fake_mgr.active_tools.return_value = fake_mgr.list_tools.return_value
        fake_mgr.last_hidden_servers = []
        meta = MetaLocalHost()
        meta.attach_manager(fake_mgr)
        result = meta._list()
        assert "telegram" in result["failed"]
        assert "spawn failed" in result["failed"]["telegram"]
        # qdrant-local is still reachable.
        assert "qdrant-local" in result["servers"]

    def test_meta_local_host_surfaces_via_manager_from_config(self):
        """End-to-end: from_config seeds MetaLocalHost and attaches
        it, so the catalog contains list_mcp_tools."""
        from src.mcp_client import McpManager

        mgr = McpManager.from_config(path=None)
        names = {t.name for t in mgr.list_tools()}
        assert "list_mcp_tools" in names
        # And we can call it through the manager.
        result = mgr.call("list_mcp_tools", {})
        assert not result.is_error
        assert "meta-local" in result.text

    def test_attach_manager_is_idempotent(self):
        """Attaching twice is fine; the second wins."""
        meta = MetaLocalHost()
        mgr_a = MagicMock()
        mgr_a.list_tools.return_value = []
        mgr_a.failed = {"a": "err"}
        meta.attach_manager(mgr_a)

        mgr_b = MagicMock()
        mgr_b.list_tools.return_value = [
            _FakeTool("x", "y", "z"),
        ]
        mgr_b.failed = {}
        mgr_b.active_tools.return_value = mgr_b.list_tools.return_value
        mgr_b.last_hidden_servers = []
        meta.attach_manager(mgr_b)

        result = meta._list()
        # The second attach wins.
        assert result["tool_count"] == 1
        assert "y" in result["servers"]

    def test_local_tool_round_trip(self):
        """The LocalTool wrapper feeds back through OpenAI cleanly."""
        meta = MetaLocalHost()
        mgr = MagicMock()
        mgr.list_tools.return_value = []
        mgr.failed = {}
        mgr.active_tools.return_value = []
        mgr.last_hidden_servers = []
        meta.attach_manager(mgr)
        tool = meta.list_tools()[0]
        assert isinstance(tool, LocalTool)
        # Drive the LocalTool ``__call__`` directly.
        result = tool({})
        assert not result.is_error
        assert '"tool_count": 0' in result.text

