# -*- coding: utf-8 -*-
"""v0.9.126.6 — web-app no-zoom guard.

User request: 「Web APP要禁止UI被放大縮小」. Three layers, source-guarded:

* index.html — viewport meta gains user-scalable=no + minimum/maximum-scale
  (mobile pinch + double-tap; iOS Safari ignores user-scalable=no, so…)
* app.js   — bindZoomGuard(): WEB-MODE-GATED (window.__WEB_MODE__ /
  isWebMode) — cancels iOS gesturestart/change/end pinch, desktop
  ctrl/cmd+wheel and ctrl/cmd+ '+'/'-'/'0' keyboard zoom;
  wired into bindEvents right after the upload/mic/paste/drop group
* style.css — html, body { touch-action: pan-x pan-y } (scrolling kept,
  pinch/double-tap zoom disallowed at the CSS layer)

The desktop pywebview GUI keeps stock zoom behaviour (web-mode gate +
desktop WebView ignores the viewport meta).
"""
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(rel: str) -> str:
    with open(os.path.join(ROOT, *rel.split("/")), encoding="utf-8") as f:
        return f.read()


class TestNoZoomGuardV0126_6(unittest.TestCase):
    def test_viewport_meta_locks_scaling(self):
        html = _read("gui/assets/index.html")
        self.assertIn("user-scalable=no", html)
        self.assertIn("maximum-scale=1.0", html)
        self.assertIn("minimum-scale=1.0", html)
        # the zoomable default meta must be gone
        self.assertNotIn(
            'content="width=device-width, initial-scale=1, '
            'viewport-fit=cover"', html)

    def test_app_js_zoom_guard_wired_web_mode_only(self):
        js = _read("gui/assets/app.js")
        self.assertIn("function bindZoomGuard", js)
        # web-mode gate FIRST (desktop pywebview unaffected)
        head = js[js.index("function bindZoomGuard"):
                  js.index("function bindZoomGuard") + 400]
        self.assertIn("isWebMode()", head)
        # pinch gestures + ctrl wheel + keyboard shortcuts all cancelled
        for tok in ("'gesturestart'", "'gesturechange'", "'gestureend'",
                    "e.ctrlKey || e.metaKey) e.preventDefault()",
                    "{ passive: false }"):
            self.assertIn(tok, js)
        self.assertIn("e.key === '+'", js)
        self.assertIn("e.key === '0'", js)
        # wired from bindEvents (next to the risky-late binder group)
        seg = js[js.index("function bindEvents()"):]
        self.assertIn("bindZoomGuard();", seg[:800])

    def test_css_touch_action_pan_only(self):
        css = _read("gui/assets/style.css")
        self.assertIn("html, body { touch-action: pan-x pan-y; }", css)


if __name__ == "__main__":
    unittest.main()
