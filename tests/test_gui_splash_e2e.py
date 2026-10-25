"""
End-to-end test of the v0.8.7+ GUI splash overlay + readiness checks.

Drives the actual ``gui/assets/index.html`` + ``gui/assets/app.js``
through jsdom + a tiny HTTP server so ``<script src=>`` + the
``api.safeCall`` bridge are exercised in a real browser-like
context. We don't need a live ``Api`` instance — the JS bridge
mock is enough to validate:

  * the splash overlay renders with three checklist rows in
    ``data-state="pending"``
  * a successful ``get_readiness`` call flips all three rows to
    ``data-state="ok"`` and the overlay fades
  * a failed ``get_readiness`` call still dismisses the overlay
    (so the user is never stranded)
  * each row has a 5 s timeout — a probe that never resolves
    is force-failed
  * even a thrown api.safeCall promise still falls through to
    boot() (no WebView crash)

The whole module is skipped if Node + npm aren't on PATH.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
ASSETS = REPO_ROOT / "gui" / "assets"

_NODE = shutil.which("node")
_NPM_OK = shutil.which("npm") is not None
pytestmark = pytest.mark.skipif(
    not (_NODE and _NPM_OK),
    reason="node + npm required for jsdom splash e2e",
)


# ─── Helpers ──────────────────────────────────────────────────────────


_JSDEOM_DIR = REPO_ROOT / ".cache" / "jsdom"


def _ensure_jsdom() -> str:
    """Install jsdom locally (idempotent). Returns its node_modules
    path so the test runner can ``require()`` it."""
    _JSDEOM_DIR.mkdir(parents=True, exist_ok=True)
    if not (_JSDEOM_DIR / "package.json").exists():
        npm = "npm.cmd" if shutil.which("npm.cmd") else "npm"
        subprocess.run(
            [npm, "init", "-y"], cwd=_JSDEOM_DIR,
            check=True, capture_output=True,
        )
        subprocess.run(
            [npm, "install", "--no-audit", "--no-fund", "--silent", "jsdom"],
            cwd=_JSDEOM_DIR,
            check=True, capture_output=True,
        )
    return str(_JSDEOM_DIR / "node_modules")
   
  
# ─── JS harness ─── 


_JS_HARNESS = r"""
const fs = require('fs');
const path = require('path');

const { JSDOM } = require('jsdom');
const ASSETS = process.env.ASSETS_DIR;
const READINESS_FIXTURE = process.env.READINESS_FIXTURE || 'all_ok';

// Minimal HTML with ONLY the splash overlay + skip button. The
// real ``app.js`` boots via DOMContentLoaded; here we run it via
// ``window.eval`` after we mutate the IIFE so post-fade code
// doesn't touch any missing DOM nodes.
const dom = new JSDOM(
  `<!doctype html><html lang="zh-TW"><head></head><body>
    <div id="splash-overlay">
      <div class="splash-card">
        <h2 id="splash-title"></h2>
        <p id="splash-version"></p>
        <p id="splash-status"></p>
        <ul id="splash-checks">
          <li data-check="backend" data-state="pending">
            <span class="splash-check-icon">⏳</span>
            <span class="splash-check-label"></span>
            <span class="splash-check-detail"></span>
          </li>
          <li data-check="qdrant" data-state="pending">
            <span class="splash-check-icon">⏳</span>
            <span class="splash-check-label"></span>
            <span class="splash-check-detail"></span>
          </li>
          <li data-check="llm" data-state="pending">
            <span class="splash-check-icon">⏳</span>
            <span class="splash-check-label"></span>
            <span class="splash-check-detail"></span>
          </li>
          <li data-check="mcp" data-state="pending">
            <span class="splash-check-icon">⏳</span>
            <span class="splash-check-label"></span>
            <span class="splash-check-detail"></span>
          </li>
        </ul>
        <button id="splash-skip"></button>
      </div>
    </div>
  </body></html>`,
  { runScripts: 'dangerously', url: 'http://127.0.0.1/' }
);

// Stub pywebview bridge. app.js calls ``api.safeCall(name)`` which
// internally does ``this.call(name)`` — so the mock must expose
// ``call`` AND ``safeCall`` as instance methods on the same
// object. We use a class for clean ``this`` semantics.
// The mock must expose every method app.js might call as a
// named function (app.js's api.call() does
// ``window.pywebview.api[method](...)``). We respond to any
// method name and route based on READINESS_FIXTURE.
// v0.9.87.3 — module-level call counter (must survive across calls;
// the mcp_pending_then_ok fixture counts get_readiness invocations).
let readinessCalls = 0;
function _readinessFixtureResponse() {
  if (READINESS_FIXTURE === 'never_resolves') {
    // Hang briefly, manually invoke the per-row ``finish`` callbacks
    // after 5 s (mirrors the real per-row setTimeout ``fail``), then
    // resolve with the same {error:'probe_timeout'} shape the real
    // overall race timeout produces (v0.9.80.0: the race window is
    // now SPLASH_MCP_TIMEOUT_MS — too long for a test, so the
    // fixture resolves the stall itself).
    setTimeout(function () {
      const probe = dom.window.__splashProbe__;
      if (probe) {
        if (probe['qdrant']) probe['qdrant']('fail', 'timed out');
        if (probe['llm']) probe['llm']('fail', 'timed out');
        if (probe['mcp']) probe['mcp']('fail', 'timed out');
      }
    }, 5100);
    return new Promise(function (resolve) {
      setTimeout(function () {
        resolve({ error: 'probe_timeout' });
      }, 6500);
    });
  }
  if (READINESS_FIXTURE === 'all_ok') return Promise.resolve({
    checks: {
      backend: { ok: true, detail: 'py 3.11' },
      qdrant: { ok: true, detail: '100 pts / 768d' },
      llm: { ok: true, detail: 'ollama' },
      mcp: { ok: true, detail: '228 tools', done: true },
    },
    ready: true,
  });
  if (READINESS_FIXTURE === 'all_fail') return Promise.resolve({
    checks: {
      backend: { ok: true, detail: 'self' },
      qdrant: { ok: false, detail: 'qdrant down' },
      llm: { ok: false, detail: 'no key' },
      mcp: { ok: false, detail: 'teams-assist connect failed',
             done: true },
    },
    ready: false,
  });
  if (READINESS_FIXTURE === 'throw') {
    return Promise.reject(new Error('bridge offline'));
  }
  // v0.9.87.3 — the user scenario: the FIRST response reports the mcp
  // slot as PENDING (done:false, fleet still building); the splash
  // must keep waiting and only fade once a later response reports
  // done:true. (The old duplicate-finish bug resolved the row as
  // 'fail' immediately and dismissed the splash mid-build.)
  if (READINESS_FIXTURE === 'mcp_pending_then_ok') {
    readinessCalls++;
    const mcpRow = readinessCalls >= 3
      ? { ok: true, detail: '228 tools', done: true }
      : { ok: false, detail: '初始化中…', done: false };
    return Promise.resolve({
      checks: {
        backend: { ok: true, detail: 'py 3.11' },
        qdrant: { ok: true, detail: '100 pts / 768d' },
        llm: { ok: true, detail: 'ollama' },
        mcp: mcpRow,
      },
      ready: mcpRow.ok,
    });
  }
  return Promise.resolve({});
}
// Build a proxy that returns _readinessFixtureResponse() for
// ``get_readiness`` and ``Promise.resolve({})`` for everything
// else, so the splash probe works and other boot() calls don't
// blow up.
const api = new Proxy({}, {
  get(_t, k) {
    if (k === 'get_readiness') return () => _readinessFixtureResponse();
    if (k === 'call' || k === 'safeCall') return () => Promise.resolve({});
    if (typeof k === 'symbol') return undefined;
    // Any other named method: return a function that resolves
    // to a benign empty object.
    return () => Promise.resolve({});
  },
});
dom.window.pywebview = { api };

// Load app.js source and blank out the post-fade boot path.
const appSrc = fs.readFileSync(path.join(ASSETS, 'app.js'), 'utf8');
function blankOut(src, fnName) {
  const re = new RegExp(
    'function\\\\s+' + fnName + '\\\\s*\\\\([^)]*\\\\)\\\\s*\\\\{', 'm');
  const m = src.match(re);
  if (!m) return src;
  let depth = 0, i = m.index + m[0].length - 1;
  for (; i < src.length; i++) {
    if (src[i] === '{') depth++;
    else if (src[i] === '}') { depth--; if (depth === 0) break; }
  }
  return src.slice(0, m.index + m[0].length) + '}' + src.slice(i + 1);
}
let mutated = blankOut(appSrc, 'boot');
mutated = blankOut(mutated, '_mdStartupCheck');
mutated = blankOut(mutated, 'setupTheme');
mutated = blankOut(mutated, 'refreshStatus');
mutated = blankOut(mutated, 'refreshSessionPicker');
// IIFE tail: redirect boot() dispatch to runReadinessChecks().
mutated = mutated.replace(
  /(document\\.addEventListener\\('DOMContentLoaded',\\s*)boot(\\);\\s*\\}\\s*else\\s*\\{\\s*)boot\\(\\);/,
  '$1runReadinessChecks$2runReadinessChecks();'
);

dom.window.eval(mutated);

let bootDone = false;
const t0 = Date.now();
function snapshot() {
  const doc = dom.window.document;
  const overlay = doc.getElementById('splash-overlay');
  const rows = Array.from(doc.querySelectorAll('#splash-checks li')).map(
    (li) => ({
      name: li.dataset.check,
      state: li.dataset.state,
      icon: li.querySelector('.splash-check-icon').textContent.trim(),
      detail: (li.querySelector('.splash-check-detail').textContent || '').trim(),
    })
  );
  return {
    bootDone,
    overlay: overlay ? {
      hidden: overlay.classList.contains('is-hidden'),
      fading: overlay.classList.contains('is-fading'),
      exists: true,
    } : { exists: false },
    rows,
    skipButton: !!doc.getElementById('splash-skip'),
    status: ((doc.getElementById('splash-status') || {}).textContent || '').trim(),
  };
}

function emit() {
  console.log('SNAPSHOT:' + JSON.stringify(snapshot()));
  setTimeout(() => process.exit(0), 50);
}

function tick() {
  const ov = dom.window.document.getElementById('splash-overlay');
  if (!ov) { bootDone = true; emit(); return; }
  if (ov.classList.contains('is-hidden')) {
    bootDone = true;
    emit();
    return;
  }
  // Capture as soon as we see ``is-fading``; we wait an extra
  // 500 ms before emitting so the CSS transition (~400 ms in
  // style.css) has set ``is-hidden`` for the snapshot.
  if (ov.classList.contains('is-fading')) {
    setTimeout(function () {
      bootDone = true;
      emit();
    }, 500);
    return;
  }
  if (Date.now() - t0 > 8000) { bootDone = true; emit(); return; }
  setTimeout(tick, 50);
}
tick();
"""


def _run_harness(fixture: str) -> dict:
    """Spawn Node, return parsed SNAPSHOT dict from the harness."""
    import json
    jsdom_dir = _ensure_jsdom()
    import os
    env = {**os.environ, "ASSETS_DIR": str(ASSETS),
           "READINESS_FIXTURE": fixture,
           # v0.9.80.0: require('jsdom') resolves via NODE_PATH — the
           # repo root has no node_modules and cwd is REPO_ROOT.
           "NODE_PATH": jsdom_dir}
    proc = subprocess.run(
        [_NODE, "-e", _JS_HARNESS],
        capture_output=True, text=True, timeout=60, env=env,
        cwd=str(REPO_ROOT),
    )
    snap = None
    for line in proc.stdout.splitlines():
        if line.startswith("SNAPSHOT:"):
            snap = json.loads(line[len("SNAPSHOT:"):])
    if snap is None:
        raise RuntimeError(
            f"harness emitted no SNAPSHOT.\n"
            f"stdout: {proc.stdout[-1500:]}\n"
            f"stderr: {proc.stderr[-1500:]}"
        )
    return snap


# ─── Tests ───────────────────────────────────────────────────────────


def test_splash_renders_three_pending_rows():
    """Pure DOM smoke — verify the splash markup is in place."""
    html = (ASSETS / "index.html").read_text(encoding="utf-8")
    assert 'id="splash-overlay"' in html
    assert 'id="splash-checks"' in html
    assert 'data-check="backend"' in html
    assert 'data-check="qdrant"' in html
    assert 'data-check="llm"' in html
    assert 'data-check="mcp"' in html  # v0.9.80.0: MCP init row
    assert 'id="splash-skip"' in html
    css = (ASSETS / "style.css").read_text(encoding="utf-8")
    assert ".splash-overlay" in css
    assert ".splash-card" in css
    assert ".splash-checks" in css
    js = (ASSETS / "app.js").read_text(encoding="utf-8")
    assert js.count("splashSkip:") >= 4
    assert js.count("readinessBackend:") >= 4


def test_splash_overlay_hides_after_all_ok_probe():
    snap = _run_harness("all_ok")
    assert snap["overlay"]["exists"], "splash overlay must render"
    assert snap["overlay"]["hidden"], (
        "overlay must fade + hide after all_ok probe"
    )
    assert snap["bootDone"] is True
    by_name = {r["name"]: r for r in snap["rows"]}
    assert by_name["backend"]["state"] == "ok"
    assert by_name["qdrant"]["state"] == "ok"
    assert by_name["llm"]["state"] == "ok"


def test_splash_overlay_hides_even_when_probe_fails():
    snap = _run_harness("all_fail")
    assert snap["overlay"]["hidden"]
    assert snap["bootDone"] is True
    by_name = {r["name"]: r for r in snap["rows"]}
    assert by_name["qdrant"]["state"] == "fail"
    assert by_name["llm"]["state"] == "fail"


def test_splash_per_row_timeout_unblocks_overlay():
    snap = _run_harness("never_resolves")
    assert snap["overlay"]["hidden"]
    assert snap["bootDone"] is True
    by_name = {r["name"]: r for r in snap["rows"]}
    assert by_name["backend"]["state"] == "ok"
    assert by_name["qdrant"]["state"] == "fail"
    assert by_name["llm"]["state"] == "fail"


def test_splash_probe_throw_still_falls_through_to_boot():
    snap = _run_harness("throw")
    assert snap["overlay"]["hidden"]
    assert snap["bootDone"] is True
    by_name = {r["name"]: r for r in snap["rows"]}
    assert by_name["backend"]["state"] == "ok"
    assert by_name["qdrant"]["state"] == "fail"
    assert by_name["llm"]["state"] == "fail"


def test_splash_waits_for_mcp_pending_then_ok():
    """v0.9.87.3 — the user scenario: the splash MUST NOT dismiss while
    the mcp slot is still pending (done:false). The first version had a
    duplicate immediate-finish block that resolved the mcp row as
    'fail' on the FIRST (pending) response, dismissing the splash
    before the fleet finished building. With the fix the splash holds
    until the poll observes done:true and the row flips to ok."""
    snap = _run_harness("mcp_pending_then_ok")
    assert snap["bootDone"] is True
    assert snap["overlay"]["hidden"]
    by_name = {r["name"]: r for r in snap["rows"]}
    assert by_name["mcp"]["state"] == "ok", (
        f"mcp row must be ok when the splash fades (got "
        f"{by_name['mcp']!r})"
    )
    assert by_name["mcp"]["detail"] == "228 tools"
