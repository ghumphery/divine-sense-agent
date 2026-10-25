"""v0.9.103.1 — Sessions-tab multi-select (batch delete + export).

Covers:
* ``Api.delete_sessions`` — the single-dict bridge payload
  (``{"ids": [...]}``; bare list / comma-string tolerated; dedup +
  non-str stringify + 1000-cap), per-id containment (ok / not_found /
  error per id), never raises;
* ``Api.export_sessions`` — ONE combined JSON atomically written under
  the write sandbox ``uploads/``; skipped ids reported; empty /
  all-missing refusals;
* live HTTP round-trips for BOTH bridges on an ephemeral port;
* frontend source guards (index.html toolbar, app.js multi-select
  logic, i18n ×4, style.css).
"""
from __future__ import annotations

import json
import re
import sys
import threading
import urllib.parse
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


@pytest.fixture()
def api(tmp_path, monkeypatch):
    """A bare Api over an ISOLATED $QDRANT_RAG_HOME + write sandbox
    (the live .env otherwise points the write root at the user home —
    the v0.9.81.1 lazy-load_dotenv env-pinning lesson)."""
    monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path / "home"))
    write = tmp_path / "write"
    write.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("QDRANT_FILESYSTEM_WRITE_ROOT", str(write))
    monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(tmp_path / "imp"))
    from gui.api import Api

    a = Api.__new__(Api)
    return a


def _mk_session(api, title, n_msgs=2, content="hello"):
    created = api.create_session(title)
    assert "error" not in created, created
    sid = created["id"]
    msgs = [
        {"role": "user", "content": content},
        {"role": "assistant", "content": content + " (reply)"},
    ][:max(1, n_msgs)]
    r = api.save_session(sid, title, msgs)
    assert r.get("status") == "ok", r
    return sid


class TestBatchDelete:
    def test_dict_payload_deletes_all(self, api):
        s1 = _mk_session(api, "one")
        s2 = _mk_session(api, "two")
        r = api.delete_sessions({"ids": [s1, s2]})
        assert r["ok"] is True and r["deleted"] == 2 and \
            r["not_found"] == 0
        sid_list = [x["id"] for x in api.list_sessions()["sessions"]]
        assert s1 not in sid_list and s2 not in sid_list

    def test_bare_list_and_comma_string_payloads(self, api):
        s1 = _mk_session(api, "one")
        s2 = _mk_session(api, "two")
        r1 = api.delete_sessions([s1])
        r2 = api.delete_sessions(",".join([s2]))
        assert r1["deleted"] == 1 and r2["deleted"] == 1

    def test_missing_ids_reported_not_fatal(self, api):
        s1 = _mk_session(api, "one")
        r = api.delete_sessions(
            {"ids": [s1, "ghost1234", "ghost5678"]})
        assert r["ok"] is True and r["deleted"] == 1 and \
            r["not_found"] == 2
        statuses = {x["id"]: x["status"] for x in r["results"]}
        assert statuses[s1] == "ok"
        assert statuses["ghost1234"] == "not_found"

    def test_empty_and_unknown_payloads_safe(self, api):
        assert api.delete_sessions({"ids": []})["deleted"] == 0
        assert api.delete_sessions(None)["deleted"] == 0
        assert api.delete_sessions({"ids": ""})["deleted"] == 0
        assert api.delete_sessions({"ids": 12345})["deleted"] == 0

    def test_dedup_and_nonstr_items(self, api):
        s1 = _mk_session(api, "one")
        r = api.delete_sessions({"ids": [s1, s1, 123]})
        assert r["ok"] is True and r["deleted"] == 1
        assert sorted(x["status"] for x in r["results"]) == \
            ["not_found", "ok"]

    def test_single_dict_positional_bridge_shape(self, api):
        """The web shim / pywebview bridges deliver the payload as ONE
        positional arg (the v0.9.96.3 note-convention)."""
        s1 = _mk_session(api, "one")
        r = api.delete_sessions({"ids": [s1]})
        assert r["ok"] is True and r["deleted"] == 1


class TestExportSessions:
    def test_combined_json_written_in_uploads(self, api, tmp_path):
        s1 = _mk_session(api, "first", content="第一段")
        s2 = _mk_session(api, "second", content="second tail")
        r = api.export_sessions({"ids": [s1, s2]})
        assert r["ok"] is True and r["count"] == 2
        p = Path(r["path"])
        assert p.name == r["name"]
        assert re.match(
            r"sessions-export-\d{8}-\d{6}\.json", r["name"])
        assert "uploads" in p.parts[-2]
        data = json.loads(p.read_text(encoding="utf-8"))
        assert data["count"] == 2 and data["skipped"] == []
        ids = [x["id"] for x in data["sessions"]]
        assert ids == [s1, s2]
        body = json.dumps(data, ensure_ascii=False)
        assert "第一段" in body and "second tail" in body
        assert r["size"] == p.stat().st_size

    def test_skips_missing_ids(self, api):
        s1 = _mk_session(api, "one")
        r = api.export_sessions({"ids": [s1, "ghost0000"]})
        assert r["ok"] is True and r["count"] == 1
        assert r["skipped"] == ["ghost0000"]
        ids = [x["id"] for x in
               json.loads(Path(r["path"]).read_text(
                   encoding="utf-8"))["sessions"]]
        assert ids == [s1]

    def test_empty_payload_refused(self, api):
        assert "error" in api.export_sessions({"ids": []})
        assert "error" in api.export_sessions(None)

    def test_all_missing_refused(self, api):
        r = api.export_sessions({"ids": ["zzzz1111", "zzzz2222"]})
        assert "error" in r and "no sessions found" in r["error"]

    def test_export_survives_deleted_current_selection(self, api):
        """Export does NOT delete — the file list is untouched."""
        s1 = _mk_session(api, "keep")
        sid_list = [x["id"] for x in api.list_sessions()["sessions"]]
        assert s1 in sid_list


# ─── live HTTP round-trips (web shim bridge shape) ──────────────


class _HttpSrv:
    def __init__(self, api, assets_dir: Path):
        from gui.web_server import make_handler
        from http.server import ThreadingHTTPServer
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
        import http.client
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
    def test_post_delete_sessions(self, api_http):
        api, assets = api_http
        s1 = _mk_session(api, "one")
        with _HttpSrv(api, assets) as srv:
            r = srv.post("/api/delete_sessions",
                         {"args": [{"ids": [s1]}]})
        assert r["ok"] is True and r["result"]["deleted"] == 1
        sid_list = [x["id"] for x in api.list_sessions()["sessions"]]
        assert s1 not in sid_list

    def test_post_export_sessions(self, api_http):
        api, assets = api_http
        s1 = _mk_session(api, "exp")
        with _HttpSrv(api, assets) as srv:
            r = srv.post("/api/export_sessions",
                         {"args": [{"ids": [s1]}]})
        assert r["ok"] is True
        data = r["result"]
        assert data["count"] == 1 and data["path"].endswith(".json")


# ─── frontend source guards ─────────────────────────────────────


class TestFrontend:
    def test_index_toolbar_and_select_column(self):
        html = _read("gui/assets/index.html")
        for frag in ("sess-sel-all", "sess-del-sel", "sess-export-sel",
                     "sess-toolbar", 'class="sel-col"',
                     'data-i18n="sessSelAll"'):
            assert frag in html, frag

    def test_app_js_multi_select_logic(self):
        js = _read("gui/assets/app.js")
        for frag in (
            "function _sessSelectedIds()",
            "function _updateSessionsToolbar()",
            "async function sessionsDeleteSelected()",
            "async function sessionsExportSelected()",
            "function _sessSelectAllHandler()",
            "head.indeterminate = ids.length > 0 && ids.length < all.length;",
            "if (!confirm(t('sessDeleteSel', { n: ids.length }))) return;",
            "api.call('delete_sessions', { ids: ids })",
            "api.call('export_sessions', { ids: ids })",
            "await downloadSandboxFile(r.path);",
            "'<td class=\"sel-col\"><input type=\"checkbox\" class=\"sess-sel\"'",
        ):
            assert frag in js, frag

    def test_select_all_bound_once_in_bindevents(self):
        js = _read("gui/assets/app.js")
        i = js.index("_sessSelectAllHandler();")
        # wired from bindEvents (single bind, never per refresh)
        seg = js[js.index("function bindEvents"):]
        assert seg.count("_sessSelectAllHandler();") == 1

    def test_error_row_colspan_covers_new_column(self):
        js = _read("gui/assets/app.js")
        body = js[js.index("async function refreshSessions"):
                  js.index("window.renderSessionList")]
        # v0.9.133.7 — the table gained the Summary column (7 cols).
        assert 'colspan="7"' in body

    def test_i18n_four_locales(self):
        js = _read("gui/assets/app.js")
        for key in (
            "sessSelAll:", "sessDeleteSel:", "sessExportSel:",
            "sessNone:", "sessDeleted:", "sessExported:",
        ):
            assert js.count(key) == 4, key

    def test_style_css_toolbar_rules(self):
        css = _read("gui/assets/style.css")
        for frag in (".sess-toolbar {", ".sess-toolbar .chk",
                     ".sel-col", "#sess-del-sel:disabled",
                     "#sess-export-sel:disabled"):
            assert frag in css, frag

    def test_row_delete_buttons_untouched(self):
        """The per-row Load / 刪除 single-session buttons survive."""
        js = _read("gui/assets/app.js")
        body = js[js.index("async function refreshSessions"):
                  js.index("function _sessSelectedIds")]
        assert 'data-act="load"' in body and 'data-act="delete"' in body