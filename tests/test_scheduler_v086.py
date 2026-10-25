"""Tests for the v0.8.6 scheduler additions.

Covers:
* ``Schedule.last_error`` + ``last_error_at`` round-trip
* ``ScheduleManager.set_enabled`` / ``clear_error`` /
  ``record_error`` / ``find_by_name``
* ``Scheduler.tick_once`` writes ``last_error`` when ``on_fire``
  raises (was: silent log-only in v0.8.5)
* ``Schedule.from_dict`` is backward-compatible with v0.8.5
  JSON that lacks ``last_error``.
"""
from __future__ import annotations

import pytest


@pytest.fixture()
def tmp_home(tmp_path, monkeypatch):
    monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path))
    yield tmp_path


class TestScheduleLastError:
    def test_defaults_to_none(self):
        from src.agent.scheduler import Schedule
        s = Schedule(prompt="x", interval_seconds=60)
        assert s.last_error is None
        assert s.last_error_at is None

    def test_round_trip_through_dict(self):
        from src.agent.scheduler import Schedule
        s = Schedule(prompt="x", interval_seconds=60)
        s.last_error = "boom"
        s.last_error_at = "2026-08-29T01:00:00"
        d = s.to_dict()
        assert d["last_error"] == "boom"
        assert d["last_error_at"] == "2026-08-29T01:00:00"
        # Round-trip back.
        s2 = Schedule.from_dict(d)
        assert s2.last_error == "boom"
        assert s2.last_error_at == "2026-08-29T01:00:00"

    def test_from_dict_backward_compat(self):
        """v0.8.5 JSON files lack last_error — must not crash."""
        from src.agent.scheduler import Schedule
        legacy = {
            "id": "abc123",
            "name": "legacy",
            "prompt": "x",
            "schedule_type": "interval_seconds",
            "interval_seconds": 60,
            "enabled": True,
            "fire_count": 3,
        }
        s = Schedule.from_dict(legacy)
        assert s.last_error is None
        assert s.last_error_at is None
        assert s.name == "legacy"
        assert s.fire_count == 3


class TestManagerNewMethods:
    def test_set_enabled_round_trip(self, tmp_home):
        from src.agent.scheduler import Schedule, ScheduleManager
        mgr = ScheduleManager(root=tmp_home / "schedules")
        s = mgr.save(Schedule(prompt="x", interval_seconds=60))
        assert s.enabled is True
        s2 = mgr.set_enabled(s.id, False)
        assert s2.enabled is False
        fresh = mgr.get(s.id)
        assert fresh.enabled is False
        s3 = mgr.set_enabled(s.id, True)
        assert s3.enabled is True

    def test_set_enabled_missing_returns_none(self, tmp_home):
        from src.agent.scheduler import ScheduleManager
        mgr = ScheduleManager(root=tmp_home / "schedules")
        assert mgr.set_enabled("nope", False) is None

    def test_record_error_persists(self, tmp_home):
        from src.agent.scheduler import Schedule, ScheduleManager
        mgr = ScheduleManager(root=tmp_home / "schedules")
        s = mgr.save(Schedule(prompt="x", interval_seconds=60))
        try:
            raise RuntimeError("synthetic boom")
        except RuntimeError as e:
            mgr.record_error(s.id, e)
        fresh = mgr.get(s.id)
        assert "synthetic boom" in fresh.last_error
        assert fresh.last_error_at is not None

    def test_record_error_missing_returns_none(self, tmp_home):
        from src.agent.scheduler import ScheduleManager
        mgr = ScheduleManager(root=tmp_home / "schedules")
        try:
            raise RuntimeError("boom")
        except RuntimeError as e:
            assert mgr.record_error("nope", e) is None

    def test_clear_error_wipes(self, tmp_home):
        from src.agent.scheduler import Schedule, ScheduleManager
        mgr = ScheduleManager(root=tmp_home / "schedules")
        s = mgr.save(Schedule(prompt="x", interval_seconds=60))
        try:
            raise RuntimeError("boom")
        except RuntimeError as e:
            mgr.record_error(s.id, e)
        assert mgr.get(s.id).last_error is not None
        cleared = mgr.clear_error(s.id)
        assert cleared.last_error is None
        assert cleared.last_error_at is None

    def test_clear_error_missing_returns_none(self, tmp_home):
        from src.agent.scheduler import ScheduleManager
        mgr = ScheduleManager(root=tmp_home / "schedules")
        assert mgr.clear_error("nope") is None

    def test_find_by_name_hit(self, tmp_home):
        from src.agent.scheduler import Schedule, ScheduleManager
        mgr = ScheduleManager(root=tmp_home / "schedules")
        s = mgr.save(Schedule(
            prompt="x", interval_seconds=60, name="morning",
        ))
        found = mgr.find_by_name("morning")
        assert found is not None
        assert found.id == s.id

    def test_find_by_name_miss(self, tmp_home):
        from src.agent.scheduler import ScheduleManager
        mgr = ScheduleManager(root=tmp_home / "schedules")
        assert mgr.find_by_name("nope") is None

    def test_find_by_name_blank_returns_none(self, tmp_home):
        from src.agent.scheduler import ScheduleManager
        mgr = ScheduleManager(root=tmp_home / "schedules")
        assert mgr.find_by_name("") is None
        assert mgr.find_by_name(None) is None


class TestTickOnceRecordsError:
    def test_on_fire_exception_writes_last_error(self, tmp_home):
        """v0.8.6 fix: tick_once records the failure on the
        schedule so get_scheduled_task_errors / GUI can show it.
        """
        from datetime import datetime
        from src.agent.scheduler import (
            Schedule, ScheduleManager, Scheduler,
        )
        mgr = ScheduleManager(root=tmp_home / "schedules")
        s = Schedule(prompt="x", interval_seconds=60)
        # Patch compute_next_fire to return a date in the past
        # so the daemon picks the schedule up immediately. We
        # can't pass next_fire_at to mgr.save() because save()
        # recomputes it; the patch is the cleanest way.
        s.compute_next_fire = lambda after=None: datetime.fromisoformat(
            "2020-01-01T00:00:00",
        )
        s = mgr.save(s)
        # Sanity check — the saved schedule must be due.
        assert mgr.get(s.id).next_fire_at < datetime.now().isoformat()

        def boom(_sched):
            raise RuntimeError("simulated failure")

        sched = Scheduler(manager=mgr, on_fire=boom)
        fired = sched.tick_once()
        assert fired == []  # failed → not counted as fired
        fresh = mgr.get(s.id)
        assert fresh.last_error is not None
        assert "simulated failure" in fresh.last_error
        assert fresh.last_error_at is not None
