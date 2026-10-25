"""Process-wide background task registry (v0.9.45.0 — Phase 2).

Session-based parallel multi-task core. Every background LLM turn the
process launches — a manual ``run_task_once`` submission, a scheduler
fire, a webhook fire, a Telegram bot turn — registers itself here with
the DEDICATED session id it writes to, so:

* ``list_tasks`` / ``get_task_result`` (``TaskLocalHost``, server
  ``task-local``) let the LLM and the GUI see what is running and what
  finished, without polling the Sessions tab.
* The session file (``task-<task_id>.json`` / ``sched-...`` /
  ``webhook-...`` / ``tgbot-...``) remains the durable record — the
  registry is in-memory only and disappears with the process, exactly
  like the scheduler/webhook daemon threads.

Design:

* **Thread-safe** — one module ``threading.Lock`` guards the dict;
  records are plain dicts (JSON-serialisable, no dataclass) so the MCP
  tools can return them verbatim.
* **Ephemeral by design** — restart clears the registry (sessions
  survive). ``MAX_FINISHED`` caps memory: the oldest finished records
  are dropped as new ones land (running records are never trimmed).
* **Zero env vars** — caps are built-in constants; nothing to tune.
* **No Agent import at module level** — the registry knows nothing
  about agents or LLMs; callers own the execution (mirrors the
  provider_chain.py zero-side-effect discipline).

The registry does NOT gate or queue anything — concurrency limiting
lives in ``turn_gate.turn_slot()`` (Phase 1), which every
``Agent.chat_turn`` crosses anyway. Queued background turns therefore
wait for a free slot when ``QDRANT_MAX_CONCURRENT_TURNS`` is set.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from datetime import datetime
from typing import Any, Callable

logger = logging.getLogger(__name__)

# statuses (module constants — the MCP tools' ``status`` filter uses
# exactly these strings).
RUNNING = "running"
DONE = "done"
FAILED = "failed"
STOPPED = "stopped"      # v0.9.120.0 — force-stopped by user request

# Keep the last N FINISHED records (running records are never trimmed).
MAX_FINISHED = 100

_LOCK = threading.Lock()
_TASKS: dict[str, dict] = {}   # task_id -> record (insertion order)
_ORDER: list[str] = []         # creation order, oldest first

# v0.9.120.0 — per-task force-stop flags. Deliberately NOT cleared in
# end_task: a zombie turn that was already abandoned by a hard timeout
# (its registry row terminal) still honours the flag at its NEXT
# tool-loop iteration — the only stop mechanism that reaches runs the
# registry has already given up on. Cleared only by reset_tasks().
_STOP_FLAGS: dict[str, threading.Event] = {}


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def begin_task(source: str, title: str, sid: str | None = None,
               llm: str | None = None) -> str:
    """Register a running task; returns its task_id.

    ``source`` is a short tag: ``"task"`` (manual run_task_once),
    ``"scheduler"``, ``"webhook"``, ``"telegram"``, ``"plan"``
    (v0.9.109+ skill stages). ``title`` is a human-readable one-liner
    (schedule name / trigger name / prompt preview). ``sid`` is the
    dedicated session the turn writes to (attach later via
    :func:`attach_sid` when it is only known after the session is
    created). ``llm`` (v0.9.110.0) is the task's pinned chat model
    (``provider:model``) — ``None`` = the default provider chain.
    """
    task_id = uuid.uuid4().hex[:8]
    rec = {
        "task_id": task_id,
        "source": str(source or "task"),
        "title": str(title or "")[:120],
        "sid": sid,
        "llm": (str(llm or "").strip() or None),
        # v0.9.132.3 — optional origin chat-session binding: the web/GUI
        # chat passes its own session id (set_origin / ``origin_sid``
        # arg) so the client can write the finished result back into
        # that conversation. Registry-only (in-memory, like the rest).
        "origin_sid": None,
        "status": RUNNING,
        "started_at": now_iso(),
        "finished_at": None,
        "answer_chars": None,
        "error": None,
    }
    with _LOCK:
        _TASKS[task_id] = rec
        _ORDER.append(task_id)
        _STOP_FLAGS[task_id] = threading.Event()
    logger.info(
        "[task-registry] started %s source=%s sid=%s title=%r",
        task_id, rec["source"], sid, rec["title"],
    )
    return task_id


def request_stop(task_id: str) -> bool:
    """v0.9.120.0 — set the task's force-stop flag. Works for RUNNING
    tasks AND for already-terminal zombie turns (their in-flight
    thread checks the flag at its next tool-loop iteration). Never
    raises; unknown id → False."""
    tid = str(task_id or "").strip()
    if not tid:
        return False
    with _LOCK:
        ev = _STOP_FLAGS.get(tid)
        if ev is None:
            logger.warning(
                "[task-registry] stop request for unknown %s", tid)
            return False
        ev.set()
        rec = _TASKS.get(tid)
        src = rec.get("source") if rec else "?"
    logger.info("[task-registry] stop requested for %s (source=%s)",
                tid, src)
    return True


def stop_requested(task_id: str) -> bool:
    """v0.9.120.0 — the task's stop flag (checks never raise; unknown
    task → False)."""
    with _LOCK:
        ev = _STOP_FLAGS.get(str(task_id or ""))
        return bool(ev is not None and ev.is_set())


def attach_sid(task_id: str, sid: str | None) -> None:
    """Fill in the session id after the fire created its session."""
    with _LOCK:
        rec = _TASKS.get(task_id)
        if rec is not None and sid:
            rec["sid"] = sid


def attach_turn(task_id: str, turn_id: str | None) -> None:
    """v0.9.133.10 — bind the detached turn's id to its registry row.

    The turn_events ring is keyed by turn_id; the ⏳ task panel uses
    this binding to poll the LIVE execution log of a promoted task
    (``poll_turn(turn_id, cursor)``) and ``_finalize_detached_turn``
    uses it to persist the log into the task session. Never raises;
    unknown id is a no-op."""
    with _LOCK:
        rec = _TASKS.get(str(task_id or ""))
        if rec is not None:
            rec["turn_id"] = (str(turn_id or "").strip() or None)


def set_origin(task_id: str, origin_sid: str | None) -> None:
    """v0.9.132.3 — bind the task to the ORIGIN chat session it was
    submitted from (web/GUI ☑ 背景執行). Never raises; unknown id is
    a no-op (the task may have already been trimmed)."""
    with _LOCK:
        rec = _TASKS.get(str(task_id or ""))
        if rec is not None and origin_sid:
            rec["origin_sid"] = str(origin_sid)


def end_task(
    task_id: str,
    status: str,
    answer_chars: int | None = None,
    error: str | None = None,
) -> None:
    """Stamp the terminal state (never raises — registry bookkeeping
    must not break the caller's fire path)."""
    with _LOCK:
        rec = _TASKS.get(task_id)
        if rec is None:
            return
        rec["status"] = (status if status in (DONE, FAILED, STOPPED)
                         else DONE)
        rec["finished_at"] = now_iso()
        if answer_chars is not None:
            rec["answer_chars"] = int(answer_chars)
        if error:
            rec["error"] = str(error)[:500]
        _trim_locked()
    logger.info(
        "[task-registry] %s -> %s (sid=%s answer_chars=%s error=%r)",
        task_id, rec["status"], rec["sid"], rec.get("answer_chars"),
        rec.get("error"),
    )
    # v0.9.108.0 — terminal notify (outside the lock; never raises).
    _emit({"kind": "terminal", **dict(rec)})


def _trim_locked() -> None:
    """Drop the oldest finished records over the cap. Caller holds
    ``_LOCK``."""
    finished_ids = [t for t in _ORDER if _TASKS[t]["status"] != RUNNING]
    overflow = len(finished_ids) - MAX_FINISHED
    if overflow <= 0:
        return
    for task_id in finished_ids[:overflow]:
        _TASKS.pop(task_id, None)
        _ORDER.remove(task_id)


def get_task(task_id: str) -> dict | None:
    """One record (shallow copy) or ``None``."""
    with _LOCK:
        rec = _TASKS.get(str(task_id or ""))
        return dict(rec) if rec is not None else None


def list_tasks(
    status: str | None = None, limit: int = 20,
) -> dict:
    """Running + finished records, newest first.

    ``status`` filters to one of ``running`` / ``done`` / ``failed``
    (None = all). ``limit`` caps the returned list (1..200). The
    summary carries live counts so a caller can show "2 running" in
    one call.
    """
    try:
        lim = max(1, min(int(limit or 20), 200))
    except (TypeError, ValueError):
        lim = 20
    with _LOCK:
        records = [dict(_TASKS[t]) for t in reversed(_ORDER)]
    running_n = sum(1 for r in records if r["status"] == RUNNING)
    # v0.9.120.0 — surface the force-stop flag on visible rows.
    for r in records:
        ev = _STOP_FLAGS.get(r.get("task_id") or "")
        r["stop_requested"] = bool(ev is not None and ev.is_set())
    if status in (RUNNING, DONE, FAILED, STOPPED):
        records = [r for r in records if r["status"] == status]
    return {
        "tasks": records[:lim],
        "returned": min(len(records), lim),
        "total": len(records),
        "running": running_n,
    }


def reset_tasks() -> None:
    """TEST-ONLY: wipe the registry (process-global singleton)."""
    with _LOCK:
        _TASKS.clear()
        _ORDER.clear()
        _STOP_FLAGS.clear()


# ── v0.9.108.0 — fail-closed notify hook + progress monitor ────────
# The registry itself sends NOTHING (zero-side-effect discipline —
# mirrors provider_chain.py): the app layer (gui/api.py) injects a
# callback; default None = silent legacy. Events:
#   {"kind": "terminal", <full record fields>}
#   {"kind": "progress", "text": "…", "running_count": N}
_NOTIFY_CB: Callable[[dict], Any] | None = None
_MONITOR_STATE: dict = {"thread": None, "stop": False}


def set_notify_callback(cb: Callable[[dict], Any] | None) -> None:
    """Install (or clear with ``None``) the terminal/progress notifier."""
    global _NOTIFY_CB
    _NOTIFY_CB = cb


def _emit(event: dict) -> None:
    """Fire the notify hook — NEVER raises, MUST NOT block the turn."""
    cb = _NOTIFY_CB
    if cb is None:
        return
    try:
        cb(event)
    except Exception as e:  # noqa: BLE001 — bookkeeping must not break
        logger.debug("[task-registry] notify callback failed: %r", e)


def start_monitor(interval_s: int) -> bool:
    """Start the progress-monitor daemon (idempotent). ``interval_s`` > 0
    required; returns ``False`` when disabled. The monitor emits a
    ``progress`` event every interval while ANY task is running."""
    try:
        interval_s = int(interval_s or 0)
    except (TypeError, ValueError):
        return False
    if interval_s <= 0:
        return False
    th = None
    with _LOCK:
        existing = _MONITOR_STATE.get("thread")
        if existing is not None and existing.is_alive():
            return True
        _MONITOR_STATE["stop"] = False
        th = threading.Thread(
            target=_monitor_loop, args=(interval_s,),
            daemon=True, name="task-monitor",
        )
        _MONITOR_STATE["thread"] = th
    th.start()
    logger.info(
        "[task-registry] progress monitor started (interval=%ds)",
        interval_s,
    )
    return True


def stop_monitor() -> None:
    """Ask the monitor loop to exit (best-effort, test hook)."""
    _MONITOR_STATE["stop"] = True


def _monitor_loop(interval_s: int) -> None:
    while not _MONITOR_STATE.get("stop"):
        time.sleep(max(1, interval_s))
        if _MONITOR_STATE.get("stop"):
            break
        try:
            running = (
                list_tasks(status=RUNNING, limit=50).get("tasks") or []
            )
            if not running:
                continue
            now = datetime.now()
            rows: list[str] = []
            for r in running:
                try:
                    st = datetime.fromisoformat(str(r["started_at"]))
                    el = int((now - st).total_seconds())
                except Exception:
                    el = -1
                rows.append(f"{r.get('title')} ({el}s)")
            _emit({
                "kind": "progress",
                "running_count": len(running),
                "text": (
                    f"⏳ 背景任務進度：{len(running)} 個 running — "
                    + "；".join(rows[:10])
                ),
            })
        except Exception as e:  # noqa: BLE001 — daemon keeps running
            logger.debug("[task-registry] monitor tick failed: %r", e)