# -*- coding: utf-8 -*-
"""v0.9.125.9 — PWA installability tests (Android standalone launch =
NO browser banner). Covers: the manifest contract (display standalone,
start_url, icons), NETWORK-ONLY service worker (never caches dynamic
API), the .webmanifest MIME, the index <link rel="manifest">, the
web-mode-gated SW registration in app.js, the real PNG icons, and
live round-trips on an ephemeral ThreadingHTTPServer."""
from __future__ import annotations

import json
import threading
from pathlib import Path

import gui.web_server as web

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "gui" / "assets"


class TestManifestContract:
    def _manifest(self) -> dict:
        return json.loads((ASSETS / "manifest.webmanifest")
                          .read_text(encoding="utf-8"))

    def test_manifest_standalone(self):
        m = self._manifest()
        assert m["name"] == "Divine Sense Agent"
        assert m["start_url"] == "/" and m["scope"] == "/"
        # THE core promise: installed launch = no browser banner
        assert m["display"] == "standalone"
        assert m["theme_color"] and m["background_color"]

    def test_icons_declared(self):
        m = self._manifest()
        sizes = {i["sizes"] for i in m["icons"]}
        assert "192x192" in sizes and "512x512" in sizes
        assert "icon.svg" in {i["src"] for i in m["icons"]}

    def test_icon_files_real_png(self):
        for size in (192, 512):
            p = ASSETS / f"icon-{size}.png"
            assert p.is_file(), p
            assert p.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
            assert p.stat().st_size > 500     # not a stub


class TestServiceWorker:
    def test_sw_is_network_only(self):
        js = (ASSETS / "sw.js").read_text(encoding="utf-8")
        assert "skipWaiting" in js and "clients.claim" in js
        assert "addEventListener('fetch'" in js
        # NO caching of any kind — the API is stateful/dynamic and the
        # ?v= cache-bust must stay authoritative.
        assert "caches." not in js
        assert "cache.add" not in js

    def test_webmanifest_mime(self):
        assert (web._MIME[".webmanifest"]
                == "application/manifest+json")


class TestFrontendWiring:
    def test_index_has_manifest_link(self):
        html = (ASSETS / "index.html").read_text(encoding="utf-8")
        assert 'rel="manifest" href="manifest.webmanifest"' in html

    def test_sw_registration_is_web_mode_gated(self):
        js = (ASSETS / "app.js").read_text(encoding="utf-8")
        i = js.find("navigator.serviceWorker")
        assert i > 0
        # registration lives inside the web-mode gate (pywebview GUI
        # must NOT try to register anything)
        assert "window.__WEB_MODE__ === true" in js[i - 400:i + 100]
        assert "sw.js" in js


class _FakeApi:
    def get_status(self):  # pragma: no cover - not invoked
        return {"version": "test"}


class _HttpSrv:
    def __init__(self, assets_dir: Path):
        handler = web.make_handler(_FakeApi(), assets_dir)
        from http.server import ThreadingHTTPServer

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

    def get(self, path: str):
        import http.client

        conn = http.client.HTTPConnection(
            "127.0.0.1", self.port, timeout=15)
        try:
            conn.request("GET", path)
            r = conn.getresponse()
            return (r.status, r.read(),
                    r.getheader("Content-Type", ""))
        finally:
            conn.close()


class TestLiveServing:
    def test_manifest_sw_icons_round_trip(self):
        with _HttpSrv(ASSETS) as srv:
            st, body, ctype = srv.get("/manifest.webmanifest")
            assert st == 200
            assert ctype.startswith("application/manifest+json")
            assert json.loads(body.decode("utf-8"))["display"] \
                == "standalone"
            st2, body2, ctype2 = srv.get("/sw.js")
            assert st2 == 200
            assert ctype2.startswith("text/javascript")
            assert b"skipWaiting" in body2
            for size in (192, 512):
                st3, body3, ctype3 = srv.get(f"/icon-{size}.png")
                assert st3 == 200
                assert body3[:8] == b"\x89PNG\r\n\x1a\n"
                assert ctype3.startswith("image/png")