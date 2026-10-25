# -*- coding: utf-8 -*-
"""v0.9.120.0 — cooperative force-stop for background tasks.

Covers: registry stop flags (request/stop_requested/persist-after-
end for zombies/list_tasks surface/STOPPED status), the Agent tool-
loop stop check (iteration boundary, no LLM call), the telegram
wiring (in-turn stop → STOPPED row + honest reply), the task-local
host tools (stop_task dispatch) and the plan-stage wiring.
"""
from __future__ import annotations

import threading
import time

import pytest


# ── registry ──────────────────────────────────────────────────────
@pytest.fixture()
def tr(tmp_path, monkeypatch):
    monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path))
    import src.agent.task_runner as m

    m.reset_tasks()
    yield m
    m.reset_tasks()


class TestRegistryStops:
    def test_request_stop_sets_flag(self, tr):
        tid = tr.begin_task("telegram", "t")
        assert tr.stop_requested(tid) is False
        assert tr.request_stop(tid) is True
        assert tr.stop_requested(tid) is True

    def test_unknown_task_stop_true_false(self, tr):
        assert tr.request_stop("") is False
        assert tr.request_stop("nope") is False
        assert tr.stop_requested("nope") is False

    def test_flag_survives_end_task_for_zombies(self, tr):
        tid = tr.begin_task("task", "t")
        tr.end_task(tid, tr.DONE)
        # still settable + readable — a zombie turn (abandoned by a
        # timeout guard) shares the same boundary.
        assert tr.request_stop(tid) is True
        assert tr.stop_requested(tid) is True

    def test_stopped_status_whitelist(self, tr):
        tid = tr.begin_task("task", "t")
        tr.end_task(tid, tr.STOPPED)
        assert tr.get_task(tid)["status"] == "stopped"
        assert tr.list_tasks(status="stopped")["tasks"][0][
            "task_id"] == tid

    def test_list_tasks_shows_stop_requested(self, tr):
        tid = tr.begin_task("telegram", "t")
        tr.request_stop(tid)
        row = next(r for r in tr.list_tasks()["tasks"]
                   if r["task_id"] == tid)
        assert row["stop_requested"] is True

    def test_reset_clears_flags(self, tr):
        tid = tr.begin_task("task", "t")
        tr.request_stop(tid)
        tr.reset_tasks()
        assert tr.stop_requested(tid) is False


# ── agent tool-loop check ────────────────────────────────────────
class TestAgentStopCheck:
    def test_stop_at_iteration_zero_never_calls_llm(self):
        from src.agent.agent import Agent, TURN_STOPPED_TEXT

        a = Agent.__new__(Agent)
        a._stop_check = lambda: True

        def _boom(*a2, **k):
            raise AssertionError("LLM must not be called after stop")

        a._create_validated = _boom
        out = a._tool_loop([], [], 0.3, 5)
        assert out == TURN_STOPPED_TEXT
        assert a.last_tool_stopped is True

    def test_no_stop_cb_untouched(self):
        from src.agent.agent import Agent

        a = Agent.__new__(Agent)
        # the loop must not crash on agents without the attr
        assert getattr(a, "_stop_check", None) is None

    def test_setter_swaps_callback(self):
        from src.agent.agent import Agent

        a = Agent.__new__(Agent)
        a.set_stop_check(lambda: True)
        assert callable(a._stop_check)
        a.set_stop_check(None)
        assert a._stop_check is None


# ── telegram wiring ──────────────────────────────────────────────
class TestTelegramStop:
    def test_mid_turn_stop_ends_with_stopped_row(self, tr):
        import src.agent.telegram_bot as m

        class SlowAgent:
            def __init__(self):
                self.last_tool_dispatches = []
                self.last_usage = {"calls": 0}

            def chat_turn(self, **kw):
                time.sleep(5.0)
                return ("ok", "", [])

        handle = m.make_telegram_on_message(lambda: SlowAgent())
        done = {}

        def _slow_call():
            done["reply"] = handle(<YOUR_TELEGRAM_CHAT_ID>, "很長的工作")

        th = threading.Thread(target=_slow_call, daemon=True)
        th.start()
        time.sleep(0.5)  # the turn is running; find its registry row
        rows = [r for r in tr.list_tasks(status="running")["tasks"]
                if r.get("sid") == "tgbot-<YOUR_TELEGRAM_CHAT_ID>"]
        assert rows, "the turn must be registered"
        tr.request_stop(rows[-1]["task_id"])
        th.join(timeout=15)
        assert "中止" in done["reply"] or \
            "stopped" in done["reply"].lower()
        assert tr.get_task(rows[-1]["task_id"])[
            "status"] == "stopped"


# ── task host tool ───────────────────────────────────────────────
class TestStopTaskTool:
    def test_surface_and_dispatch(self, tr):
        from src.mcp_client.host import TaskLocalHost

        h = TaskLocalHost(mcp_manager=None)
        names = [t.name for t in h.list_tools()]
        assert "stop_task" in names
        tool = next(t for t in h.list_tools()
                    if t.name == "stop_task")
        assert tool.server_name == "task-local"
        assert tool.input_schema["required"] == ["task_id"]
        tid = tr.begin_task("telegram", "t")
        out = h._stop({"task_id": tid})
        assert out["ok"] is True
        assert tr.stop_requested(tid) is True

    def test_stop_dispatch_after_end_still_sets(self, tr):
        from src.mcp_client.host import TaskLocalHost

        h = TaskLocalHost(mcp_manager=None)
        tid = tr.begin_task("telegram", "t")
        tr.end_task(tid, tr.DONE)
        assert h._stop({"task_id": tid})["ok"] is True

    def test_stdio_parity_has_stop(self):
        from src.mcp_server.task_server import TaskMCPServer
        from src.mcp_client.host import TaskLocalHost

        srv = TaskMCPServer()
        spec_names = [t.name for t in srv._tool_specs()]
        host_names = [t.name for t in TaskLocalHost().list_tools()]
        assert spec_names == host_names
        assert "stop_task" in spec_names


# ── plan stage wiring ────────────────────────────────────────────
@pytest.fixture()
def temp_core(tmp_path, monkeypatch):
    monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path))
    import src.agent.skill_compiler as sc

    sc._reset_for_tests()
    sc.set_agent_factory(None)
    sc.set_plan_notify_callback(None)
    yield sc
    sc._reset_for_tests()
    sc.set_agent_factory(None)
    sc.set_plan_notify_callback(None)


class TestPlanStageStop:
    def test_stop_plan_reaches_stage_thread(self, tr, temp_core):
        sc = temp_core
        rec = sc.compile_plan(
            name="stopme", stages=[{"title": "s1", "prompt": "p"}],
            stage_timeout_s=30,
        )

        class Slow:
            def chat_turn(self, **kw):
                time.sleep(9.0)
                return ("done", "", [])

        sc.set_agent_factory(lambda: Slow())
        sc.run_plan(rec["id"])
        time.sleep(0.8)
        sid1 = rec["stages"][0]["id"]
        tid = next(s for s in
                   (sc.get_plan_status(rec["id"]) or {}).get("stages")
                   if s.get("id") == sid1).get("task_id")
        sc.stop_plan(rec["id"])  # sets the flag on the stage task
        assert tr.stop_requested(tid) is True
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if tr.get_task(tid)["status"] == "stopped":
                break
            time.sleep(0.5)
        assert tr.get_task(tid)[
            "status"] == "stopped"  # not left failed by a race