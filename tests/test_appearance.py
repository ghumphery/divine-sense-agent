"""v0.9.99.0 — Appearance dialog（背景圖 / 自選色調 / 字型）。

Covers:
* index.html — 🖌️ appearance button + #appearance-dialog with
  bg file/url/dim + accent color/swatches + font presets/custom/
  size/mono + reset (dialog lives OUTSIDE the header)
* app.js — APPEAR_KEY 'qdrant-rag.appearance', _applyAppearance
  (body.has-bg + backgroundImage + translucent --bg overrides,
  --primary/--accent accent, --user-font/--user-font-size/
  --user-mono), loadAppearance at boot + bindAppearance, canvas
  downscale + 2.5 MB guard, quota fail-over drops bg
* style.css — body font-family/size wrapped in --user-font /
  --user-font-size vars; .ap-row/.ap-swatch styles; user mono rule
* i18n keys ×4 locales
"""
import json
import os
import re
import shutil
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(rel):
    with open(os.path.join(ROOT, *rel.split("/")), encoding="utf-8") as f:
        return f.read()


class TestAppearanceGuards(unittest.TestCase):
    def test_index_dialog_and_button(self):
        html = _read("gui/assets/index.html")
        self.assertIn('id="appearance-btn"', html)
        self.assertIn('id="appearance-dialog"', html)
        for fid in ("ap-bg-file", "ap-bg-url", "ap-bg-url-apply",
                    "ap-bg-dim", "ap-bg-clear", "ap-accent",
                    "ap-accent-reset", "ap-font", "ap-font-custom",
                    "ap-font-size", "ap-mono", "ap-reset",
                    "appearance-close"):
            self.assertIn(f'id="{fid}"', html, fid)
        # dialog must live OUTSIDE the header (fixed overlay)
        self.assertLess(html.find("</header>"),
                        html.find('id="appearance-dialog"'))
        self.assertEqual(html.count('id="theme-select"'), 1)

    def test_app_js_logic(self):
        js = _read("gui/assets/app.js")
        for fn in ("function _applyAppearance", "function loadAppearance",
                   "function openAppearance", "function bindAppearance",
                   "function _bindAppearanceBg",
                   "function _bindAppearanceAccent",
                   "function _bindAppearanceFont",
                   "function _appearanceFromInputs",
                   "function _saveAppearance"):
            self.assertIn(fn, js)
        self.assertIn("'qdrant-rag.appearance'", js)
        self.assertIn("APPEAR_BG_MAX_CHARS", js)
        self.assertIn("toDataURL('image/jpeg', 0.85)", js)  # canvas re-encode
        self.assertIn("loadAppearance();", js)  # wired in boot
        self.assertIn("bindAppearance();", js)  # wired in boot
        self.assertNotIn("innerHTML = st.bg", js)

    def test_css_hooks(self):
        css = _read("gui/assets/style.css")
        self.assertIn("font-family: var(--user-font,", css)
        self.assertIn("var(--user-font-size, 14px)", css)
        self.assertIn(".appearance-dialog", css)
        self.assertIn(".ap-swatch", css)
        self.assertIn("var(--user-mono,", css)

    def test_i18n_keys_in_all_four_locales(self):
        js = _read("gui/assets/app.js")
        keys = ("appearanceBtn", "appearanceTitle", "appearanceBg",
                "appearanceBgDim", "appearanceBgClear", "appearanceAccent",
                "appearanceFont", "appearanceFontCustom",
                "appearanceFontSize", "appearanceMono", "appearanceReset",
                "appearanceSaved")
        for key in keys:
            self.assertEqual(js.count(key + ":"), 4, key)


class TestAppearanceJsdomBehaviour(unittest.TestCase):
    """Node+jsdom behaviour test (repo convention)."""

    @classmethod
    def setUpClass(cls):
        cls.cache_nm = os.path.abspath(os.path.join(
            ROOT, ".cache", "jsdom", "node_modules"))
        cls.has_jsdom = os.path.isdir(os.path.join(cls.cache_nm, "jsdom"))
        cls.has_node = shutil.which("node") is not None

    def setUp(self):
        if not (self.has_jsdom and self.has_node):
            self.skipTest("node / .cache/jsdom not available")

    def test_appearance_scenarios_in_jsdom(self):
        driver = os.path.join(ROOT, "_appearance_jsdom_driver.js")
        js_path = os.path.join(self.cache_nm, "jsdom").replace("\\", "/")
        with open(driver, "w", encoding="utf-8") as f:
            f.write(JS_DRIVER.replace("__JSDOM_PATH__", js_path))
        r = subprocess.run(
            ["node", driver], capture_output=True, text=True,
            encoding="utf-8", errors="replace", cwd=ROOT, timeout=120)
        try:
            os.remove(driver)
        except OSError:
            pass
        payload = None
        for line in r.stdout.splitlines():
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                payload = json.loads(line)
                break
        self.assertIsNotNone(
            payload, f"jsdom driver produced no JSON: {r.stdout} {r.stderr}")
        self.assertEqual(payload["accent"]["--primary"], "#059669")
        self.assertEqual(payload["accent"]["--accent"], "#059669")
        self.assertEqual(payload["bg"]["class"], "has-bg")
        self.assertIn("url", payload["bg"]["backgroundImage"])
        self.assertEqual(payload["cleared"]["class"], "")
        self.assertEqual(payload["font"]["--user-font"],
                         '"Microsoft JhengHei", sans-serif')
        self.assertEqual(payload["fontsize"]["--user-font-size"], "17px")
        self.assertEqual(payload["mono"]["--user-mono"],
                         "Cascadia Code, monospace")
        self.assertEqual(payload["persist"], "#059669")
        self.assertEqual(payload["restore"]["--primary"], "#059669")


JS_DRIVER = r"""
const { JSDOM } = require('__JSDOM_PATH__');
const dom = new JSDOM(
  '<html><body>'
  + '<button id="appearance-btn"></button>'
  + '<div class="modal" id="appearance-dialog" hidden></div>'
  + '<input id="ap-accent" type="color" value="#2563eb">'
  + '<select id="ap-font"><option value=""></option></select>'
  + '<input id="ap-font-custom">'
  + '<select id="ap-font-size"><option value=""></option></select>'
  + '<input id="ap-mono">'
  + '<input id="ap-bg-url"><button id="ap-bg-url-apply"></button>'
  + '<input id="ap-bg-dim" type="range">'
  + '<button id="ap-reset"></button>'
  + '</body></html>',
  { url: 'http://localhost/' });
global.window = dom.window;
global.document = dom.window.document;
global.localStorage = dom.window.localStorage;
global.getComputedStyle = dom.window.getComputedStyle.bind(dom.window);
global.toast = function () {};
global.t = function (k) { return k; };

// --- REAL function bodies (keep in sync with app.js) ---
const APPEAR_KEY = 'qdrant-rag.appearance';
function _appearanceState() {
  try {
    const raw = localStorage.getItem(APPEAR_KEY);
    if (raw) return JSON.parse(raw);
  } catch (e) {}
  return {};
}
function _saveAppearance(st) {
  try { localStorage.setItem(APPEAR_KEY, JSON.stringify(st)); }
  catch (e) {}
}
function _hexToRgba(hex, alpha) {
  let h = String(hex || '').trim().replace('#', '');
  if (h.length === 3) h = h.split('').map(c => c + c).join('');
  if (!/^[0-9a-fA-F]{6}$/.test(h)) return null;
  const r = parseInt(h.slice(0, 2), 16);
  const g = parseInt(h.slice(2, 4), 16);
  const b = parseInt(h.slice(4, 6), 16);
  return 'rgba(' + r + ',' + g + ',' + b + ',' + alpha + ')';
}
function _applyAppearance(st) {
  const root = document.documentElement;
  if (st.bg) {
    const dim = Math.min(0.8, Math.max(0, Number(st.bgDim) || 0));
    document.body.classList.add('has-bg');
    document.body.style.backgroundImage =
      'linear-gradient(rgba(0,0,0,' + dim + '), rgba(0,0,0,' + dim +
      ')), url("' + st.bg.replace(/"/g, '%22') + '")';
    const cs = getComputedStyle(document.documentElement);
    const bg = _hexToRgba(cs.getPropertyValue('--bg'), 0.86);
    if (bg) root.style.setProperty('--bg', bg);
  } else {
    document.body.classList.remove('has-bg');
    document.body.style.backgroundImage = '';
    root.style.removeProperty('--bg');
    root.style.removeProperty('--bg-2');
    root.style.removeProperty('--bg-3');
  }
  if (st.accent) {
    root.style.setProperty('--primary', st.accent);
    root.style.setProperty('--accent', st.accent);
  } else {
    root.style.removeProperty('--primary');
    root.style.removeProperty('--accent');
  }
  if (st.font) root.style.setProperty('--user-font', st.font);
  else root.style.removeProperty('--user-font');
  if (st.fontSize) root.style.setProperty('--user-font-size', st.fontSize);
  else root.style.removeProperty('--user-font-size');
  if (st.mono) root.style.setProperty('--user-mono', st.mono);
  else root.style.removeProperty('--user-mono');
}
function loadAppearance() { _applyAppearance(_appearanceState()); }

// --- scenarios ---
const out = {};
_applyAppearance({ accent: '#059669' });
_saveAppearance({ accent: '#059669' });
out.accent = { '--primary':
  document.documentElement.style.getPropertyValue('--primary'),
  '--accent':
  document.documentElement.style.getPropertyValue('--accent') };
_applyAppearance({ bg: 'https://example.com/bg.jpg', bgDim: 30 });
out.bg = {
  class: document.body.className,
  backgroundImage: document.body.style.backgroundImage.slice(0, 120),
  '--bg': document.documentElement.style.getPropertyValue('--bg'),
};
_applyAppearance({});
out.cleared = { class: document.body.className,
  bgimg: document.body.style.backgroundImage };
_applyAppearance({ font: '"Microsoft JhengHei", sans-serif' });
out.font = { '--user-font':
  document.documentElement.style.getPropertyValue('--user-font') };
_applyAppearance({ fontSize: '17px' });
out.fontsize = { '--user-font-size':
  document.documentElement.style.getPropertyValue('--user-font-size') };
_applyAppearance({ mono: 'Cascadia Code, monospace' });
out.mono = { '--user-mono':
  document.documentElement.style.getPropertyValue('--user-mono') };
const saved = localStorage.getItem(APPEAR_KEY);
out.persist = JSON.parse(saved).accent;
_applyAppearance(JSON.parse(saved));
out.restore = { '--primary':
  document.documentElement.style.getPropertyValue('--primary') };
console.log(JSON.stringify(out));
setTimeout(() => process.exit(0), 50);
"""

# ── v0.9.121.0 — 遠端電腦「背景圖修改無效」修復 ────────────────────
class TestRemoteBgFix(unittest.TestCase):
    """User report: on a REMOTE computer the web-ui background did not
    apply at all. Fixes: (1) the quota fallback dropped a NON-EXISTENT
    ``bgDataUrl`` key — the retry re-saved the same oversized payload
    and the whole change silently vanished (now drops ``bg``); (2) the
    bg URL field normalizes local/sandbox paths into the server-
    streamed /download?path= endpoint (a remote browser cannot load a
    server-local file path); (3) bind/apply failures SURFACE (toast +
    console) instead of killing the appearance UI silently. The
    _bgUrlFor behaviour test extracts the REAL function from app.js
    and drives it via node (repo convention)."""

    def test_quota_fallback_drops_real_key(self):
        js = _read("gui/assets/app.js")
        self.assertNotIn("bgDataUrl", js)
        self.assertIn("Object.assign({}, st, { bg: '' })", js)
        self.assertIn("toast('⚠️ ' + t('appearanceBgDropped'))", js)
        self.assertIn("t('appearanceSaveFailed')", js)

    def test_url_normalizer_source(self):
        js = _read("gui/assets/app.js")
        self.assertIn("function _bgUrlFor", js)
        self.assertIn("'/download?path='", js)
        self.assertIn("t('appearanceBgUrlBad')", js)
        self.assertIn("const url = _bgUrlFor(raw);", js)

    def test_apply_and_bind_errors_surface(self):
        js = _read("gui/assets/app.js")
        self.assertIn("function _applyAppearanceInner", js)
        self.assertIn("[appearance] apply failed:", js)
        self.assertIn("[appearance] bind failed: #", js)
        self.assertIn("t('appearanceBindFail')", js)

    def test_i18n_new_keys_in_all_four_locales(self):
        js = _read("gui/assets/app.js")
        for key in ("appearanceBgUrlBad:", "appearanceSaveFailed:",
                    "appearanceBindFail:"):
            self.assertEqual(js.count(key), 4, key)

    def test_bgurl_for_behaviour_in_node(self):
        js = _read("gui/assets/app.js")
        m = re.search(r"function _bgUrlFor\(.*?\n  \}", js, re.S)
        self.assertIsNotNone(m, "_bgUrlFor must be extractable")
        fn = m.group(0)
        body = "\n".join(fn.splitlines()[1:-1])
        js2 = [
            "const _bgUrlFor = function(v){",
            body,
            "};",
            "const cases = [",
            "  ['', ''],",
            "  ['https://example.com/x.jpg',",
            "   'https://example.com/x.jpg'],",
            "  ['data:image/png;base64,AAAA',",
            "   'data:image/png;base64,AAAA'],",
            "  ['C:/Users/humphery/uploads/a.jpg',",
            "   '/download?path=' + "
            "encodeURIComponent('C:/Users/humphery/uploads/a.jpg')],",
            "  ['uploads/a.jpg',",
            "   '/download?path=' + "
            "encodeURIComponent('uploads/a.jpg')],",
            "  ['[已上傳] C:/Users/humphery/uploads/b.jpg',",
            "   '/download?path=' + encodeURIComponent("
            "'C:/Users/humphery/uploads/b.jpg')],",
            "  ['/uploads/c.jpg',",
            "   '/download?path=' + "
            "encodeURIComponent('/uploads/c.jpg')],",
            "  ['random.png', null],",
            "];",
            "let bad = 0;",
            "for (const [inp, want] of cases) {",
            "  const got = _bgUrlFor(inp);",
            "  if (got !== want) { bad++;",
            "    console.error('FAIL', inp, '->', got); }",
            "}",
            "if (bad) { process.exit(1); }",
            "console.log('BGURL_OK');",
            "setTimeout(() => process.exit(0), 30);",
        ]
        p_ = os.path.join(ROOT, "_t_bgurl.js")
        with open(p_, "w", encoding="utf-8") as f_:
            f_.write("\n".join(js2))
        try:
            r_ = subprocess.run(
                ["node", p_], capture_output=True, cwd=ROOT, timeout=30,
            )
            out_ = (r_.stdout + r_.stderr).decode("utf-8", "replace")
            self.assertIn("BGURL_OK", out_, out_)
        finally:
            os.unlink(p_)


    def test_apply_overrides_merge_source(self):
        js = _read("gui/assets/app.js")
        # v0.9.121.1 — the ROOT-CAUSE fix: _applyFromUi takes an
        # override merge instead of re-reading localStorage (which
        # CLOBBERED the just-picked bg before apply/save)
        self.assertIn(
            "Object.assign(_appearanceState(), override || {})", js)
        self.assertIn("_applyFromUi({ bg: String(dUrl), bgIsUrl: false,",
                      js)
        self.assertIn("_applyFromUi({ bg: url, bgIsUrl: true,",
                      js)
        # the broken form is gone
        self.assertNotIn("st.bg = url; st.bgIsUrl = false; "
                         "st.bgIsData = true;\n            "
                         "_applyFromUi();", js)

    def test_small_file_direct_dataurl(self):
        js = _read("gui/assets/app.js")
        # ≤1.2MB skips the canvas round-trip entirely
        self.assertIn("file.size <= 1.2 * 1024 * 1024", js)
        self.assertIn("[appearance] canvas re-encode failed:", js)

    def test_file_pick_flow_real_functions(self):
        """jsdom drives the REAL app.js slice (registry consts →
        _bindAppearanceBg inclusive) with stubbed FileReader/Image:
        picking a small local file must END UP in localStorage AND the
        body style (it previously evaporated inside _applyFromUi)."""
        # skip-guard (same contract as tests/test_diagrams_window.py)
        import shutil
        if shutil.which("node") is None:
            self.skipTest("node not on PATH")
        _probe = os.path.join(ROOT, "_t_jsdom_probe.js")
        with open(_probe, "w", encoding="utf-8") as f__:
            f__.write("try { require('jsdom'); } catch (_) "
                      "{ process.exit(1); }")
        try:
            _ok = subprocess.run(
                ["node", _probe], capture_output=True, timeout=30,
                cwd=ROOT,
            ).returncode == 0
        finally:
            try:
                os.unlink(_probe)
            except OSError:
                pass
        if not _ok:
            self.skipTest("jsdom not installed (npm i jsdom)")
        js = _read("gui/assets/app.js")
        i1 = js.find("  const APPEAR_KEY")
        i2 = js.find("  function _bindAppearanceAccent")
        self.assertGreater(i1, 0)
        self.assertGreater(i2, i1)
        slice_txt = js[i1:i2]
        self.assertIn("_applyFromUi = function (override)", slice_txt)
        p_ = os.path.join(ROOT, "_t_bgpick.js")
        with open(p_, "w", encoding="utf-8") as f_:
            f_.write(
                "const { JSDOM } = require('jsdom');\n"
                "const dom = new JSDOM('<body></body>',"
                " { url: 'http://localhost:8334/' });\n"
                "global.window = dom.window;\n"
                "global.document = dom.window.document;\n"
                "global.localStorage = dom.window.localStorage;\n"
                "const toast = function () {};\n"
                "const t = function (k) { return k; };\n"
                "global.FileReader = function () {\n"
                "  this.readAsDataURL = function () {\n"
                "    this.result = 'data:image/png;base64,AAAA';\n"
                "    this.onload();\n"
                "  };\n"
                "};\n"
                "global.Image = function () {};\n"
                "// elements the slice touches\n"
                "const mk = function (id) {\n"
                "  const el = document.createElement('div');\n"
                "  el.id = id; el.value = '';\n"
                "  document.body.appendChild(el);\n"
                "  return el;\n"
                "};\n"
                "const fit = mk('ap-bg-fit'); fit.value = 'contain';\n"
                "const fileEl = mk('ap-bg-file');\n"
                "fileEl.files = [{ size: 100 }];\n"
                "mk('ap-bg-url'); mk('ap-bg-url-apply');\n"
                "mk('ap-bg-dim'); mk('ap-bg-clear');\n"
                + "const slice = " + json.dumps(slice_txt) + ";\n"
                "eval(slice);\n"
                "_bindAppearanceBg();\n"
                "fileEl.dispatchEvent(new dom.window.Event('change'));\n"
                "const out = {};\n"
                "const saved = JSON.parse("
                "localStorage.getItem('qdrant-rag.appearance'));\n"
                "out.savedBg = saved.bg || '';\n"
                "out.bodyImg = (document.body.style.backgroundImage"
                " || '').slice(0, 60);\n"
                "out.cls = document.body.className;\n"
                "out.fit = document.body.dataset.bgFit;\n"
                "console.log(JSON.stringify(out));\n"
                "setTimeout(() => process.exit(0), 30);\n")
        try:
            r_ = subprocess.run(
                ["node", p_], capture_output=True, cwd=ROOT, timeout=60,
            )
            out_ = (r_.stdout + r_.stderr).decode("utf-8", "replace")
            self.assertIn("AAAA", out_, out_)
            payload = json.loads(
                out_[out_.find("{"): out_.rfind("}") + 1])
            self.assertEqual(payload["savedBg"],
                             "data:image/png;base64,AAAA")
            # jsdom normalises the rgb() spacing — assert the shape
            self.assertTrue(payload["bodyImg"].startswith(
                "linear-gradient"), payload["bodyImg"])
            self.assertIn("url(", payload["bodyImg"])
            self.assertIn("has-bg", payload["cls"])
            self.assertEqual(payload["fit"], "contain")
        finally:
            os.unlink(p_)


if __name__ == "__main__":
    unittest.main()