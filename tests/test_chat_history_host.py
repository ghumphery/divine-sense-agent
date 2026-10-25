"""v0.9.21.0 — ChatHistoryLocalHost tests (chat-history MCP core).

The LLM can query historical conversations through three READ-ONLY
tools over the persisted sessions ($QDRANT_RAG_HOME/sessions/
<sid>.json via SessionManager):

    list_chat_sessions    enumerate sessions, newest first
    search_chat_history   keyword search across message content
    get_chat_session      read one session's messages (bounded)

Covers: tool surface + schemas; list (sched-* hidden by default,
keyword filter, limit, newest-first ordering); search (multi-session
hits, role filter, sid scope, snippet windows, caps, case
insensitivity, error paths); get (sid required, not-found error,
last_n / per-message truncation, scheduler sessions readable);
manager registration + end-to-end dispatch + Meta catalog; stdio
server parity; system prompt trigger (tools mode only).

All data lives in a per-test tmp QDRANT_RAG_HOME — nothing touches
the real sessions folder.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture()
def seeded_home(monkeypatch, tmp_path):
    """Hermetic sessions folder: two user chats + one scheduler fire
    session, with known keywords per session."""
    monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path))
    from src.agent.session import Session, SessionManager

    sess_dir = tmp_path / "sessions"
    sess_dir.mkdir(parents=True, exist_ok=True)
    sm = SessionManager(sess_dir)

    a = Session(sid="aaaa1111aaaa", title="討論部署流程")
    a.messages = [
        {"role": "user", "content": "幫我檢查 docker deploy 的步驟",
         "ts": "2026-09-06T10:00:00"},
        {"role": "assistant",
         "content": "部署流程：先跑 tests，再 docker build，最後 push。",
         "ts": "2026-09-06T10:00:05", "sources": ["deploy.md"]},
        {"role": "user", "content": "那 rollback 呢？",
         "ts": "2026-09-06T10:05:00"},
        {"role": "assistant", "content": "rollback 用上一個 tag 重 build。",
         "ts": "2026-09-06T10:06:00"},
    ]
    sm.save(a)

    b = Session(sid="bbbb2222bbbb", title="SQL 效能調校")
    b.messages = [
        {"role": "user", "content": "查詢很慢，幫我看 SQL index",
         "ts": "2026-09-06T14:00:00"},
        {"role": "assistant", "content": "加 composite index 後查詢變快。",
         "ts": "2026-09-06T14:01:00"},
    ]
    sm.save(b)

    s = Session(
        sid="sched-xyz-20260907000000",
        title="[schedule] daily report",
    )
    s.messages = [
        {"role": "user", "content": "daily report prompt",
         "ts": "2026-09-07T09:00:00"},
        {"role": "assistant", "content": "report generated",
         "ts": "2026-09-07T09:00:03"},
    ]
    sm.save(s)

    # SessionManager.save() stamps updated_at=_now() (second
    # resolution) — backfill distinct timestamps so newest-first
    # ordering is deterministic regardless of save speed.
    for sid, ts in (
        ("aaaa1111aaaa", "2026-09-06T10:06:00"),
        ("bbbb2222bbbb", "2026-09-06T14:01:00"),
        ("sched-xyz-20260907000000", "2026-09-07T09:00:03"),
    ):
        p = sm.root / f"{sid}.json"
        d = json.loads(p.read_text(encoding="utf-8"))
        d["updated_at"] = ts
        p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")

    yield sm


def _host():
    from src.mcp_client.host import ChatHistoryLocalHost

    return ChatHistoryLocalHost()


def _tool(host, name):
    for t in host.list_tools():
        if t.name == name:
            return t
    raise KeyError(name)


# ─── tool surface ─────────────────────────────────────────────


class TestToolSurface:
    def test_three_tools(self):
        names = [t.name for t in _host().list_tools()]
        assert names == [
            "list_chat_sessions", "search_chat_history",
            "get_chat_session",
        ]

    def test_server_name(self):
        host = _host()
        for t in host.list_tools():
            assert t.server_name == "chat-history-local"

    def test_required_fields(self):
        assert _tool(_host(), "search_chat_history") \
            .input_schema.get("required") == ["keyword"]
        assert _tool(_host(), "get_chat_session") \
            .input_schema.get("required") == ["sid"]
        assert not _tool(_host(), "list_chat_sessions") \
            .input_schema.get("required")


# ─── list_chat_sessions ──────────────────────────────────────


class TestListSessions:
    def test_hides_scheduler_sessions_by_default(self, seeded_home):
        out = _host()._list({})
        ids = [s["id"] for s in out["sessions"]]
        assert "aaaa1111aaaa" in ids and "bbbb2222bbbb" in ids
        assert "sched-xyz-20260907000000" not in ids

    def test_include_scheduled(self, seeded_home):
        out = _host()._list({"include_scheduled": True})
        ids = {s["id"] for s in out["sessions"]}
        assert "sched-xyz-20260907000000" in ids

    def test_kind_field(self, seeded_home):
        out = _host()._list({"include_scheduled": True})
        kinds = {s["id"]: s["kind"] for s in out["sessions"]}
        assert kinds["aaaa1111aaaa"] == "chat"
        assert kinds["sched-xyz-20260907000000"] == "schedule-fire"

    def test_keyword_title_filter(self, seeded_home):
        out = _host()._list({"keyword": "SQL"})
        ids = [s["id"] for s in out["sessions"]]
        assert ids == ["bbbb2222bbbb"]

    def test_limit_truncates(self, seeded_home):
        out = _host()._list({"limit": 1})
        assert out["returned"] == 1
        assert out["total_sessions"] == 2
        assert out["truncated"] is True

    def test_newest_first(self, seeded_home):
        out = _host()._list({})
        ids = [s["id"] for s in out["sessions"]]
        assert ids[0] == "bbbb2222bbbb"  # saved last

    def test_empty_history(self, tmp_path, monkeypatch):
        monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path))
        out = _host()._list({})
        assert out["total_sessions"] == 0 and out["sessions"] == []


# ─── search_chat_history ─────────────────────────────────────


class TestSearchHistory:
    def test_hits_across_sessions(self, seeded_home):
        out = _host()._search({"keyword": "docker"})
        assert out["total_hits"] >= 2
        assert any(
            m["session_id"] == "aaaa1111aaaa" for m in out["matches"]
        )

    def test_case_insensitive(self, seeded_home):
        out = _host()._search({"keyword": "DOCKER"})
        assert out["total_hits"] >= 2

    def test_role_filter(self, seeded_home):
        out = _host()._search({"keyword": "docker", "role": "user"})
        assert all(m["role"] == "user" for m in out["matches"])
        assert out["total_hits"] == 1  # only the user msg mentions it

    def test_sid_scope(self, seeded_home):
        out = _host()._search(
            {"keyword": "docker", "sid": "aaaa1111aaaa"},
        )
        assert all(
            m["session_id"] == "aaaa1111aaaa" for m in out["matches"]
        )

    def test_sid_not_found(self, seeded_home):
        out = _host()._search({"keyword": "x", "sid": "nope00000000"})
        assert "error" in out

    def test_empty_keyword_error(self, seeded_home):
        out = _host()._search({})
        assert "error" in out

    def test_snippet_window(self, seeded_home):
        out = _host()._search({"keyword": "docker"})
        snips = [m["snippet"] for m in out["matches"]]
        assert all("docker" in s.lower() for s in snips)
        assert all(len(s) <= 300 for s in snips)  # default cap 280 + …

    def test_hides_scheduler_by_default(self, seeded_home):
        out = _host()._search({"keyword": "report"})
        assert out["total_hits"] == 0
        out2 = _host()._search({
            "keyword": "report", "include_scheduled": True,
        })
        assert out2["total_hits"] >= 1

    def test_limit_and_truncated(self, seeded_home):
        # seed a second "docker"-heavy session so keyword hits span
        # multiple messages → limit=1 forces truncation.
        from src.agent.session import Session, SessionManager

        sm = SessionManager(seeded_home.root)
        c = Session(sid="cccc3333cccc", title="docker 後續")
        c.messages = [
            {"role": "user", "content": "docker compose 起不來",
             "ts": "2026-09-06T15:00:00"},
            {"role": "assistant",
             "content": "docker compose 需先 docker login registry。",
             "ts": "2026-09-06T15:01:00"},
        ]
        sm.save(c)
        out = _host()._search({"keyword": "docker", "limit": 1})
        assert out["returned"] == 1
        assert out["total_hits"] > 1 and out["truncated"] is True

    def test_long_content_snippet_not_full_message(self, seeded_home):
        from src.agent.session import Session, SessionManager

        sm = SessionManager(seeded_home.root)
        # keyword sits in the MIDDLE of a 6KB message — the snippet
        # must be a ±window around it (both ellipses), not the head.
        long_msg = "x" * 3000 + " deployment " + "y" * 3000
        c = Session(sid="cccc3333cccc", title="長訊息")
        c.messages = [{"role": "user", "content": long_msg,
                       "ts": "2026-09-06T16:00:00"}]
        sm.save(c)
        out = _host()._search({
            "keyword": "deployment", "sid": "cccc3333cccc",
            "max_chars": 280,
        })
        s = out["matches"][0]["snippet"]
        assert len(s) < 400
        assert s.startswith("…") and s.endswith("…")
        assert "deployment" in s


# ─── get_chat_session ────────────────────────────────────────


class TestGetSession:
    def test_missing_sid_error(self, seeded_home):
        assert "error" in _host()._get({})

    def test_not_found_error(self, seeded_home):
        assert "error" in _host()._get({"sid": "nope00000000"})

    def test_returns_messages(self, seeded_home):
        out = _host()._get({"sid": "aaaa1111aaaa"})
        assert out["n_messages"] == 4
        assert out["returned"] == 4
        assert out["messages"][0]["role"] == "user"
        assert out["messages"][1]["sources"] == ["deploy.md"]

    def test_last_n_truncation(self, seeded_home):
        out = _host()._get({"sid": "aaaa1111aaaa", "last_n": 2})
        assert out["n_messages"] == 4 and out["returned"] == 2
        assert "most recent 2 of 4" in out["note"]

    def test_msg_content_truncation(self, seeded_home):
        from src.agent.session import Session, SessionManager

        sm = SessionManager(seeded_home.root)
        c = Session(sid="dddd4444dddd", title="truncate me")
        c.messages = [{"role": "user", "content": "y" * 3000,
                       "ts": "2026-09-06T17:00:00"}]
        sm.save(c)
        out = _host()._get({"sid": "dddd4444dddd", "max_msg_chars": 200})
        assert out["returned"] == 1
        assert len(out["messages"][0]["content"]) < 300
        assert "…[truncated 200]" in out["messages"][0]["content"]

    def test_scheduler_session_readable_by_id(self, seeded_home):
        out = _host()._get({"sid": "sched-xyz-20260907000000"})
        assert out["title"] == "[schedule] daily report"


# ─── LocalTool coercion round trip ───────────────────────────


class TestLocalToolRoundTrip:
    def test_search_bare_args(self, seeded_home):
        result = _tool(_host(), "search_chat_history")(
            {"keyword": "docker"},
        )
        assert not result.is_error
        payload = json.loads(result.text)
        assert payload["total_hits"] >= 2

    def test_search_wrapper_key_args(self, seeded_home):
        """v0.8.6.1 coercion — {'args': {...}} wrapper shapes must
        reach the handler, not crash it."""
        result = _tool(_host(), "search_chat_history")(
            {"args": {"keyword": "docker"}},
        )
        assert not result.is_error

    def test_get_none_sid_is_error_text(self, seeded_home):
        result = _tool(_host(), "get_chat_session")(None)
        assert not result.is_error  # handler returned an error dict
        payload = json.loads(result.text)
        assert "error" in payload


# ─── manager registration ────────────────────────────────────


class TestManagerRegistration:
    def test_from_config_registers_chat_history(self, seeded_home):
        from src.mcp_client.manager import McpManager

        mgr = McpManager.from_config(path=None, include_local_hosts=True)
        names = {t.name for t in mgr.list_tools()}
        assert "list_chat_sessions" in names
        assert "search_chat_history" in names
        assert "get_chat_session" in names
        assert mgr._chat_history_local_host is not None
        assert "chat-history-local" not in mgr.failed

    def test_dispatch_reaches_host(self, seeded_home):
        from src.mcp_client.manager import McpManager

        mgr = McpManager.from_config(path=None, include_local_hosts=True)
        result = mgr.call("search_chat_history", {"keyword": "docker"})
        assert not result.is_error
        payload = json.loads(result.text)
        assert payload["total_hits"] >= 2

    def test_meta_catalog_lists_chat_history(self, seeded_home,
                                             monkeypatch):
        # the live .env gates MCP_ACTIVE_SERVERS — a gate that does
        # not list chat-history-local hides it from the META catalog
        # (that is the v0.9.13.2 discovery-trap contract). Wipe the
        # gate so the full surface is visible for this check.
        monkeypatch.delenv("MCP_ACTIVE_SERVERS", raising=False)
        from src.mcp_client.host import MetaLocalHost
        from src.mcp_client.manager import McpManager

        mgr = McpManager.from_config(path=None, include_local_hosts=True)
        meta = MetaLocalHost()
        meta.attach_manager(mgr)
        result = meta._list()
        assert "list_chat_sessions" in json.dumps(result)


# ─── stdio server ────────────────────────────────────────────


class TestStdioServer:
    def test_tool_specs_match_host_surface(self):
        from src.mcp_client.host import ChatHistoryLocalHost
        from src.mcp_server.chat_history_server import _build_tool_specs

        spec_names = {s.name for s in _build_tool_specs()}
        host_names = {t.name for t in ChatHistoryLocalHost().list_tools()}
        assert spec_names == host_names

    def test_specs_required_fields_match(self):
        from src.mcp_server.chat_history_server import _build_tool_specs

        for spec in _build_tool_specs():
            if spec.name in ("search_chat_history", "get_chat_session"):
                assert spec.inputSchema.get("required")
            else:
                assert not spec.inputSchema.get("required")

    def test_server_handler_paths(self, seeded_home):
        from src.mcp_server.chat_history_server import ChatHistoryMCPServer

        server = ChatHistoryMCPServer()
        assert len(server._tool_specs()) == 3
        blocks = server._do_search({"keyword": "docker"})
        payload = json.loads(blocks[0].text)
        assert payload["total_hits"] >= 2


# ─── system prompt ───────────────────────────────────────────


class TestSystemPrompt:
    def _agent(self):
        from src.agent.agent import Agent

        a = Agent()
        a._client = MagicMock()
        a._model = "mock-model"
        a._provider = "mock"
        return a

    def test_tools_mode_mentions_chat_history(self):
        a = self._agent()
        prompt = a._system_prompt(tools_enabled=True)
        assert "list_chat_sessions" in prompt
        assert "search_chat_history" in prompt
        assert "get_chat_session" in prompt
        assert "上次聊過什麼" in prompt  # zh trigger words wired

    def test_legacy_mode_excluded(self):
        a = self._agent()
        prompt = a._system_prompt(tools_enabled=False)
        assert "list_chat_sessions" not in prompt