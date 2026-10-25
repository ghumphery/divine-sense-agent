# -*- coding: utf-8 -*-
"""v0.9.122.0 — multi-line snippets + MCP management (snippets-local).

User request: 「現在的 snippet 只有單行，以 MCP 架構提供可以編輯
多行的 snippet 的功能和管理功能」. Covers: the pure store (multi-line
verbatim / caps / dedupe / move / corrupt-read), the GUI Api
route-through (same shared file), the SnippetsLocalHost surface +
stdio parity, and the prompt/gate wiring.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ── core ──────────────────────────────────────────────────────────
@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path))
    import src.snippet_store as ss

    yield ss
    monkeypatch.delenv("QDRANT_RAG_HOME", raising=False)


class TestSnippetStore:
    def test_add_multi_line_verbatim(self, store):
        text = "第一行\n第二行\n第三行"
        out = store.add_snippet(text)
        assert out["ok"] is True and out["multi"] is True
        assert store.load_snippets()[0] == text  # \n kept VERBATIM

    def test_list_carries_full_text_and_flags(self, store):
        store.add_snippet("單行 A")
        store.add_snippet("行一\n行二")
        out = store.list_snippets()
        assert out["ok"] is True and out["count"] == 2
        e0, e1 = out["snippets"]
        assert e0["preview"] == "單行 A" and e0["multi"] is False
        assert e1["preview"] == "行一 …" and e1["multi"] is True
        assert e1["lines"] == 2 and e1["text"] == "行一\n行二"

    def test_edit_replaces_multi_line(self, store):
        store.add_snippet("舊")
        out = store.set_snippet(0, "新內容\n第二行")
        assert out["ok"] is True and out["multi"] is True
        assert store.load_snippets() == ["新內容\n第二行"]

    def test_edit_out_of_range_and_empty(self, store):
        store.add_snippet("a")
        assert "out of range" in store.set_snippet(5, "x")["error"]
        assert "required" in store.set_snippet(0, "   ")["error"]
        assert "out of range" in store.delete_snippet(9)["error"]

    def test_delete_and_move(self, store):
        for s in ("a", "b", "c"):
            store.add_snippet(s)
        assert store.delete_snippet(1)["count"] == 2
        assert store.load_snippets() == ["a", "c"]
        assert store.move_snippet(1, 0)["to"] == 0
        assert store.load_snippets() == ["c", "a"]

    def test_add_at_position(self, store):
        for s in ("a", "b"):
            store.add_snippet(s)
        assert store.add_snippet("NEW", at=0)["at"] == 0
        assert store.load_snippets() == ["NEW", "a", "b"]

    def test_duplicates_refused(self, store):
        store.add_snippet("x")
        assert "duplicate" in store.add_snippet("x")["error"]
        store.add_snippet("y")
        assert "duplicate" in store.set_snippet(1, "x")["error"]

    def test_caps_100_x_2000(self, store):
        for i in range(105):
            out = store.add_snippet(f"s{i}")
            if "cap" in str(out.get("error", "")):
                break
        items = store.load_snippets()
        assert len(items) == store.MAX_SNIPPETS
        assert "cap" in store.add_snippet("overflow")["error"]
        # char cap: a longer text truncates to MAX_CHARS (room first)
        store.delete_snippet(0)
        out = store.add_snippet("z" + "字" * 3000)
        assert out["ok"] is True
        assert len(store.load_snippets()[-1]) <= store.MAX_CHARS

    def test_crlf_normalized_and_legacy_compat(self, store):
        store.add_snippet("行一\r\n行二")
        assert store.load_snippets()[0] == "行一\n行二"
        # legacy list-with-ints tolerated (the v0.9.101 payloads)
        store.save_snippets(["ok", 42, None, "", "ok"])
        assert store.load_snippets() == ["ok"]
        store.snippets_path().write_text("not json",
                                         encoding="utf-8")
        assert store.load_snippets() == []    # corrupt → [], no raise


# ── gui api route-through ────────────────────────────────────────
class TestGuiRouteThrough:
    def test_snippets_get_save_multi_line(self, tmp_path, monkeypatch):
        import gui.api as _api

        monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path))
        api = _api.Api.__new__(_api.Api)
        out = api.snippets_save({"snippets": [
            "查詢 K 線", "多行\n第二行"]})
        assert out["ok"] is True
        assert out["snippets"][1] == "多行\n第二行"
        got = api.snippets_get()
        assert got["snippets"] == out["snippets"]
        f = tmp_path / "snippets.json"
        assert json.loads(f.read_text(encoding="utf-8"))[
            "snippets"] == out["snippets"]


# ── MCP host + stdio ─────────────────────────────────────────────
class TestSnippetHost:
    def test_surface_and_dispatch(self, store):
        from src.mcp_client.host import SnippetsLocalHost

        h = SnippetsLocalHost()
        names = [t.name for t in h.list_tools()]
        assert names == ["snippet_list", "snippet_add", "snippet_edit",
                         "snippet_delete", "snippet_move"]
        assert all(t.server_name == "snippets-local"
                   for t in h.list_tools())
        assert h._op_add({"text": "行A\n行B"})["multi"] is True
        assert store.load_snippets()[0] == "行A\n行B"

    def test_manager_actually_seeds_the_host(self, store):
        from src.mcp_client.manager import McpManager

        mgr = McpManager.from_config(path="/nonexistent/mcp.json")
        names = {t.name for t in mgr.list_tools()
                 if t.server_name == "snippets-local"}
        assert names == {"snippet_list", "snippet_add", "snippet_edit",
                         "snippet_delete", "snippet_move"}
        assert "snippets-local" not in mgr.failed

    def test_stdio_parity(self):
        from src.mcp_client.host import SnippetsLocalHost
        from src.mcp_server.snippet_server import SnippetMCPServer

        srv = SnippetMCPServer()
        host_names = [t.name for t in SnippetsLocalHost().list_tools()]
        spec = [t.name for t in srv._tool_specs()]
        assert spec == host_names


# ── wiring guards ────────────────────────────────────────────────
class TestWiringGuards:
    def test_prompt_block_and_route_row(self):
        agent = Path(__file__).resolve().parent.parent.joinpath(
            "src/agent/agent.py").read_text(encoding="utf-8")
        assert "Snippets / quick phrases" in agent
        assert '"snippets-local"' in agent
        import src.agent.agent as A

        servers, _ = A._toolroute_servers_for(
            None, "把這段常用的回覆加入片語", "")
        assert "snippets-local" in servers

    def test_gate_line_contains_snippets(self):
        env = Path(__file__).resolve().parent.parent / ".env"
        raw = env.read_text(encoding="utf-8", errors="replace")
        assert "snippets-local" in raw