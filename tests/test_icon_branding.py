"""v0.9.103.3 — project icon branding (the Divine Sense dog).

v0.9.127.4 — the whole set was REBUILT from the user-provided
``C:/Users/humphery/images/dog_icon.png`` (1024x1024 full-color
puppy; the historical v0.9.103.3 source was
``C:\\Users\\humphery\\dog_icon_bw.svg``). Committed under
``gui/assets/``:

* ``icon.svg`` — an SVG wrapper embedding the 512px PNG as a base64
  data-URI (web favicon + header logo keep the SAME hrefs);
* ``icon.ico`` — 16/32/48/64/128/256 (legacy favicon + the
  PyInstaller exe icon wired in ``gui.spec``);
* ``icon-192.png`` — PWA manifest icon + the android launcher
  (``res/drawable-nodpi/launch_screen.png``);
* ``icon-256.png`` — preview/repo art.

No cairo/magick needed at runtime — the icons are committed assets.
"""
from __future__ import annotations

import http.client
import json
import re
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "gui" / "assets"


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


class TestIconAssets:
    def test_ico_exists_and_loadable(self):
        from PIL import Image

        ico = Image.open(ASSETS / "icon.ico")
        assert ico.format == "ICO"
        assert sorted(ico.ico.sizes()) == [
            (16, 16), (32, 32), (48, 48), (64, 64),
            (128, 128), (256, 256),
        ]

    def test_ico_frame_sizes_render(self):
        from PIL import Image

        ico = Image.open(ASSETS / "icon.ico")
        for size in ((16, 16), (32, 32), (256, 256)):
            ico.size = size
            frame = ico.copy()
            assert frame.size == size

    def test_svg_wraps_the_dog_raster(self):
        # v0.9.127.4 — the svg is a wrapper embedding the 512px PNG
        # (the v0.9.103.3 hand-built vector was replaced).
        svg = _read("gui/assets/icon.svg")
        assert 'viewBox="0 0 512 512"' in svg
        assert "data:image/png;base64," in svg
        assert "<image" in svg and 'width="512" height="512"' in svg

    def test_preview_png_256(self):
        from PIL import Image

        png = Image.open(ASSETS / "icon-256.png")
        # v0.9.127.4 — the v0.9.103.3 name/size mismatch (256 file,
        # 512x512 image, locked in by the old test) is fixed.
        assert png.size == (256, 256) and png.mode == "RGBA"


class TestIndexWiring:
    def test_favicon_links_present(self):
        html = (ASSETS / "index.html").read_text(encoding="utf-8")
        assert '<link rel="icon" type="image/svg+xml" href="icon.svg" />' \
            in html
        assert '<link rel="icon" type="image/x-icon" href="icon.ico"' \
            in html

    def test_logo_img_replaces_emoji(self):
        html = (ASSETS / "index.html").read_text(encoding="utf-8")
        assert 'class="logo-logo"' not in html
        assert '<span class="logo"><img class="logo-img"' \
            ' src="icon.svg" alt="" /></span>' in html
        assert "🧠" not in html
        css = (ASSETS / "style.css").read_text(encoding="utf-8")
        assert ".brand .logo .logo-img {" in css
        assert "border-radius: 50%" in css

    def test_spec_icon_wired(self):
        spec = (ROOT / "gui.spec").read_text(encoding="utf-8")
        assert re.search(
            r"icon=str\(PROJECT_ROOT / 'gui' / 'assets' / 'icon\.ico'\)",
            spec) is not None
        # the old commented wrong-path placeholder must be gone
        assert "assets' / 'icon.ico') if available" not in spec


# ─── live HTTP: the favicon really serves ────────────────────────


class TestHttpServing:
    def test_icon_ico_and_svg_served(self, tmp_path):
        from gui.web_server import make_handler

        assets = Path(ROOT) / "gui" / "assets"
        handler = make_handler(object(), assets)
        from http.server import ThreadingHTTPServer
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        server.daemon_threads = True
        port = server.server_address[1]
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        try:
            for path, ctype in (
                ("/icon.ico", "image/x-icon"),
                ("/icon.svg", "image/svg+xml"),
            ):
                conn = http.client.HTTPConnection(
                    "127.0.0.1", port, timeout=10)
                try:
                    conn.request("GET", path)
                    r = conn.getresponse()
                    body = r.read()
                    assert r.status == 200, path
                    assert ctype in (r.getheader("Content-Type") or ""), path
                    assert len(body) > 0, path
                finally:
                    conn.close()
        finally:
            server.shutdown()
            server.server_close()