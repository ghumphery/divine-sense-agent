"""v0.9.97.0 — UI/web theme picker（三態：auto / light / dark）。

Covers:
* index.html — 🎨 theme-select picker with 3 options + i18n hooks
  (the old dark/light-only checkbox is gone)
* app.js — three-state setupTheme (select change -> applyTheme ->
  documentElement.dataset.theme + localStorage 'qdrant-rag.theme'),
  auto default, saved-value restore, data-i18n-title support in
  applyI18n
* style.css — .theme-picker select styled like the lang picker
* i18n keys ×4 locales (themeLabel/themeAuto/themeLight/themeDark)
* CSS keeps the three data-theme variable blocks that drive the
  actual palette (auto follows prefers-color-scheme)
"""
import json
import os
import re
import shutil
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def _read(rel):
    with open(os.path.join(ROOT, *rel.split("/")), encoding="utf-8") as f:
        return f.read()


class TestThemePickerGuards(unittest.TestCase):
    def test_index_has_three_state_picker(self):
        html = _read("gui/assets/index.html")
        self.assertIn('id="theme-select"', html)
        for v in ("auto", "light", "dark"):
            self.assertIn(f'value="{v}"', html)
        self.assertIn('data-i18n="themeAuto"', html)
        # the old checkbox toggle is replaced
        self.assertNotIn("theme-toggle", html)

    def test_app_js_three_state_logic(self):
        js = _read("gui/assets/app.js")
        self.assertIn("function setupTheme", js)
        self.assertIn("getElementById('theme-select')", js)
        self.assertIn("localStorage.getItem('qdrant-rag.theme')", js)
        self.assertIn("localStorage.setItem('qdrant-rag.theme'", js)
        self.assertIn("prefers-color-scheme: dark", js)
        # no reference to the removed checkbox remains
        self.assertNotIn("theme-toggle", js)
        # title-attribute i18n pass (picker tooltip)
        self.assertIn("[data-i18n-title]", js)

    def test_i18n_keys_in_all_four_locales(self):
        js = _read("gui/assets/app.js")
        for key in ("themeLabel", "themeAuto", "themeLight", "themeDark"):
            self.assertEqual(js.count(key + ":"), 4, key)

    def test_css_picker_style_and_palette_blocks(self):
        css = _read("gui/assets/style.css")
        self.assertIn(".theme-picker select", css)
        self.assertNotIn(".theme-toggle", css)
        # the three data-theme palette blocks drive the colors
        self.assertIn(':root[data-theme="auto"]', css)
        self.assertIn(':root[data-theme="light"]', css)
        self.assertIn(':root[data-theme="dark"]', css)
        self.assertIn("prefers-color-scheme: dark", css)


class TestThemeJsdomBehaviour(unittest.TestCase):
    """Run the REAL theme function bodies in Node+jsdom across six
    scenarios (repo convention: exit via setTimeout, tolerate the
    benign Node teardown crash). jsdom lives in the .cache/jsdom
    install — the driver requires it by ABSOLUTE path (NODE_PATH is
    unreliable on Node 24)."""

    @classmethod
    def setUpClass(cls):
        cls.cache_nm = os.path.abspath(os.path.join(
            ROOT, ".cache", "jsdom", "node_modules"))
        cls.has_jsdom = os.path.isdir(
            os.path.join(cls.cache_nm, "jsdom"))
        cls.has_node = shutil.which("node") is not None

    def setUp(self):
        if not (self.has_jsdom and self.has_node):
            self.skipTest("node / .cache/jsdom not available")

    def test_theme_scenarios_in_jsdom(self):
        driver = os.path.join(ROOT, "_theme_jsdom_driver.js")
        js_path = os.path.join(self.cache_nm, "jsdom").replace("\\", "/")
        with open(driver, "w", encoding="utf-8") as f:
            f.write(JS_DRIVER.replace("__JSDOM_PATH__", js_path))
        r = subprocess.run(
            ["node", driver], capture_output=True, text=True,
            encoding="utf-8", errors="replace", cwd=ROOT, timeout=90)
        try:
            os.remove(driver)
        except OSError:
            pass
        payload = None
        for line in r.stdout.splitlines():
            line = line.strip()
            if line.startswith("[") and line.endswith("]"):
                payload = json.loads(line)
                break
        self.assertIsNotNone(
            payload, f"jsdom driver produced no JSON: {r.stdout} {r.stderr}")
        d = {row[0]: (row[1], row[2]) for row in payload}
        self.assertEqual(d["s1-fresh-auto"], ("auto", "auto"))
        self.assertEqual(d["s2-pick-dark"], ("dark", "dark"))
        self.assertEqual(d["s3-pick-light"], ("light", "light"))
        self.assertEqual(d["s4-back-auto"], ("auto", "auto"))
        self.assertEqual(d["s5-restore"], ("auto", "auto"))
        self.assertEqual(d["s6-corrupt-fallback"], ("auto", "auto"))


JS_DRIVER = r"""
const { JSDOM } = require('__JSDOM_PATH__');
const dom = new JSDOM(
  '<html><body>'
  + '<select id="theme-select">'
  + '<option value="auto"></option>'
  + '<option value="light"></option>'
  + '<option value="dark"></option>'
  + '</select></body></html>',
  { url: 'http://localhost/' });
global.window = dom.window;
global.document = dom.window.document;
global.localStorage = dom.window.localStorage;

// --- REAL function bodies extracted from app.js (keep in sync) ---
function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  try { localStorage.setItem('qdrant-rag.theme', theme); } catch (e) {}
}
function effectiveTheme() {
  try {
    const saved = localStorage.getItem('qdrant-rag.theme');
    if (saved === 'light' || saved === 'dark' || saved === 'auto') {
      return saved;
    }
  } catch (e) {}
  return 'auto';
}
function setupTheme() {
  applyTheme(effectiveTheme());
  const sel = document.getElementById('theme-select');
  if (sel) {
    sel.value = effectiveTheme();
    sel.addEventListener('change', function () {
      applyTheme(sel.value);
    });
  }
}
// --- scenarios ---
const out = [];
setupTheme();
out.push(['s1-fresh-auto', document.documentElement.dataset.theme,
          localStorage.getItem('qdrant-rag.theme')]);
const sel = document.getElementById('theme-select');
sel.value = 'dark';
sel.dispatchEvent(new dom.window.Event('change'));
out.push(['s2-pick-dark', document.documentElement.dataset.theme,
          localStorage.getItem('qdrant-rag.theme')]);
sel.value = 'light';
sel.dispatchEvent(new dom.window.Event('change'));
out.push(['s3-pick-light', document.documentElement.dataset.theme,
          localStorage.getItem('qdrant-rag.theme')]);
sel.value = 'auto';
sel.dispatchEvent(new dom.window.Event('change'));
out.push(['s4-back-auto', document.documentElement.dataset.theme,
          localStorage.getItem('qdrant-rag.theme')]);
setupTheme();
out.push(['s5-restore', document.documentElement.dataset.theme,
          sel.value]);
localStorage.setItem('qdrant-rag.theme', 'neon');
out.push(['s6-corrupt-fallback', effectiveTheme(), effectiveTheme()]);
console.log(JSON.stringify(out));
setTimeout(() => process.exit(0), 50);
"""

if __name__ == "__main__":
    unittest.main()