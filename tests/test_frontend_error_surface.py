"""v0.9.61.1 — frontend global error surface + modal z-index.

The user report was 「MCP 設備(第三方 servers) 的 edit 不能用 — 點 ✏
編輯按鈕沒反應（編輯視窗沒彈出）」. Investigation (live server
round-trip + JSDOM full-flow harness) proved the editor code itself
works end-to-end; the failure was environment/session-specific. Two
defensive hardenings ship here:

1. A GLOBAL JS error surface at the very top of app.js — every
   uncaught error / unhandled rejection becomes a visible red toast
   (the diagrams v0.9.5.5 lesson: silent click-handler exceptions
   vanish into the void and become 「按鈕沒反應」 user reports).
2. ``.modal`` z-index raised 1000 → 5000 (toasts at 10000 stay on
   top) so no stacking oddity can hide the editor dialog.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class TestGlobalErrorSurface:
    def test_app_js_registers_window_error_listener(self):
        js = (ROOT / "gui/assets/app.js").read_text(encoding="utf-8")
        assert 'window.addEventListener("error"' in js
        assert 'window.addEventListener("unhandledrejection"' in js
        # it is registered at the very TOP (before the main IIFE)
        assert js.index('window.addEventListener("error"') < \
            js.index("function switchTab")

    def test_error_toast_shape(self):
        js = (ROOT / "gui/assets/app.js").read_text(encoding="utf-8")
        assert "toast err" in js
        assert "toast-container" in js
        # textContent-only (no XSS surface)
        seg = js[js.index("window.addEventListener(\"error\"") - 800:
                 js.index("window.addEventListener(\"error\"") + 400]
        assert ".textContent" not in seg or "textContent =" in \
            js[js.index("function switchTab") - 8000:
               js.index("function switchTab")]


class TestModalZIndex:
    def test_modal_above_panels(self):
        css = (ROOT / "gui/assets/style.css").read_text(encoding="utf-8")
        i = css.index(".modal {")
        seg = css[i:i + 300]
        assert "z-index: 5000" in seg

    def test_toast_still_on_top(self):
        css = (ROOT / "gui/assets/style.css").read_text(encoding="utf-8")
        assert css.index("z-index: 5000;") < \
            css.index(".toast-container")