"""v0.9.22.0 — scheduler daemon auto-resume at boot.

Root cause fixed: the scheduler daemon is a THREAD inside the app
process; schedules persist (schedules/*.json) but every GUI/web
restart killed the daemon, and the v0.8.6.3 auto-start only fired
when the LLM ADDED a schedule. Live evidence: the「下午2點股市異常
分析」cron (next_fire_at=2026-09-07T14:00:00) sat at fire_count=0
because the daemon's last "started" line was 11:37 — the user had
restarted the web UI to apply the openai-compat provider config and
nothing resumed the daemon for the 14:00 fire.

Fix: Api._prewarm_agent() (the first get_status poll) now calls
_resume_scheduler_daemon(agent) — resumes the daemon whenever at
least one schedule is enabled + lifecycle-active. The missed
next_fire_at catches up on the first tick (tick_once fires anything
due in the past). Opt out: QDRANT_SCHEDULER_AUTORESUME=off.

Covers: resume-on-enabled / skip-when-none / skip-when-env-off /
skip-when-status-failed / non-fatal on start_scheduler failure /
prewarm integration + prewarm-failure isolation.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class _StubAgent:
    def __init__(self, start_raises: Exception | None = None):
        self.started = False
        self.start_calls = 0
        self._start_raises = start_raises

    @property
    def mcp_manager(self):  # prewarm touches this
        return MagicMock()

    @property
    def client(self):  # prewarm touches this
        return MagicMock()

    def start_scheduler(self):
        if self._start_raises:
            raise self._start_raises
        self.started = True
        self.start_calls += 1


@pytest.fixture()
def api():
    from gui.api import Api

    return Api.__new__(Api)


def _patch_list(monkeypatch, schedules: list):
    from src.agent import scheduler as sched_mod

    monkeypatch.setattr(
        sched_mod.ScheduleManager, "list",
        lambda self: schedules,
    )


def _sched(name="daily", enabled=True, status=None):
    return SimpleNamespace(
        id=f"id-{name}", name=name, enabled=enabled, status=status,
    )


class TestResumeSchedulerDaemon:
    def test_resumes_when_enabled_schedule_exists(
        self, api, monkeypatch,
    ):
        monkeypatch.delenv("QDRANT_SCHEDULER_AUTORESUME", raising=False)
        _patch_list(monkeypatch, [_sched("下午2點股市異常分析")])
        agent = _StubAgent()
        api._resume_scheduler_daemon(agent)
        assert agent.started is True

    def test_skips_when_no_schedules(self, api, monkeypatch):
        monkeypatch.delenv("QDRANT_SCHEDULER_AUTORESUME", raising=False)
        _patch_list(monkeypatch, [])
        agent = _StubAgent()
        api._resume_scheduler_daemon(agent)
        assert agent.started is False

    def test_skips_when_all_disabled(self, api, monkeypatch):
        monkeypatch.delenv("QDRANT_SCHEDULER_AUTORESUME", raising=False)
        _patch_list(monkeypatch, [_sched(enabled=False)])
        agent = _StubAgent()
        api._resume_scheduler_daemon(agent)
        assert agent.started is False

    def test_skips_when_status_failed(self, api, monkeypatch):
        """enabled=True but lifecycle status failed/expired/exhausted
        → not resumable (tick would skip it too)."""
        monkeypatch.delenv("QDRANT_SCHEDULER_AUTORESUME", raising=False)
        _patch_list(monkeypatch, [
            _sched("broken", enabled=True, status="failed"),
            _sched("expired", enabled=True, status="expired"),
        ])
        agent = _StubAgent()
        api._resume_scheduler_daemon(agent)
        assert agent.started is False

    def test_env_off_disables(self, api, monkeypatch):
        monkeypatch.setenv("QDRANT_SCHEDULER_AUTORESUME", "off")
        _patch_list(monkeypatch, [_sched()])
        agent = _StubAgent()
        api._resume_scheduler_daemon(agent)
        assert agent.started is False

    def test_env_off_case_insensitive(self, api, monkeypatch):
        monkeypatch.setenv("QDRANT_SCHEDULER_AUTORESUME", "OFF")
        _patch_list(monkeypatch, [_sched()])
        agent = _StubAgent()
        api._resume_scheduler_daemon(agent)
        assert agent.started is False

    def test_env_on_resumes(self, api, monkeypatch):
        monkeypatch.setenv("QDRANT_SCHEDULER_AUTORESUME", "on")
        _patch_list(monkeypatch, [_sched()])
        agent = _StubAgent()
        api._resume_scheduler_daemon(agent)
        assert agent.started is True

    def test_start_scheduler_failure_swallowed(self, api, monkeypatch):
        """A daemon start failure must NEVER propagate (non-fatal —
        the GUI Schedules tab button remains the manual path)."""
        monkeypatch.delenv("QDRANT_SCHEDULER_AUTORESUME", raising=False)
        _patch_list(monkeypatch, [_sched()])
        agent = _StubAgent(start_raises=RuntimeError("boom"))
        api._resume_scheduler_daemon(agent)  # must not raise
        assert agent.started is False


# ─── prewarm integration ─────────────────────────────────────


class TestPrewarmIntegration:
    def test_pwarm_agent_calls_resume(self, api, monkeypatch):
        """_prewarm_agent must invoke _resume_scheduler_daemon(agent)
        on the success path (the first get_status poll = boot)."""
        agent = _StubAgent()
        api._get_agent = lambda: agent
        calls: list = []
        monkeypatch.setattr(
            api, "_resume_scheduler_daemon",
            # staticmethod: prevent instance-attr function binding
            # from slipping `self` in as the first arg.
            staticmethod(lambda a: calls.append(a)),
        )
        monkeypatch.delenv("QDRANT_SCHEDULER_AUTORESUME", raising=False)
        api._prewarm_agent()
        assert calls == [agent]

    def test_resume_failure_does_not_break_prewarm(
        self, api, monkeypatch,
    ):
        """_get_agent works, but the resume path raises — prewarm
        itself must still complete without raising (the try/except
        wrapper around the prewarm body covers it)."""
        agent = _StubAgent()
        api._get_agent = lambda: agent
        monkeypatch.delenv("QDRANT_SCHEDULER_AUTORESUME", raising=False)

        def boom(_a):
            raise RuntimeError("resume exploded")

        monkeypatch.setattr(
            api, "_resume_scheduler_daemon", staticmethod(boom),
        )
        api._prewarm_agent()  # must not raise
        assert agent.started is False  # resume did not run

    def test_resume_failure_inside_method_swallowed(
        self, api, monkeypatch,
    ):
        """Even without the prewarm wrapper, _resume_scheduler_daemon
        swallows its own ScheduleManager failures (e.g. corrupt
        schedules dir)."""
        monkeypatch.delenv("QDRANT_SCHEDULER_AUTORESUME", raising=False)
        from src.agent import scheduler as sched_mod

        monkeypatch.setattr(
            sched_mod.ScheduleManager, "list",
            lambda self: (_ for _ in ()).throw(RuntimeError("corrupt")),
        )
        agent = _StubAgent()
        api._resume_scheduler_daemon(agent)  # must not raise
        assert agent.started is False