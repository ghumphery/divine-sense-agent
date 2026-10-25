"""Tests for v0.9.9.4+ — per-bubble start/end time headers above
every chat bubble (``.chat-msg-time`` inside a ``.chat-msg-row``
wrapper). Supersedes v0.9.9.3's whole-transcript strip per user
feedback.

Two layers, mirroring the repo's GUI test conventions:

* Source-level guards (pure Python): the strip is GONE from
  index.html and style.css, app.js defines the three helpers
  (``_fmtChatTime`` / ``_sameChatDay`` / ``_chatMsgTimeText``),
  renderChat() wraps every bubble in a ``.chat-msg-row`` and renders
  the header via textContent (no innerHTML), appendChatMsg carries
  ``startTs``, sendChat captures the request start and passes it to
  the assistant append, setLang() re-renders (the 開始/結束 labels
  follow the locale switch), and all four locales carry the labels.
* Behaviour (Node + jsdom): the REAL functions are extracted from
  app.js by balanced-brace slicing and executed — legacy (ts-less)
  messages get no header, user bubbles show ``HH:MM`` (today) or
  the full ``YYYY/MM/DD HH:MM`` (past days), assistant bubbles show
  開始 → 結束 windows including the cross-midnight date upgrade,
  invalid ts yields ``null``, and an invalid startTs falls back to
  the single end time.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
ASSETS = REPO_ROOT / "gui" / "assets"
NODE_MODULES = REPO_ROOT / "node_modules" / "jsdom"

_NODE = shutil.which("node")
_NPM_OK = shutil.which("npm") is not None
_JSDOM_OK = NODE_MODULES.is_dir()

pytestmark = pytest.mark.skipif(
    not (_NODE and _NPM_OK and _JSDOM_OK),
    reason="node + npm + jsdom required for chat-time-bar e2e",
)


def _read(path: str) -> str:
    return (ASSETS / path).read_text(encoding="utf-8")


def _extract_fn(src: str, name: str) -> str:
    """Balanced-brace slice of ``function <name>(...) { ... }``."""
    marker = f"function {name}("
    start = src.index(marker)
    i = src.index("{", start)
    depth = 0
    for j in range(i, len(src)):
        c = src[j]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return src[start : j + 1]
    raise AssertionError(f"unbalanced braces in {name}")


# ─── source-level guards ─────────────────────────────────────


def test_index_html_strip_removed():
    """v0.9.9.3's whole-transcript strip is gone from index.html."""
    html = _read("index.html")
    assert "chat-time-bar" not in html


def test_css_strip_removed_row_and_time_present():
    css = _read("style.css")
    assert "chat-time-bar" not in css
    assert ".chat-msg-row {" in css
    assert ".chat-msg-time {" in css
    # The bubble keeps its own 80% cap; inside the row it must be
    # relaxed to 100% so the two caps don't multiply down to 64%.
    assert ".chat-msg-row .chat-msg { max-width: 100%; }" in css


def test_app_js_defines_time_helpers():
    js = _read("app.js")
    for name in (
        "_fmtChatTime", "_sameChatDay", "_chatMsgTimeText",
        "_chatTokensText",
    ):
        assert f"function {name}(" in js, f"{name} missing"
    # The strip renderer must be fully removed, not just unused.
    assert "renderChatTimeBar" not in js


def test_render_chat_builds_header_above_bubble():
    """Every bubble is wrapped in a .chat-msg-row and the time
    header is rendered with textContent (no innerHTML). v0.9.12.0+
    joins the per-pair token usage into the same header line."""
    body = _extract_fn(_read("app.js"), "renderChat")
    assert "_chatMsgTimeText(m)" in body
    assert "_chatTokensText(m)" in body
    assert "'chat-msg-row '" in body
    assert "'chat-msg-time'" in body
    assert "metaParts.join(' · ')" in body


def test_append_chat_msg_carries_start_ts():
    body = _extract_fn(_read("app.js"), "appendChatMsg")
    assert "startTs" in body
    assert "msg.startTs = startTs" in body
    # v0.9.12.0+ — the pair's token usage rides along too.
    assert "msg.usage = usage" in body


def test_send_chat_captures_request_start():
    """The send moment is the 開始 of the assistant generation
    window — sendChat must capture it and pass it through."""
    body = _extract_fn(_read("app.js"), "sendChat")
    assert "const reqStart = new Date().toISOString()" in body
    # v0.9.123.0 — two-phase payload: usage/tools_used ride the
    # terminal result; reqStart stays the 開始 of the window.
    assert (
        "appendChatMsg('assistant', res.answer, res.sources, reqStart,"
        in body
    )


def test_setlang_rerenders_chat_for_time_labels():
    """Per-bubble headers carry t() labels — a language switch must
    re-run renderChat so the labels follow the locale."""
    js = _read("app.js")
    setlang_start = js.index("function setLang(")
    nxt = js.find("function ", setlang_start + 10)
    body = js[setlang_start:nxt]
    assert "renderChat()" in body


def test_app_js_all_four_locales_have_labels():
    js = _read("app.js")
    for key in ("chatStart", "chatEnd"):
        # 4 locale dicts (zh-TW / zh-CN / en / ja) + usages without
        # the colon — counting the colon form locks all four.
        assert js.count(f"{key}:") == 4, (
            f"{key} must exist in all 4 locale dicts, "
            f"found {js.count(key + ':')}"
        )
# ─── behaviour (Node + jsdom, real extracted functions) ──────

_DRIVER_TEMPLATE = """// Auto-generated by tests/test_chat_time_bar.py — do not edit.
const { JSDOM } = require('jsdom');
const dom = new JSDOM(
  '<!doctype html><html><body><div id="chat-time-bar" hidden></div></body></html>'
);
const document = dom.window.document;

// Stub for the IIFE-captured t() the real function closes over.
function t(key) { return ({ chatStart: 'START', chatEnd: 'END' })[key] || key; }

__FMT__

__SAME__

__TIME__

__TOK__

const results = {};

// s1 — legacy session (message without ts): no header at all.
results.s1 = _chatMsgTimeText({ role: 'user', content: 'hi' });

// s2 — user bubble sent today: HH:MM only (never a date part).
const nowIso = new Date().toISOString();
results.s2 = _chatMsgTimeText({ role: 'user', ts: nowIso });

// s3 — user bubble from a past day: full YYYY/MM/DD HH:MM.
results.s3 = _chatMsgTimeText(
  { role: 'user', ts: '2020-01-02T03:04:00' }
);

// s4 — assistant window, start and end on the same day.
results.s4 = _chatMsgTimeText({
  role: 'assistant', startTs: '2026-08-20T09:15:00',
  ts: '2026-08-20T09:16:00',
});

// s5 — assistant window crossing midnight: both sides upgrade
// to the full date form.
results.s5 = _chatMsgTimeText({
  role: 'assistant', startTs: '2026-08-20T23:50:00',
  ts: '2026-08-21T00:10:00',
});

// s6 — assistant reply without startTs (legacy saved sessions):
// single end time only.
results.s6 = _chatMsgTimeText({
  role: 'assistant', ts: '2020-01-02T03:04:00',
});

// s7 — invalid ts string: null (no header, never garbage).
results.s7 = _chatMsgTimeText({ role: 'user', ts: 'not-a-date' });

// s8 — invalid startTs: falls back to the single end time.
results.s8 = _chatMsgTimeText({
  role: 'assistant', startTs: 'garbage', ts: '2020-01-02T03:04:00',
});

// s9 — token usage line (v0.9.12.0+): formatted with separators.
results.s9 = _chatTokensText({
  role: 'assistant', ts: '2026-08-20T09:16:00',
  usage: { prompt_tokens: 4290, completion_tokens: 154,
           total_tokens: 4444, reasoning_tokens: 0,
           cached_tokens: 10880, calls: 1 },
});

// s10 — multi-call tool loop: call count appended.
results.s10 = _chatTokensText({
  role: 'assistant', ts: '2026-08-20T09:16:00',
  usage: { prompt_tokens: 36000, completion_tokens: 500,
           total_tokens: 36500, reasoning_tokens: 0, calls: 3 },
});

// s11 — no usage / all-zero counters → null (legacy sessions).
results.s11a = _chatTokensText({ role: 'assistant', ts: '2026-08-20T09:16:00' });
// s12 — usage without cached_tokens field → no cache segment.
results.s12 = _chatTokensText({
  role: 'assistant',
  usage: { prompt_tokens: 100, completion_tokens: 5,
           total_tokens: 105 },
});
results.s11b = _chatTokensText({
  role: 'assistant',
  usage: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 },
});

console.log(JSON.stringify(results));
// Node 24 + JSDOM 29 can crash with STATUS_STACK_OVERFLOW during
// teardown (repo convention, see tests/test_diagrams_window.py);
// exit cleanly before teardown begins.
setTimeout(function () { process.exit(0); }, 50);
"""


def _run_node_jsdom(js_body: str) -> str:
    """Write the driver to a tmp file under the project root and run
    it with node (cwd=repo root so require('jsdom') resolves). cmd.exe
    mangles ``node -e`` quoting, hence the file round-trip."""
    driver = REPO_ROOT / "_chat_time_bar_driver.js"
    driver.write_text(js_body, encoding="utf-8")
    try:
        proc = subprocess.run(
            ["node", str(driver)],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
    finally:
        driver.unlink(missing_ok=True)
    out = (proc.stdout or "").strip()
    # 3221226505 == 0xC00000FD (teardown stack overflow) is benign
    # when stdout already carries the JSON payload.
    if proc.returncode not in (0, 3221226505) and not out:
        raise AssertionError(
            f"node driver failed rc={proc.returncode}: "
            f"{(proc.stderr or '')[-800:]}"
        )
    if not out:
        raise AssertionError(
            f"node driver produced no stdout rc={proc.returncode}: "
            f"{(proc.stderr or '')[-800:]}"
        )
    return out


def test_jsdom_per_bubble_time_text():
    """Run the REAL _fmtChatTime / _sameChatDay / _chatMsgTimeText
    (extracted from app.js) across the 8 message scenarios."""
    js = _read("app.js")
    body = (
        _DRIVER_TEMPLATE
        .replace("__FMT__", _extract_fn(js, "_fmtChatTime"))
        .replace("__SAME__", _extract_fn(js, "_sameChatDay"))
        .replace("__TIME__", _extract_fn(js, "_chatMsgTimeText"))
        .replace("__TOK__", _extract_fn(js, "_chatTokensText"))
    )
    r = json.loads(_run_node_jsdom(body).splitlines()[-1])

    # s1 legacy ts-less → no header.
    assert r["s1"] is None
    # s2 today → bare HH:MM (timezone-independent assertion).
    assert re.fullmatch(r"\d{2}:\d{2}", r["s2"]), r["s2"]
    # s3 past day → full date form.
    assert r["s3"] == "2020/01/02 03:04"
    # s4 same-day assistant window → START/END labels, HH:MM each.
    m = re.fullmatch(r"START (\d{2}:\d{2}) → END (\d{2}:\d{2})", r["s4"])
    assert m, r["s4"]
    assert m.group(1) == "09:15"
    assert m.group(2) == "09:16"
    # s5 cross-midnight window → both sides carry the full date.
    assert r["s5"] == "START 2026/08/20 23:50 → END 2026/08/21 00:10"
    # s6 legacy assistant (no startTs) → single end time only.
    assert r["s6"] == "2020/01/02 03:04"
    assert "START" not in r["s6"]
    # s7 invalid ts → null (never render garbage).
    assert r["s7"] is None
    # s8 invalid startTs → graceful fallback to the end time.
    assert r["s8"] == "2020/01/02 03:04"
    # s9 token usage line → formatted with thousands separators.
    assert r["s9"] == "↑ 4,290 / ↓ 154 tok · ⚡ 10,880 cached"
    # s10 multi-call tool loop → call count appended.
    assert r["s10"].startswith("↑ 36,000 / ↓ 500 tok")
    assert "3 calls" in r["s10"]
    # cached_tokens absent/zero → no cache segment
    assert "⚡" not in r["s10"]
    # s11 no usage / zeros → null.
    assert r["s11a"] is None
    assert r["s11b"] is None
    # s12 no cached field → plain tokens only
    assert r["s12"] == "↑ 100 / ↓ 5 tok"
    assert "⚡" not in r["s12"]
