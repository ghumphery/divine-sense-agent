"""Regression for v0.8.6.3: LLM-built schedule silently never fires.

User report (2026-08-31):
  使用 llm 建立 排程,建立後 排程 不會按時執行

Root cause: the scheduler daemon was only ever started by an
explicit ``Agent.start_scheduler()`` call (GUI button / CLI
``schedules daemon`` subcommand). When the LLM built a schedule
via ``add_scheduled_task`` in the chat, the JSON file was written
but the daemon thread was never spun up — so the schedule sat
forever with ``next_fire_at`` in the past and never fired.

Fix: ``SchedulerLocalHost._add`` invokes an ``auto_start``
callback after a successful save. ``McpManager`` holds the
callback (set by ``Agent`` lazily) and pushes it down to the
``SchedulerLocalHost`` via ``set_auto_start_callback``. The
callback calls ``Agent.start_scheduler()``, which is idempotent
(``Scheduler.start()`` no-ops if the thread is alive).

These tests pin every link of that chain.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest


# ─── SchedulerLocalHost._add auto-start hook ────────────────


class TestSchedulerLocalHostAutoStart:
    def test_auto_start_default_is_none(self, tmp_path, monkeypatch):
        monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path))
        from src.mcp_client.host import SchedulerLocalHost
        h = SchedulerLocalHost()
        assert h._auto_start is None

    def test_attach_auto_start_stores_callback(self, tmp_path, monkeypatch):
        monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path))
        from src.mcp_client.host import SchedulerLocalHost
        h = SchedulerLocalHost()
        cb = MagicMock()
        h.attach_auto_start(cb)
        assert h._auto_start is cb

    def test_add_invokes_callback_after_save(self, tmp_path, monkeypatch):
        monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path))
        from src.mcp_client.host import SchedulerLocalHost
        h = SchedulerLocalHost()
        cb = MagicMock()
        h.attach_auto_start(cb)
        tools = {t.name: t for t in h.list_tools()}
        tools["add_scheduled_task"]({
            "prompt": "x", "interval_seconds": 60,
        })
        assert cb.call_count == 1

    def test_add_does_not_invoke_when_no_callback(
        self, tmp_path, monkeypatch,
    ):
        """If nobody wired the hook, _add still works — it just
        doesn't try to start a daemon."""
        monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path))
        from src.mcp_client.host import SchedulerLocalHost
        h = SchedulerLocalHost()
        tools = {t.name: t for t in h.list_tools()}
        r = tools["add_scheduled_task"]({
            "prompt": "x", "interval_seconds": 60,
        })
        assert "id" in r.text

    def test_add_swallows_callback_exceptions(self, tmp_path, monkeypatch):
        """A misconfigured callback must not break the success
        response — the schedule is already saved; user can start
        the daemon manually if auto-start fails."""
        monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path))
        from src.mcp_client.host import SchedulerLocalHost
        h = SchedulerLocalHost()

        def boom():
            raise RuntimeError("simulated")
        h.attach_auto_start(boom)
        tools = {t.name: t for t in h.list_tools()}
        r = tools["add_scheduled_task"]({
            "prompt": "x", "interval_seconds": 60,
        })
        assert "id" in r.text  # success despite callback raise

    def test_callback_fires_only_on_success_not_on_validation_error(
        self, tmp_path, monkeypatch,
    ):
        """Validation errors must short-circuit BEFORE the
        callback fires — we don't want to spin up a daemon for a
        schedule we never saved."""
        monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path))
        from src.mcp_client.host import SchedulerLocalHost
        h = SchedulerLocalHost()
        cb = MagicMock()
        h.attach_auto_start(cb)
        tools = {t.name: t for t in h.list_tools()}
        r = tools["add_scheduled_task"]({})  # no prompt → error
        assert "error" in r.text.lower() or "must" in r.text.lower()
        assert cb.call_count == 0

    def test_attach_manager_picks_up_callback_from_mcp_manager(
        self, tmp_path, monkeypatch,
    ):
        """If a McpManager with auto_start_callback is passed to
        attach_manager(), the callback is captured even if
        attach_auto_start wasn't called explicitly."""
        monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path))
        from src.mcp_client.host import SchedulerLocalHost
        from src.mcp_client import McpManager
        h = SchedulerLocalHost()
        cb = MagicMock()
        mgr = McpManager()
        mgr.auto_start_callback = cb
        h.attach_manager(mgr)
        assert h._auto_start is cb


# ─── McpManager.set_auto_start_callback wiring ──────────────


class TestMcpManagerAutoStartSetter:
    def test_setter_stores_callback(self):
        from src.mcp_client import McpManager
        mgr = McpManager()
        cb = MagicMock()
        mgr.set_auto_start_callback(cb)
        assert mgr.auto_start_callback is cb

    def test_setter_pushes_to_scheduler_host(self):
        from src.mcp_client import McpManager
        from src.mcp_client.host import SchedulerLocalHost
        mgr = McpManager()
        # Stub the host so we don't have to build a real one.
        sched = MagicMock(spec=SchedulerLocalHost)
        mgr._scheduler_local_host = sched
        cb = MagicMock()
        mgr.set_auto_start_callback(cb)
        sched.attach_auto_start.assert_called_once_with(cb)

    def test_setter_none_clears_without_calling_host(self):
        """Passing None means "no auto-start" — must NOT raise
        even if a host reference exists."""
        from src.mcp_client import McpManager
        from src.mcp_client.host import SchedulerLocalHost
        mgr = McpManager()
        sched = MagicMock(spec=SchedulerLocalHost)
        mgr._scheduler_local_host = sched
        mgr.set_auto_start_callback(None)
        assert mgr.auto_start_callback is None
        sched.attach_auto_start.assert_not_called()

    def test_setter_swallows_host_errors(self):
        """If the host's attach_auto_start raises, the setter
        still stores the callback so the Agent at least has it
        for direct use."""
        from src.mcp_client import McpManager
        from src.mcp_client.host import SchedulerLocalHost
        mgr = McpManager()
        sched = MagicMock(spec=SchedulerLocalHost)
        sched.attach_auto_start.side_effect = RuntimeError("boom")
        mgr._scheduler_local_host = sched
        cb = MagicMock()
        # Must not raise.
        mgr.set_auto_start_callback(cb)
        assert mgr.auto_start_callback is cb

    def test_from_config_registers_scheduler_host_reference(self):
        """The McpManager produced by from_config must keep a
        reference to the in-process SchedulerLocalHost so the
        Agent can wire its callback AFTER the manager is built."""
        from src.mcp_client import McpManager
        from src.mcp_client.host import SchedulerLocalHost
        mgr = McpManager.from_config(include_local_hosts=True)
        assert mgr._scheduler_local_host is not None
        assert isinstance(mgr._scheduler_local_host, SchedulerLocalHost)


# ─── End-to-end: Agent wires callback on first mcp_manager ──


class TestAgentAutoStartWiring:
    def test_first_mcp_manager_access_sets_callback(self, tmp_path, monkeypatch):
        monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path))
        from src.agent.agent import Agent
        agent = Agent()
        agent._provider = "stub"
        agent._model = "stub"
        # Trigger lazy mcp_manager.
        mgr = agent.mcp_manager
        assert mgr.auto_start_callback is not None
        # It should be the bound method.
        assert mgr.auto_start_callback.__self__ is agent

    def test_auto_start_callback_actually_starts_daemon(
        self, tmp_path, monkeypatch,
    ):
        """End-to-end: invoking the callback spins up the daemon
        thread; the daemon stays alive until shutdown."""
        monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path))
        from src.agent.agent import Agent
        agent = Agent()
        agent._provider = "stub"
        agent._model = "stub"
        cb = agent.mcp_manager.auto_start_callback
        cb()
        sch = agent.scheduler
        assert sch.is_running() is True
        try:
            agent.stop_scheduler()
            # Give the thread a moment to die.
            time.sleep(0.2)
            assert agent.scheduler.is_running() is False
        except Exception:
            pass

    def test_auto_start_is_idempotent(self, tmp_path, monkeypatch):
        """Multiple invocations are safe — Scheduler.start()
        no-ops when the thread is alive."""
        monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path))
        from src.agent.agent import Agent
        agent = Agent()
        agent._provider = "stub"
        agent._model = "stub"
        cb = agent.mcp_manager.auto_start_callback
        cb()
        first_thread = agent.scheduler._thread
        cb()
        cb()
        # Same thread object → no thread leak.
        assert agent.scheduler._thread is first_thread
        try:
            agent.stop_scheduler()
        except Exception:
            pass

    def test_callback_swallows_scheduler_start_exceptions(
        self, tmp_path, monkeypatch,
    ):
        """If ``start_scheduler()`` raises (e.g. due to a
        misconfigured ScheduleManager), the callback must not
        propagate — the schedule is already on disk; the user
        can recover via the GUI."""
        monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path))
        from src.agent.agent import Agent
        agent = Agent()
        agent._provider = "stub"
        agent._model = "stub"
        cb = agent.mcp_manager.auto_start_callback
        # Force start_scheduler to raise by stubbing the
        # scheduler property to a broken object.
        class BrokenScheduler:
            def start(self):
                raise RuntimeError("simulated")
            def is_running(self):
                return False
        agent._scheduler = BrokenScheduler()
        # Must not raise.
        cb()
