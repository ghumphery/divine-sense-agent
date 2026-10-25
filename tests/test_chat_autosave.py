"""Tests for v0.8.7+ chat auto-save safety net.

Covers:
  * Python closing hook reads chat_session from
    ``window.evaluate_js`` → ``window.pywebview.state`` and
    persists via the SessionManager.
  * Edge cases: missing state, missing chat_session key,
    null id, evaluate_js raising, garbage JSON, pre-existing
    session overwrites — all surface in real use cases.
  * JS source-level sanity checks that the safety-net
    functions + their ``bindEvents`` wiring are present.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture()
def tmp_home(tmp_path, monkeypatch):
    monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path))
    yield tmp_path


def _make_window(state=None, evaluate_js_side_effect=None):
    """Build a minimal mock of pywebview.window with just the
    surface our closing hook needs."""
    win = MagicMock()
    if evaluate_js_side_effect is not None:
        win.evaluate_js.side_effect = evaluate_js_side_effect
    elif state is not None:
        win.evaluate_js.return_value = json.dumps({"chat_session": state})
    else:
        win.evaluate_js.return_value = None
    return win


# ─── Python closing hook (mocked window) ────────────────────────


def test_close_hook_is_noop_v0871(tmp_home):
    """v0.8.7.1: the Python-side close hook must NOT call
    ``evaluate_js`` on the teardown path - that round-trips
    through the WebView JS thread which is being torn down
    and DEADLOCKS the GUI process (user log 2026-08-31
    "white screen freeze on close"). Verify the hook
    returns without touching ``evaluate_js`` and without
    doing any disk I/O.
    """
    from gui.app import _save_chat_draft_on_close
    win = MagicMock()
    _save_chat_draft_on_close(win)
    # The whole point: evaluate_js must NOT be called.
    win.evaluate_js.assert_not_called()


def test_close_hook_silent_when_no_state(tmp_home):
    """evaluate_js returns None (jsdom test, no pywebview) —
    the hook must NOT crash and must NOT create a bogus file."""
    from gui.app import _save_chat_draft_on_close

    _save_chat_draft_on_close(_make_window(state=None))

    files = list(tmp_home.glob("sessions/*.json")) + list(tmp_home.glob("**/sessions/*.json"))
    files = [f for f in files if f.is_file()]
    assert files == []


def test_close_hook_silent_when_no_chat_session_key(tmp_home):
    """State may have other keys (boot_time etc.) without
    chat_session — silently skip."""
    from gui.app import _save_chat_draft_on_close

    win = MagicMock()
    win.evaluate_js.return_value = json.dumps({"boot_time": "2026"})
    _save_chat_draft_on_close(win)

    files = list(tmp_home.glob("sessions/*.json")) + list(tmp_home.glob("**/sessions/*.json"))
    files = [f for f in files if f.is_file()]
    assert files == []


def test_close_hook_silent_when_chat_id_is_null(tmp_home):
    """Fresh window — state has chat_session with id=null. Hook
    must not error and must not create a session with a null id."""
    from gui.app import _save_chat_draft_on_close

    chat = {"id": None, "title": "", "messages": []}
    _save_chat_draft_on_close(_make_window(state=chat))

    files = list(tmp_home.glob("sessions/*.json")) + list(tmp_home.glob("**/sessions/*.json"))
    files = [f for f in files if f.is_file()]
    assert files == []


def test_close_hook_swallows_evaluate_js_exception(tmp_home):
    """Hook must never block the OS close dialog on a JS
    round-trip failure — caught and logged, not propagated."""
    from gui.app import _save_chat_draft_on_close

    win = MagicMock()
    win.evaluate_js.side_effect = RuntimeError("WebView torn down")
    # Must NOT raise.
    _save_chat_draft_on_close(win)


def test_close_hook_does_not_touch_disk(tmp_home):
    """v0.8.7.1: hook returns synchronously, no SessionManager
    calls, no files written. Disk persistence is owned by the
    JS-side beforeunload + autoSaveChat path.
    """
    from gui.app import _save_chat_draft_on_close
    win = MagicMock()
    _save_chat_draft_on_close(win)
    # No sessions directory should appear.
    sessions = list(tmp_home.glob("**/sessions/*.json"))
    assert sessions == [], (
        f"close hook should not write to sessions/, got: {sessions}"
    )


def test_close_hook_handles_bad_json(tmp_home):
    """Garbage in the JS state string — log + skip, no crash."""
    from gui.app import _save_chat_draft_on_close

    win = MagicMock()
    win.evaluate_js.return_value = "{not json at all"
    _save_chat_draft_on_close(win)

    files = list(tmp_home.glob("sessions/*.json")) + list(tmp_home.glob("**/sessions/*.json"))
    files = [f for f in files if f.is_file()]
    assert files == []


# ─── JS source-level safety-net checks ──────────────────────────
#
# Full jsdom e2e (mocking api.call + pywebview.state) lives in
# the existing test_gui_splash_e2e.py harness. These static checks
# keep each layer of the safety net from disappearing in a
# refactor that the e2e harness doesn't notice.

def test_app_js_defines_required_safety_functions():
    """``gui/assets/app.js`` must define all three v0.8.7+
    safety-net functions. If any name disappears, fast-fail here
    pinpoints the regression before runtime."""
    src = (Path(__file__).resolve().parents[1]
           / "gui" / "assets" / "app.js").read_text(encoding="utf-8")
    for fn in (
        "autoSaveChat",
        "syncStateMirror",
        "_beforeUnloadSave",
    ):
        assert f"function {fn}" in src, (
            f"app.js is missing the v0.8.7+ safety-net function "
            f"{fn}() — closing-hook chain breaks."
        )


def test_app_js_wires_beforeunload_listener():
    """``bindEvents`` must register a beforeunload listener so a
    clean X-click close triggers the JS-side last-ditch save."""
    src = (Path(__file__).resolve().parents[1]
           / "gui" / "assets" / "app.js").read_text(encoding="utf-8")
    assert "addEventListener('beforeunload'" in src
    assert "_beforeUnloadSave" in src


def test_app_js_wires_visibilitychange_mirror():
    """``visibilitychange`` must mirror chat_session to Python so
    an alt-tab away mid-stream still lands a save on close."""
    src = (Path(__file__).resolve().parents[1]
           / "gui" / "assets" / "app.js").read_text(encoding="utf-8")
    assert "visibilitychange" in src
    assert "syncStateMirror" in src


def test_app_js_auto_save_after_assistant_reply():
    """sendChat must call autoSaveChat() after appending the
    assistant message — covers mid-reply close (network round-
    trip out for coffee, user clicks X)."""
    src = (Path(__file__).resolve().parents[1]
           / "gui" / "assets" / "app.js").read_text(encoding="utf-8")
    idx = src.find("async function sendChat")
    assert idx != -1
    end = src.find("\n  }", idx)
    body = src[idx:end]
    assert "autoSaveChat(" in body, (
        "sendChat must call autoSaveChat() so safety net covers "
        "mid-reply close"
    )


def test_app_js_saves_immediately_on_send():
    """v0.9.20.0+ 即時儲存: sendChat must call autoSaveChat() right
    after appending the USER message and BEFORE awaiting the turn
    call — so the user's question + session survive a crash / tab
    close during a long tool-loop turn (desktop automation / local
    LLM). v0.9.123.0: the awaited call is the two-phase
    ``start_turn`` (live execution log) — same contract."""
    src = (Path(__file__).resolve().parents[1]
           / "gui" / "assets" / "app.js").read_text(encoding="utf-8")
    # v0.9.127.1 — the body lives in _sendChatCore now (the sendChat
    # wrapper only adds the never-silent catch); the ORDER contract
    # (user-append < save < turn) still holds inside the core.
    idx = src.find("async function _sendChatCore()")
    if idx == -1:
        idx = src.find("async function sendChat")
    assert idx != -1
    end = src.find("\n  }", idx)
    body = src[idx:end]
    sent_at = body.find("appendChatMsg('user', text);")
    saved_at = body.find("autoSaveChat();")
    turn_at = body.find("'start_turn'")
    assert sent_at != -1 and saved_at != -1 and turn_at != -1
    assert sent_at < saved_at < turn_at, (
        "autoSaveChat() must run after the user bubble is appended "
        "and BEFORE the turn call await (send-time instant save)"
    )


def test_app_js_saves_on_error_paths():
    """Both error exits in sendChat (no response / r.error) must
    also call autoSaveChat() — any bubble rendered on screen has
    to land in the session, or a refresh loses history."""
    src = (Path(__file__).resolve().parents[1]
           / "gui" / "assets" / "app.js").read_text(encoding="utf-8")
    # v0.9.127.1 — error exits live in _sendChatCore (the sendChat
    # wrapper only adds the never-silent catch).
    idx = src.find("async function _sendChatCore()")
    if idx == -1:
        idx = src.find("async function sendChat")
    end = src.find("\n  }", idx)
    body = src[idx:end]
    err1 = body.find("appendChatMsg('error', t('errGeneric'")
    err2 = body.find("appendChatMsg('error', r.error)")
    assert err1 != -1 and err2 != -1
    tail1 = body[err1:body.find("appendChatMsg('assistant'")]
    assert "autoSaveChat()" in tail1, (
        "the no-response error branch must call autoSaveChat()"
    )
    tail2 = body[err2:body.find("appendChatMsg('assistant'")]
    assert "autoSaveChat()" in tail2, (
        "the r.error branch must call autoSaveChat()"
    )


def test_app_js_sendbeacon_on_web_unload():
    """``_beforeUnloadSave`` must use ``navigator.sendBeacon`` in web
    mode — plain fetch can be CANCELLED by the browser at unload
    time (both desktop and mobile), losing the last-ditch save.
    The beacon body must match the POST /api/<method> args shape
    ([sid, title, messages]) — the handler json.loads(raw) without
    checking Content-Type."""
    src = (Path(__file__).resolve().parents[1]
           / "gui" / "assets" / "app.js").read_text(encoding="utf-8")
    idx = src.find("function _beforeUnloadSave")
    assert idx != -1
    end = src.find("\n  }", idx)
    body = src[idx:end]
    assert "__WEB_MODE__" in body
    assert "navigator.sendBeacon" in body
    assert "'/api/save_session'" in body
    assert "JSON.stringify([" in body
    assert "chatSession.id, chatSession.title || '', chatSession.messages" in body


def test_app_js_state_mirror_on_session_mutations():
    """``newSession`` / ``loadSessionById`` / ``saveSession`` /
    ``clearChat`` must all syncStateMirror() so the Python
    closing hook sees the latest id + title + messages."""
    src = (Path(__file__).resolve().parents[1]
           / "gui" / "assets" / "app.js").read_text(encoding="utf-8")
    for fn in (
        "async function newSession",
        "async function loadSessionById",
        "async function saveSession",
        "function clearChat",
    ):
        i = src.find(fn)
        assert i != -1, f"missing {fn}"
        end = src.find("\n  }", i)
        body = src[i:end]
        assert "syncStateMirror(" in body, (
            f"{fn} must call syncStateMirror() so the Python "
            f"closing hook sees the updated chat_session"
        )


def test_app_py_registers_closing_and_closed_handlers():
    """``gui/app.py`` must wire both ``window.events.closing`` and
    ``window.events.closed`` to the v0.8.7+ handlers."""
    src = (Path(__file__).resolve().parents[1]
           / "gui" / "app.py").read_text(encoding="utf-8")
    assert "window.events.closing" in src
    assert "window.events.closed" in src
    assert "_save_chat_draft_on_close" in src
    assert "_on_window_closed" in src


def test_app_py_drops_confirm_close_arg():
    """``confirm_close=True`` was intentionally removed in v0.8.7+
    so the closing hook can run BEFORE the OS dialog (the dialog
    is otherwise a hard barrier to a sync save)."""
    src = (Path(__file__).resolve().parents[1]
           / "gui" / "app.py").read_text(encoding="utf-8")
    assert "confirm_close=True" not in src, (
        "confirm_close=True was removed in v0.8.7+ so the closing "
        "hook can run *before* the OS dialog. If you re-add it, "
        "the JS-side beforeunload save is the only path that "
        "fires — the Python hook will be too late."
    )



# ─── v0.8.7.1: localStorage backup + boot recovery ─────────────


def test_app_js_uses_localstorage_for_draft_backup():
    """v0.8.7.1: the JS side must persist the latest chat draft
    into ``localStorage`` BEFORE the API round-trip. If the
    round-trip + beforeunload both fail, the next boot can
    recover from this backup (the Python closing hook is
    no-op in v0.8.7.1)."""
    src = (Path(__file__).resolve().parents[1]
           / "gui" / "assets" / "app.js").read_text(encoding="utf-8")
    assert "localStorage.setItem('chat_draft_backup'" in src, (
        "autoSaveChat() must write to localStorage BEFORE the "
        "save_session round-trip - if it doesn't, the close "
        "path has no JS-side backup."
    )


def test_app_js_clears_localstorage_after_success():
    """When the round-trip succeeds, the localStorage backup
    must be cleared so it isn't confused with an unsaved
    draft on the next boot."""
    src = (Path(__file__).resolve().parents[1]
           / "gui" / "assets" / "app.js").read_text(encoding="utf-8")
    assert "localStorage.removeItem('chat_draft_backup')" in src


def test_app_js_boots_draft_recovery_function():
    """v0.8.7.1: ``bootDraftRecovery()`` must exist and be
    called from ``bindEvents`` so a draft that survived a
    crash / force-kill is restored on the next launch."""
    src = (Path(__file__).resolve().parents[1]
           / "gui" / "assets" / "app.js").read_text(encoding="utf-8")
    assert "function bootDraftRecovery" in src
    assert "bootDraftRecovery()" in src


def test_app_py_close_hook_does_not_use_evaluate_js():
    """v0.8.7.1: the Python closing hook is a no-op stub. If
    someone re-introduces ``window.evaluate_js(...)`` on the
    close path they re-introduce the deadlock (user log
    2026-08-31)."""
    src = (Path(__file__).resolve().parents[1]
           / "gui" / "app.py").read_text(encoding="utf-8")
    # The function body (after the docstring) must not CALL
    # ``evaluate_js``. Docstring mentions are fine - they
    # explain WHY we don\'t.
    import re
    func_start = src.find("def _save_chat_draft_on_close")
    # Skip to the docstring closing triple-quote.
    quote_start = src.find('"""', func_start)
    quote_end = src.find('"""', quote_start + 3)
    body_start = quote_end + 3
    # Find the next top-level def (or class) to bound the function.
    next_def = re.search(r"\n(?:def|class) ", src[body_start:])
    body_end = body_start + (next_def.start() if next_def else len(src) - body_start)
    body = src[body_start:body_end]
    assert "evaluate_js" not in body, (
        "_save_chat_draft_on_close body must NOT call window.evaluate_js - "
        "the WebView JS thread is being torn down on close; the call "
        "deadlocks. Use JS-side localStorage + beforeunload instead."
    )
