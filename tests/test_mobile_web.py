from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "gui" / "assets" / "index.html"
CSS = ROOT / "gui" / "assets" / "style.css"


# ─── viewport meta (index.html) ──────────────────────────────


class TestMobileViewport:
    """v0.9.15.2 — index.html 必須帶手機 viewport meta。

    沒有 viewport meta 時手機瀏覽器以虛擬 980px 桌面寬度排版再
    縮小，style.css 的 max-width 斷點全部失效 — 這是「手機上
    UI 太小」的根本原因，也是最關鍵的一行。
    """

    def test_index_has_viewport_meta(self):
        html = INDEX.read_text(encoding="utf-8")
        assert 'name="viewport"' in html
        assert "width=device-width" in html
        assert "initial-scale=1" in html

    def test_index_has_theme_color_metas(self):
        html = INDEX.read_text(encoding="utf-8")
        assert 'name="theme-color"' in html

    def test_index_has_mobile_web_app_metas(self):
        html = INDEX.read_text(encoding="utf-8")
        assert 'name="mobile-web-app-capable"' in html
        assert 'name="apple-mobile-web-app-capable"' in html

    def test_viewport_meta_inside_head(self):
        """meta 必須在 <head> 內（放在 body 會被部分瀏覽器忽略）。"""
        html = INDEX.read_text(encoding="utf-8")
        head_end = html.index("</head>")
        assert html.index('name="viewport"') < head_end


# ─── responsive CSS (style.css) ──────────────────────────────


class TestMobileCss:
    """style.css 必須有手機斷點 + 關鍵手機規則。"""

    def test_has_tablet_breakpoint(self):
        css = CSS.read_text(encoding="utf-8")
        assert "@media (max-width: 820px)" in css

    def test_has_phone_breakpoint(self):
        css = CSS.read_text(encoding="utf-8")
        assert "@media (max-width: 480px)" in css

    def test_mobile_blocks_come_after_base_rules(self):
        """手機區塊必須在檔尾（同權重覆寫需後寫入勝出）。"""
        css = CSS.read_text(encoding="utf-8")
        assert css.index(".chat-msg { padding") < css.index(
            "@media (max-width: 820px)"
        )

    def test_dvh_chat_log(self):
        """聊天視窗高度用 dvh — 100vh 在手機會被網址列遮住。"""
        css = CSS.read_text(encoding="utf-8")
        assert "100dvh" in css

    def test_ios_zoom_prevention_16px(self):
        """輸入框字號 16px 防 iOS Safari 聚焦自動放大。"""
        css = CSS.read_text(encoding="utf-8")
        assert "font-size: 16px" in css
        # 更高特異性的表單選擇器也要覆蓋到。
        assert ".mcp-edit-form input, .mcp-edit-form select" in css

    def test_modal_full_width_on_phone(self):
        css = CSS.read_text(encoding="utf-8")
        assert css.count(".modal-content { min-width: 0; width: 94vw; }") == 1

    def test_toast_container_full_width(self):
        css = CSS.read_text(encoding="utf-8")
        assert ".toast-container { left: 12px; right: 12px;" in css

    def test_data_table_horizontal_scroll(self):
        css = CSS.read_text(encoding="utf-8")
        assert ".data-table {\n    display: block; overflow-x: auto;" in css

    def test_touch_niceties(self):
        css = CSS.read_text(encoding="utf-8")
        assert "-webkit-tap-highlight-color" in css
        assert "touch-action: manipulation" in css

    def test_mcp_server_item_stacks(self):
        css = CSS.read_text(encoding="utf-8")
        assert ".mcp-server-item { flex-direction: column; }" in css


# ─── live HTTP round-trip（真實 web server 服務的 index）──────


class _HttpSrv:
    """同 tests/test_web_server.py 的最小版：ephemeral port。"""

    def __init__(self, assets_dir: Path):
        from http.server import ThreadingHTTPServer

        from gui.web_server import make_handler

        handler = make_handler(_FakeApi(), assets_dir)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        import threading

        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True
        )

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()

    def get(self, path: str) -> tuple[int, bytes]:
        import http.client

        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request("GET", path)
            r = conn.getresponse()
            return r.status, r.read()
        finally:
            conn.close()


class _FakeApi:
    """make_handler 只在 POST /api/<method> 才觸及 api —
    本測試只 GET /，所以空殼即可。"""

    def get_status(self):  # pragma: no cover - not called
        return {"version": "test"}


class TestServedIndexMobile:
    def test_served_index_contains_viewport(self):
        assets = ROOT / "gui" / "assets"
        with _HttpSrv(assets) as srv:
            status, body = srv.get("/")
        assert status == 200
        assert b'name="viewport"' in body
        assert b"width=device-width" in body
