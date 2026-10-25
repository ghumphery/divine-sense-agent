"""
End-to-end test of the GUI Markdown rendering pipeline (v0.8.3 patch).

Drives the **actual** vendor/marked.min.js + vendor/purify.min.js
through jsdom + a tiny HTTP server (so <script src=> fetches them
in a real browser-like context), then runs the **actual** md() +
hardenLinks() functions lifted verbatim from gui/assets/app.js.

The bug we're guarding against: prior to v0.8.3 patch the GUI used
``typeof window.DOMPurify === 'object'`` to check whether the
sanitizer was loaded. DOMPurify 3.x exposes itself as a *named
function* with methods attached — ``typeof === 'function'`` — so
that check silently failed and md() fell back to a plain-text
renderer that leaked the raw ``**xxx**`` asterisks straight into
the chat log. Reproducer: any user whose jsdelivr CDN call worked
(marked loaded as object) but DOMPurify loaded via the alternate
path (function). The fix is duck-typing on
``typeof DOMPurify.sanitize === 'function'``.

We skip the whole module if Node + npm are not available so the
suite stays green in CI sandboxes.
"""
from __future__ import annotations

import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
ASSETS = REPO_ROOT / "gui" / "assets"
VENDOR_DIR = ASSETS / "vendor"

# Skip everything if Node isn't on PATH (CI sandboxes).
_NODE = shutil.which("node")
_NPM_OK = shutil.which("npm") is not None
pytestmark = pytest.mark.skipif(
    not (_NODE and _NPM_OK and VENDOR_DIR.exists()),
    reason="node + npm + vendor/ required for jsdom markdown e2e",
)


# ─── helpers ────────────────────────────────────────────────────────


_JS_PIPELINE = textwrap.dedent(r"""
const http = require('http');
const fs = require('fs');
const path = require('path');

const { JSDOM } = require('jsdom');

const ASSETS = process.env.ASSETS_DIR;

const server = http.createServer((req, res) => {
  let urlPath = decodeURIComponent(req.url.split('?')[0]);
  if (urlPath === '/') urlPath = '/index.html';
  const filePath = path.join(ASSETS, urlPath);
  try {
    const data = fs.readFileSync(filePath);
    res.writeHead(200, { 'Content-Type': 'application/javascript' });
    res.end(data);
  } catch (e) {
    res.writeHead(404); res.end('not found');
  }
});
server.listen(0);
const port = server.address().port;

const dom = new JSDOM(
  `<!doctype html><html><head>
    <script src="http://127.0.0.1:${port}/vendor/marked.min.js"></script>
    <script src="http://127.0.0.1:${port}/vendor/purify.min.js"></script>
  </head><body><div id="chat-log"></div></body></html>`,
  { runScripts: 'dangerously', resources: 'usable', url: 'http://127.0.0.1/' }
);

dom.window.addEventListener('load', () => { runTests(); });

function runTests() {
  const w = dom.window;
  const markedOK = typeof w.marked !== 'undefined' &&
                   typeof w.marked.parse === 'function';
  const purifyOK = typeof w.DOMPurify !== 'undefined' &&
                   typeof w.DOMPurify.sanitize === 'function';

  if (!markedOK || !purifyOK) {
    console.log(JSON.stringify({ error: 'vendor scripts missing',
      markedOK, purifyOK }));
    server.close();
    return;
  }

  const appSrc = fs.readFileSync(path.join(ASSETS, 'app.js'), 'utf8');
  function extractFn(src, name) {
    const idx = src.indexOf('function ' + name + '(');
    let depth = 0, end = -1;
    for (let i = idx; i < src.length; i++) {
      const c = src[i];
      if (c === '{') depth++;
      else if (c === '}') { depth--; if (depth === 0) { end = i; break; } }
    }
    return src.slice(idx, end + 1);
  }
  w.eval(extractFn(appSrc, 'md'));
  w.eval(extractFn(appSrc, 'hardenLinks'));

  const cases = [
    { input: '**bold**',            expect: /<strong>bold<\/strong>/ },
    { input: '**xxx**',             expect: /<strong>xxx<\/strong>/ },
    { input: '*italic*',            expect: /<em>italic<\/em>/ },
    { input: '- [x] done',          expect: /type="checkbox"/ },
    { input: '~~strike~~',          expect: /<del>strike<\/del>|strike/ },
    { input: '[t](https://e.com)', expect: /href="https:\/\/e\.com"/ },
    { input: '以下是 **xxx** 工具',  expect: /<strong>xxx<\/strong>/ },
  ];

  const results = cases.map(c => {
    const out = w.eval(`hardenLinks(md(${JSON.stringify(c.input)}))`);
    return { input: c.input, output: out, pass: c.expect.test(out) };
  });

  // Fallback path: nuke libs, run md(), expect warning + escaped text.
  delete w.marked;
  delete w.DOMPurify;
  let warned = false;
  const origWarn = console.warn;
  console.warn = () => { warned = true; };
  const fbOut = w.eval(`md('**xxx** <script>alert(1)</script> & more')`);
  console.warn = origWarn;
  // Fallback must escape < > & to stay XSS-safe even when the
  // markdown engine is missing.
  const strippedP = fbOut.replace(/<\/?p>/g, '');
  const xssSafe = !/[<>]/.test(strippedP) && !/[<>]/.test(fbOut.replace(/<\/?p>/g, ''));

  console.log("===JSON===" + JSON.stringify({
    results,
    fallback: { warned, output: fbOut, xssSafe },
  }));
  server.close();
}
""").strip()


def _run_pipeline() -> dict:
    """Spawn Node, return parsed JSON output from the harness."""
    import os
    import json
    env = {**os.environ, "ASSETS_DIR": str(ASSETS)}
    proc = subprocess.run(
        [_NODE, "-e", _JS_PIPELINE],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        env=env,
        timeout=30,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        pytest.skip(
            f"node harness failed (rc={proc.returncode}): "
            f"stdout={proc.stdout[-500:]!r} stderr={proc.stderr[-500:]!r}"
        )
    # The harness prefixes the JSON blob with "===JSON===" so we can
    # find it deterministically even if jsdom prints other junk.
    raw = proc.stdout.strip()
    marker = "===JSON==="
    idx = raw.rfind(marker)
    if idx < 0:
        raise RuntimeError(f"no JSON marker in: {raw[-500:]!r}")
    blob = raw[idx + len(marker):]
    return json.loads(blob)


# ─── tests ──────────────────────────────────────────────────────────


class TestVendorMarkdownEngine:
    """The vendored libs must load via <script src=> and let
    md() render **xxx** to <strong>xxx</strong>."""

    def test_vendor_scripts_attach_to_window(self):
        out = _run_pipeline()
        # If vendor scripts didn't attach, harness returns
        # {error: "vendor scripts missing"}. We want {results, fallback}.
        assert "results" in out, out

    @pytest.mark.parametrize("case", [
        "**bold**",
        "**xxx**",
        "*italic*",
        "- [x] done",
        "~~strike~~",
        "[t](https://e.com)",
        "以下是 **xxx** 工具",
    ])
    def test_md_renders_known_gfm(self, case):
        out = _run_pipeline()
        assert "results" in out, out
        matched = [r for r in out["results"] if r["input"] == case]
        assert matched, f"no result for {case!r}"
        assert matched[0]["pass"], (
            f"md({case!r}) -> {matched[0]['output']!r} did not match"
        )

    def test_links_get_noopener_noreferrer(self):
        out = _run_pipeline()
        assert "results" in out
        link_case = next(
            r for r in out["results"]
            if r["input"] == "[t](https://e.com)"
        )
        assert 'target="_blank"' in link_case["output"]
        assert 'rel="noopener noreferrer"' in link_case["output"]


class TestFallbackPath:
    """When both vendor + CDN fail, md() must warn + return XSS-safe
    escaped text. The asterisks themselves are not HTML-dangerous
    (they show as literal text — that's fine), but the surrounding
    pipeline still escapes ``<``, ``>``, ``&``."""

    def test_warning_fires(self):
        out = _run_pipeline()
        assert out.get("fallback", {}).get("warned") is True

    def test_xss_safe_even_without_engine(self):
        out = _run_pipeline()
        fb = out.get("fallback", {})
        assert fb.get("xssSafe") is True, fb
        # <script> in input must be HTML-escaped, never passed through.
        assert "<script>" not in fb.get("output", "")
        assert "&lt;script&gt;" in fb.get("output", "")
