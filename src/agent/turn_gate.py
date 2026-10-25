"""Process-wide concurrency gate for LLM chat turns (v0.9.45.0).

The parallel-multi-task safety net (Phase 1). Chat turns now arrive
from FOUR concurrent sources — GUI chat, Scheduler daemon fires,
WebhookServer fires (ThreadingHTTPServer), Telegram bot daemon — and
they all share ONE provider account (rate limits, token cost) and ONE
machine (desktop automation, exec sandbox). ``turn_slot()`` caps how
many turns run at the same time.

Design:

* **Opt-in** — ``QDRANT_MAX_CONCURRENT_TURNS`` unset/empty means
  UNLIMITED (byte-identical pre-0.9.45.0 behaviour). Invalid /
  non-positive values fall back to unlimited with a warning only
  (a typo must never wedge the chat pipeline) — same discipline as
  ``_resolve_max_tool_iterations``.
* **Re-entrant per-thread** — a turn that opens another turn on the
  SAME thread (e.g. the LLM calling ``run_scheduled_task_now`` /
  ``run_webhook_trigger_now`` mid-turn, which runs another
  ``Agent.chat_turn`` synchronously) reuses the thread's held slot.
  Without this a ``max=1`` gate would deadlock on the nested call.
* **Bounded queueing** — when the cap is reached, the caller waits up
  to ``QDRANT_TURN_WAIT_SECONDS`` (default 120) for a free slot and
  raises ``TurnGateBusy`` (a clear, actionable RuntimeError) instead
  of hanging forever. Callers already degrade gracefully (GUI error
  envelope, webhook 500, scheduler last_error stamp).
* **Zero-side-effect module** — no ``src.config`` import at module
  level (that import runs ``load_dotenv`` and would re-inject .env
  values a test fixture just wiped; mirrors ``provider_chain.py``).
  The gate itself is built lazily on first ``turn_slot()`` and cached
  for the process lifetime — changing the env vars requires a
  restart (documented in .env.example).

Scope: ``Agent.ask`` / ``Agent.chat_turn`` only. The vision OCR chain
(ImageDescriber — used with 4 workers during ingest) and background
non-LLM work are deliberately NOT gated.
"""
from __future__ import annotations

import logging
import os
import threading
from contextlib import contextmanager

logger = logging.getLogger(__name__)

DEFAULT_TURN_WAIT_SECONDS = 120.0


def _warn_bad_value(key: str, raw: str, fallback: str) -> None:
    logger.warning(
        "[turn-gate] %s=%r is not a valid value — falling back to %s",
        key, raw, fallback,
    )


def resolve_max_concurrent_turns() -> int | None:
    """``QDRANT_MAX_CONCURRENT_TURNS`` — how many LLM turns may run at
    the same time across the whole process (GUI + scheduler + webhook +
    telegram + CLI all share the cap).

    Unset/empty → ``None`` = unlimited (pre-0.9.45.0 behaviour).
    Invalid / non-positive → ``None`` with a warning (never raises).
    """
    raw = (os.environ.get("QDRANT_MAX_CONCURRENT_TURNS", "") or "").strip()
    if not raw:
        return None
    try:
        n = int(raw)
    except ValueError:
        _warn_bad_value("QDRANT_MAX_CONCURRENT_TURNS", raw, "unlimited")
        return None
    if n <= 0:
        _warn_bad_value("QDRANT_MAX_CONCURRENT_TURNS", raw, "unlimited")
        return None
    return n


def resolve_turn_wait_seconds() -> float:
    """``QDRANT_TURN_WAIT_SECONDS`` — how long a queued turn waits for
    a free slot before raising ``TurnGateBusy``. Default 120 s (a turn
    can legitimately run for minutes — desktop automation burns 20
    tool iterations — so the default queue wait must be generous)."""
    raw = (os.environ.get("QDRANT_TURN_WAIT_SECONDS", "") or "").strip()
    if not raw:
        return DEFAULT_TURN_WAIT_SECONDS
    try:
        v = float(raw)
    except ValueError:
        _warn_bad_value(
            "QDRANT_TURN_WAIT_SECONDS", raw, str(DEFAULT_TURN_WAIT_SECONDS),
        )
        return DEFAULT_TURN_WAIT_SECONDS
    if v <= 0:
        _warn_bad_value(
            "QDRANT_TURN_WAIT_SECONDS", raw, str(DEFAULT_TURN_WAIT_SECONDS),
        )
        return DEFAULT_TURN_WAIT_SECONDS
    return v


class TurnGateBusy(RuntimeError):
    """Raised when no turn slot became free within the wait budget."""


class _GateState:
    """Cached process-wide gate (built once on first use)."""

    def __init__(self, max_turns: int | None, wait_seconds: float):
        self.max_turns = max_turns
        self.wait_seconds = wait_seconds
        # Bounded: a stray extra release (a bug) would raise instead of
        # silently inflating the cap.
        self.sem = (
            threading.BoundedSemaphore(max_turns) if max_turns else None
        )
        self.lock = threading.Lock()
        self.holds = 0  # slots currently held (capped mode only)


_GATE_LOCK = threading.Lock()
_GATE: _GateState | None = None
_SLOT_LOCAL = threading.local()  # per-thread nesting depth


def _get_gate() -> _GateState:
    """Double-checked lazy build — the env is read ONCE per process."""
    global _GATE
    gate = _GATE
    if gate is None:
        with _GATE_LOCK:
            gate = _GATE
            if gate is None:
                gate = _GateState(
                    resolve_max_concurrent_turns(),
                    resolve_turn_wait_seconds(),
                )
                _GATE = gate
                if gate.max_turns:
                    logger.info(
                        "[turn-gate] active: max_concurrent_turns=%d "
                        "wait_seconds=%gs",
                        gate.max_turns, gate.wait_seconds,
                    )
    return gate


def reset_turn_gate() -> None:
    """TEST-ONLY: drop the cached gate so the next ``turn_slot()``
    re-reads the env vars. In-flight holders keep counting against the
    old semaphore — never call this while turns are running."""
    global _GATE
    with _GATE_LOCK:
        _GATE = None


def held_turns() -> int:
    """Diagnostics: how many gate slots are currently held (always 0
    in unlimited mode — per-thread depth cannot be summed)."""
    gate = _GATE
    if gate is None or gate.sem is None:
        return 0
    with gate.lock:
        return gate.holds


@contextmanager
def turn_slot():
    """One LLM turn's concurrency slot. Re-entrant per thread.

    Unlimited config: pure depth tracking — zero overhead, no blocking.
    Capped config: acquire a semaphore slot (wait up to the configured
    budget), raise ``TurnGateBusy`` on timeout. Nested ``turn_slot()``
    on the SAME thread reuses the held slot — a mid-turn
    ``run_scheduled_task_now`` (which opens another ``Agent.chat_turn``
    synchronously) cannot deadlock even with ``...=1``.
    """
    depth = getattr(_SLOT_LOCAL, "depth", 0)
    if depth > 0:
        # Nested turn on this thread — reuse the held slot.
        _SLOT_LOCAL.depth = depth + 1
        try:
            yield
        finally:
            _SLOT_LOCAL.depth = depth
        return

    gate = _get_gate()
    if gate.sem is None:
        # Unlimited: depth bookkeeping only (so nesting stays correct
        # even if the gate were swapped mid-process by tests).
        _SLOT_LOCAL.depth = 1
        try:
            yield
        finally:
            _SLOT_LOCAL.depth = 0
        return

    if not gate.sem.acquire(timeout=gate.wait_seconds):
        raise TurnGateBusy(
            f"Concurrent turn limit reached (QDRANT_MAX_CONCURRENT_TURNS="
            f"{gate.max_turns}); no slot became free within "
            f"{gate.wait_seconds:g}s. Retry later, or raise the limit / "
            f"the wait in .env (QDRANT_TURN_WAIT_SECONDS)."
        )
    with gate.lock:
        gate.holds += 1
    _SLOT_LOCAL.depth = 1
    try:
        yield
    finally:
        _SLOT_LOCAL.depth = 0
        with gate.lock:
            gate.holds -= 1
        gate.sem.release()