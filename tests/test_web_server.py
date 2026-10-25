"""v0.9.15.0 — Web UI mode (gui/web_server.py) tests.

The web server serves the SAME ``gui/assets/`` frontend as the
pywebview GUI and exposes the SAME ``gui.api.Api`` methods over
``POST /api/<method>``, with a ``/pywebview-shim.js`` injected into
index.html that implements ``window.pywebview`` on top of ``fetch``.

Covers:
* shim injection + shim contract (api proxy, state, FileDialog,
  create_file_dialog, pywebviewready);
* ``call_api_method`` dispatch (positional args, unknown / private /
  non-callable / raising methods, ``__file_dialog`` routing);
* live HTTP round-trips on an ephemeral port (index shim injection,
  static serving, traversal rejection, /healthz, /api envelopes,
  JSON error paths);
* version parity: /api/get_status must return the pyproject.toml
  version (single source of truth);
* ``gui.app`` gains the ``--web`` / ``--host`` / ``--web-port`` /
  ``--no-browser`` selector flags.

v0.9.15.1 — bind-any-local-IP support:
* ``_format_url`` / ``_urls_for`` (wildcard → loopback + LAN URLs,
  IPv6 bracketing) and ``_enumerate_lan_ips`` (stdlib probes);
* ``run_web_server`` binding paths (wildcard bind + browser on a
  usable loopback URL, env resolution AFTER ``load_dotenv``,
  IPv6 → AF_INET6 server class, non-loopback warning, bind
  failure exit code, live IPv6 loopback bind).

v0.9.15.3 — heartbeat log-noise fix:
* ``get_status`` is polled every 30 s PER OPEN PAGE (each browser
  tab / phone runs its own ``setInterval``), so at INFO it floods
  app.log; heartbeat requests are now logged at DEBUG while every
  other method stays at INFO.
"""
from __future__ import annotations

import http.client
import json
import logging
import re
import socket
import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import gui.web_server as web
from gui.web_server import (
    _inject_shim,
    _SHIM_JS,
    call_api_method,
    make_handler,
    _format_url,
    _urls_for,
    _enumerate_lan_ips,
    _ThreadingHTTPServer6,
    run_web_server,
    DEFAULT_WEB_PORT,
)


def _pyproject_version() -> str:
    pp = Path(__file__).resolve().parents[1] / "pyproject.toml"
    m = re.search(r'^version\s*=\s*"([^"]+)"', pp.read_text(encoding="utf-8"), re.M)
    assert m, "pyproject.toml version not found"
    return m.group(1)


@pytest.fixture(autouse=True)
def _quiet_gui_loggers(caplog):
    """v0.9.96.1 — tests intentionally make Api methods RAISE (mock
    raise_effect) to exercise the graceful-degradation paths; the
    ``logger.exception`` calls in gui.web / gui.api then write full
    tracebacks into the PRODUCTION ``gui/app.log``. Detach the gui
    loggers from the root handlers for the duration of each test."""
    for name in ("gui.web", "gui.api", "gui"):
        logging.getLogger(name).propagate = False
    yield
    for name in ("gui.web", "gui.api", "gui"):
        logging.getLogger(name).propagate = True


# ─── shim injection + contract ───────────────────────────────


class TestShim:
    def test_inject_after_head(self):
        html = "<html><head><meta charset='utf-8'></head><body></body></html>"
        out = _inject_shim(html)
        assert '<script src="/pywebview-shim.js"></script>' in out
        assert out.index("/pywebview-shim.js") < out.index("<meta")

    def test_inject_is_idempotent_shape(self):
        html = "<html><HEAD></HEAD></html>"
        out = _inject_shim(html)
        assert out.count("/pywebview-shim.js") == 1

    def test_shim_implements_pywebview_surface(self):
        assert "window.pywebview" in _SHIM_JS
        assert "pywebviewready" in _SHIM_JS
        assert "create_file_dialog" in _SHIM_JS
        assert "FileDialog" in _SHIM_JS
        assert "fetch('/api/'" in _SHIM_JS
        assert "state" in _SHIM_JS


# ─── call_api_method dispatch ────────────────────────────────


class TestCallApiMethod:
    def test_public_method_dispatched_positionally(self):
        api = MagicMock()
        api.echo.return_value = {"echo": ["a", 1]}
        out = call_api_method(api, "echo", ["a", 1])
        assert out == {"ok": True, "result": {"echo": ["a", 1]}}
        api.echo.assert_called_once_with("a", 1)

    def test_unknown_method_rejected(self):
        api = MagicMock(spec=["echo"])  # no other attrs
        out = call_api_method(api, "ghost", [])
        assert out["ok"] is False
        assert "ghost" in out["error"]

    def test_private_method_rejected(self):
        api = MagicMock(spec=["_secret", "echo"])
        out = call_api_method(api, "_secret", [])
        assert out["ok"] is False

    def test_non_callable_attr_rejected(self):
        class _A:
            not_callable = 42

        out = call_api_method(_A(), "not_callable", [])
        assert out["ok"] is False

    def test_raising_method_returns_error_envelope(self):
        api = MagicMock()
        api.boom.side_effect = RuntimeError("boom")
        out = call_api_method(api, "boom", [])
        assert out["ok"] is False
        assert "boom" in out["error"]

    def test_file_dialog_routed_and_monkeypatched(self, monkeypatch):
        import gui.web_server as ws
        monkeypatch.setattr(
            ws, "_native_file_dialog", lambda mode: ["C:/x/a.png"],
        )
        out = call_api_method(MagicMock(), "__file_dialog", ["open"])
        assert out == {"ok": True, "result": ["C:/x/a.png"]}


# ─── live HTTP round-trips ───────────────────────────────────


class _HttpSrv:
    """Real ThreadingHTTPServer on an ephemeral loopback port."""

    def __init__(self, api, assets_dir: Path):
        handler = make_handler(api, assets_dir)
        from http.server import ThreadingHTTPServer
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True,
        )

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()

    def get(self, path: str) -> tuple[int, str, bytes]:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request("GET", path)
            r = conn.getresponse()
            return r.status, r.getheader("Content-Type") or "", r.read()
        finally:
            conn.close()

    def get_raw(
        self, path: str, headers: dict | None = None,
    ) -> tuple[int, dict, bytes]:
        """v0.9.97.1 — raw GET returning the full headers (ETag /
        conditional-request tests)."""
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request("GET", path, headers=headers or {})
            r = conn.getresponse()
            hdrs = {k.lower(): v for k, v in r.getheaders()}
            return r.status, hdrs, r.read()
        finally:
            conn.close()

    def post(self, path: str, body: str) -> dict:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request(
                "POST", path, body=body,
                headers={"Content-Type": "application/json"},
            )
            r = conn.getresponse()
            return json.loads(r.read().decode("utf-8"))
        finally:
            conn.close()


@pytest.fixture
def real_assets() -> Path:
    return Path(__file__).resolve().parents[1] / "gui" / "assets"


class TestHttpRoundTrips:
    def test_index_cache_bust_version_query(self, real_assets):
        """v0.9.97.1 — the served index references app.js / style.css /
        the shim with a per-release ?v= query so ANY browser or
        WebView cache layer can never key a stale app.js again (the
        user-reported splash bug — rows spin, never green, jump to
        main — was the stale-JS pre-v0.9.87.3 bug signature)."""
        with _HttpSrv(MagicMock(), real_assets) as srv:
            status, _ctype, body = srv.get("/")
        assert status == 200
        html = body.decode("utf-8")
        import tomllib

        pp = Path(__file__).resolve().parents[1] / "pyproject.toml"
        ver = tomllib.load(open(pp, "rb"))["project"]["version"]
        for asset in ("app.js", "style.css", "/pywebview-shim.js"):
            assert f'{asset}?v={ver}"' in html, asset
        # v0.9.127.4 — the favicon + logo refs are stamped too
        # (covers the header logo img src="icon.svg?v=..." as well)
        assert f'icon.svg?v={ver}"' in html
        assert f'icon.ico?v={ver}"' in html
        # still a valid frontend
        assert b"pywebview-shim.js" in body

    def test_static_assets_carry_etag(self, real_assets):
        with _HttpSrv(MagicMock(), real_assets) as srv:
            status, hdrs, _body = srv.get_raw("/app.js")
        assert status == 200
        assert hdrs.get("etag", "").startswith('"')
        assert hdrs.get("cache-control") == "no-cache"

    def test_index_and_shim_are_no_store(self, real_assets):
        """v0.9.132.6 — the INDEX itself must never be cacheable: a
        stale index bakes stale asset URLs (?v=<old>) so the version
        keying never reaches the browser (a long-lived tab does not
        revalidate). no-store on every navigation."""
        with _HttpSrv(MagicMock(), real_assets) as srv:
            for path in ("/", "/index.html", "/pywebview-shim.js"):
                status, hdrs, _body = srv.get_raw(path)
                assert status == 200, path
                assert hdrs.get("cache-control") == "no-store", path
                assert hdrs.get("pragma") == "no-cache", path
                assert "etag" not in {k.lower() for k in hdrs}, path

    def test_conditional_get_returns_304(self, real_assets):
        with _HttpSrv(MagicMock(), real_assets) as srv:
            _s, hdrs, _b = srv.get_raw("/app.js")
            etag = hdrs["etag"]
            status, hdrs2, body = srv.get_raw(
                "/app.js", headers={"If-None-Match": etag})
        assert status == 304
        assert body == b""
        assert hdrs2.get("etag") == etag

    def test_index_tabs_before_main(self, real_assets):
        """v0.9.99.8 — the tab nav sits directly under the brand topbar
        (BEFORE <main>), and the document carries exactly ONE <main>
        element (the stray second <main> that used to wrap the admin
        panels is gone)."""
        html = (Path(real_assets) / "index.html").read_text(
            encoding="utf-8")
        body = html[html.index("<body"):]
        assert body.count("<main") == 1
        assert body.count("</main>") == 1
        assert body.count('<nav class="tabs"') == 1
        assert body.index("</header>") < body.index('<nav class="tabs"')
        assert body.index('<nav class="tabs"') < body.index("<main")

    def test_tabs_pinned_under_topbar(self, real_assets):
        """v0.9.99.9 — the app shell is a flex COLUMN: body > topbar /
        tabs are flex:0 (pinned) and .content is flex:1 (the only
        scroller), so the tab bar stays fixed under the brand bar
        instead of scrolling away with the page."""
        css = (Path(real_assets) / "style.css").read_text(
            encoding="utf-8")
        i_body = css.index("body {\n  display: flex")
        shell = css[i_body:css.index("}", i_body)]
        assert "flex-direction: column" in shell
        assert css.count(".topbar { flex: 0 0 auto; }") == 1
        assert css.count(".tabs   { flex: 0 0 auto; }") == 1
        i_c = css.index(".content {\n  flex: 1 1 auto; min-height: 0;")
        assert ".content { overflow: auto; padding: 16px; width: 100%; }" in css

    def test_snippets_panel_wiring(self, real_assets):
        """v0.9.100.0/100.1 — quick-phrases: the ⚡ button + panel +
        add flow exist in the frontend, the panel sits DIRECTLY ABOVE
        the chat input box, and the JS carries the full state chain
        (localStorage persist, insert-at-caret, dup/max guards)."""
        html = (Path(real_assets) / "index.html").read_text(
            encoding="utf-8")
        for frag in ("chat-snips-btn", "chat-snips-panel", "chat-snips-add",
                     "chat-snips-input", "chat-snips-list", "snipsBtn",
                     "snipsAdd", "snipsPlaceholder"):
            assert frag in html, frag
        # v0.9.100.1 — panel must be AFTER #chat-log and BEFORE .chat-input
        body = html[html.index("<body"):]
        assert body.index('id="chat-log"') < body.index('id="chat-snips-panel"')
        assert body.index('id="chat-snips-panel"') < body.index(
            '<div class="chat-input">')
        # v0.9.101.3 — the manage toggle lives INSIDE .snips-row AFTER
        # the add-wrap (default = ▼ 顯示新增列 at the END of the chip
        # row; the add-wrap starts hidden)
        row_open = body.index('class="snips-row"')
        row_close = body.index("</div>", body.index("</span>", row_open))
        list_i = body.index('id="chat-snips-list"', row_open)
        add_i = body.index('id="chat-snips-add-wrap"', row_open)
        mg_i = body.index('id="chat-snips-manage"', row_open)
        assert row_open < list_i < add_i < mg_i < row_close
        assert 'id="chat-snips-add-wrap" hidden' in body
        # add-wrap must NOT be inside chat-snips-list (replaceChildren
        # would swallow it — the v0.9.101.1 bug)
        assert body.index('id="chat-snips-add-wrap"') > body.index(
            'id="chat-snips-list"')
        js = (Path(real_assets) / "app.js").read_text(encoding="utf-8")
        for frag in ("qdrant-rag.snippets", "function loadSnippets",
                     "function saveSnippets", "function renderSnippets",
                     "function insertSnippet", "function toggleSnipsPanel",
                     "function addSnippetFromInput", "function bindSnippets",
                     "bindSnippets();", "snipsDup", "snipsMax", "snipsEmpty"):
            assert frag in js, frag
        # XSS-safe rendering: chips must be built via textContent only
        assert "snip-text" in js and ".innerHTML" not in js.split(
            "function renderSnippets")[1].split("function insertSnippet")[0]
        css = (Path(real_assets) / "style.css").read_text(encoding="utf-8")
        for frag in ("#chat-snips-panel[hidden]", ".snip-chip",
                     ".snip-chip .snip-text", ".snip-chip .snip-del",
                     ".snips-row", "#chat-snips-add-wrap[hidden]"):
            assert frag in css, frag
        # i18n ×4
        for key in ("snipsBtn", "snipsAdd", "snipsPlaceholder", "snipsCount",
                    "snipsDup", "snipsMax", "snipsEmpty"):
            assert js.count(key + ":") >= 4, key

    def test_snippets_global_store_api(self, real_assets, tmp_path, monkeypatch):
        """v0.9.101.0 — the snippet store is SERVER-SIDE GLOBAL:
        Api.snippets_get / snippets_save round-trip through
        $QDRANT_RAG_HOME/snippets.json (shared by ALL devices), with
        sanitize (strip/2000-char cap/dedupe/100 cap — v0.9.122.0
        multi-line upgrade routed through src/snippet_store.py) and
        atomic writes."""
        import json as _json
        import gui.api as _api

        monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path))
        api = _api.Api.__new__(_api.Api)  # no __init__ side effects
        out = api.snippets_get()
        assert out == {"ok": True, "snippets": []}
        out = api.snippets_save({"snippets": [
            "查 華邦電 今日 kline", "  ", "查 華邦電 今日 kline",
            "x" * 500, 42, "步驟一\n步驟二"]})
        assert out["ok"] is True
        # v0.9.122.0 — multi-line entries kept VERBATIM; 500-char cap
        # is now inside the new 2000-char limit
        assert out["snippets"] == [
            "查 華邦電 今日 kline", "x" * 500, "步驟一\n步驟二"]
        # the file really holds the shared list
        f = tmp_path / "snippets.json"
        assert _json.loads(f.read_text(encoding="utf-8"))["snippets"] == (
            out["snippets"])
        # 100-cap + round-trip read
        out = api.snippets_save({"snippets": [f"p{i}" for i in range(120)]})
        assert len(out["snippets"]) == 100
        assert api.snippets_get()["snippets"] == out["snippets"]
        # bad payload tolerated
        assert api.snippets_save(None)["ok"] is True
        assert api.snippets_save({"snippets": "junk"})["ok"] is True

    def test_index_gets_shim_injected(self, real_assets):
        with _HttpSrv(MagicMock(), real_assets) as srv:
            status, ctype, body = srv.get("/")
        assert status == 200
        assert "text/html" in ctype
        assert b"/pywebview-shim.js" in body
        assert b"<body" in body  # still the real frontend

    def test_shim_served_as_js(self, real_assets):
        with _HttpSrv(MagicMock(), real_assets) as srv:
            status, ctype, body = srv.get("/pywebview-shim.js")
        assert status == 200
        assert "javascript" in ctype
        assert b"window.pywebview" in body

    def test_static_asset_served(self, real_assets):
        with _HttpSrv(MagicMock(), real_assets) as srv:
            status, ctype, body = srv.get("/app.js")
        assert status == 200
        assert "javascript" in ctype
        assert b"pywebview" in body

    def test_traversal_blocked(self, real_assets, tmp_path):
        secret = tmp_path / "secret.txt"
        secret.write_text("top secret", encoding="utf-8")
        with _HttpSrv(MagicMock(), real_assets) as srv:
            status, _ctype, body = srv.get("/../secret.txt")
        assert status == 403
        assert b"secret" not in body

    def test_missing_asset_404(self, real_assets):
        with _HttpSrv(MagicMock(), real_assets) as srv:
            status, _ctype, body = srv.get("/nope.js")
        assert status == 404

    def test_healthz(self, real_assets):
        with _HttpSrv(MagicMock(), real_assets) as srv:
            status, _ctype, body = srv.get("/healthz")
        assert status == 200
        assert json.loads(body)["ok"] is True

    def test_api_envelope_ok(self, real_assets):
        api = MagicMock()
        api.echo.return_value = {"hello": "world"}
        with _HttpSrv(api, real_assets) as srv:
            out = srv.post("/api/echo", json.dumps(["x", 3]))
        assert out == {"ok": True, "result": {"hello": "world"}}
        api.echo.assert_called_once_with("x", 3)

    def test_api_args_envelope_unwrapped(self, real_assets):
        api = MagicMock()
        api.echo.return_value = {"args": [1, 2]}
        with _HttpSrv(api, real_assets) as srv:
            out = srv.post("/api/echo", json.dumps({"args": [1, 2]}))
        assert out == {"ok": True, "result": {"args": [1, 2]}}
        api.echo.assert_called_once_with(1, 2)

    def test_api_non_json_result_degrades(self, real_assets):
        """A non-JSON-serialisable result (stray object) must come back
        as an error envelope — the request thread must never die
        without a response."""
        api = MagicMock()
        api.weird.return_value = object()
        with _HttpSrv(api, real_assets) as srv:
            out = srv.post("/api/weird", "[]")
        assert out["ok"] is False
        assert "non-JSON-serialisable" in out["error"]

    def test_api_unknown_method_error_envelope(self, real_assets):
        api = MagicMock(spec=["echo"])
        with _HttpSrv(api, real_assets) as srv:
            out = srv.post("/api/ghost", "[]")
        assert out["ok"] is False

    def test_api_invalid_json_error_envelope(self, real_assets):
        with _HttpSrv(MagicMock(), real_assets) as srv:
            out = srv.post("/api/echo", "{not json")
        assert out["ok"] is False
        assert "invalid JSON" in out["error"]

    def test_get_status_version_forwarded(self, real_assets):
        """The web layer must forward Api.get_status()'s version
        verbatim. (Parity with pyproject.toml is locked by
        tests/test_gui_api.py — the real Api kicks a ~15 s background
        prewarm + real MCP connects on its first poll, so the live
        HTTP test uses a stub returning the pyproject version.)"""
        api = MagicMock()
        api.get_status.return_value = {"version": _pyproject_version()}
        with _HttpSrv(api, real_assets) as srv:
            out = srv.post("/api/get_status", "[]")
        assert out["ok"] is True
        assert out["result"]["version"] == _pyproject_version()

    # ── v0.9.21.1: brand-side version chip sync ──

    def test_index_html_has_no_hardcoded_version(self, real_assets):
        """index.html used to carry a hardcoded 'v0.8.6' inside
        ``#app-version`` that NO JS ever updated (v0.9.14.1 rewired
        gui/api.py but missed this HTML fallback literal) — the
        brand chip showed a stale version forever. pyproject.toml is
        the single source of truth: the HTML fallback literal must
        be GONE (app.js paints it from get_status's version)."""
        html = (real_assets / "index.html").read_text(encoding="utf-8")
        import re as _re

        m = _re.search(
            r'id="app-version"\s*>([^<]*)<', html,
        )
        assert m is not None, "app-version chip must still exist"
        assert not _re.match(r"v\d", m.group(1)), (
            f"index.html #app-version must NOT hardcode a version "
            f"(got {m.group(1)!r}) — app.js paints it from "
            f"get_status (pyproject single source of truth)"
        )

    def test_app_js_paints_app_version_chip(self):
        """app.js refreshStatus() must update ``#app-version`` with
        the get_status version so the brand chip follows
        pyproject.toml."""
        js = (Path(__file__).resolve().parents[1]
              / "gui" / "assets" / "app.js").read_text(encoding="utf-8")
        idx = js.find("async function refreshStatus")
        assert idx != -1
        end = js.find("\n  }", idx)
        body = js[idx:end]
        assert "getElementById('app-version')" in body, (
            "refreshStatus must paint the brand-side #app-version chip"
        )
        assert "textContent = 'v' + r.version" in body

    # ── v0.9.87.4: splash-version chip sync (the SECOND drift) ──

    def test_index_html_splash_version_no_hardcoded_literal(
        self, real_assets,
    ):
        """v0.9.87.4 — ``#splash-version`` carried the SAME hardcoded
        'v0.8.6' drift (the v0.9.21.1 fix patched ``#app-version`` but
        missed the splash element). The literal must be GONE —
        app.js paints it from get_readiness's version."""
        html = (real_assets / "index.html").read_text(encoding="utf-8")
        import re as _re

        m = _re.search(
            r'id="splash-version"\s*>([^<]*)<', html,
        )
        assert m is not None, "splash-version element must still exist"
        assert not _re.match(r"v\d", m.group(1)), (
            f"index.html #splash-version must NOT hardcode a version "
            f"(got {m.group(1)!r}) — app.js paints it from "
            f"get_readiness (pyproject single source of truth)"
        )
        # the stale v0.8.6 literal must not appear anywhere in the HTML
        assert "v0.8.6" not in html, (
            "index.html must not carry any hardcoded version literal"
        )

    def test_app_js_paints_splash_version(self):
        """app.js runReadinessChecks() must paint ``#splash-version``
        from get_readiness's version so the splash follows
        pyproject.toml."""
        js = (Path(__file__).resolve().parents[1]
              / "gui" / "assets" / "app.js").read_text(encoding="utf-8")
        idx = js.find("getElementById('splash-version')")
        assert idx != -1, (
            "runReadinessChecks must paint the #splash-version chip"
        )
        assert "v' + r.version" in js, (
            "splash version must come from the readiness payload"
        )


# ─── gui.app selector flags ──────────────────────────────────


class TestAppSelector:
    def test_web_flag_parses(self):
        from gui.app import _parse_args
        args = _parse_args(["--web"])
        assert args.web is True
        assert args.no_browser is False

    def test_web_flags_default_off(self):
        from gui.app import _parse_args
        args = _parse_args([])
        assert args.web is False

    def test_web_host_port_flags(self):
        from gui.app import _parse_args
        args = _parse_args(
            ["--web", "--host", "127.0.0.1", "--web-port", "9000",
             "--no-browser"],
        )
        assert args.host == "127.0.0.1"
        assert args.web_port == 9000
        assert args.no_browser is True


# ─── v0.9.15.1 — bind-any-local-IP ───────────────────────────────


class _FakeServer:
    """Stands in for ThreadingHTTPServer — records the bind address;
    ``serve_forever`` returns immediately so ``run_web_server``
    exits without blocking the test."""

    instances: list = []
    address_family = socket.AF_INET

    def __init__(self, server_address, handler):
        self.server_address = server_address
        self.handler = handler
        self.daemon_threads = False
        type(self).instances.append(self)

    def serve_forever(self, poll_interval: float = 0.5) -> None:
        pass

    def server_close(self) -> None:
        pass


class _FakeServer6(_FakeServer):
    address_family = socket.AF_INET6


@pytest.fixture
def fake_servers(monkeypatch):
    # NOTE: reset ONLY the base-class list — _FakeServer6 inherits it
    # (no shadowing), so instances[-1] sees every created server
    # regardless of address family.
    _FakeServer.instances = []
    monkeypatch.setattr(web, "ThreadingHTTPServer", _FakeServer)
    monkeypatch.setattr(web, "_ThreadingHTTPServer6", _FakeServer6)
    # Deterministic LAN enumeration — the real one depends on the
    # box's NICs; tests that care override this again.
    monkeypatch.setattr(web, "_enumerate_lan_ips", lambda: ["192.168.1.50"])
    # v0.9.126.1 — the LIVE .env now carries TLS cert/key values; the
    # run_web_server entry re-loads .env and _tls_material() would
    # resolve them and flip every URL to https. These binding tests
    # assert the PLAIN-http contracts — pin the resolver to None (the
    # TLS-name banner test overrides this patch itself).
    monkeypatch.setattr(web, "_tls_material", lambda: None)
    yield _FakeServer


class TestFormatUrl:
    def test_ipv4_plain(self):
        assert _format_url("127.0.0.1", 8334) == "http://127.0.0.1:8334/"

    def test_ipv6_bracketed(self):
        assert _format_url("::1", 9000) == "http://[::1]:9000/"
        assert _format_url("fe80::1", 80) == "http://[fe80::1]:80/"

    def test_hostname_plain(self):
        assert _format_url("mypc", 8334) == "http://mypc:8334/"


class TestUrlsFor:
    def test_loopback_single_url(self):
        urls, browser = _urls_for("127.0.0.1", 8334)
        assert urls == ["http://127.0.0.1:8334/"]
        assert browser == urls[0]

    def test_wildcard_lists_loopback_plus_lan(self, monkeypatch):
        monkeypatch.setattr(
            web, "_enumerate_lan_ips",
            lambda: ["192.168.1.50", "10.0.0.5"],
        )
        urls, browser = _urls_for("0.0.0.0", 8334)
        assert urls == [
            "http://127.0.0.1:8334/",
            "http://192.168.1.50:8334/",
            "http://10.0.0.5:8334/",
        ]
        # the browser must open a URL that actually resolves
        assert browser == "http://127.0.0.1:8334/"

    def test_wildcard_v6_uses_v6_loopback(self, monkeypatch):
        monkeypatch.setattr(
            web, "_enumerate_lan_ips", lambda: ["192.168.1.50"],
        )
        urls, browser = _urls_for("::", 8334)
        assert urls == ["http://[::1]:8334/"]
        assert browser == "http://[::1]:8334/"

    def test_specific_lan_ip_single_url(self):
        urls, browser = _urls_for("192.168.1.7", 9000)
        assert urls == ["http://192.168.1.7:9000/"]
        assert browser == urls[0]


class TestEnumerateLanIps:
    def test_returns_unique_non_loopback_strings(self):
        ips = _enumerate_lan_ips()
        assert isinstance(ips, list)
        assert all(isinstance(i, str) for i in ips)
        assert all(not i.startswith("127.") for i in ips)
        assert len(ips) == len(set(ips))  # dedup'd

    def test_never_raises_when_everything_fails(self, monkeypatch):
        def _boom(*a, **kw):
            raise OSError("no network")

        monkeypatch.setattr(socket, "gethostname", lambda: "no-such-host")
        monkeypatch.setattr(web.socket, "getaddrinfo", _boom)

        class _DeadSock:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def connect(self, *a):
                raise OSError("unreachable")

            def getsockname(self):
                return ("127.0.0.1", 0)

        monkeypatch.setattr(
            web.socket, "socket", lambda *a, **kw: _DeadSock(),
        )
        assert _enumerate_lan_ips() == []


class TestHeartbeatLogging:
    """v0.9.15.3 — ``get_status`` is a per-open-page 30 s heartbeat
    (``app.js::setInterval(refreshStatus, 30000)``). Logging it at
    INFO floods app.log with one page open and is unusable with
    several (each page polls independently). Heartbeat -> DEBUG,
    every other method -> INFO."""

    def test_get_status_logged_at_debug_not_info(self, real_assets, caplog):
        with _HttpSrv(MagicMock(), real_assets) as srv, \
                caplog.at_level(logging.DEBUG, logger="gui.web"):
            srv.post("/api/get_status", "[]")
        # Match the REQUEST log shape only ("POST /api/get_status ...")
        # — with a MagicMock api the call itself may additionally
        # surface an unrelated "non-JSON result" ERROR record.
        records = [r for r in caplog.records
                   if r.name == "gui.web"
                   and "POST /api/get_status" in r.getMessage()]
        assert records, "get_status request must still be logged (DEBUG)"
        assert all(r.levelno == logging.DEBUG for r in records)

    def test_other_methods_logged_at_info(self, real_assets, caplog):
        api = MagicMock()
        api.echo.return_value = {"hi": 1}
        with _HttpSrv(api, real_assets) as srv, \
                caplog.at_level(logging.INFO, logger="gui.web"):
            srv.post("/api/echo", '["x"]')
        assert any(
            r.name == "gui.web"
            and r.levelno == logging.INFO
            and "POST /api/echo" in r.getMessage()
            for r in caplog.records
        )


class TestTlsNameBanner:
    """v0.9.126.1 — the banner prints the CERTIFIED name (Android
    installs must go through the name the SAN covers, not an IP)."""

    def test_certified_line_printed_when_https(
        self, real_assets, fake_servers, monkeypatch, capsys, tmp_path,
    ):
        cert = tmp_path / "c.pem"
        key = tmp_path / "k.pem"
        cert.write_text("CERT", encoding="utf-8")
        key.write_text("KEY", encoding="utf-8")
        # the fixture pins _tls_material to None — override it directly
        # (env-independent; the loader may re-inject .env values)
        monkeypatch.setattr(
            web, "_tls_material",
            lambda: (str(cert), str(key)))
        monkeypatch.setenv("QDRANT_WEB_TLS_NAME", "tchtool.app")
        monkeypatch.setenv("QDRANT_WEB_HOST", "0.0.0.0")
        rc = run_web_server(
            host="127.0.0.1", port=8560,
            open_browser=False, api=MagicMock(),
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert "https://127.0.0.1:8560/" in out     # scheme = https
        assert "certified as: https://tchtool.app:8560/" in out
        assert "use THIS name, not an IP" in out

    def test_name_line_absent_without_tls(
        self, real_assets, fake_servers, monkeypatch, capsys,
    ):
        for var in ("QDRANT_WEB_TLS_CERT", "QDRANT_WEB_TLS_KEY",
                    "QDRANT_WEB_TLS_AUTO", "QDRANT_WEB_TLS_NAME"):
            monkeypatch.delenv(var, raising=False)
        rc = run_web_server(
            host="127.0.0.1", port=8561,
            open_browser=False, api=MagicMock(),
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert "certified as" not in out
        assert "http://127.0.0.1:8561/" in out

    def test_certified_name_is_primary_and_browser_url(
        self, real_assets, fake_servers, monkeypatch, capsys, tmp_path,
    ):
        """v0.9.126.8 — with a certified name the NAME is the CANONICAL
        url: the banner's MAIN line and the auto-opened browser use
        https://<NAME>:<port>/; loopback is demoted to a secondary
        "also reachable at" line."""
        cert = tmp_path / "c.pem"
        key = tmp_path / "k.pem"
        cert.write_text("CERT", encoding="utf-8")
        key.write_text("KEY", encoding="utf-8")
        monkeypatch.setattr(
            web, "_tls_material",
            lambda: (str(cert), str(key)))
        monkeypatch.setenv("QDRANT_WEB_TLS_NAME", "tchtool.app")
        opened: list[str] = []
        monkeypatch.setattr(web.webbrowser, "open", opened.append)
        rc = run_web_server(
            host="127.0.0.1", port=8562,
            open_browser=True, api=MagicMock(),
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert "web UI: https://tchtool.app:8562/" in out
        assert "also reachable at: https://127.0.0.1:8562/" in out
        assert opened == ["https://tchtool.app:8562/"]


class TestRunWebServerBinding:
    def test_wildcard_binds_all_and_browser_opens_loopback(
        self, real_assets, fake_servers, monkeypatch, capsys,
    ):
        monkeypatch.setenv("QDRANT_WEB_HOST", "0.0.0.0")
        monkeypatch.setenv("QDRANT_WEB_PORT", "9999")
        opened: list[str] = []
        monkeypatch.setattr(web.webbrowser, "open", opened.append)
        rc = run_web_server(api=MagicMock(), open_browser=True)
        assert rc == 0
        assert fake_servers.instances[-1].server_address == ("0.0.0.0", 9999)
        assert opened == ["http://127.0.0.1:9999/"]
        out = capsys.readouterr().out
        # the LAN URL is printed so other devices know where to connect
        assert "http://192.168.1.50:9999/" in out
        assert "also reachable at" in out

    def test_explicit_host_wins_over_env(
        self, real_assets, fake_servers, monkeypatch,
    ):
        monkeypatch.setenv("QDRANT_WEB_HOST", "0.0.0.0")
        rc = run_web_server(
            host="192.168.1.7", port=8555,
            open_browser=False, api=MagicMock(),
        )
        assert rc == 0
        assert fake_servers.instances[-1].server_address == (
            "192.168.1.7", 8555,
        )

    def test_dotenv_loaded_before_env_resolution(
        self, real_assets, fake_servers, monkeypatch,
    ):
        calls: list = []
        import dotenv
        monkeypatch.setattr(
            dotenv, "load_dotenv", lambda *a, **kw: calls.append(a),
        )
        monkeypatch.setenv("QDRANT_WEB_HOST", "192.0.2.7")
        rc = run_web_server(open_browser=False, api=MagicMock())
        assert rc == 0
        assert calls, "load_dotenv must run BEFORE QDRANT_WEB_HOST is read"
        assert fake_servers.instances[-1].server_address == (
            "192.0.2.7", DEFAULT_WEB_PORT,
        )

    def test_ipv6_host_uses_v6_server_class(
        self, real_assets, fake_servers,
    ):
        rc = run_web_server(
            host="::1", port=8666, open_browser=False, api=MagicMock(),
        )
        assert rc == 0
        assert isinstance(fake_servers.instances[-1], _FakeServer6)

    def test_non_loopback_warning_logged(
        self, real_assets, fake_servers, caplog,
    ):
        with caplog.at_level(logging.WARNING, logger="gui.web"):
            run_web_server(
                host="0.0.0.0", port=8777,
                open_browser=False, api=MagicMock(),
            )
        assert "ALL interfaces" in caplog.text

    def test_bind_failure_returns_1(self, real_assets, caplog):
        """TEST-NET-3 is never assigned to a local interface — the
        real bind must fail cleanly with exit code 1 (no traceback)."""
        with caplog.at_level(logging.ERROR, logger="gui.web"):
            rc = run_web_server(
                host="203.0.113.9", port=8334,
                open_browser=False, api=MagicMock(),
            )
        assert rc == 1
        assert "bind FAILED" in caplog.text

    def test_live_ipv6_loopback_bind(self, real_assets):
        """The v6 server class must actually bind ``::1`` (before
        v0.9.15.1 any IPv6 host raised OSError on the AF_INET socket)."""
        handler = make_handler(MagicMock(), real_assets)
        server = None
        try:
            server = _ThreadingHTTPServer6(("::1", 0), handler)
        except OSError:
            pytest.skip("no IPv6 loopback on this machine")
        else:
            assert server.server_address[0] in ("::1", "::1%0")
        finally:
            if server is not None:
                server.server_close()


class TestSnippetCursorInsert:
    """v0.9.115.3 — snippets insert AT THE CURSOR with the caret left
    right AFTER the inserted text (user: 不要每次重頭開始). The test
    driver runs the REAL production ``insertSnippet`` — sliced out of
    app.js and evaluated under Node against a minimal fake-textarea
    DOM stub (the function's whole contract surface) — no jsdom
    needed; plain ``node`` (on PATH) suffices."""

    @staticmethod
    def _run_node_driver(script: str) -> dict:
        """Write ``script`` to a tmp .js under the project root (node
        module resolution for any require the extraction does — the
        insertSnippet slice itself needs none), run it, parse stdout
        as JSON; a non-zero exit raises an AssertionError."""
        import json
        import shutil
        import subprocess
        import tempfile
        if shutil.which("node") is None:
            pytest.skip("node not on PATH; skipping the JS driver tests")
        project_root = Path(__file__).resolve().parents[1]
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".js", delete=False, encoding="utf-8",
            dir=str(project_root),
        ) as f:
            f.write(script)
            path = f.name
        try:
            r = subprocess.run(
                ["node", path],
                capture_output=True, text=True, timeout=45,
                cwd=str(project_root),
            )
        finally:
            try:
                Path(path).unlink()
            except OSError:
                pass
        if r.returncode != 0:
            raise AssertionError(
                f"node driver failed: stdout={r.stdout!r} "
                f"stderr={r.stderr!r}"
            )
        return json.loads(r.stdout)

    def test_insert_snippet_source_is_cursor_aware(self, real_assets):
        js = (Path(real_assets) / "app.js").read_text(encoding="utf-8")
        body = js.split("function insertSnippet")[1].split(
            "function toggleSnipsPanel")[0]
        # reads the caret
        assert "input.selectionStart" in body
        assert "input.selectionEnd" in body
        # caret parked RIGHT AFTER the inserted text (computed offset,
        # not always the end-of-value)
        assert "input.setSelectionRange(caret, caret)" in body
        assert "start + ins.length" in body
        # the OLD append-at-end implementation is gone
        assert \
            "input.value = cur + (needsSp ? ' ' : '') + text;" not in js
        assert "setSelectionRange(input.value.length" not in js

    def test_insert_snippet_inserts_at_caret(self, real_assets):
        """REAL run of the production insertSnippet under Node: empty →
        prepend-and-caret; mid-text caret → insert AT the caret (the
        old code appended at the tail); an existing selection is
        REPLACED; the tail-glue separator rule is preserved."""
        driver = r'''
/* minimal fake-textarea DOM stub — the exact contract surface
   insertSnippet uses (value / selectionStart / selectionEnd /
   focus / setSelectionRange). */
function mkInput() {
  return {
    value: '',
    selectionStart: 0,
    selectionEnd: 0,
    focused: false,
    focus: function () { this.focused = true; },
    setSelectionRange: function (s, e) {
      this.selectionStart = s;
      this.selectionEnd = e;
    },
  };
}
const input = mkInput();
const document = {
  getElementById: function (id) {
    return id === 'chat-input' ? input : null;
  },
};
/* evaluate the REAL production insertSnippet — it is immediately
   followed by toggleSnipsPanel in app.js, so the slice is exact. */
const fs = require('fs');
const src = fs.readFileSync('gui/assets/app.js', 'utf-8');
const a = src.indexOf('function insertSnippet');
const b = src.indexOf('function toggleSnipsPanel');
if (a < 0 || b <= a) {
  console.log(JSON.stringify({ fatal: 'anchors not found' }));
  process.exit(0);
}
const fn = new Function('document', src.slice(a, b)
  + '\nreturn insertSnippet;')(document);
const out = [];
function run(name, value, s, e, snippet) {
  input.value = value;
  input.selectionStart = s;
  input.selectionEnd = e;
  fn(snippet);
  out.push({ name: name, value: input.value,
             ss: input.selectionStart, se: input.selectionEnd,
             focused: input.focused });
}
run('empty', '', 0, 0, '中華電信');
run('mid', '請問 的機器是哪台', 2, 2, '加密分持');
run('replace', 'abc.def', 3, 7, 'NEW');
run('glue', 'abc', 3, 3, 'X');
run('noglue', 'abc ', 4, 4, 'X');
console.log(JSON.stringify(out));
'''
        out = self._run_node_driver(driver)
        # the slice-extraction fatal marker only appears as an object
        assert not (isinstance(out, dict) and out.get("fatal")), out
        assert isinstance(out, list) and out, out
        by = {o["name"]: o for o in out}
        # 1 — empty input: the snippet starts the draft, caret after
        e1 = by["empty"]
        assert e1["value"] == "中華電信"
        assert e1["ss"] == 4 and e1["se"] == 4 and e1["focused"]
        # 2 — mid-text collapsed caret: land AT the caret with the
        # preserved glue rule ("請問 加密分持 的機器是哪台") —
        # the old code appended at the very END and parked there
        e2 = by["mid"]
        assert e2["value"] == "請問 加密分持 的機器是哪台"
        assert e2["ss"] == 7 and e2["se"] == 7
        # 3 — a selection is REPLACED, caret after the replacement
        e3 = by["replace"]
        assert e3["value"] == "abcNEW"
        assert e3["ss"] == 6 and e3["se"] == 6
        # 4 — tail append keeps the glue-space rule (no regression)
        e4 = by["glue"]
        assert e4["value"] == "abc X" and e4["ss"] == 5
        # 5 — trailing space present → no double separator
        e5 = by["noglue"]
        assert e5["value"] == "abc X" and e5["ss"] == 5

    def test_insert_snippet_chains_at_last_caret(self, real_assets):
        """The user's core ask: dropping snippet #2 continues where #1
        inserted (the caret sits right after the drop) — it does NOT
        teleport to the tail or restart from the top of the input; a
        selection is replaced with the caret parked after it."""
        driver = r'''
function mkInput() {
  return {
    value: '', selectionStart: 0, selectionEnd: 0, focused: false,
    focus: function () { this.focused = true; },
    setSelectionRange: function (s, e) {
      this.selectionStart = s; this.selectionEnd = e;
    },
  };
}
const input = mkInput();
const document = {
  getElementById: function (id) {
    return id === 'chat-input' ? input : null;
  },
};
const fs = require('fs');
const src = fs.readFileSync('gui/assets/app.js', 'utf-8');
const a = src.indexOf('function insertSnippet');
const b = src.indexOf('function toggleSnipsPanel');
const fn = new Function('document', src.slice(a, b)
  + '\nreturn insertSnippet;')(document);
const out = [];
fn('甲');                       // empty → "甲", caret right after
out.push({ v: input.value, ss: input.selectionStart,
           se: input.selectionEnd });
fn('乙');                       // chain #2 — right after #1
out.push({ v: input.value, ss: input.selectionStart,
           se: input.selectionEnd });
input.value = '前半 後半';
input.selectionStart = 2;
input.selectionEnd = 3;         /* select the space */
fn('插入');                      // replace the selection
out.push({ v: input.value, ss: input.selectionStart,
           se: input.selectionEnd });
console.log(JSON.stringify(out));
'''
        out = self._run_node_driver(driver)
        # chain: 甲 → 甲 乙 (the preserved glue rule between drops),
        # caret parked RIGHT AFTER the latest drop
        assert out[0] == {"v": "甲", "ss": 1, "se": 1}
        assert out[1] == {"v": "甲 乙", "ss": 3, "se": 3}
        # explicit selection → replaced VERBATIM (no added separator),
        # caret after the inserted text
        assert out[2] == {"v": "前半插入後半", "ss": 4, "se": 4}
