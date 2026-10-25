# -*- coding: utf-8 -*-
"""v0.9.123.0 — frontend source guards for the live turn execution log
(⚙ panel + 🛑 abort + history expander) and the two-phase Api surface."""
import sys
from pathlib import Path

sys.path.insert(0, ".")

ROOT = Path(__file__).resolve().parents[1]


def _read(name):
    return (ROOT / gui_rel(name)).read_text(encoding="utf-8")


def gui_rel(name):
    return Path("gui") / "assets" / name


def _appjs():
    return _read("app.js")


def test_api_surface_is_public_and_web_dispatchable():
    import gui.api as gam

    src = gam.__file__
    py = Path(src).read_text(encoding="utf-8")
    for tok in ("def start_turn(", "def poll_turn(", "def stop_turn(",
                "from src.agent import turn_events",
                "begin_turn_stream", "signal_turn_stop"):
        assert tok in py, tok


def test_sendchat_uses_two_phase_pipeline():
    js = _appjs()
    i = js.find("async function sendChat")
    # v0.9.132.1 — 6000 → 8000: the v0.9.127.x send-safety wrapper +
    # slash-command surface pushed the 'start_turn' literal past the
    # old 6000-char window (pre-existing drift).
    body = js[i:i + 8000]
    assert "'start_turn'" in body
    assert "pollTurnStream(" in body
    # the legacy one-shot chat_turn call is GONE from sendChat
    assert "'chat_turn'" not in body


def test_poller_renders_events_and_terminal_states():
    js = _appjs()
    i = js.find("async function pollTurnStream")
    assert i != -1
    body = js[i:js.find("\n  }\n", i)]
    for tok in ("chat-exec", "chat-exec-line", "chat-exec-status",
                "chat-exec-abort", "'poll_turn'", "'stop_turn'",
                "turnExecRun", "turnExecDone", "turnExecStopped",
                "turnExecError", "next_cursor"):
        assert tok in body, tok
    # textContent-only rendering (no innerHTML XSS surface)
    assert "innerHTML" not in body


def test_stop_events_and_gone_state():
    js = _appjs()
    i = js.find("async function pollTurnStream")
    body = js[i:i + 5000]
    assert "turnAbortSent" in body
    assert "turnGone" in body
    assert "stop_turn" in body


def test_history_replay_expander():
    js = _appjs()
    assert "m.turnLog" in js
    assert "chat-exec-view" in js
    assert "turnLogShow" in js
    # the replay box is textContent-built (no innerHTML XSS surface)
    i = js.find("chat-exec-static")
    assert i != -1
    seg = js[i:i + 1200]
    assert "ln.textContent" in seg and "innerHTML" not in seg


def test_abort_patience_unlock_guard():
    """v0.9.124.8 — the🛑 flow: the stop Event only bites at the next
    tool-loop boundary (93–330 s in-flight LLM rounds observed), which
    kept the send button locked for minutes = the reported「UI 就
    lock」. After 20 s past the abort request the poller must RELEASE
    the UI (turn gate handles queueing) while still polling for the
    true terminal state."""
    js = _appjs()
    # the patience constant + the three state-keeper names exist
    assert "_ABORT_PATIENCE_MS = 20000" in js
    assert "abortRequested = true" in js
    assert "abortSentAt = Date.now()" in js
    assert "abortUnlocked = true" in js
    # the unlock lives INSIDE the running-branch of the poller (the
    # true terminal state still renders the bubble + release below)
    i = js.find("async function pollTurnStream")
    # v0.9.132.1 — 6000 → 8000: the auto-background detach branch now
    # sits between the poller head and the v0.9.124.8 abort block.
    body = js[i:i + 8000]
    assert "_ABORT_PATIENCE_MS" in body
    assert "turnAbortPending" in body
    # the onRelease call inside the unlock branch
    assert "abortUnlocked = true;\n          statusEl.textContent = " \
        "t('turnAbortPending');\n          if (onRelease) onRelease();" \
        in body


def test_abort_pending_i18n_four_locales():
    js = _appjs()
    assert js.count("turnAbortPending:") == 4


def test_i18n_keys_in_all_four_locales():
    js = _appjs()
    for key in ("turnExecRun", "turnExecDone", "turnExecError",
                "turnExecStopped", "turnAbort", "turnAbortSent",
                "turnGone", "turnLogShow"):
        assert js.count(key + ":") >= 4, key


def test_css_present():
    css = (ROOT / gui_rel("style.css")).read_text(encoding="utf-8")
    for frag in (".chat-exec {", ".chat-exec-head {", ".chat-exec-abort {",
                 ".chat-exec-lines {", ".chat-exec-line {",
                 ".chat-exec-static {", ".chat-exec-view {"):
        assert frag in css, frag


def test_session_payload_carries_turnlog():
    js = _appjs()
    # the send path stores the events on the assistant message
    assert "msgs[msgs.length - 1].turnLog" in js
    # renderChat replays them (guarded above) — and the saved session
    # payload (save_session) is unchanged: messages ride as-is.
    assert "save_session" in js