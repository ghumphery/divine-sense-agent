"""v0.9.103.5 — session title rename (Sessions-tab ✏️ per-row update).

Covers:
* ``SessionManager.rename`` — title changes; messages / created_at /
  updated_at untouched (the newest-first list must not re-order); the
  long-term-memory ingest hook is SKIPPED on rename (spy) while a
  plain save() still calls it (contrast); missing sid / empty title →
  None; the file is not rewritten when the title is invalid.
* ``Api.update_session_title`` — the single-dict bridge payload
  convention (``{"id", "title"}``, key aliases sid / session_id /
  new_title; a ``(sid, title)`` 2-item sequence tolerated); clean
  errors for no-id / empty title / not-found; never raises on garbage
  payloads; messages + updated_at untouched afterwards.
* live HTTP round-trip (web shim bridge shape: ONE positional dict).
* frontend source guards (app.js rename wiring + i18n ×4) and the
  cli.py ``sessions rename`` subcommand + handler.
"""
from __future__ import annotations

import argparse
import http.client
import json
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """ISOLATED $QDRANT_RAG_HOME (the live .env otherwise points the
    sessions dir at the user home — the v0.9.81.1 lazy-load_dotenv
    env-pinning lesson)."""
    monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


# ─── SessionManager.rename ───────────────────────────────────────


class TestSessionManagerRename:
    def test_rename_updates_title_keeps_body(self, home):
        from src.agent.session import SessionManager

        mgr = SessionManager()
        s = mgr.create()
        sid = s.id
        mgr.append_message(sid, {"role": "user", "content": "第一則"})
        mgr.append_message(sid, {"role": "assistant", "content": "好的"})
        before = mgr.load(sid)
        assert before.title != "新股價分析"

        r = mgr.rename(sid, "  新股價分析  ")
        assert r is not None and r.title == "新股價分析"

        after = mgr.load(sid)
        assert after.title == "新股價分析"
        assert [m["content"] for m in after.messages] == ["第一則", "好的"]
        assert after.created_at == before.created_at
        assert after.updated_at == before.updated_at

    def test_rename_missing_sid_none(self, home):
        from src.agent.session import SessionManager

        mgr = SessionManager()
        assert mgr.rename("ghost1234", "anything") is None

    def test_rename_empty_title_none_file_untouched(self, home):
        from src.agent.session import SessionManager

        mgr = SessionManager()
        s = mgr.create()
        mgr.append_message(s.id, {"role": "user", "content": "hi"})
        sid = s.id
        before = json.loads((mgr.root / f"{sid}.json").read_text(
            encoding="utf-8"))
        assert mgr.rename(sid, "   ") is None
        assert mgr.rename(sid, "") is None
        assert mgr.rename(sid, None) is None  # type: ignore[arg-type]
        after = json.loads((mgr.root / f"{sid}.json").read_text(
            encoding="utf-8"))
        assert after == before

    def test_rename_never_reroutes_list_order(self, home):
        from src.agent.session import Session, SessionManager

        mgr = SessionManager()
        # Explicit distinct timestamps — two sessions created within
        # the SAME wall-clock second would tie on updated_at and make
        # the order assertion flaky.
        s1 = Session()
        s1.messages = [{"role": "user", "content": "older"}]
        s2 = Session()
        s2.messages = [{"role": "user", "content": "newer"}]
        s1.updated_at = "2026-09-01T00:00:00"
        s2.updated_at = "2026-09-02T00:00:00"
        mgr.save(s1, ingest=False, touch=False)
        mgr.save(s2, ingest=False, touch=False)
        assert mgr.list()[0]["id"] == s2.id  # newest first
        mgr.rename(s1.id, "old but renamed")
        assert mgr.list()[0]["id"] == s2.id  # order UNCHANGED by rename
        assert mgr.load(s1.id).updated_at == "2026-09-01T00:00:00"

    def test_rename_skips_memory_ingest(self, home, monkeypatch):
        import src.agent.memory_store as ms

        calls: list[str] = []

        def _spy(sess):
            calls.append(str(sess.id))
            return None

        monkeypatch.setattr(ms, "ingest_session_pair", _spy)
        monkeypatch.setattr(ms, "memory_enabled", lambda: True)
        from src.agent.session import SessionManager

        mgr = SessionManager()
        s = mgr.create()
        sid = s.id
        mgr.append_message(sid, {"role": "user", "content": "q"})
        mgr.append_message(
            sid, {"role": "assistant", "content": "a real answer"})
        assert calls == [sid, sid]  # both appends ingested
        mgr.rename(sid, "renamed")
        assert calls == [sid, sid]  # the rename ingested NOTHING


# ─── Api.update_session_title ─────────────────────────────────────


@pytest.fixture()
def api(tmp_path, monkeypatch):
    """A bare Api over an ISOLATED $QDRANT_RAG_HOME + write sandbox,
    with the memory ingest hard-off (this suite must never touch the
    live Qdrant memory collection) and a CLEAN class singleton (the
    lazy SessionManager would otherwise stay pinned to whatever
    earlier test created)."""
    monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path / "home"))
    write = tmp_path / "write"
    write.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("QDRANT_FILESYSTEM_WRITE_ROOT", str(write))
    monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(tmp_path / "imp"))
    import gui.api as gui_api
    import src.agent.memory_store as ms

    monkeypatch.setattr(ms, "memory_enabled", lambda: False)
    monkeypatch.setattr(gui_api.Api, "_session_mgr", None)
    from gui.api import Api

    return Api.__new__(Api)


def _mk_sess(api, title="first"):
    created = api.create_session(title)
    assert "error" not in created, created
    sid = created["id"]
    r = api.save_session(sid, title, [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi there"},
    ])
    assert r.get("status") == "ok", r
    return sid


def _title_of(api, sid) -> str:
    ss = [x for x in api.list_sessions()["sessions"] if x["id"] == sid]
    return ss[0]["title"] if ss else ""


def _updated_of(api, sid) -> str:
    ss = [x for x in api.list_sessions()["sessions"] if x["id"] == sid]
    return ss[0].get("updated_at") if ss else ""


class TestUpdateSessionTitle:
    def test_dict_payload_renames(self, api):
        s1 = _mk_sess(api)
        r = api.update_session_title({"id": s1, "title": "新股價分析"})
        assert r["status"] == "ok" and r["title"] == "新股價分析"
        sessions = api.list_sessions()["sessions"]
        assert _title_of(api, s1) == "新股價分析" and sessions

    def test_key_aliases_tolerated(self, api):
        s1 = _mk_sess(api)
        r1 = api.update_session_title({"sid": s1, "new_title": "t2"})
        r2 = api.update_session_title({"session_id": s1, "title": "t3"})
        assert r1["status"] == "ok" and r1["title"] == "t2"
        assert r2["status"] == "ok" and r2["title"] == "t3"
        assert _title_of(api, s1) == "t3"

    def test_tuple_payload_tolerated(self, api):
        s1 = _mk_sess(api)
        r = api.update_session_title((s1, "tuple-title"))
        assert r["status"] == "ok" and r["title"] == "tuple-title"
        assert _title_of(api, s1) == "tuple-title"

    def test_messages_and_updated_at_untouched(self, api):
        s1 = _mk_sess(api)
        before_upd = _updated_of(api, s1)
        r = api.update_session_title({"id": s1, "title": "only title"})
        assert r["status"] == "ok"
        s = api.load_session(s1)
        assert len(s["messages"]) == 2
        assert s["messages"][0]["content"] == "hello"
        assert _updated_of(api, s1) == before_upd

    def test_empty_title_refused(self, api):
        s1 = _mk_sess(api)
        for bad in ("", "   ", "\t"):
            r = api.update_session_title({"id": s1, "title": bad})
            assert "error" in r and "title is empty" in r["error"], r
        assert _title_of(api, s1) == "first"  # file untouched

    def test_not_found_refused(self, api):
        r = api.update_session_title({"id": "ghost1234", "title": "x"})
        assert "error" in r and "not found" in r["error"]

    def test_garbage_payloads_never_raise(self, api):
        for payload in (None, 12345, {"foo": 1}, {}, [], ("solo",),
                        ["", ""], {"id": "", "title": "x"}):
            r = api.update_session_title(payload)
            assert "error" in r, payload

    def test_payload_parser_matrix(self, api):
        f = api._session_title_from_payload
        assert f({"id": "a", "title": "b"}) == ("a", "b")
        assert f({"sid": "a", "new_title": "b"}) == ("a", "b")
        assert f(("a", "b")) == ("a", "b")
        assert f(["a", "b"]) == ("a", "b")
        assert f("a") == ("a", "")
        assert f(None) == ("", "")
        assert f(123) == ("", "")
        assert f({"id": 7, "title": 9}) == ("7", "9")
        assert f({"id": "  ", "title": " "}) == ("", "")
        assert f({"id": None, "title": None}) == ("", "")


# ─── live HTTP round-trip (web shim bridge shape) ────────────────


class _HttpSrv:
    def __init__(self, api, assets_dir: Path):
        from gui.web_server import make_handler

        handler = make_handler(api, assets_dir)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()

    def post(self, path, body) -> dict:
        conn = http.client.HTTPConnection(
            "127.0.0.1", self.port, timeout=10)
        try:
            conn.request(
                "POST", path, body=json.dumps(body),
                headers={"Content-Type": "application/json"})
            r = conn.getresponse()
            return json.loads(r.read().decode("utf-8"))
        finally:
            conn.close()


@pytest.fixture()
def api_http(tmp_path, monkeypatch, api):
    """Reuse the isolated Api; expose the real assets dir for the
    handler so the shim injection path runs like production."""
    return api, Path(ROOT) / "gui" / "assets"


class TestHttpBridge:
    def test_post_update_session_title(self, api_http):
        api, assets = api_http
        s1 = _mk_sess(api)
        with _HttpSrv(api, assets) as srv:
            r = srv.post("/api/update_session_title",
                         {"args": [{"id": s1, "title": "renamed"}]})
        assert r["ok"] is True
        assert r["result"]["status"] == "ok"
        assert r["result"]["title"] == "renamed"
        assert _title_of(api, s1) == "renamed"

    def test_post_update_session_title_not_found(self, api_http):
        api, assets = api_http
        with _HttpSrv(api, assets) as srv:
            r = srv.post("/api/update_session_title",
                         {"args": [{"id": "ghost0000", "title": "x"}]})
        # The envelope stays ok:true; the RESULT carries the error.
        assert r["ok"] is True
        assert "not found" in r["result"]["error"]


# ─── frontend source guards ──────────────────────────────────────


class TestFrontend:
    def test_app_js_rename_wiring(self):
        js = _read("gui/assets/app.js")
        for frag in (
            'data-act="rename"',
            "escapeHtml(t('sessRename'))",
            "renBtn.dataset.curTitle = s.title || '';",
            "await sessionsRename(id, btn.dataset.curTitle || '');",
            "async function sessionsRename(id, currentTitle) {",
            "const input = prompt(t('sessRenamePrompt'), currentTitle || '');",
            "if (input === null) return;  // cancelled",
            "if (!title) { toast(t('sessRenameEmpty'), 'warn'); return; }",
            "api.call('update_session_title',",
            "{ id: id, title: title });",
            "chatSession.title = r.title || title;",
            "syncStateMirror();",
            "refreshSessionPicker();",
            "refreshSessions();",
        ):
            assert frag in js, frag

    def test_i18n_four_locales(self):
        js = _read("gui/assets/app.js")
        for key in ("sessRename:", "sessRenamePrompt:",
                    "sessRenameEmpty:", "sessRenamed:"):
            assert js.count(key) == 4, key

    def test_row_buttons_all_three(self):
        """The per-row Load / ✏️ / 刪除 buttons coexist."""
        js = _read("gui/assets/app.js")
        body = js[js.index("async function refreshSessions"):
                  js.index("function _sessSelectedIds")]
        assert 'data-act="load"' in body
        assert 'data-act="delete"' in body
        assert 'data-act="rename"' in body

    def test_no_batch_rename_toolbar(self):
        """Rename is PER-ROW by design — no batch toolbar button."""
        html = _read("gui/assets/index.html")
        assert "sess-rename-sel" not in html


# ─── cli.py sessions rename ──────────────────────────────────────


class TestChatTabRename:
    """v0.9.133.8 — ✏️ rename the CURRENT session from the CHAT tab
    (the ✏️ sat only in the Sessions table before)."""

    def _read(self, name: str) -> str:
        import io
        import os

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        return io.open(
            os.path.join(root, "gui", "assets", name),
            encoding="utf-8", errors="replace").read()

    def test_index_has_chat_rename_button(self):
        html = self._read("index.html")
        assert 'id="chat-rename"' in html
        assert 'data-i18n="sessRename"' in html

    def test_app_wires_button_and_fallback(self):
        js = self._read("app.js")
        assert "async function renameCurrentSession()" in js
        assert "addEventListener('click', renameCurrentSession)" in js
        # no session file yet → saveSession() first
        assert "await saveSession();" in js
        # the real rename reuses the shared helper (prompt + lockstep)
        assert "await sessionsRename(chatSession.id," in js
        assert "renBtn.title = t('sessRenamePrompt')" in js


class TestCli:
    def test_subcommand_and_handler_exist(self):
        src = _read("cli.py")
        assert '"rename", parents=[common]' in src
        assert '"title", type=str, nargs="+"' in src
        body = src[src.index("def _handle_sessions"):]
        assert "mgr.rename(args.session_id, new_title)" in body
        assert "新標題不可為空白" in body

    def test_rename_end_to_end(self, home, capsys):
        from src.agent.session import SessionManager

        mgr = SessionManager()
        s = mgr.create()
        mgr.append_message(s.id, {"role": "user", "content": "q"})
        from cli import _handle_sessions

        args = argparse.Namespace(
            sessions_cmd="rename", session_id=s.id,
            title=["新股價", "分析"])
        _handle_sessions(args)
        out = capsys.readouterr().out
        assert "Renamed" in out and "新股價 分析" in out
        assert mgr.load(s.id).title == "新股價 分析"

    def test_rename_cli_empty_title(self, home, capsys):
        from src.agent.session import SessionManager

        mgr = SessionManager()
        s = mgr.create()
        from cli import _handle_sessions

        args = argparse.Namespace(
            sessions_cmd="rename", session_id=s.id, title=["   "])
        _handle_sessions(args)
        out = capsys.readouterr().out
        assert "新標題不可為空白" in out
        assert mgr.load(s.id).title == ""  # untouched

    def test_rename_cli_missing_session(self, home, capsys):
        from cli import _handle_sessions

        args = argparse.Namespace(
            sessions_cmd="rename", session_id="ghost9999",
            title=["x"])
        _handle_sessions(args)
        assert "No session with id ghost9999" in capsys.readouterr().out