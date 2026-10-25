"""v0.9.123.0 — in-process per-turn execution event stream.

The GUI/web two-phase chat (``Api.start_turn`` → ``Api.poll_turn``)
needs to show the LIVE execution log of a prompt turn: every LLM round,
tool call, tool result, wrap-up, bail-out and force-stop. The Agent
emits these through an ``emit_turn_event`` callback attached by the Api
(v0.9.123.0 ``Agent.begin_turn_stream``); this module is the store that
callback writes into and ``poll_turn`` reads from.

Design mirrors the repo's in-process LocalTool discipline:

* Thread-safe, NEVER raises across the whole surface — a streaming
  hiccup must never tear down a running turn.
* Ring semantics: at most ``_EVENTS_PER_TURN`` events per turn (the
  overflow replaces the LAST event with a cap note), at most
  ``_LIVE_TURNS`` turns alive (FIFO eviction — a still-running evicted
  turn simply stops streaming; the final result is already on its way
  to the session store, nothing is lost).
* Events are SHORT previews (tool results are capped at
  ``_EVENT_CHARS`` — the full tool result already lives in the
  conversation messages; the log is for humans watching, not a
  second context window).
* The terminal record (``done`` / ``error`` / ``stopped``) carries the
  SAME payload shape as the legacy ``Api.chat_turn`` return
  (answer/context/sources/usage/tools_used + error) so the frontend
  renders the final bubble identically either way.
"""
from __future__ import annotations

import time
import threading
from typing import Any

_EVENT_CHARS = 400
_KIND_CHARS = 24
_EVENTS_PER_TURN = 120
_LIVE_TURNS = 8
_RESULT_STR_TRUNC = 8000

_LOCK = threading.Lock()
# _TURNS: turn_id -> {"status": "running"|"done"|"error"|"stopped",
#                     "events": [{ts, kind, text}...], "result": dict|None}
_TURNS: "dict[str, dict[str, Any]]" = {}


def _truncate(value: Any, limit: int) -> str:
    try:
        s = str(value or "")
    except Exception:  # noqa: BLE001 — a repr() that raised
        s = ""
    return s if len(s) <= limit else s[:limit] + "…"


def reset_for_tests() -> None:
    with _LOCK:
        _TURNS.clear()


def start(turn_id: str) -> None:
    """Register a turn as ``running`` (FIFO eviction past the cap)."""
    try:
        with _LOCK:
            _TURNS[str(turn_id)] = {"status": "running", "events": [],
                                    "result": None}
            while len(_TURNS) > _LIVE_TURNS:
                _TURNS.pop(next(iter(_TURNS)), None)
    except Exception:  # noqa: BLE001
        pass


def record(turn_id: str, kind: str, text: str) -> None:
    """Append one live event (never raises; silently drops when the
    turn was evicted or is unknown)."""
    try:
        with _LOCK:
            t = _TURNS.get(str(turn_id))
            if t is None:
                return
            ev = {
                "ts": time.strftime("%H:%M:%S"),
                "kind": _truncate(kind, _KIND_CHARS),
                "text": _truncate(text, _EVENT_CHARS),
            }
            if len(t["events"]) < _EVENTS_PER_TURN:
                t["events"].append(ev)
            else:
                t["events"][-1] = {
                    "ts": ev["ts"], "kind": "note",
                    "text": "…(事件數已達上限，後續略)",
                }
    except Exception:  # noqa: BLE001
        pass


def finish(turn_id: str, status: str, result: Any = None) -> None:
    """Stamp the terminal status + the chat_turn-shaped result dict
    (never raises; unknown turn ids are dropped)."""
    try:
        with _LOCK:
            t = _TURNS.get(str(turn_id))
            if t is None:
                return
            t["status"] = status if status in ("done", "error", "stopped") \
                else "error"
            if isinstance(result, dict):
                safe: dict[str, Any] = {}
                for k, v in result.items():
                    if isinstance(v, (int, float, bool)) or v is None:
                        safe[k] = v
                    elif isinstance(v, list):
                        safe[k] = [_truncate(x, 400) for x in v[:20]]
                    elif isinstance(v, dict):
                        d2: dict[str, Any] = {}
                        for kk, vv in list(v.items())[:12]:
                            if (isinstance(vv, (int, float, bool))
                                    or vv is None):
                                d2[str(kk)[:64]] = vv
                            else:
                                d2[str(kk)[:64]] = _truncate(vv, 400)
                        safe[k] = d2
                    else:
                        safe[k] = _truncate(v, _RESULT_STR_TRUNC)
                t["result"] = safe
    except Exception:  # noqa: BLE001
        pass


def snapshot(turn_id: str, cursor: int = 0) -> dict[str, Any]:
    """Incremental read for ``Api.poll_turn``: events after ``cursor``
    plus the current status/result. Never raises."""
    try:
        cur = int(cursor or 0)
    except Exception:  # noqa: BLE001 — a garbage cursor degrades to 0
        cur = 0
    try:
        with _LOCK:
            t = _TURNS.get(str(turn_id))
            if t is None:
                return {
                    "found": False, "status": "unknown",
                    "events": [], "result": None,
                    "next_cursor": cur,
                }
            evs = t["events"]
            if cur < 0 or cur > len(evs):
                cur = len(evs)
            return {
                "found": True,
                "status": t["status"],
                "events": [dict(e) for e in evs[cur:]],
                "result": (dict(t["result"])
                           if isinstance(t["result"], dict) else None),
                "next_cursor": len(evs),
            }
    except Exception as e:  # noqa: BLE001 — even the answer must degrade
        return {
            "found": False, "status": "unknown", "events": [],
            "result": None, "error": repr(e)[:200],
            "next_cursor": cur,
        }