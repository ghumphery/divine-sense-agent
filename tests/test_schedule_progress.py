"""Tests for v0.8.7+ Schedule progress / lifecycle features.

Covers:
  * ``max_fires`` cap → status="exhausted", compute_next_fire → None
  * ``expires_at`` cap → status="expired", compute_next_fire → None
  * ``expires_at`` in the future → schedule still fires
  * ``set_max_fires`` / ``set_expires_at`` setters with validation
  * ``get_progress`` shape + lifecycle fields + relative-time math
  * ``list_runs`` reads history tail + truncates long answers
  * One-shot with ``fire_count > 0`` auto-disables via
    ``compute_next_fire`` (so tick_once phase 1+2 cleans up)

All tests use a tmp ``QDRANT_RAG_HOME`` so they don't pollute the
real ``~/.qdrant-rag-agent/schedules/`` directory.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture()
def tmp_home(tmp_path, monkeypatch):
    monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path))
    yield tmp_path


# ─── max_fires cap ──────────────────────────────────────────────


def test_max_fires_cap_auto_disables(tmp_home):
    from src.agent.scheduler import Schedule, ScheduleManager

    mgr = ScheduleManager()
    s = Schedule(
        name="cap", prompt="p", schedule_type="interval_seconds",
        interval_seconds=60, max_fires=3,
    )
    mgr.save(s)
    s.fire_count = 3
    mgr.save(s)
    loaded = mgr.get(s.id)
    nxt = loaded.compute_next_fire()
    assert nxt is None
    assert loaded.status == "exhausted"
    assert loaded.enabled is False


def test_max_fires_one_shot_flips_to_exhausted(tmp_home):
    """A once_at with max_fires=1 that fires once flips to
    status="exhausted" (not just disabled)."""
    from src.agent.scheduler import Schedule, ScheduleManager

    mgr = ScheduleManager()
    s = Schedule(
        name="one", prompt="p", schedule_type="once_at",
        once_at=(datetime.now() + timedelta(seconds=10)).isoformat(),
        max_fires=1,
    )
    mgr.save(s)
    s.fire_count = 1
    mgr.save(s)
    loaded = mgr.get(s.id)
    nxt = loaded.compute_next_fire()
    assert nxt is None
    assert loaded.status == "exhausted"


def test_max_fires_unlimited_keeps_running(tmp_home):
    from src.agent.scheduler import Schedule, ScheduleManager

    mgr = ScheduleManager()
    s = Schedule(
        name="inf", prompt="p", schedule_type="interval_seconds",
        interval_seconds=60, max_fires=None,
    )
    s.fire_count = 9999  # large but uncapped
    mgr.save(s)
    nxt = mgr.get(s.id).compute_next_fire()
    assert nxt is not None
    assert mgr.get(s.id).enabled is True


def test_set_max_fires_validates_positive(tmp_home):
    from src.agent.scheduler import Schedule, ScheduleManager

    mgr = ScheduleManager()
    s = Schedule(name="x", prompt="p", schedule_type="interval_seconds",
                 interval_seconds=60)
    mgr.save(s)
    with pytest.raises(ValueError):
        mgr.set_max_fires(s.id, 0)
    with pytest.raises(ValueError):
        mgr.set_max_fires(s.id, -1)


def test_set_max_fires_none_clears_cap(tmp_home):
    from src.agent.scheduler import Schedule, ScheduleManager

    mgr = ScheduleManager()
    s = Schedule(name="x", prompt="p", schedule_type="interval_seconds",
                 interval_seconds=60, max_fires=5)
    mgr.save(s)
    mgr.set_max_fires(s.id, None)
    assert mgr.get(s.id).max_fires is None


# ─── expires_at cap ─────────────────────────────────────────────


def test_expires_at_in_past_auto_disables(tmp_home):
    from src.agent.scheduler import Schedule, ScheduleManager

    mgr = ScheduleManager()
    s = Schedule(
        name="past", prompt="p", schedule_type="interval_seconds",
        interval_seconds=60,
        expires_at=(datetime.now() - timedelta(hours=1)).isoformat(),
    )
    mgr.save(s)
    loaded = mgr.get(s.id)
    nxt = loaded.compute_next_fire()
    assert nxt is None
    assert loaded.status == "expired"
    assert loaded.enabled is False


def test_expires_at_in_future_keeps_running(tmp_home):
    from src.agent.scheduler import Schedule, ScheduleManager

    mgr = ScheduleManager()
    s = Schedule(
        name="future", prompt="p", schedule_type="interval_seconds",
        interval_seconds=60,
        expires_at=(datetime.now() + timedelta(hours=24)).isoformat(),
    )
    mgr.save(s)
    nxt = mgr.get(s.id).compute_next_fire()
    assert nxt is not None


def test_set_expires_at_validates_iso(tmp_home):
    from src.agent.scheduler import Schedule, ScheduleManager

    mgr = ScheduleManager()
    s = Schedule(name="x", prompt="p", schedule_type="interval_seconds",
                 interval_seconds=60)
    mgr.save(s)
    with pytest.raises(ValueError):
        mgr.set_expires_at(s.id, "not-a-date")
    # ``Z`` suffix accepted (normalised to +00:00).
    mgr.set_expires_at(s.id, "2026-12-31T23:59:59Z")
    assert mgr.get(s.id).expires_at == "2026-12-31T23:59:59Z"


def test_expires_at_caps_one_shot_fire_time(tmp_home):
    """A once_at whose fire time is past expires_at should be
    marked expired even though fire_count is still 0."""
    from src.agent.scheduler import Schedule, ScheduleManager

    mgr = ScheduleManager()
    s = Schedule(
        name="late", prompt="p", schedule_type="once_at",
        once_at=(datetime.now() + timedelta(days=2)).isoformat(),
        expires_at=(datetime.now() + timedelta(days=1)).isoformat(),
    )
    mgr.save(s)
    loaded = mgr.get(s.id)
    nxt = loaded.compute_next_fire()
    assert nxt is None
    assert loaded.status == "expired"

# ─── get_progress shape ─────────────────────────────────────────


def test_get_progress_shape(tmp_home):
    from src.agent.scheduler import Schedule, ScheduleManager

    mgr = ScheduleManager()
    s = Schedule(name="prog", prompt="hello world",
                 schedule_type="interval_seconds",
                 interval_seconds=120, max_fires=10)
    s.fire_count = 4
    mgr.save(s)
    prog = mgr.get_progress(s.id)
    assert prog is not None
    for key in (
        "id", "name", "prompt_preview", "schedule_type", "enabled",
        "status", "fire_count", "max_fires", "fires_remaining",
        "last_fire_at", "next_fire_at", "seconds_until_next_fire",
        "expires_at", "seconds_until_expiry",
        "last_error", "last_error_at", "last_session_id", "recent_runs",
    ):
        assert key in prog, f"missing key {key!r}"
    assert prog["fire_count"] == 4
    assert prog["max_fires"] == 10
    assert prog["fires_remaining"] == 6
    assert prog["prompt_preview"] == "hello world"


def test_get_progress_fires_remaining_caps_at_zero(tmp_home):
    """Past max_fires → fires_remaining = 0 (not negative)."""
    from src.agent.scheduler import Schedule, ScheduleManager

    mgr = ScheduleManager()
    s = Schedule(name="over", prompt="p",
                 schedule_type="interval_seconds",
                 interval_seconds=60, max_fires=2)
    s.fire_count = 5  # over the cap
    mgr.save(s)
    prog = mgr.get_progress(s.id)
    assert prog["fires_remaining"] == 0


def test_get_progress_seconds_until_next_fire(tmp_home):
    from src.agent.scheduler import Schedule, ScheduleManager

    mgr = ScheduleManager()
    s = Schedule(name="soon", prompt="p",
                 schedule_type="interval_seconds",
                 interval_seconds=60)
    mgr.save(s)
    prog = mgr.get_progress(s.id)
    secs = prog["seconds_until_next_fire"]
    assert secs is not None
    # Should be approximately 60 — allow ±5s for clock jitter.
    assert 55 <= secs <= 65


def test_get_progress_unknown_sid_returns_none(tmp_home):
    from src.agent.scheduler import ScheduleManager

    assert ScheduleManager().get_progress("does-not-exist") is None


# ─── list_runs ──────────────────────────────────────────────────


def test_list_runs_empty_when_no_history(tmp_home):
    from src.agent.scheduler import Schedule, ScheduleManager

    mgr = ScheduleManager()
    s = Schedule(name="empty", prompt="p",
                 schedule_type="interval_seconds",
                 interval_seconds=60)
    mgr.save(s)
    assert mgr.list_runs(s.id, limit=5) == []


def test_list_runs_reads_tail(tmp_home):
    from src.agent.scheduler import Schedule, ScheduleManager

    mgr = ScheduleManager()
    s = Schedule(name="hist", prompt="p",
                 schedule_type="interval_seconds",
                 interval_seconds=60)
    mgr.save(s)
    # Append 12 fake rows directly to history.
    for i in range(12):
        mgr.append_history(s.id, {
            "ts": f"2026-08-{i+1:02d}T00:00:00",
            "prompt": f"q{i}",
            "answer": (f"answer-row-{i} " * 60),  # > 500 chars
            "sources": [f"file{i}.txt"],
            "session_id": f"sched-{s.id}-{i}",
            "error": None,
        })
    runs = mgr.list_runs(s.id, limit=5)
    assert len(runs) == 5
    # We appended in order 0..11; tail is rows 7..11.
    assert runs[0]["prompt"] == "q7"
    assert runs[-1]["prompt"] == "q11"
    # Answer is truncated to 500 chars + ellipsis.
    assert runs[0]["answer"].endswith("…")
    assert len(runs[0]["answer"]) <= 502


def test_list_runs_unknown_sid_returns_none(tmp_home):
    from src.agent.scheduler import ScheduleManager

    assert ScheduleManager().list_runs("missing", limit=5) is None


# ─── backward compatibility ─────────────────────────────────────


def test_legacy_schedule_without_new_fields_still_works(tmp_home):
    """v0.8.6-era JSON (no max_fires / expires_at / status) must
    still load and run cleanly — default values must be safe."""
    from src.agent.scheduler import Schedule, ScheduleManager

    mgr = ScheduleManager()
    s = Schedule(name="legacy", prompt="p",
                 schedule_type="interval_seconds",
                 interval_seconds=60)
    # All new fields are None by default.
    assert s.max_fires is None
    assert s.expires_at is None
    assert s.status is None
    mgr.save(s)
    loaded = mgr.get(s.id)
    assert loaded.compute_next_fire() is not None  # still active
    # ``get_progress`` returns status="active" by default.
    prog = mgr.get_progress(s.id)
    assert prog["status"] == "active"


def test_one_shot_finished_auto_disables_via_compute_next_fire(tmp_home):
    """Once a one-shot has fired (``fire_count > 0``), a subsequent
    ``compute_next_fire`` call must auto-disable it. This matches
    the v0.8.6 test that ``tick_once`` used to handle in-line."""
    from src.agent.scheduler import Schedule, ScheduleManager

    mgr = ScheduleManager()
    s = Schedule(
        name="once", prompt="p", schedule_type="once_at",
        once_at=(datetime.now() + timedelta(seconds=10)).isoformat(),
    )
    mgr.save(s)
    s.fire_count = 1
    s.next_fire_at = None
    mgr.save(s)
    loaded = mgr.get(s.id)
    nxt = loaded.compute_next_fire()
    assert nxt is None
    assert loaded.enabled is False
    assert loaded.status in ("exhausted", "disabled")
