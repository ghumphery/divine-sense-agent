"""Regression test for the v0.8.7 silent-swallow of the boot-race
"pywebview API not ready" rejection.

Root cause: pywebview injects ``window.pywebview.api`` AFTER the
page finishes loading, but app.js fires several ``api.safeCall()``
requests during boot (splash readiness probe, refreshStatus poll,
session picker refresh). The first call to ``call()`` rejects
with ``Error('pywebview API not ready')``; ``safeCall()`` then
catch'd and showed an error toast — visible in the top right of
every fresh launch.

Fix: ``safeCall`` now silently returns ``null`` for that exact
error message and skips the toast. Real errors still surface.

This test loads the real ``gui/assets/app.js`` IIFE into jsdom
WITHOUT a pywebview stub so ``api.call()`` rejects with the boot-
race message, then asserts ``safeCall()`` returns ``null`` and
no toast is created.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
ASSETS = REPO_ROOT / "gui" / "assets"

_NODE = shutil.which("node")
_NPM_OK = shutil.which("npm") is not None
pytestmark = pytest.mark.skipif(
    not (_NODE and _NPM_OK),
    reason="node + npm required for jsdom silent-api-not-ready e2e",
)


# JS harness: load app.js with a pywebview stub that returns the
# same shape as a real ``call()`` would during the boot race
# (pywebview global not present). We extract just the api object
# and run a synthetic call sequence to verify safeCall swallows
# the rejection silently.
_JS_HARNESS = r"""
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const ASSETS = process.env.ASSETS_DIR;

// Load app.js as text, blank out everything that touches the DOM
// beyond the api object itself, then eval just the IIFE prelude
// (the api = { call, safeCall } block) so we can probe safeCall
// in isolation.
const appSrc = fs.readFileSync(path.join(ASSETS, 'app.js'), 'utf8');

// Cut from the top of the IIFE to the closing brace of the api
// literal. The api = { call, safeCall } is at the top of the
// IIFE — let's grab the first balanced object literal.
function extractApiBlock(src) {
  const start = src.indexOf('const api = {');
  if (start < 0) throw new Error('api block not found');
  let depth = 0, i = src.indexOf('{', start);
  for (; i < src.length; i++) {
    if (src[i] === '{') depth++;
    else if (src[i] === '}') {
      depth--;
      if (depth === 0) return src.substring(start, i + 1);
    }
  }
  throw new Error('unbalanced api block');
}

// Build a minimal DOM with a toast-container so we can count
// toast divs afterwards.
const dom = new JSDOM(
  `<!doctype html><html><body>
    <div id="toast-container"></div>
  </body></html>`,
  { runScripts: 'dangerously', url: 'http://127.0.0.1/' }
);
const w = dom.window;

// Eval the api block — it expects window.pywebview / toast / t /
// api already in scope. We provide minimal stubs.
// Inline the api block, but rewrite ``const api = { ... };``
// to ``window.api = { ... };`` so we can reach it from outside
// the eval scope. We also replace ``this.call(...)`` references
// inside safeCall with ``window.api.call(...)`` so the binding
// stays correct after the rewrite.
let apiSrc = extractApiBlock(appSrc);
apiSrc = apiSrc.replace('const api = {', 'window.api = {');
// safeCall uses ``this.call(...)`` — after we drop the ``const``
// binding, ``this`` in safeCall (called as w.api.safeCall(...))
// still refers to w.api, so no rewrite needed there.
w.eval(`
  const toast = (msg, level, ms) => {
    const c = document.getElementById('toast-container');
    if (!c) { console.log('[toast]', msg); return; }
    const d = document.createElement('div');
    d.className = 'toast ' + (level || 'info');
    d.textContent = msg;
    c.appendChild(d);
  };
  const t = (key, vars) => {
    const dict = { errGeneric: 'Error: {msg}' };
    let s = dict[key];
    if (!s) s = key;
    if (vars) {
      for (const k in vars) {
        s = s.replace('{' + k + '}', vars[k]);
      }
    }
    return s;
  };
  ${apiSrc}
`);

// Capture results.
const result = {};

// Test 1: safeCall('get_readiness') — boot race case.
(async function () {
  const r = await w.api.safeCall('get_readiness');
  result.safeCallReadyReturnedNull = (r === null);
  result.toastCountAfterReady =
    dom.window.document.getElementById('toast-container').children.length;

  // Test 2: safeCall with a DIFFERENT error — must still toast.
  // We re-define call() to reject with something else.
  w.eval(`
    api.call = (method, ...args) => {
      return Promise.reject(new Error('synthetic boom'));
    };
  `);
  const r2 = await w.api.safeCall('get_status');
  result.safeCallOtherErrorReturnedNull = (r2 === null);
  result.toastCountAfterOther =
    dom.window.document.getElementById('toast-container').children.length;

  console.log('SILENT_REPORT:' + JSON.stringify(result));
  setTimeout(() => process.exit(0), 50);
})();
"""


def _run() -> dict:
    env = {**__import__("os").environ, "ASSETS_DIR": str(ASSETS)}
    proc = subprocess.run(
        [_NODE, "-e", _JS_HARNESS],
        capture_output=True, text=True, timeout=30, env=env,
        cwd=str(REPO_ROOT),
    )
    snap = None
    for line in proc.stdout.splitlines():
        if line.startswith("SILENT_REPORT:"):
            snap = json.loads(line[len("SILENT_REPORT:"):])
    if snap is None:
        raise RuntimeError(
            f"harness emitted no SILENT_REPORT.\n"
            f"stdout: {proc.stdout[-1000:]}\n"
            f"stderr: {proc.stderr[-1000:]}"
        )
    return snap


def test_safeCall_swallows_pywebview_not_ready():
    """Boot-race case: safeCall returns null, no toast emitted."""
    snap = _run()
    assert snap["safeCallReadyReturnedNull"] is True
    assert snap["toastCountAfterReady"] == 0, (
        "boot-race safeCall must NOT show a toast — that was the "
        "user-visible bug fixed in v0.8.7"
    )


def test_safeCall_still_toasts_on_real_errors():
    """Other errors must still produce a toast."""
    snap = _run()
    assert snap["safeCallOtherErrorReturnedNull"] is True
    assert snap["toastCountAfterOther"] == 1, (
        "real errors must still surface to the user via toast"
    )
