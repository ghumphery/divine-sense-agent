"""Tests for the Google Keep 記事本 MCP extension (v0.9.88.0+).

All Keep interaction is mock-driven (KEEP_MODE=mock default — the
in-memory note book; live REST is covered by resolvers/gates only).
"""
from __future__ import annotations

import json
import os
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path))
    # chdir so the PROJECT's real config/mcp.json / google_oauth/ never
    # leak into the resolver tests (the calendar-sharing discovery).
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("KEEP_MODE", raising=False)
    monkeypatch.delenv("KEEP_WRITES_ENABLED", raising=False)
    monkeypatch.delenv("KEEP_CLIENT_SECRET_PATH", raising=False)
    monkeypatch.delenv("KEEP_TOKEN_PATH", raising=False)
    yield


def _make_calendar_entry(tmp_path):
    """Fake the google-calendar MCP wiring: config/mcp.json entry with
    GOOGLE_OAUTH_CREDENTIALS → google_oauth/client_secret_*.json."""
    d = tmp_path / "google_oauth"
    d.mkdir(parents=True, exist_ok=True)
    secret = d / "client_secret_test123.json"
    secret.write_text(json.dumps({
        "installed": {"client_id": "cid-test",
                      "client_secret": "csec-test",
                      "redirect_uris": ["http://localhost"]},
    }), encoding="utf-8")
    cfg = tmp_path / "config"
    cfg.mkdir(exist_ok=True)
    (cfg / "mcp.json").write_text(json.dumps({
        "servers": [{"name": "google-calendar", "transport": "stdio",
                     "command": ["npx", "-y", "@cocal/google-calendar-mcp"],
                     "env": {"GOOGLE_OAUTH_CREDENTIALS":
                             "google_oauth/client_secret_test123.json"}},
        ],
    }), encoding="utf-8")
    return d, secret


class TestEngineMock:
    def test_default_mode_is_mock(self):
        from src.keep_tools import MODE_MOCK, KeepEngine

        e = KeepEngine()
        assert e.mode == MODE_MOCK and e.is_mock

    def test_create_list_get_delete_roundtrip(self):
        from src.keep_tools import KeepEngine

        e = KeepEngine()
        out = e.create_note("魔法標題XYZ", "1. 牛奶 2. 麵包", confirm=True)
        assert out["title"] == "魔法標題XYZ" and out["mode"] == "mock"
        assert out["text"].startswith("1. 牛奶")
        found = e.list_notes("魔法標題")
        assert found["count"] == 1 and found["notes"][0]["id"] == \
            out["id"]
        got = e.get_note(out["id"])
        assert got["title"] == "魔法標題XYZ"
        assert e.delete_note(out["id"], confirm=True)["ok"] is True
        assert e.get_note(out["id"]) == {
            "error": f"找不到記事 {out['id']}", "mode": "mock",
        }

    def test_create_requires_title(self):
        from src.keep_tools import KeepEngine

        assert "error" in KeepEngine().create_note("", "x", confirm=True)

    def test_create_gate_needs_confirm(self):
        from src.keep_tools import KeepEngine

        out = KeepEngine().create_note("標題", "內文", confirm=False)
        assert "error" in out and "confirm=true" in out["error"]

    def test_delete_gate_needs_confirm(self):
        from src.keep_tools import KeepEngine

        e = KeepEngine()
        seeded = e.mock_list()[0]["id"]
        out = e.delete_note(seeded, confirm=False)
        assert "error" in out and "confirm=true" in out["error"]
        assert e.get_note(seeded)["title"]  # NOT deleted

    def test_invalid_mode_falls_back_to_mock(self, monkeypatch):
        from src.keep_tools import MODE_MOCK, KeepEngine

        monkeypatch.setenv("KEEP_MODE", "yolo")
        assert KeepEngine().mode == MODE_MOCK

    def test_list_query_filters(self):
        from src.keep_tools import KeepEngine

        e = KeepEngine()
        e.create_note("購物", "牛奶", confirm=True)
        assert e.list_notes("牛奶")["count"] == 1
        assert e.list_notes("不存在的關鍵字")["count"] == 0


class TestGates:
    def _live_engine(self, monkeypatch, writes=False):
        monkeypatch.setenv("KEEP_MODE", "live")
        if writes:
            monkeypatch.setenv("KEEP_WRITES_ENABLED", "on")
        from src.keep_tools import KeepEngine

        return KeepEngine()

    def test_live_write_refused_without_master_switch(self, monkeypatch):
        e = self._live_engine(monkeypatch)
        out = e.create_note("標題", "內文", confirm=True)
        assert "error" in out and "KEEP_WRITES_ENABLED" in out["error"]
        out = e.delete_note("notes/whatever", confirm=True)
        assert "error" in out and "KEEP_WRITES_ENABLED" in out["error"]

    def test_live_write_with_confirm_and_switch_calls_api(self,
                                                          monkeypatch):
        e = self._live_engine(monkeypatch, writes=True)
        e.live_create = MagicMock(return_value={
            "name": "notes/abc123", "title": "標題",
            "body": {"text": {"text": "內文"}},
            "createTime": "t", "updateTime": "t",
        })
        out = e.create_note("標題", "內文", confirm=True)
        assert out["title"] == "標題"
        e.live_create.assert_called_once()

    def test_live_delete_with_confirm_and_switch(self, monkeypatch):
        e = self._live_engine(monkeypatch, writes=True)
        e.live_delete = MagicMock(return_value={})
        out = e.delete_note("notes/abc", confirm=True)
        assert out["ok"] is True
        e.live_delete.assert_called_once()

    def test_live_requires_token(self, monkeypatch):
        e = self._live_engine(monkeypatch, writes=True)
        e.load_token = MagicMock(return_value=None)
        out = e.list_notes()
        assert "error" in out and "keep-auth" in out["error"]


class TestAuth:
    def test_token_path_default_under_rag_home(self):
        from src.keep_tools import KeepEngine

        e = KeepEngine()
        assert "token.json" in e.token_path()

    def test_load_token_missing(self):
        from src.keep_tools import KeepEngine

        e = KeepEngine()
        assert e.load_token() is None

    def test_save_and_load_token(self, tmp_path):
        from src.keep_tools import KeepEngine

        e = KeepEngine()
        p = e.save_token({"access_token": "t", "expiry": 0})
        tok = e.load_token()
        assert tok is not None and tok["access_token"] == "t"
        assert "token.json" in p

    def test_run_auth_flow_missing_secret(self):
        from src.keep_tools import KeepEngine

        out = KeepEngine().run_auth_flow()
        assert "error" in out and "client secret" in out["error"]

    def test_consent_scope_modes(self, tmp_path):
        _make_calendar_entry(tmp_path)
        from src.keep_tools import KEEP_SCOPE, KeepEngine

        e = KeepEngine()
        assert e._consent_scopes("calendar") == list(e.CALENDAR_SCOPES)
        assert e._consent_scopes("keep") == [KEEP_SCOPE]
        combined = e._consent_scopes("combined")
        assert KEEP_SCOPE in combined and \
            "https://www.googleapis.com/auth/calendar" in combined
        assert e.run_auth_flow(scope_mode="bogus")["error"]

    def test_auth_flow_mode_notes(self, tmp_path, monkeypatch):
        """calendar-mode consent lands the SHARED token with a clear
        keep-stays-mock note (the invalid_scope fallback path)."""
        _make_calendar_entry(tmp_path)
        from unittest.mock import patch as _patch

        from src.keep_tools import KeepEngine

        e = KeepEngine()
        fake_creds = MagicMock()
        fake_creds.token = "at"
        fake_creds.refresh_token = "rt"
        fake_creds.scopes = ["s1"]
        fake_creds.expiry = None
        with _patch(
            "google_auth_oauthlib.flow.InstalledAppFlow",
        ) as MockFlow:
            MockFlow.from_client_secrets_file.return_value = \
                MagicMock(run_local_server=MagicMock(
                    return_value=fake_creds))
            out = e.run_auth_flow(scope_mode="calendar")
        assert out["ok"] is True and out["scope_mode"] == "calendar"
        assert out["scopes"] == list(e.CALENDAR_SCOPES)
        assert "mock" in out["note"]
        # keep-mode note
        with _patch(
            "google_auth_oauthlib.flow.InstalledAppFlow",
        ) as MockFlow:
            MockFlow.from_client_secrets_file.return_value = \
                MagicMock(run_local_server=MagicMock(
                    return_value=fake_creds))
            out2 = e.run_auth_flow(scope_mode="keep")
        assert out2["ok"] is True and out2["scope_mode"] == "keep"


class TestKeepScopeGuard:
    """v0.9.88.4 — official Keep API is enterprise-only; a personal
    account's shared token carries calendar scopes only. Live calls
    with such a token must return the clean why-note instead of a
    raw Google 403."""

    def test_has_keep_scope(self, tmp_path):
        from src.keep_tools import KeepEngine

        e = KeepEngine()
        assert e.has_keep_scope() is False  # no token at all
        e.save_token({"access_token": "t", "expiry": 0,
                      "scope": "https://www.googleapis.com/auth/calendar "
                               "https://www.googleapis.com/auth/calendar.events"})
        assert e.has_keep_scope() is False  # calendar-only token
        e.save_token({"access_token": "t", "expiry": 0,
                      "scope": "https://www.googleapis.com/auth/keep "
                               "https://www.googleapis.com/auth/calendar"})
        assert e.has_keep_scope() is True

    def test_live_call_without_keep_scope_returns_why_note(
        self, tmp_path, monkeypatch,
    ):
        _make_calendar_entry(tmp_path)
        from src.keep_tools import KeepEngine

        e = KeepEngine()
        monkeypatch.setenv("KEEP_MODE", "live")
        e2 = KeepEngine()
        # calendar-only shared token (the personal-account reality)
        e2.save_token({"access_token": "t", "expiry": 0,
                       "refresh_token": "rt",
                       "scope": "https://www.googleapis.com/auth/calendar",
                       "client_id": "cid", "client_secret": "cs"})
        e2._access_token = lambda: "t"  # fresh token, no refresh POST
        out = e2.live_list()
        assert "keep scope" in out["error"]
        assert "enterprise-only" in out["error"]


class TestLocalNoteBook:
    """v0.9.89.0 — 自建本地筆記本（KEEP_MODE=local；JSON 檔持久化）。

    Same 5 keep-local tools, now backed by a real persistent store —
    the personal-account replacement for the enterprise-only official
    Keep API."""

    def _engine(self, monkeypatch=None):
        if monkeypatch is not None:
            monkeypatch.setenv("KEEP_MODE", "local")
        from src.keep_tools import KeepEngine

        return KeepEngine()

    def test_local_mode_resolves(self, monkeypatch):
        from src.keep_tools import MODE_LOCAL

        e = self._engine(monkeypatch)
        assert e.mode == MODE_LOCAL and e.is_mock is False

    def test_local_create_persists_across_engines(self, monkeypatch):
        from src.keep_tools import MODE_LOCAL

        e = self._engine(monkeypatch)
        out = e.create_note("購物清單", "牛奶、麵包", confirm=True)
        assert out["mode"] == MODE_LOCAL and out["id"].startswith("n-")
        # A NEW engine instance reads the SAME store → persisted
        e2 = self._engine()
        got = e2.get_note(out["id"])
        assert got["title"] == "購物清單" and got["text"] == "牛奶、麵包"
        assert e2.list_notes("購物")["count"] == 1

    def test_local_write_gate_needs_confirm(self, monkeypatch):
        e = self._engine(monkeypatch)
        out = e.create_note("標題", "內文", confirm=False)
        assert "error" in out and "confirm=true" in out["error"]
        assert e.status()["notes_count"] == 0  # nothing written

    def test_local_delete_gate_and_roundtrip(self, monkeypatch):
        e = self._engine(monkeypatch)
        out = e.create_note("臨時", "x", confirm=True)
        assert "error" in e.delete_note(out["id"], confirm=False)
        assert e.delete_note(out["id"], confirm=True)["ok"] is True
        assert "error" in e.get_note(out["id"])
        assert e.status()["notes_count"] == 0

    def test_local_list_query_and_recency(self, monkeypatch):
        e = self._engine(monkeypatch)
        e.create_note("會議記錄", "週會", confirm=True)
        e.create_note("採購", "牛奶", confirm=True)
        lst = e.list_notes()
        assert lst["count"] == 2
        # newest first
        assert lst["notes"][0]["title"] == "採購"
        assert e.list_notes("牛奶")["count"] == 1
        assert e.list_notes("不存在的關鍵字")["count"] == 0

    def test_local_status_carries_store_info(self, monkeypatch):
        e = self._engine(monkeypatch)
        st = e.status()
        assert st["mode"] == "local"
        assert st["notes_path"].endswith("notes.json")
        assert st["notes_count"] == 0

    def test_local_no_token_needed(self, monkeypatch):
        """The self-built notebook needs NO Google OAuth at all."""
        e = self._engine(monkeypatch)
        assert e.load_token() is None
        out = e.create_note("免授權", "純本地", confirm=True)
        assert out["mode"] == "local"


class TestNotebookFull:
    """v0.9.90.0 — 日記/備忘錄/記事本 擴充：修改、分類、markdown、檢視檔。"""

    def _engine(self, monkeypatch=None):
        if monkeypatch is not None:
            monkeypatch.setenv("KEEP_MODE", "local")
        from src.keep_tools import KeepEngine

        return KeepEngine()

    def test_update_roundtrip_local(self, monkeypatch):
        e = self._engine(monkeypatch)
        out = e.create_note("原始標題", "原始內容", confirm=True,
                            category="日記")
        upd = e.update_note(out["id"], body="# 更新內容\n- 項目一",
                            category="備忘錄", confirm=True)
        assert upd["title"] == "原始標題"  # title untouched (None)
        assert upd["text"].startswith("# 更新內容")
        assert upd["category"] == "備忘錄"
        assert upd["updateTime"] >= upd["createTime"]
        # persisted
        e2 = self._engine()
        assert e2.get_note(out["id"])["text"].startswith("# 更新內容")

    def test_update_gate_needs_confirm(self, monkeypatch):
        e = self._engine(monkeypatch)
        out = e.create_note("t", "b", confirm=True)
        r = e.update_note(out["id"], body="new", confirm=False)
        assert "error" in r and "confirm=true" in r["error"]
        assert e.get_note(out["id"])["text"] == "b"  # unchanged

    def test_update_not_found(self, monkeypatch):
        e = self._engine(monkeypatch)
        r = e.update_note("n-none", body="x", confirm=True)
        assert "error" in r and "找不到記事" in r["error"]

    def test_category_filter_and_status(self, monkeypatch):
        e = self._engine(monkeypatch)
        e.create_note("日記一", "今天天氣好", confirm=True,
                      category="日記")
        e.create_note("備忘一", "記得買牛奶", confirm=True,
                      category="備忘錄")
        e.create_note("無分類", "隨手記", confirm=True)
        assert e.list_notes(category="日記")["count"] == 1
        assert e.list_notes(category="備忘錄")["count"] == 1
        assert e.list_notes()["count"] == 3
        st = e.status()
        assert st["categories"] == ["備忘錄", "日記"]

    def test_markdown_preserved_and_preview(self, monkeypatch, tmp_path):
        e = self._engine(monkeypatch)
        body = "# 標題\n\n- **粗體**項目\n- `code`\n"
        out = e.create_note("md 筆記", body, confirm=True)
        assert e.get_note(out["id"])["text"] == body  # markdown 原樣
        md = e.note_markdown(out["id"])
        assert md["markdown"].startswith("# 標題筆記") or \
            "# 標題\n" in md["markdown"]
        assert "分類" in md["markdown"]

    def test_update_live_honest_error(self, monkeypatch):
        from src.keep_tools import KeepEngine

        monkeypatch.setenv("KEEP_MODE", "live")
        monkeypatch.setenv("KEEP_WRITES_ENABLED", "on")
        e = KeepEngine()
        e.load_token = lambda: {"access_token": "t", "expiry": 0,
                                "scope": "s"}
        r = e.update_note("notes/x", body="y", confirm=True)
        assert "update 方法" in r["error"]

    def test_host_preview_writes_sandbox(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "sandbox").mkdir()
        monkeypatch.setenv("QDRANT_FILESYSTEM_WRITE_ROOT",
                           str(tmp_path / "sandbox"))
        from src.mcp_client.host import KeepLocalHost

        host = KeepLocalHost()
        monkeypatch.setenv("KEEP_MODE", "local")
        host2 = KeepLocalHost()
        out = host2._create_note({"title": "檢視測試", "body": "內容A",
                                  "category": "日記", "confirm": True})
        pv = host2._preview_note({"note_id": out["id"]})
        assert pv["ok"] is True
        assert pv["path"].endswith(".md") and "notes/" in pv["path"]
        content = open(pv["path"], encoding="utf-8").read()
        assert "# 檢視測試" in content and "內容A" in content

    def test_host_update_and_create_handlers(self, monkeypatch):
        e = self._engine(monkeypatch)
        from src.mcp_client.host import KeepLocalHost

        monkeypatch.setenv("KEEP_MODE", "local")
        host = KeepLocalHost()
        out = host._create_note({"title": "t1", "body": "b1",
                                 "category": "c1", "confirm": True})
        assert out["category"] == "c1"
        upd = host._update_note({"note_id": out["id"], "title": "t2",
                                 "confirm": True})
        assert upd["title"] == "t2"
        assert "error" in host._update_note({"note_id": out["id"]})


class TestSharedCalendarToken:
    """v0.9.88.1 — Keep shares ONE OAuth client + token file with the
    google-calendar MCP (config/mcp.json GOOGLE_OAUTH_CREDENTIALS)."""

    def test_secret_and_token_paths_follow_calendar(self, tmp_path):
        d, secret = _make_calendar_entry(tmp_path)
        from src.keep_tools import KeepEngine

        e = KeepEngine()
        assert e._client_secret_path() == str(secret)
        assert e._token_path() == str(d / "token.json")

    def test_explicit_env_still_wins(self, tmp_path, monkeypatch):
        _make_calendar_entry(tmp_path)
        monkeypatch.setenv("KEEP_CLIENT_SECRET_PATH",
                           str(tmp_path / "mine.json"))
        monkeypatch.setenv("KEEP_TOKEN_PATH",
                           str(tmp_path / "mine_token.json"))
        from src.keep_tools import KeepEngine

        e = KeepEngine()
        assert e._client_secret_path().endswith("mine.json")
        assert e._token_path().endswith("mine_token.json")

    def test_consent_scopes_cover_keep_and_calendar(self, tmp_path):
        _make_calendar_entry(tmp_path)
        from src.keep_tools import KEEP_SCOPE, KeepEngine

        scopes = KeepEngine()._consent_scopes()
        assert KEEP_SCOPE in scopes
        assert "https://www.googleapis.com/auth/calendar" in scopes
        assert "https://www.googleapis.com/auth/calendar.events" \
            in scopes

    def test_consent_scopes_merge_existing(self, tmp_path, monkeypatch):
        _make_calendar_entry(tmp_path)
        from src.keep_tools import KeepEngine

        e = KeepEngine()
        (tmp_path / "google_oauth" / "token.json").write_text(
            json.dumps({"token": "t", "scope":
                        "https://www.googleapis.com/auth/gmail.readonly"}),
            encoding="utf-8")
        scopes = e._consent_scopes()
        assert "https://www.googleapis.com/auth/gmail.readonly" in scopes

    def test_save_token_writes_googleapis_shared_shape(self, tmp_path):
        from src.keep_tools import KeepEngine

        e = KeepEngine()
        p = e.save_token({
            "access_token": "at", "refresh_token": "rt",
            "scope": "s1 s2", "expiry": 1700000000,
            "client_id": "cid", "client_secret": "csec",
        })
        d = json.loads(open(p, encoding="utf-8").read())
        assert d["access_token"] == "at" and d["token_type"] == "Bearer"
        assert d["scope"] == "s1 s2"
        assert d["expiry_date"] == 1700000000_000  # ms epoch
        assert d["client_id"] == "cid" and d["client_secret"] == "csec"
        # load back
        tok = e.load_token()
        assert tok["access_token"] == "at" and tok["scope"] == "s1 s2"

    def test_load_token_reads_googleapis_shape(self, tmp_path):
        """The calendar MCP may refresh the shared file first — the
        googleapis shape ({token, expiry_date ms}) must read cleanly
        and a FRESH token must not trigger a refresh call."""
        import time as _time
        from pathlib import Path

        from src.keep_tools import KeepEngine

        e = KeepEngine()
        ms = int((_time.time() + 3600) * 1000)
        tok_path = Path(e.token_path())
        tok_path.parent.mkdir(parents=True, exist_ok=True)
        tok_path.write_text(json.dumps(
            {"token": "gcloud-at", "expiry_date": ms,
             "refresh_token": "rt", "scope": "s",
             "client_id": "cid", "client_secret": "cs"}),
            encoding="utf-8")
        tok = e.load_token()
        assert tok is not None and tok["access_token"] == "gcloud-at"
        assert e._access_token() == "gcloud-at"  # fresh → no refresh POST

    def test_refresh_creds_fall_back_to_secret_file(self, tmp_path):
        _make_calendar_entry(tmp_path)
        from src.keep_tools import KeepEngine

        e = KeepEngine()
        # token file WITHOUT client_id/secret (npm-server refresh shape)
        (tmp_path / "google_oauth" / "token.json").write_text(
            json.dumps({"token": "t", "expiry_date": 0,
                        "refresh_token": "rt", "scope": "s"}),
            encoding="utf-8")
        assert e._client_id_secret() == ("cid-test", "csec-test")


class TestHost:
    def _host(self):
        from src.mcp_client.host import KeepLocalHost

        return KeepLocalHost()

    def test_five_tools_exposed(self):
        host = self._host()
        tools = host.list_tools()
        assert [t.name for t in tools] == [
            "keep_list_notes", "keep_create_note", "keep_update_note",
            "keep_preview_note", "keep_get_note", "keep_delete_note",
            "keep_status",
        ]
        assert all(t.server_name == "keep-local" for t in tools)

    def test_handlers_e2e(self):
        host = self._host()
        out = host._create_note({"title": "標題", "body": "內文",
                                 "confirm": True})
        assert out["title"] == "標題"
        lst = host._list_notes({"query": "標題"})
        assert lst["count"] == 1
        assert host._get_note({"note_id": out["id"]})["title"] == "標題"
        assert host._delete_note(
            {"note_id": out["id"], "confirm": True})["ok"] is True
        # gate from the host handler too
        assert "error" in host._create_note({"title": "x"})
        assert "error" in host._delete_note({"note_id": "x"})
        assert host._status({})["mode"] == "mock"

    def test_schema_requires_confirm_for_writes(self):
        host = self._host()
        by = {t.name: t for t in host.list_tools()}
        assert by["keep_create_note"].input_schema["required"] == [
            "title", "confirm"]
        assert by["keep_delete_note"].input_schema["required"] == [
            "note_id", "confirm"]


class TestManagerWiring:
    def test_from_config_registers_keep_host(self, tmp_path):
        from src.mcp_client.manager import McpManager

        cfg = tmp_path / "mcp.json"
        cfg.write_text('{"servers": []}', encoding="utf-8")
        mgr = McpManager.from_config(str(cfg))
        names = {t.name for t in mgr.list_tools()}
        assert "keep_list_notes" in names
        assert mgr._keep_local_host is not None
        assert mgr._keep_local_host.SERVER_NAME == "keep-local"


class TestStdioParity:
    def test_specs_match_host(self):
        from src.mcp_client.host import KeepLocalHost
        from src.mcp_server.keep_server import KeepMCPServer

        server = KeepMCPServer()
        host_names = [t.name for t in KeepLocalHost().list_tools()]
        server_names = [t.name for t in server._tool_specs()]
        assert server_names == host_names


class TestSystemPrompt:
    def _agent(self):
        from src.agent.agent import Agent

        a = Agent()
        a._client = MagicMock()
        a._model = "mock-model"
        a._provider = "mock"
        return a

    def test_keep_block_present_with_tools(self):
        prompt = self._agent()._system_prompt(tools_enabled=True)
        assert "keep_list_notes" in prompt
        assert "keep_create_note" in prompt
        assert "記事本" in prompt

    def test_keep_block_absent_legacy(self):
        prompt = self._agent()._system_prompt(tools_enabled=False)
        assert "keep_list_notes" not in prompt