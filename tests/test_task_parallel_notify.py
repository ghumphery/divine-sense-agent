# -*- coding: utf-8 -*-
"""v0.9.108.0 — task parallel cap + notify hook + monitor + limits tests."""
import time

import pytest


@pytest.fixture()
def tr():
    import src.agent.task_runner as m
    m.reset_tasks()
    m.set_notify_callback(None)
    m.stop_monitor()
    yield m
    m.reset_tasks()
    m.set_notify_callback(None)
    m.stop_monitor()


class TestTaskMaxParallel:
    def test_unset_default_4(self, monkeypatch):
        from src.mcp_client.host import _task_max_parallel

        monkeypatch.delenv("QDRANT_TASK_MAX_PARALLEL", raising=False)
        assert _task_max_parallel() == 4

    @pytest.mark.parametrize("raw,want", [("7", 7), ("0", 0),
                                          ("abc", 4), ("-2", 4), (" ", 4)])
    def test_matrix(self, monkeypatch, raw, want):
        from src.mcp_client.host import _task_max_parallel

        monkeypatch.setenv("QDRANT_TASK_MAX_PARALLEL", raw)
        assert _task_max_parallel() == want


class TestRunOnceCap:
    def _host(self):
        from src.mcp_client.host import TaskLocalHost

        return TaskLocalHost(mcp_manager=None)

    def test_cap_rejects_at_spawn_site(self, monkeypatch, tr):
        monkeypatch.delenv("QDRANT_TASK_MAX_PARALLEL", raising=False)
        h = self._host()
        ids = [tr.begin_task("task", f"t{i}") for i in range(4)]
        out = h._run_once({"prompt": "x", "title": "overflow"})
        assert "error" in out
        assert out["cap"] == 4 and out["running_count"] == 4
        assert "list_tasks" in out["error"]
        # registry unchanged (no 5th task spawned)
        assert tr.list_tasks(status="running",
                             limit=50)["running"] == 4

    def test_under_cap_submits_without_spawning(self, monkeypatch, tr):
        import src.mcp_client.host as hostmod

        monkeypatch.setenv("QDRANT_TASK_MAX_PARALLEL", "2")
        h = self._host()

        class _FakeThread:
            started = False

            def __init__(self, *a, **kw):
                pass

            def start(self):
                _FakeThread.started = True  # never allowed to run

        real = hostmod.threading.Thread
        monkeypatch.setattr(hostmod.threading, "Thread", _FakeThread)
        out = h._run_once({"prompt": "hello job", "title": "j1"})
        assert out.get("ok") is True and out.get("status") == "running"
        assert _FakeThread.started is True  # thread object created,
        # but start() is inert → no real agent build in tests.

    def test_zero_is_unlimited(self, monkeypatch, tr, tmp_path):
        monkeypatch.setenv("QDRANT_TASK_MAX_PARALLEL", "0")
        import src.mcp_client.host as hostmod

        h = self._host()

        class _FakeThread:
            def __init__(self, *a, **kw):
                pass

            def start(self):
                pass

        monkeypatch.setattr(hostmod.threading, "Thread", _FakeThread)
        for i in range(6):
            out = h._run_once({"prompt": "p", "title": f"t{i}"})
            assert out.get("ok") is True


class TestNotifyHook:
    def test_terminal_event_fires(self, tr):
        seen = []
        tr.set_notify_callback(lambda ev: seen.append(ev))
        tid = tr.begin_task("task", "job A")
        tr.end_task(tid, tr.DONE, answer_chars=123)
        time.sleep(0.05)
        assert len(seen) == 1
        ev = seen[0]
        assert ev["kind"] == "terminal"
        assert ev["status"] == "done"
        assert ev["answer_chars"] == 123
        assert ev["title"] == "job A"

    def test_no_callback_silent(self, tr):
        tr.set_notify_callback(None)
        tid = tr.begin_task("task", "quiet")
        tr.end_task(tid, tr.FAILED, error="boom")  # must not raise

    def test_callback_exception_swallowed(self, tr):
        def _bad(ev):
            raise RuntimeError("cb down")

        tr.set_notify_callback(_bad)
        tid = tr.begin_task("task", "cb")
        tr.end_task(tid, tr.DONE)  # swallowed → no raise

    def test_failed_status_notified(self, tr):
        seen = []
        tr.set_notify_callback(lambda ev: seen.append(ev))
        tid = tr.begin_task("task", "fail job")
        tr.end_task(tid, tr.FAILED, error="x died")
        ev = seen[0]
        assert ev["status"] == "failed"


class TestProgressMonitor:
    def test_monitor_pushes_progress(self, tr):
        seen = []
        tr.set_notify_callback(lambda ev: seen.append(ev))
        tr.begin_task("task", "long job")
        assert tr.start_monitor(1) is True
        time.sleep(2.6)
        progress = [e for e in seen if e.get("kind") == "progress"]
        assert progress and progress[0]["running_count"] >= 1
        assert "long job" in progress[0]["text"]

    def test_zero_interval_disabled(self, tr):
        assert tr.start_monitor(0) is False
        assert tr.start_monitor("abc") is False

    def test_start_idempotent(self, tr):
        assert tr.start_monitor(60) is True
        assert tr.start_monitor(60) is True  # second call = already alive


class TestRuntimeLimits:
    def _bare_agent(self):
        from src.agent.agent import Agent

        a = object.__new__(Agent)
        a._provider = "openrouter"
        a._model = "test-model"
        a._client = object()
        a.last_provider = "openrouter"
        return a

    def test_status_has_limits(self, monkeypatch):
        monkeypatch.delenv("MCP_ACTIVE_SERVERS", raising=False)
        a = self._bare_agent()
        out = a._runtime_ops("status")
        assert out["ok"] is True
        lim = out["limits"]
        assert lim["max_tool_iterations"] >= 1
        assert lim["tool_call_watchdog_s"] >= 0
        assert lim["tool_result_cap_chars"] > 0
        assert "max_concurrent_turns" in lim
        assert "turn_wait_seconds" in lim
        assert lim["task_max_parallel"] >= 0
        assert lim["tasks_running"] >= 0
        assert "limits_note" in out

    def test_unknown_op_unchanged(self):
        a = self._bare_agent()
        assert "error" in a._runtime_ops("nope")


class TestGuiWiringSource:
    def test_wiring_present(self):
        import io
        from pathlib import Path

        src = Path("gui/api.py").read_text(encoding="utf-8")
        assert "def _wire_task_notify" in src
        assert "task_runner.set_notify_callback(_cb)" in src
        assert "task_runner.start_monitor(interval)" in src

    def test_fail_closed_gate(self):
        from pathlib import Path

        src = Path("gui/api.py").read_text(encoding="utf-8")
        assert 'os.environ.get("QDRANT_TASK_NOTIFY"' in src
        assert 'in ("on", "1", "true", "yes")' in src
        assert "not token or not ids" in src