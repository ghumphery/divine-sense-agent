"""Tests for ``src.agent.scheduler``.

All tests use a tmp ``QDRANT_RAG_HOME`` so they don't pollute the
real ``~/.qdrant-rag-agent/schedules/`` directory. The
``Scheduler`` daemon is exercised via ``tick_once()`` so we never
have to wait on a real-time clock.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture()
def tmp_home(tmp_path, monkeypatch):
    """Point ``$QDRANT_RAG_HOME`` at ``tmp_path`` for the test."""
    monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path))
    yield tmp_path


# ─── cron parsing ──────────────────────────────────────────────


def test_parse_field_wildcard():
    from src.agent.scheduler import _parse_field
    assert _parse_field("*", 0, 5) == {0, 1, 2, 3, 4, 5}


def test_parse_field_step():
    from src.agent.scheduler import _parse_field
    assert _parse_field("*/15", 0, 59) == {0, 15, 30, 45}


def test_parse_field_range():
    from src.agent.scheduler import _parse_field
    assert _parse_field("9-17", 0, 23) == set(range(9, 18))


def test_parse_field_list():
    from src.agent.scheduler import _parse_field
    assert _parse_field("0,15,45", 0, 59) == {0, 15, 45}


def test_parse_field_clamps_out_of_range_raises():
    """Out-of-range values raise ValueError (the field is
    effectively empty after clamping)."""
    from src.agent.scheduler import _parse_field
    with pytest.raises(ValueError):
        _parse_field("100", 0, 59)


def test_cron_matches_every_minute_only_minute_granular():
    from src.agent.scheduler import cron_matches
    # ``* * * * *`` matches every minute boundary; the second
    # field is the only minute-resolution filter.
    assert cron_matches("* * * * *", datetime(2026, 8, 27, 14, 30))
    # Hour-specific cron excludes minute boundaries outside that hour.
    assert not cron_matches("0 9 * * *", datetime(2026, 8, 27, 14, 0))


def test_cron_matches_specific_time():
    from src.agent.scheduler import cron_matches
    assert cron_matches("30 9 * * *", datetime(2026, 8, 27, 9, 30))
    assert not cron_matches("30 9 * * *", datetime(2026, 8, 27, 9, 31))


def test_cron_matches_weekday_specific():
    from src.agent.scheduler import cron_matches
    # weekday()=0 means Monday.
    expr = "0 9 * * 0"
    assert cron_matches(expr, datetime(2026, 8, 31, 9, 0))   # Mon
    assert not cron_matches(expr, datetime(2026, 8, 27, 9, 0))  # Wed


def test_cron_matches_step():
    from src.agent.scheduler import cron_matches
    assert cron_matches("*/15 * * * *", datetime(2026, 8, 27, 9, 0))
    assert cron_matches("*/15 * * * *", datetime(2026, 8, 27, 9, 15))
    assert not cron_matches("*/15 * * * *", datetime(2026, 8, 27, 9, 7))


def test_next_cron_fire_returns_minute_boundary():
    from src.agent.scheduler import next_cron_fire
    base = datetime(2026, 8, 27, 14, 30, 15)
    nxt = next_cron_fire("0 15 * * *", after=base)
    assert nxt == datetime(2026, 8, 27, 15, 0)


def test_next_cron_fire_never_matches_raises():
    from src.agent.scheduler import next_cron_fire
    with pytest.raises(ValueError):
        # Feb 30 never exists.
        next_cron_fire("0 0 30 2 *", after=datetime(2026, 1, 1))


# ─── Schedule dataclass ────────────────────────────────────────


def test_schedule_compute_next_fire_once_at():
    from src.agent.scheduler import Schedule
    s = Schedule(
        schedule_type="once_at",
        once_at="2027-01-01T00:00:00",
    )
    nxt = s.compute_next_fire()
    assert nxt == datetime(2027, 1, 1, 0, 0, 0)


def test_schedule_compute_next_fire_once_at_disables_after_fire():
    from src.agent.scheduler import Schedule
    s = Schedule(
        schedule_type="once_at",
        once_at="2026-01-01T00:00:00",
        fire_count=1,
    )
    assert s.compute_next_fire() is None


def test_schedule_compute_next_fire_interval_anchors_on_last_fire():
    """``last_fire + interval`` is the candidate; if it falls in
    the past relative to ``after``, we catch up to ``after +
    interval`` so the schedule doesn't fire repeatedly for missed
    ticks."""
    from src.agent.scheduler import Schedule
    s = Schedule(
        schedule_type="interval_seconds",
        interval_seconds=60,
        last_fire_at="2026-08-27T10:00:00",
    )
    # Candidate 10:01:00 is in the past → catch up to after+60.
    nxt = s.compute_next_fire(after=datetime(2026, 8, 27, 10, 1, 30))
    assert nxt == datetime(2026, 8, 27, 10, 2, 30)
    # Candidate still in the future → use it as-is.
    nxt2 = s.compute_next_fire(after=datetime(2026, 8, 27, 10, 0, 30))
    assert nxt2 == datetime(2026, 8, 27, 10, 1, 0)


def test_schedule_compute_next_fire_interval_catches_up_if_in_past():
    from src.agent.scheduler import Schedule
    s = Schedule(
        schedule_type="interval_seconds",
        interval_seconds=60,
        last_fire_at="2026-01-01T00:00:00",
    )
    nxt = s.compute_next_fire(after=datetime(2026, 8, 27, 12, 0, 0))
    assert nxt == datetime(2026, 8, 27, 12, 1, 0)


def test_schedule_compute_next_fire_disabled_returns_none():
    from src.agent.scheduler import Schedule
    s = Schedule(
        schedule_type="interval_seconds",
        interval_seconds=60,
        enabled=False,
    )
    assert s.compute_next_fire() is None


def test_schedule_compute_next_fire_unknown_type_raises():
    from src.agent.scheduler import Schedule
    s = Schedule(schedule_type="never")
    with pytest.raises(ValueError):
        s.compute_next_fire()


# ─── ScheduleManager ───────────────────────────────────────────


def test_manager_save_and_list_round_trip(tmp_home):
    from src.agent.scheduler import Schedule, ScheduleManager
    mgr = ScheduleManager()
    s = Schedule(
        name="hi",
        prompt="ping",
        schedule_type="interval_seconds",
        interval_seconds=600,
    )
    mgr.save(s)
    items = mgr.list()
    assert len(items) == 1
    assert items[0].id == s.id
    assert items[0].prompt == "ping"
    assert items[0].next_fire_at is not None


def test_manager_delete_removes_file_and_history(tmp_home):
    from src.agent.scheduler import Schedule, ScheduleManager
    mgr = ScheduleManager()
    s = Schedule(
        name="x", prompt="p", schedule_type="interval_seconds",
        interval_seconds=10,
    )
    mgr.save(s)
    mgr.append_history(s.id, {"ts": "2026-08-27", "prompt": "p"})
    assert (tmp_home / "schedules" / f"{s.id}.json").exists()
    assert (tmp_home / "schedules" / f"{s.id}.history.jsonl").exists()
    assert mgr.delete(s.id)
    assert not (tmp_home / "schedules" / f"{s.id}.json").exists()
    assert not (tmp_home / "schedules" / f"{s.id}.history.jsonl").exists()


def test_manager_path_traversal_rejected(tmp_home):
    from src.agent.scheduler import ScheduleManager
    mgr = ScheduleManager()
    with pytest.raises(ValueError):
        mgr._path("../escape")
    with pytest.raises(ValueError):
        mgr.get("../../../etc/passwd")


def test_manager_append_history_trims_to_max(tmp_home):
    from src.agent.scheduler import ScheduleManager, MAX_HISTORY
    mgr = ScheduleManager()
    for i in range(MAX_HISTORY + 10):
        mgr.append_history("s1", {"ts": str(i)})
    h = mgr.get_history("s1", limit=100)
    assert len(h) == MAX_HISTORY
    assert h[0]["ts"] == "10"  # first 10 dropped
    assert h[-1]["ts"] == str(MAX_HISTORY + 9)


def test_manager_get_returns_none_for_missing(tmp_home):
    from src.agent.scheduler import ScheduleManager
    mgr = ScheduleManager()
    assert mgr.get("nope") is None


def test_manager_save_recomputes_next_fire(tmp_home):
    from src.agent.scheduler import Schedule, ScheduleManager
    mgr = ScheduleManager()
    s = Schedule(
        name="x", prompt="p", schedule_type="interval_seconds",
        interval_seconds=300,
    )
    mgr.save(s)
    loaded = mgr.get(s.id)
    assert loaded.next_fire_at is not None
    nxt = datetime.fromisoformat(loaded.next_fire_at)
    delta = (nxt - datetime.now()).total_seconds()
    assert 0 <= delta <= 305


# ─── Scheduler (tick_once) ─────────────────────────────────────


def test_scheduler_tick_once_fires_due_schedule(tmp_home):
    import json
    from src.agent.scheduler import (
        Schedule, ScheduleManager, Scheduler,
    )
    mgr = ScheduleManager()
    s = Schedule(
        name="immediate", prompt="p", schedule_type="interval_seconds",
        interval_seconds=60,
    )
    mgr.save(s)
    # Bypass ``mgr.save()`` (which would recompute next_fire_at);
    # patch the on-disk JSON directly so the scheduler sees a
    # due schedule.
    past = (datetime.now() - timedelta(seconds=10)).isoformat(
        timespec="seconds"
    )
    raw = json.loads(mgr._path(s.id).read_text(encoding="utf-8"))
    raw["next_fire_at"] = past
    mgr._path(s.id).write_text(
        json.dumps(raw, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    fired: list = []
    sched = Scheduler(
        manager=mgr, on_fire=lambda x: fired.append(x.id)
    )
    out = sched.tick_once()
    assert s.id in fired
    assert s.id in out


def _force_past_fire(mgr, schedule_id):
    """Mutate the on-disk JSON so ``next_fire_at`` is in the past
    without triggering ``save()`` (which would recompute it)."""
    import json
    past = (datetime.now() - timedelta(seconds=10)).isoformat(
        timespec="seconds"
    )
    raw = json.loads(mgr._path(schedule_id).read_text(encoding="utf-8"))
    raw["next_fire_at"] = past
    mgr._path(schedule_id).write_text(
        json.dumps(raw, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def test_scheduler_tick_once_skips_disabled(tmp_home):
    from src.agent.scheduler import (
        Schedule, ScheduleManager, Scheduler,
    )
    mgr = ScheduleManager()
    s = Schedule(
        name="off", prompt="p", schedule_type="interval_seconds",
        interval_seconds=60, enabled=False,
    )
    mgr.save(s)
    _force_past_fire(mgr, s.id)
    fired: list = []
    sched = Scheduler(
        manager=mgr, on_fire=lambda x: fired.append(x.id)
    )
    sched.tick_once()
    assert s.id not in fired


def test_scheduler_tick_once_auto_disables_one_shot_after_fire(tmp_home):
    from src.agent.scheduler import (
        Schedule, ScheduleManager, Scheduler,
    )
    mgr = ScheduleManager()
    s = Schedule(
        name="once", prompt="p", schedule_type="once_at",
        once_at=(datetime.now() + timedelta(seconds=10)).isoformat(),
    )
    mgr.save(s)
    s.fire_count = 1
    s.next_fire_at = None
    mgr.save(s)
    fired: list = []
    sched = Scheduler(
        manager=mgr, on_fire=lambda x: fired.append(x.id)
    )
    sched.tick_once()
    assert s.id not in fired
    loaded = mgr.get(s.id)
    assert loaded.enabled is False


def test_scheduler_tick_once_handles_on_fire_exception(tmp_home):
    """on_fire raising must NOT propagate — exceptions are
    caught and logged so the daemon thread keeps ticking. The
    fired list does NOT include failed IDs (only successful
    fires are reported)."""
    from src.agent.scheduler import (
        Schedule, ScheduleManager, Scheduler,
    )
    mgr = ScheduleManager()
    s = Schedule(
        name="bad", prompt="p", schedule_type="interval_seconds",
        interval_seconds=60,
    )
    mgr.save(s)
    _force_past_fire(mgr, s.id)

    def boom(_):
        raise RuntimeError("on fire failed")

    sched = Scheduler(manager=mgr, on_fire=boom)
    # Must not raise — exceptions are caught inside tick_once.
    out = sched.tick_once()
    # Failed fires are not in the returned list.
    assert s.id not in out


def test_scheduler_start_stop_lifecycle(tmp_home):
    import time
    from src.agent.scheduler import Scheduler
    sched = Scheduler()
    sched.start()
    assert sched.is_running()
    time.sleep(0.1)
    sched.stop(timeout=2.0)
    assert not sched.is_running()


def test_scheduler_start_is_idempotent(tmp_home):
    from src.agent.scheduler import Scheduler
    sched = Scheduler()
    sched.start()
    first = sched._thread
    sched.start()  # no-op
    assert sched._thread is first
    sched.stop(timeout=2.0)


# ─── make_agent_on_fire ────────────────────────────────────────


class _FakeAgent:
    def __init__(self, answer="ok", error=None):
        self._answer = answer
        self._error = error

    def chat_turn(self, **_):
        return {
            "answer": self._answer,
            "sources": ["a.pdf"],
            "error": self._error,
        }


def test_make_agent_on_fire_writes_session_and_history(tmp_home):
    from src.agent.scheduler import (
        Schedule, ScheduleManager, make_agent_on_fire,
    )
    # Set up: schedule + a fake agent factory.
    s = Schedule(
        name="my-prompt", prompt="hello world",
        schedule_type="interval_seconds",
        interval_seconds=60,
    )
    agent = _FakeAgent(answer="hi from LLM")
    on_fire = make_agent_on_fire(agent_factory=lambda: agent)
    on_fire(s)
    # Lifecycle fields updated.
    assert s.fire_count == 1
    assert s.last_session_id is not None
    assert s.next_fire_at is not None  # interval schedule keeps firing
    # Session was created.
    sess_path = tmp_home / "sessions" / f"{s.last_session_id}.json"
    assert sess_path.exists()
    import json as _json
    sess = _json.loads(sess_path.read_text(encoding="utf-8"))
    msgs = sess["messages"]
    assert any(
        m.get("role") == "user" and "hello world" in m.get("content", "")
        for m in msgs
    )
    assert any(
        m.get("role") == "assistant" and "hi from LLM" in m.get("content", "")
        for m in msgs
    )
    # History ring-buffer got one record.
    hist_path = tmp_home / "schedules" / f"{s.id}.history.jsonl"
    assert hist_path.exists()
    lines = [
        ln for ln in hist_path.read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]
    assert len(lines) == 1
    rec = _json.loads(lines[0])
    assert rec["prompt"] == "hello world"
    assert rec["answer"] == "hi from LLM"


def test_make_agent_on_fire_tuple_chat_turn(tmp_home):
    """v0.9.23.0 — ``Agent.chat_turn`` returns the TUPLE
    (answer, context_md, sources_list); the old dict-only contract
    (`result.get("answer")`) matched only the test fakes, so every
    REAL Agent fire recorded answer=None / assistant "[error] None"
    / history "answer=0 chars" even when the LLM had produced a
    full summary (the 14:55 stock-analysis fire did exactly that)."""
    import json as _json
    from src.agent.scheduler import (
        Schedule, ScheduleManager, make_agent_on_fire,
    )

    class _TupleAgent:
        def chat_turn(self, **_):
            return ("完整分析內容", "", ["deploy.md"])

    s = Schedule(
        name="my-prompt", prompt="hello world",
        schedule_type="interval_seconds",
        interval_seconds=60,
    )
    mgr = ScheduleManager()
    mgr.save(s)
    on_fire = make_agent_on_fire(agent_factory=lambda: _TupleAgent())
    on_fire(s)

    sess_path = tmp_home / "sessions" / f"{s.last_session_id}.json"
    sess = _json.loads(sess_path.read_text(encoding="utf-8"))
    assistant = [
        m for m in sess["messages"] if m.get("role") == "assistant"
    ]
    assert assistant and assistant[-1]["content"] == "完整分析內容", (
        "the LLM's real answer must land in the session — not "
        "[error] None"
    )
    assert assistant[-1]["sources"] == ["deploy.md"]

    hist_path = tmp_home / "schedules" / f"{s.id}.history.jsonl"
    rec = _json.loads(
        hist_path.read_text(encoding="utf-8").splitlines()[0]
    )
    assert rec["answer"] == "完整分析內容"
    assert rec["sources"] == ["deploy.md"]


def test_make_agent_on_fire_tuple_empty_answer(tmp_home):
    """A tuple fire with an EMPTY answer still records the assistant
    row (as [error] None when error is None) and fire_count/history
    advance — the scheduler must not crash on an empty LLM reply."""
    import json as _json
    from src.agent.scheduler import (
        Schedule, ScheduleManager, make_agent_on_fire,
    )

    class _EmptyTupleAgent:
        def chat_turn(self, **_):
            return ("", "", [])

    s = Schedule(
        name="my-prompt", prompt="hello world",
        schedule_type="interval_seconds",
        interval_seconds=60,
    )
    mgr = ScheduleManager()
    mgr.save(s)
    on_fire = make_agent_on_fire(agent_factory=lambda: _EmptyTupleAgent())
    on_fire(s)  # must not raise

    assert s.fire_count == 1
    sess_path = tmp_home / "sessions" / f"{s.last_session_id}.json"
    sess = _json.loads(sess_path.read_text(encoding="utf-8"))
    assistant = [m for m in sess["messages"] if m.get("role") == "assistant"]
    assert assistant and assistant[-1]["content"] == "[error] None"


def test_make_agent_on_fire_once_at_disables_after_fire(tmp_home):
    from src.agent.scheduler import (
        Schedule, ScheduleManager, make_agent_on_fire,
    )
    s = Schedule(
        name="once", prompt="p",
        schedule_type="once_at",
        once_at=(datetime.now() + timedelta(seconds=10)).isoformat(),
    )
    on_fire = make_agent_on_fire(
        agent_factory=lambda: _FakeAgent()
    )
    on_fire(s)
    assert s.enabled is False
    assert s.next_fire_at is None


# ─── GUI bridge API methods ────────────────────────────────────


def test_api_validate_schedule_rejects_bad_type():
    from gui.api import Api
    api = Api()
    assert api._validate_schedule({
        "prompt": "x", "schedule_type": "nonsense",
    }) is not None


def test_api_validate_schedule_rejects_missing_prompt():
    from gui.api import Api
    api = Api()
    err = api._validate_schedule({
        "schedule_type": "interval_seconds",
        "interval_seconds": 60,
    })
    assert err and "prompt" in err


def test_api_add_schedule_validates_and_writes(tmp_home, monkeypatch):
    from gui.api import Api
    api = Api()
    # Force the scheduler to look at our tmp dir by pointing
    # QDRANT_RAG_HOME (the schedule manager reads that env var).
    # The API doesn't honour it directly — but the ScheduleManager
    # uses the default location. Patch the class to redirect.
    from src.agent.scheduler import ScheduleManager
    monkeypatch.setattr(
        ScheduleManager, "__init__",
        lambda self, root=None: object.__setattr__(
            self, "root", tmp_home / "schedules"
        ) or None,
    )
    # Bad: missing interval.
    bad = api.add_schedule({
        "name": "x",
        "prompt": "hi",
        "schedule_type": "interval_seconds",
    })
    assert "error" in bad
    assert "validation" in bad["error"].lower()
    # Good: succeeds and the file lands in tmp_home.
    good = api.add_schedule({
        "name": "x",
        "prompt": "hi",
        "schedule_type": "interval_seconds",
        "interval_seconds": 60,
    })
    assert good.get("status") == "ok"
    assert good["schedule"]["next_fire_at"] is not None


def test_api_list_schedules_returns_structure(tmp_home, monkeypatch):
    from gui.api import Api
    api = Api()
    from src.agent.scheduler import (
        ScheduleManager, Schedule,
    )
    monkeypatch.setattr(
        ScheduleManager, "__init__",
        lambda self, root=None: object.__setattr__(
            self, "root", tmp_home / "schedules"
        ) or None,
    )
    # Pre-create one schedule.
    ScheduleManager().save(Schedule(
        name="hi", prompt="p", schedule_type="interval_seconds",
        interval_seconds=60,
    ))
    r = api.list_schedules()
    assert r.get("status") == "ok"
    assert len(r["schedules"]) == 1
    assert r["daemon"] in ("running", "stopped")


def test_api_delete_schedule_missing_returns_error(tmp_home, monkeypatch):
    from gui.api import Api
    api = Api()
    r = api.delete_schedule("nonexistent-id")
    assert "error" in r


def test_api_get_schedule_history_missing_returns_error():
    from gui.api import Api
    api = Api()
    r = api.get_schedule_history("nonexistent-id")
    assert "error" in r
