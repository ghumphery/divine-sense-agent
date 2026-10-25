# -*- coding: utf-8 -*-
"""v0.9.132.1 — long foreground turns auto-detach into the background
task registry: ``Api.detach_turn_to_background`` promotes a RUNNING
two-phase chat turn (same UX as the ☑ 背景執行 checkbox) and
``_finalize_detached_turn`` stamps the registry row + writes the
task-<id> session at the terminal state. The running work is never
killed or redone."""
import sys
import threading
import time
import types

import pytest

sys.path.insert(0, ".")


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path))
    from src.agent import task_runner

    task_runner.reset_tasks()
    yield tmp_path
    task_runner.reset_tasks()


@pytest.fixture
def te():
    from src.agent import turn_events as m

    m.reset_for_tests()
    yield m
    m.reset_for_tests()


def _fake_agent(chat_fn, stop_hook=None):
    a = types.SimpleNamespace()
    a.last_usage = None
    a.last_tool_dispatches = []
    a.last_tool_stopped = False
    a.begin_turn_stream = lambda tid, cb: None
    a.end_turn_stream = lambda tid: None
    a.signal_turn_stop = lambda tid: False
    a.chat_turn = chat_fn
    if stop_hook is not None:
        a.set_stop_check = stop_hook
    return a


def _make_api(agent):
    import gui.api as gam

    g = gam.Api.__new__(gam.Api)
    g._get_agent = lambda: agent
    g._wire_openwith_diagrams_opener = lambda ag: None
    return g


def _wait_running(te, tid, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if te.snapshot(tid)["status"] == "running":
            return
        time.sleep(0.02)


def _wait_terminal(te, tid, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if te.snapshot(tid)["status"] != "running":
            return te.snapshot(tid)
        time.sleep(0.02)
    return te.snapshot(tid)


def _wait_task_terminal(task_runner, task_id, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        rec = task_runner.get_task(task_id)
        if rec is not None and rec["status"] != "running":
            return rec
        time.sleep(0.02)
    return task_runner.get_task(task_id)


class TestDetachApi:
    def test_running_turn_detaches_and_finalizes(self, env, te):
        release = threading.Event()

        def chat(**kw):
            release.wait(10)
            return ("背景答覆", "ctx", ["a.md"])

        api = _make_api(_fake_agent(chat))
        tid = api.start_turn("長任務幫我查", history=[])["turn_id"]
        _wait_running(te, tid)
        r = api.detach_turn_to_background(tid)
        assert r.get("ok") is True
        assert r.get("task_id") and r.get("sid") == f"task-{r['task_id']}"
        # idempotent — the second call returns the SAME task
        r2 = api.detach_turn_to_background(tid)
        assert r2["task_id"] == r["task_id"] and r2.get("already") is True
        from src.agent import task_runner

        assert task_runner.get_task(r["task_id"])["status"] == "running"
        # unknown turn → honest error
        assert "error" in api.detach_turn_to_background("nope")
        # finish the turn → the registry row + session are stamped
        release.set()
        snap = _wait_terminal(te, tid)
        assert snap["status"] == "done"
        rec = _wait_task_terminal(task_runner, r["task_id"])
        assert rec["status"] == "done"
        assert rec["answer_chars"] == len("背景答覆")
        from src.agent.session import SessionManager

        sess = SessionManager().load(rec["sid"])
        assert sess is not None
        assert [m["role"] for m in sess.messages] == ["user", "assistant"]
        assert sess.messages[0]["content"] == "長任務幫我查"
        assert sess.messages[1]["content"] == "背景答覆"
        assert sess.messages[1]["task_id"] == r["task_id"]
        # v0.9.133.10 — the turn log rides in: the task session keeps
        # a 查看執行 log expander like foreground bubbles, and the
        # registry row carries the turn id for panel log polling.
        assert isinstance(sess.messages[1].get("turnLog"), list)
        assert task_runner.get_task(r["task_id"])["turn_id"] == tid
        # after the terminal state a new detach is refused
        assert "error" in api.detach_turn_to_background(tid)

    def test_terminal_turn_refused(self, env, te):
        api = _make_api(_fake_agent(lambda **kw: ("done", "", [])))
        tid = api.start_turn("q")["turn_id"]
        _wait_terminal(te, tid)
        r = api.detach_turn_to_background(tid)
        assert "error" in r and "already" not in r

    def test_error_path_writes_failed_session(self, env, te):
        release = threading.Event()

        def boom(**kw):
            release.wait(10)
            raise RuntimeError("qdrant down")

        api = _make_api(_fake_agent(boom))
        tid = api.start_turn("q")["turn_id"]
        _wait_running(te, tid)
        r = api.detach_turn_to_background(tid)
        assert r.get("ok") is True
        release.set()
        snap = _wait_terminal(te, tid)
        assert snap["status"] == "error"
        from src.agent import task_runner

        rec = _wait_task_terminal(task_runner, r["task_id"])
        assert rec["status"] == "failed"
        assert "qdrant down" in (rec.get("error") or "")
        from src.agent.session import SessionManager

        sess = SessionManager().load(rec["sid"])
        assert "[error]" in sess.messages[-1]["content"]
        assert sess.messages[-1]["error"] is True

    def test_task_stop_bridges_to_turn_scoped_stop(self, env, te):
        release = threading.Event()
        stops = []

        def chat(**kw):
            release.wait(10)
            return ("x", "", [])

        agent = _fake_agent(chat)
        agent.signal_turn_stop = lambda tid: stops.append(tid) or True
        api = _make_api(agent)
        tid = api.start_turn("q")["turn_id"]
        _wait_running(te, tid)
        r = api.detach_turn_to_background(tid)
        assert r.get("ok") is True
        from src.agent import task_runner

        # v0.9.132.2 regression — the SHARED _stop_check slot is never
        # touched (the v0.9.132.1 chain leaked: one stopped detached
        # task force-stopped every later chat turn on the main agent).
        assert getattr(agent, "_stop_check", None) is None
        task_runner.request_stop(r["task_id"])
        deadline = time.time() + 5
        while time.time() < deadline and not stops:
            time.sleep(0.05)
        # the watcher bridges the flag into THIS turn's scoped stop
        assert stops == [tid]
        release.set()
        _wait_terminal(te, tid)
        _wait_task_terminal(task_runner, r["task_id"])

    def test_detach_source_never_touches_shared_stop_slot(self, env):
        import inspect

        import gui.api as gam

        src = inspect.getsource(gam.Api.detach_turn_to_background)
        assert "set_stop_check" not in src
        # v0.9.132.2 — any mention of `_stop_check` must live in a
        # COMMENT line only (the shared slot is never read/written).
        for line in src.splitlines():
            if "_stop_check" in line:
                assert line.strip().startswith("#"), line

    def test_finalize_is_noop_for_never_detached(self, env, te):
        api = _make_api(_fake_agent(lambda **kw: ("x", "", [])))
        # must not raise and must not touch the registry
        api._finalize_detached_turn("nope", "done", "x", None)
        from src.agent import task_runner

        assert task_runner.list_tasks()["total"] == 0

    def test_finalize_survives_session_save_failure(self, env, monkeypatch):
        api = _make_api(_fake_agent(lambda **kw: ("x", "", [])))
        from src.agent import task_runner

        tid_rec = task_runner.begin_task("chat", "q")
        import threading as _t

        api._detached_lock = _t.Lock()
        api._detached_turns = {
            "t9": {"task_id": tid_rec, "sid": "task-x", "question": "q"},
        }
        from src.agent.session import SessionManager

        def _boom(self, _sess):
            raise RuntimeError("disk full")

        monkeypatch.setattr(SessionManager, "save", _boom)
        api._finalize_detached_turn("t9", "done", "answer", None)
        rec = task_runner.get_task(tid_rec)
        assert rec["status"] == "done"


class TestFrontendGuards:
    @staticmethod
    def _js():
        from pathlib import Path

        return Path("gui/assets/app.js").read_text(encoding="utf-8")

    def test_poller_auto_background_branch(self):
        js = self._js()
        assert "_TURN_BG_AFTER_MS = 60000" in js
        assert "bgDetached = false" in js
        assert "bgDetached = true" in js
        assert "'detach_turn_to_background'" in js
        assert "t('turnBgDetached')" in js
        # the SAME taskSubmitted message the ☑ 背景執行 branch shows
        assert "t('taskSubmitted', { id: det.task_id, sid: det.sid })" in js

    def test_i18n_four_locales(self):
        assert self._js().count("turnBgDetached:") == 4

    def test_render_chat_remounts_live_panels(self):
        js = self._js()
        assert "const liveExecPanels = new Set();" in js
        i = js.find("function renderChat(")
        j = js.find("function _isLocalImgPath", i)
        body = js[i:j]
        assert "liveExecPanels.forEach(function (p)" in body
        assert "!p.dataset.terminal" in body

    def test_poller_marks_terminal_panels(self):
        js = self._js()
        i = js.find("async function pollTurnStream")
        j = js.find("function appendChatMsg", i)
        body = js[i:j]
        assert body.count("panel.dataset.terminal = '1';") == 3
        assert "liveExecPanels.add(panel);" in body
        assert "liveExecPanels.delete(panel);" in body
