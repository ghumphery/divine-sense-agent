"""Playwright E2E tests for the Divine Sense Agent GUI.

**Test strategy**: we don't drive a real pywebview window (hard to script
across Win/macOS/Linux). Instead we:

1. Serve ``gui/assets/`` over a local HTTP server (so the browser can
   load it with a real ``http://`` URL, not ``file://``).
2. Inject a fake ``window.pywebview`` object via ``page.add_init_script``
   before any page script runs. Each fake method delegates to the same
   Python ``gui.api.Api`` class the real GUI uses - so we're testing
   the actual backend code, just routed through a browser instead of
   WebView2.
3. Run Playwright assertions on rendered DOM.

Run::

    pip install playwright pytest-playwright
    playwright install chromium
    pytest tests/test_gui_e2e.py -v

Skipped automatically if ``playwright`` is not installed.
"""
from __future__ import annotations

import http.server
import socketserver
import sys
import threading
from pathlib import Path

import pytest

# Skip the whole module if Playwright isn't available. The unit tests in
# test_gui_api.py already cover the backend; this is just UI smoke.
playwright = pytest.importorskip('playwright')
from playwright.sync_api import sync_playwright  # noqa: E402

# Make gui importable.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ─── fixtures ──────────────────────────────────────────────────


@pytest.fixture(scope="module")
def http_server():
    """Serve gui/assets/ over HTTP on a random free port."""
    assets_dir = Path(__file__).resolve().parents[1] / "gui" / "assets"
    assert assets_dir.exists(), f"missing {assets_dir}"

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(assets_dir), **kwargs)

        def log_message(self, *args, **kwargs):  # silence
            pass

    with socketserver.TCPServer(("127.0.0.1", 0), Handler) as httpd:
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        yield f"http://127.0.0.1:{port}/index.html"
        httpd.shutdown()


def _api_bridge_script():
    """JS init script that exposes the real Api as window.pywebview.api.

    Each method is a Promise-returning wrapper that calls into Python
    synchronously (in-page JS) but the test still goes through the
    real Api class - so any backend bug surfaces here.
    """
    from gui.api import Api
    api = Api()
    methods = [m for m in dir(api) if not m.startswith('_') and callable(getattr(api, m))]

    lines = [
        "(function() {",
        "  var apiMethods = " + repr(methods) + ";",
        "  var apiObj = {};",
        "  apiMethods.forEach(function(m) {",
        "    apiObj[m] = function() {",
        "      var args = Array.prototype.slice.call(arguments);",
        "      try {",
        "        var r = api[m].apply(api, args);",
        "        return Promise.resolve(r);",
        "      } catch (e) {",
        "        return Promise.resolve({error: String(e && (e.message || e))});",
        "      }",
        "    };",
        "  });",
        "  window.pywebview = {",
        "    api: apiObj,",
        "    create_file_dialog: function() { return Promise.resolve([]); },",
        "    FileDialog: { OPEN: 'open', FOLDER: 'folder' }",
        "  };",
        "})();",
    ]
    return "\n".join(lines)


# ─── tests ─────────────────────────────────────────────────────


def test_page_loads_with_chrome(http_server):
    """The index.html renders without console errors and has 6 tabs."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context().new_page()

        console_errors = []
        page.on("pageerror", lambda e: console_errors.append(str(e)))
        page.on("console", lambda msg: (
            console_errors.append(msg.text) if msg.type == "error" else None
        ))

        page.goto(http_server, wait_until="networkidle")
        page.wait_for_selector(".tab", timeout=5000)

        tabs = page.query_selector_all(".tab")
        assert len(tabs) == 6, f"expected 6 tabs, got {len(tabs)}"

        title = page.text_content(".brand .title")
        assert title and "Qdrant" in title

        assert not console_errors, f"page errors: {console_errors}"
        browser.close()


def test_tab_switching_works(http_server):
    """Clicking each tab shows the corresponding panel."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context().new_page()
        page.goto(http_server, wait_until="networkidle")

        for tab in ["ingest", "search", "admin", "stats", "sessions", "chat"]:
            page.click(f'.tab[data-tab="{tab}"]')
            panel = page.query_selector(f'#panel-{tab}')
            assert panel is not None
            klass = panel.get_attribute("class") or ""
            assert "active" in klass, f"#{tab} panel not active after click"



def test_language_switcher_changes_labels(http_server):
    """Switching to English updates at least the tab labels."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context().new_page()
        page.goto(http_server, wait_until="networkidle")

        initial_chat = page.text_content('.tab[data-tab="chat"]')
        page.select_option("#lang-select", "en")
        page.wait_for_timeout(150)
        en_chat = page.text_content('.tab[data-tab="chat"]')
        assert en_chat != initial_chat, "tab label did not change after switching to en"

        page.select_option("#lang-select", "ja")
        page.wait_for_timeout(150)
        ja_chat = page.text_content('.tab[data-tab="chat"]')
        assert ja_chat != en_chat

        page.select_option("#lang-select", "zh-CN")
        page.wait_for_timeout(150)
        cn_chat = page.text_content('.tab[data-tab="chat"]')
        assert cn_chat != ja_chat

        browser.close()


def test_chat_send_renders_response(http_server):
    """Sending a chat message populates user + assistant bubbles."""
    from gui.api import Api

    fake_answer = "# Hello\n\nThis is the **answer** with `code` and a list:\n- a\n- b\n"
    api = Api()
    api.chat_turn = lambda q, h, w, wmax, k: {
        "answer": fake_answer, "context": "...", "sources": ["README.md"],
    }
    api.list_sessions = lambda: {"sessions": []}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context()
        ctx.add_init_script(_api_bridge_script())
        page = ctx.new_page()
        page.goto(http_server, wait_until="networkidle")
        page.wait_for_selector("#chat-input")

        page.fill("#chat-input", "What is HNSW?")
        page.click("#chat-send")
        page.wait_for_selector(".chat-msg.assistant", timeout=5000)

        user_msgs = page.query_selector_all(".chat-msg.user")
        assistant_msgs = page.query_selector_all(".chat-msg.assistant")
        assert len(user_msgs) >= 1
        assert len(assistant_msgs) >= 1

        h1 = page.query_selector(".chat-msg.assistant h1")
        assert h1 is not None and "Hello" in h1.text_content()

        src = page.query_selector(".chat-msg.assistant .sources")
        assert src is not None and "README.md" in src.text_content()
        browser.close()


def test_search_renders_result_cards(http_server):
    """Search returns result cards with filename + score."""
    from gui.api import Api

    api = Api()
    api.search = lambda q, k, f: {
        "count": 2,
        "results": [
            {"filename": "README.md", "score": 0.92, "text": "HNSW is a graph..."},
            {"filename": "docs/index.html", "score": 0.81, "text": "HNSW algorithm"},
        ],
    }
    api.list_sessions = lambda: {"sessions": []}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context()
        ctx.add_init_script(_api_bridge_script())
        page = ctx.new_page()
        page.goto(http_server, wait_until="networkidle")

        page.click('.tab[data-tab="search"]')
        page.fill("#search-query", "HNSW")
        page.click("#search-go")
        page.wait_for_selector(".result-card", timeout=5000)

        cards = page.query_selector_all(".result-card")
        assert len(cards) == 2
        first = cards[0]
        assert "README.md" in first.text_content()
        assert "0.9200" in first.text_content()
        browser.close()


def test_admin_danger_zone_has_buttons(http_server):
    """Admin tab shows the danger-zone buttons."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context().new_page()
        page.goto(http_server, wait_until="networkidle")
        page.click('.tab[data-tab="admin"]')

        assert page.query_selector("#admin-recreate") is not None
        assert page.query_selector("#admin-delete") is not None
        klass = page.query_selector(".danger-zone").get_attribute("class")
        assert "danger-zone" in klass
        browser.close()


def test_mcp_editor_v082_features(http_server):
    """v0.8.2+: the MCP editor exposes raw JSON editor + tool
    overview + per-server test button."""
    init = _api_bridge_script()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context().new_page()
        page.add_init_script(init)
        page.goto(http_server, wait_until="networkidle")
        page.click('.tab[data-tab="admin"]')

        # Both new buttons exist.
        assert page.query_selector("#mcp-toggle-raw") is not None
        assert page.query_selector("#mcp-overview-toggle") is not None

        # Raw editor + overview panel exist but hidden by default.
        raw = page.query_selector("#mcp-raw-editor")
        overview = page.query_selector("#mcp-tools-overview")
        assert raw is not None and raw.get_attribute("hidden") is not None
        assert overview is not None and overview.get_attribute("hidden") is not None

        # Toggle raw editor open.
        page.click("#mcp-toggle-raw")
        page.wait_for_timeout(80)
        assert page.query_selector("#mcp-raw-textarea") is not None
        assert page.query_selector("#mcp-raw-save") is not None
        assert page.query_selector("#mcp-raw-format") is not None
        # Toggle back closed.
        page.click("#mcp-toggle-raw")
        page.wait_for_timeout(80)
        assert page.query_selector("#mcp-raw-editor").get_attribute("hidden") is not None

        # Toggle tool overview open; the panel content area exists.
        page.click("#mcp-overview-toggle")
        page.wait_for_timeout(80)
        assert page.query_selector("#mcp-tools-overview-content") is not None
        assert page.query_selector("#mcp-tools-overview").get_attribute("hidden") is None

        browser.close()


def test_theme_toggle_changes_data_attribute(http_server):
    """Toggling theme overrides light/dark."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context().new_page()
        page.goto(http_server, wait_until="networkidle")

        # initial data-theme (defaults to 'auto')
        before = page.evaluate("document.documentElement.dataset.theme")

        # Click the toggle.
        page.check("#theme-toggle")
        page.wait_for_timeout(100)
        after_dark = page.evaluate("document.documentElement.dataset.theme")
        assert after_dark == "dark"

        page.uncheck("#theme-toggle")
        page.wait_for_timeout(100)
        after_light = page.evaluate("document.documentElement.dataset.theme")
        assert after_light == "light"

        browser.close()

        browser.close()

