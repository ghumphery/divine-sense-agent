"""v0.9.13.0+ MCP_ACTIVE_SERVERS gate tests — toolroute-safe.

These tests build a REAL Agent(); the live .env may set
QDRANT_TOOLROUTE which would now make every turn spend a Jev call
and (mode=on) reroute the surface — force it off for the whole file
(the same lazy-load_dotenv re-injection lesson from v0.9.81.1).
"""
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(autouse=True)
def _toolroute_off(monkeypatch):
    monkeypatch.setenv("QDRANT_TOOLROUTE", "off")
    yield
    monkeypatch.setenv("QDRANT_TOOLROUTE", "off")


@pytest.fixture()
def roots(tmp_path, monkeypatch):
    """Pin the filesystem sandboxes + home at a per-test tmp dir so
    the from_config local-host seeding never touches the cwd."""
    r = tmp_path / "read"
    r.mkdir()
    w = tmp_path / "write"
    w.mkdir()
    monkeypatch.setenv("QDRANT_FILESYSTEM_READ_ROOT", str(r))
    monkeypatch.setenv("QDRANT_FILESYSTEM_WRITE_ROOT", str(w))
    monkeypatch.setenv("WATCH_DIR", str(tmp_path / "watch"))
    monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path))
    return tmp_path


def _agent():
    from src.agent.agent import Agent

    return Agent()


def _tool_names(a):
    return {t["function"]["name"] for t in a._chat_tools()}


# ─── gate behaviour ──────────────────────────────────────────


class TestGate:
    def test_empty_gate_exposes_full_surface(self, roots, monkeypatch):
        monkeypatch.delenv("MCP_ACTIVE_SERVERS", raising=False)
        monkeypatch.setenv("MCP_SURFACE_MAX_TOOLS", "0")
        a = _agent()
        # Agent() runs load_dotenv (override=False) which can RE-INJECT
        # the live .env's MCP_ACTIVE_SERVERS after our delenv — clear it
        # again AFTER construction so the gate really is off.
        monkeypatch.delenv("MCP_ACTIVE_SERVERS", raising=False)
        full = a.mcp_manager.openai_tools()
        assert len(full) > 0
        # gate off exposes EVERYTHING the manager has (superset: remote
        # stdio servers keep inserting tools asynchronously, and a
        # collision rename re-keys an entry to the tail — neither a
        # strict list nor an exact set compare is stable here).
        chat_names = {t["function"]["name"] for t in a._chat_tools()}
        full_names = {t["function"]["name"] for t in full}
        assert chat_names >= full_names

    def test_gate_exposes_only_listed_servers(
        self, roots, monkeypatch
    ):
        monkeypatch.setenv(
            "MCP_ACTIVE_SERVERS", "datetime-local,meta-local"
        )
        a = _agent()
        assert _tool_names(a) == {"get_current_time", "list_mcp_tools"}

    def test_unknown_names_skipped_with_warning(
        self, roots, monkeypatch, caplog
    ):
        monkeypatch.setenv(
            "MCP_ACTIVE_SERVERS", "datetime-local,nope-server"
        )
        a = _agent()
        assert _tool_names(a) == {"get_current_time"}
        assert any(
            "unknown servers skipped" in r.message
            for r in caplog.records
        )

    def test_zero_match_falls_back_to_full(self, roots, monkeypatch):
        monkeypatch.setenv("MCP_ACTIVE_SERVERS", "nope-a,nope-b")
        monkeypatch.setenv("MCP_SURFACE_MAX_TOOLS", "0")
        a = _agent()
        assert len(a._chat_tools()) == len(
            a.mcp_manager.openai_tools()
        )

    def test_gate_is_comma_space_tolerant(self, roots, monkeypatch):
        monkeypatch.setenv(
            "MCP_ACTIVE_SERVERS", " datetime-local , meta-local , "
        )
        a = _agent()
        assert _tool_names(a) == {"get_current_time", "list_mcp_tools"}


# ─── integration: the gate feeds the tool loop ───────────────


class TestChatTurnIntegration:
    def test_chat_turn_uses_gated_surface(self, roots, monkeypatch):
        """chat_turn with tools ON must pass the GATED list to the
        loop, not the full manager surface."""
        monkeypatch.setenv(
            "MCP_ACTIVE_SERVERS", "datetime-local,meta-local"
        )
        a = _agent()
        mgr = MagicMock()
        mgr.has_tools.return_value = True
        specs = []
        for name, srv in (
            ("get_current_time", "datetime-local"),
            ("search_documents", "qdrant-local"),
        ):
            spec = MagicMock()
            spec.tool.name = name
            spec.tool.server_name = srv
            spec.to_openai.return_value = {
                "type": "function",
                "function": {
                    "name": name,
                    "description": "",
                    "parameters": {},
                },
            }
            specs.append(spec)
        mgr.active_specs.return_value = [
            s for s in specs
            if s.tool.server_name in
            {"datetime-local", "meta-local"}
        ]
        mgr.list_tools.return_value = specs
        mgr.openai_tools.return_value = [
            s.to_openai() for s in specs
        ]
        a._mcp_manager = mgr

        tools = a._chat_tools()
        assert {t["function"]["name"] for t in tools} == {
            "get_current_time",
        }

    def test_dispatch_still_routes_by_name_after_gating(
        self, roots, monkeypatch
    ):
        """Gating only hides schemas from the LLM — the manager
        dispatch keeps working for every registered tool (e.g. a
        schedule fire calling a hidden tool)."""
        monkeypatch.setenv("MCP_ACTIVE_SERVERS", "datetime-local")
        from src.mcp_client.manager import McpManager

        mgr = McpManager.from_config(path=None, include_local_hosts=True)
        result = mgr.call("search_documents", {"query": "x", "top_k": 1})
        # Qdrant may be down in CI — the point is it DISPATCHED to
        # the qdrant tool rather than 'Unknown tool'.
        assert "Unknown tool" not in (result.text or "")


# ─── meta catalog gate (v0.9.13.2+) ──────────────────────────


class TestMetaCatalogGate:
    def test_meta_catalog_filtered_with_hidden_note(
        self, roots, monkeypatch
    ):
        """The LLM must never discover tools it cannot call — the
        catalog lists only the EXPOSED tools and names the hidden
        servers with a do-not-fake note (live failure 2026-09-04:
        launch was never dispatched, the model echoed the call as
        plain text)."""
        monkeypatch.setenv(
            "MCP_ACTIVE_SERVERS", "datetime-local,meta-local"
        )
        from src.mcp_client.host import MetaLocalHost
        from src.mcp_client.manager import McpManager

        mgr = McpManager.from_config(path=None, include_local_hosts=True)
        meta = MetaLocalHost()
        meta.attach_manager(mgr)
        result = meta._list()
        names = {t["name"] for t in result["tools"]}
        assert names <= {"get_current_time", "list_mcp_tools"}
        # The gate MUST hide something (the full surface has 100+
        # tools) and the allowed servers must never be hidden.
        # (Do NOT assert a specific remote server — remote connects
        # are environment-dependent.)
        assert result["hidden_servers"]
        assert not (
            {"datetime-local", "meta-local"}
            & set(result["hidden_servers"])
        )
        assert "CANNOT be called" in result["hidden_note"]

    def test_no_gate_no_hidden_note(self, roots, monkeypatch):
        """Gate off → full catalog, no hidden metadata."""
        monkeypatch.delenv("MCP_ACTIVE_SERVERS", raising=False)
        monkeypatch.setenv("MCP_SURFACE_MAX_TOOLS", "0")
        from src.mcp_client.host import MetaLocalHost
        from src.mcp_client.manager import McpManager

        mgr = McpManager.from_config(path=None, include_local_hosts=True)
        meta = MetaLocalHost()
        meta.attach_manager(mgr)
        result = meta._list()
        assert "hidden_servers" not in result
        assert "hidden_note" not in result


# ─── per-tool allow-list override (v0.9.14.0+) ───────────────


class TestPerToolOverride:
    def _mgr(self):
        from src.mcp_client.manager import McpManager

        return McpManager.from_config(path=None, include_local_hosts=True)

    def test_explicit_tool_list_overrides_env_gate(
        self, roots, monkeypatch
    ):
        monkeypatch.setenv("MCP_ACTIVE_SERVERS", "meta-local")
        mgr = self._mgr()
        specs = mgr.active_specs(["get_current_time"])
        assert {s.tool.name for s in specs} == {"get_current_time"}

    def test_explicit_empty_list_disables_everything(self, roots):
        """An explicit EMPTY list = the caller disabled every tool —
        honoured (no fallback), the request carries no tools."""
        mgr = self._mgr()
        assert mgr.active_specs([]) == []

    def test_none_uses_env_gate(self, roots, monkeypatch):
        monkeypatch.setenv("MCP_ACTIVE_SERVERS", "datetime-local")
        mgr = self._mgr()
        specs = mgr.active_specs(None)
        assert {s.tool.name for s in specs} == {"get_current_time"}

    def test_unknown_tool_names_ignored(self, roots):
        mgr = self._mgr()
        specs = mgr.active_specs(["get_current_time", "ghost-tool"])
        assert {s.tool.name for s in specs} == {"get_current_time"}

    def test_hidden_servers_tracked_per_tool(self, roots):
        mgr = self._mgr()
        mgr.active_specs(["get_current_time"])
        assert mgr.last_hidden_servers
        assert "datetime-local" not in mgr.last_hidden_servers

    def test_active_tools_returns_mcp_tools(self, roots):
        mgr = self._mgr()
        tools = mgr.active_tools(["get_current_time"])
        assert {t.name for t in tools} == {"get_current_time"}