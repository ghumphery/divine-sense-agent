"""v0.9.46.0 — Phase-2 parallel-multi-task task framework.

* ``src/agent/task_runner.py`` — the process-wide background-task
  registry (begin/end/list/get, status filters, finished-cap trim,
  thread safety).
* ``TaskLocalHost`` (server ``task-local``) — run_task_once submits
  ONE background LLM turn through a fresh Agent into session
  ``task-<task_id>``; list_tasks / get_task_result observe all
  sources; manager wiring + stdio parity.
* ``Agent._system_prompt`` — the Background-tasks trigger block
  (``tools_enabled=True`` only).
* scheduler / webhook / telegram fires auto-register in the registry.
"""
from __future__ import annotations

import threading
import time

import pytest


@pytest.fixture(autouse=True)
def _fresh_registry(tmp_path, monkeypatch):
    """Isolated QDRANT_RAG_HOME + an empty process-global registry
    around every test."""
    monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path))
    from src.agent import task_runner

    task_runner.reset_tasks()
    yield
    task_runner.reset_tasks()


def _wait_status(task_id: str, deadline_s: float = 5.0):
    from src.agent import task_runner

    deadline = time.time() + deadline_s
    rec = task_runner.get_task(task_id)
    while time.time() < deadline:
        rec = task_runner.get_task(task_id)
        if rec is not None and rec["status"] != "running":
            return rec
        time.sleep(0.02)
    return rec


class _StubAgent:
    """chat_turn stub returning a canned tuple (or raising)."""

    def __init__(self, answer="背景任務完成", fail=False):
        self.answer = answer
        self.fail = fail

    def chat_turn(self, **kw):
        if self.fail:
            raise RuntimeError("provider down")
        return (self.answer, "", [])


# ─── registry ────────────────────────────────────────────────


class TestRegistry:
    def test_begin_creates_running_record(self):
        from src.agent import task_runner

        tid = task_runner.begin_task("task", "demo", sid="task-x")
        rec = task_runner.get_task(tid)
        assert rec["status"] == "running"
        assert rec["source"] == "task"
        assert rec["title"] == "demo"
        assert rec["sid"] == "task-x"
        assert rec["finished_at"] is None

    def test_end_task_stamps_done_and_lists(self):
        from src.agent import task_runner

        tid = task_runner.begin_task("scheduler", "demo")
        task_runner.end_task(tid, "done", answer_chars=42)
        rec = task_runner.get_task(tid)
        assert rec["status"] == "done"
        assert rec["answer_chars"] == 42
        assert rec["finished_at"] is not None
        out = task_runner.list_tasks()
        assert out["returned"] == 1
        assert out["tasks"][0]["task_id"] == tid

    def test_end_task_unknown_id_never_raises(self):
        from src.agent import task_runner

        task_runner.end_task("nope", "done")

    def test_attach_sid(self):
        from src.agent import task_runner

        tid = task_runner.begin_task("webhook", "w1")
        task_runner.attach_sid(tid, "webhook-w1-1")
        assert task_runner.get_task(tid)["sid"] == "webhook-w1-1"

    def test_failed_stamps_error(self):
        from src.agent import task_runner

        tid = task_runner.begin_task("task", "boom")
        task_runner.end_task(tid, "failed", error="RuntimeError('x')")
        rec = task_runner.get_task(tid)
        assert rec["status"] == "failed"
        assert "RuntimeError" in rec["error"]

    def test_status_filter_and_running_count(self):
        from src.agent import task_runner

        t1 = task_runner.begin_task("task", "a")
        t2 = task_runner.begin_task("task", "b")
        task_runner.end_task(t1, "done", answer_chars=3)
        only_done = task_runner.list_tasks(status="done")
        assert [r["task_id"] for r in only_done["tasks"]] == [t1]
        all_out = task_runner.list_tasks()
        assert all_out["running"] == 1  # t2 still running
        task_runner.end_task(t2, "done")
        assert task_runner.list_tasks()["running"] == 0

    def test_finished_cap_trims_oldest(self, monkeypatch):
        from src.agent import task_runner

        monkeypatch.setattr(task_runner, "MAX_FINISHED", 3)
        ids = [
            task_runner.begin_task("task", f"t{i}") for i in range(6)
        ]
        for tid in ids:
            task_runner.end_task(tid, "done", answer_chars=1)
        out = task_runner.list_tasks()
        # Only the newest 3 finished survive (running records never
        # trim, so total == MAX_FINISHED here).
        assert out["total"] == 3
        assert ids[-1] in [r["task_id"] for r in out["tasks"]]
        assert ids[0] not in [r["task_id"] for r in out["tasks"]]

    def test_newest_first_and_limit(self):
        from src.agent import task_runner

        tids = [task_runner.begin_task("task", f"t{i}") for i in range(5)]
        out = task_runner.list_tasks(limit=2)
        assert [r["task_id"] for r in out["tasks"]] == list(
            reversed(tids)
        )[:2]
        assert out["returned"] == 2

    def test_thread_safety_burst(self):
        from src.agent import task_runner

        errors: list = []
        lock = threading.Lock()

        def worker(i):
            try:
                tid = task_runner.begin_task("task", f"w{i}")
                task_runner.attach_sid(tid, f"task-{tid}")
                task_runner.end_task(tid, "done", answer_chars=1)
            except Exception as e:  # pragma: no cover
                with lock:
                    errors.append(e)

        threads = [
            threading.Thread(target=worker, args=(i,)) for i in range(10)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(5)
        assert not errors
        assert task_runner.list_tasks()["total"] == 10


# ─── TaskLocalHost ───────────────────────────────────────────


class TestTaskLocalHost:
    def _host(self):
        from src.mcp_client.host import TaskLocalHost

        return TaskLocalHost()

    def test_tool_surface(self):
        host = self._host()
        tools = host.list_tools()
        # v0.9.110.0 — the 4th tool ``list_task_models`` joined.
        # v0.9.120.0 — the 5th tool ``stop_task`` (force-stop) joined.
        assert [t.name for t in tools] == [
            "run_task_once", "list_tasks", "get_task_result",
            "list_task_models", "stop_task",
        ]
        assert all(t.server_name == "task-local" for t in tools)
        by_name = {t.name: t for t in tools}
        assert by_name["run_task_once"].input_schema.get(
            "required",
        ) == ["prompt"]
        assert by_name["get_task_result"].input_schema.get(
            "required",
        ) == ["task_id"]

    def test_run_once_empty_prompt_error(self):
        out = self._host()._run_once({})
        assert "error" in out
        assert "prompt" in out["error"]

    def test_run_once_submits_and_completes(self, tmp_path):
        from src.agent import task_runner
        from src.agent.session import SessionManager

        host = self._host()
        monkey_stub = _StubAgent("背景任務完成")
        # v0.9.110+ — _fresh_agent takes (llm=None) even when unset.
        host._fresh_agent = lambda llm=None: monkey_stub  # noqa: E731
        out = host._run_once({
            "title": "研究任務", "prompt": "請整理 README 重點",
        })
        assert out["ok"] is True
        assert out["status"] == "running"
        assert out["sid"] == f"task-{out['task_id']}"
        rec = _wait_status(out["task_id"])
        assert rec is not None and rec["status"] == "done"
        assert rec["answer_chars"] == len("背景任務完成")
        # The dedicated session persists the full turn.
        sess = SessionManager().load(out["sid"])
        assert sess is not None
        roles = [m["role"] for m in sess.messages]
        assert roles == ["user", "assistant"]
        assert sess.messages[1]["content"] == "背景任務完成"

    def test_run_once_chat_turn_failure_stamps_failed(self, tmp_path):
        from src.agent.session import SessionManager

        host = self._host()
        host._fresh_agent = lambda llm=None: _StubAgent(  # noqa: E731
            fail=True,
        )
        out = host._run_once({"prompt": "會失敗的工作"})
        assert out["ok"] is True
        rec = _wait_status(out["task_id"])
        assert rec is not None and rec["status"] == "failed"
        assert "provider down" in (rec["error"] or "")
        sess = SessionManager().load(out["sid"])
        assert sess is not None
        assert sess.messages[1]["content"].startswith("[error]")

    def test_list_unknown_status_error(self):
        out = self._host()._list({"status": "bogus"})
        assert "error" in out

    def test_get_result_includes_preview(self):
        from src.agent import task_runner
        from src.agent.session import Session, SessionManager

        tid = task_runner.begin_task("task", "預覽", sid="task-pv")
        sess = Session(sid="task-pv", title="[task] 預覽")
        sess.messages.append({"role": "user", "content": "q", "ts": "t"})
        sess.messages.append({
            "role": "assistant", "content": "答案內容ABC", "ts": "t",
        })
        SessionManager().save(sess)
        task_runner.end_task(tid, "done", answer_chars=7)
        out = self._host()._get({"task_id": tid})
        assert out["status"] == "done"
        assert out["answer_preview"] == "答案內容ABC"
        assert out["preview_truncated"] is False

    def test_get_unknown_task_id_error(self):
        out = self._host()._get({"task_id": "deadbeef"})
        assert "error" in out
        assert "unknown task_id" in out["error"]


# ─── manager wiring + stdio parity ───────────────────────────


class TestManagerWiring:
    def test_from_config_registers_task_host(self):
        from src.mcp_client import McpManager

        mgr = McpManager.from_config(path=None)
        names = {t.name for t in mgr.list_tools()}
        assert {"run_task_once", "list_tasks", "get_task_result"} <= names
        assert mgr._task_local_host is not None
        assert "task-local" not in mgr.failed

    def test_list_tasks_dispatches_through_manager(self):
        from src.mcp_client import McpManager

        mgr = McpManager.from_config(path=None)
        result = mgr.call("list_tasks", {})
        assert not result.is_error
        assert "tasks" in result.text


class TestStdioServer:
    def test_tool_specs_match_host_surface(self):
        from src.mcp_client.host import TaskLocalHost
        from src.mcp_server.task_server import _build_tool_specs

        spec_names = {s.name for s in _build_tool_specs()}
        host_names = {t.name for t in TaskLocalHost().list_tools()}
        assert spec_names == host_names


# ─── system prompt block ─────────────────────────────────────


def _prompt_agent():
    from src.agent.agent import Agent

    a = Agent()
    a._provider = "stub"
    a._model = "stub-model"
    a._mcp_manager = None
    return a


class TestSystemPrompt:
    def test_background_block_present_when_tools_enabled(self):
        prompt = _prompt_agent()._system_prompt(tools_enabled=True)
        assert "run_task_once" in prompt
        assert "get_task_result" in prompt
        assert "背景執行" in prompt  # zh trigger words wired

    def test_legacy_mode_excluded(self):
        prompt = _prompt_agent()._system_prompt(tools_enabled=False)
        assert "run_task_once" not in prompt


# ─── fire registration (scheduler / webhook / telegram) ──────


class TestFireRegistration:
    def test_scheduler_fire_registers_in_registry(self, tmp_path):
        from src.agent import task_runner
        from src.agent.scheduler import Schedule, make_agent_on_fire

        s = Schedule(
            id="tr1", name="註冊示範", prompt="hello",
            schedule_type="once_at",
            next_fire_at="2026-01-01T00:00:00", enabled=True,
        )
        on_fire = make_agent_on_fire(
            agent_factory=lambda: _StubAgent("排程答案"),
        )
        on_fire(s)
        recs = task_runner.list_tasks(status="done")["tasks"]
        sched_recs = [r for r in recs if r["source"] == "scheduler"]
        assert sched_recs, "the scheduler fire must register itself"
        rec = sched_recs[0]
        assert rec["answer_chars"] == len("排程答案")
        # The registry record points at the fire's dedicated session.
        assert rec["sid"] == s.last_session_id
        assert (rec["sid"] or "").startswith("sched-")

    def test_scheduler_fire_failure_stamps_failed(self, tmp_path):
        from src.agent import task_runner
        from src.agent.scheduler import Schedule, make_agent_on_fire

        s = Schedule(
            id="tr2", name="失敗示範", prompt="會炸的排程",
            schedule_type="once_at",
            next_fire_at="2026-01-01T00:00:00", enabled=True,
        )
        on_fire = make_agent_on_fire(
            agent_factory=lambda: _StubAgent(fail=True),
        )
        with pytest.raises(Exception):
            on_fire(s)
        recs = task_runner.list_tasks(status="failed")["tasks"]
        sched_recs = [r for r in recs if r["source"] == "scheduler"]
        assert sched_recs
        assert "provider down" in (sched_recs[0]["error"] or "")

    def test_webhook_fire_registers_in_registry(self, tmp_path):
        from src.agent import task_runner
        from src.agent.webhook import WebhookTrigger, make_webhook_fire

        w = WebhookTrigger(
            id="trw1", name="webhook示範", prompt="hi",
            access_key="k", enabled=True,
        )
        fire = make_webhook_fire(agent_factory=lambda: _StubAgent("wh答案"))
        out = fire(w, "payload")
        assert out.get("ok") is not False
        recs = task_runner.list_tasks(status="done")["tasks"]
        wh_recs = [r for r in recs if r["source"] == "webhook"]
        assert wh_recs, "the webhook fire must register itself"
        assert wh_recs[0]["answer_chars"] == len("wh答案")
        assert (wh_recs[0]["sid"] or "").startswith("webhook-")

    def test_telegram_turn_registers_in_registry(self, tmp_path):
        from src.agent import task_runner
        from src.agent.telegram_bot import make_telegram_on_message

        handle = make_telegram_on_message(
            agent_factory=lambda: _StubAgent("tg答案"),
        )
        handle(4242, "hello bot")
        recs = task_runner.list_tasks(status="done")["tasks"]
        tg_recs = [r for r in recs if r["source"] == "telegram"]
        assert tg_recs, "the telegram turn must register itself"
        assert tg_recs[0]["answer_chars"] == len("tg答案")
        assert (tg_recs[0]["sid"] or "").startswith("tgbot-4242")