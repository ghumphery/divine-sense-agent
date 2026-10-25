"""v0.9.47.0 — Phase-3 parallel-multi-task GUI (task panel + bg run).

* ``Api.list_background_tasks`` / ``get_background_task`` /
  ``submit_background_task`` — the three bridges the chat UI uses
  (submit reuses ``TaskLocalHost._run_once`` with the MAIN agent's
  McpManager).
* ``get_status`` carries ``running_tasks`` (badge source, never raises
  into the poll loop).
* Frontend source guards: ⏳ button + panel + Background checkbox +
  the sendChat branch + badge painting + 4-locale i18n + CSS.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

_ASSETS = Path(__file__).resolve().parents[1] / "gui" / "assets"


def _read(name: str) -> str:
    return (_ASSETS / name).read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _fresh_registry(tmp_path, monkeypatch):
    monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path))
    from src.agent import task_runner

    task_runner.reset_tasks()
    yield
    task_runner.reset_tasks()


def _api():
    from gui.api import Api

    # No __init__: these bridge methods touch no instance state except
    # submit_background_task's _get_agent (patched per-test).
    return Api.__new__(Api)


# ─── Python bridges ──────────────────────────────────────────


class TestListBackgroundTasks:
    def test_delegates_to_registry_with_filters(self, monkeypatch):
        from src.agent import task_runner
        from gui.api import Api

        seen: dict = {}

        def fake_list(status=None, limit=20):
            seen["status"] = status
            seen["limit"] = limit
            return {"tasks": [], "running": 0, "total": 0,
                    "returned": 0}

        monkeypatch.setattr(task_runner, "list_tasks", fake_list)
        out = Api.__new__(Api).list_background_tasks(
            status="running", limit=5,
        )
        assert seen == {"status": "running", "limit": 5}
        assert out["tasks"] == []

    def test_registry_error_degrades_to_error_dict(self, monkeypatch):
        from src.agent import task_runner
        from gui.api import Api

        def boom(status=None, limit=20):
            raise RuntimeError("registry down")

        monkeypatch.setattr(task_runner, "list_tasks", boom)
        out = Api.__new__(Api).list_background_tasks()
        assert "error" in out
        assert "registry down" in out["error"]


class TestGetBackgroundTask:
    def test_unknown_id_returns_error(self):
        from gui.api import Api

        out = Api.__new__(Api).get_background_task("deadbeef")
        assert "error" in out
        assert "unknown task_id" in out["error"]

    def test_record_plus_session_preview(self):
        from src.agent import task_runner
        from src.agent.session import Session, SessionManager
        from gui.api import Api

        tid = task_runner.begin_task("task", "預覽", sid="task-pv")
        sess = Session(sid="task-pv", title="[task] 預覽")
        sess.messages.append({"role": "user", "content": "q", "ts": "t"})
        sess.messages.append({
            "role": "assistant", "content": "答案內容", "ts": "t",
        })
        SessionManager().save(sess)
        task_runner.end_task(tid, "done", answer_chars=4)
        out = Api.__new__(Api).get_background_task(tid)
        assert out["status"] == "done"
        assert out["answer_preview"] == "答案內容"
        assert out["preview_truncated"] is False


class TestSubmitBackgroundTask:
    def test_empty_prompt_error(self):
        from gui.api import Api

        out = Api.__new__(Api).submit_background_task("t", "   ")
        assert "error" in out
        assert "empty" in out["error"]

    def test_reuses_task_local_host_with_shared_manager(
        self, monkeypatch,
    ):
        from gui.api import Api
        from src.mcp_client.host import TaskLocalHost

        fake_agent = MagicMock()
        fake_agent.mcp_manager = "SENTINEL-MANAGER"
        api = Api.__new__(Api)
        monkeypatch.setattr(api, "_get_agent", lambda: fake_agent)

        captured: dict = {}

        def fake_run_once(self, args):
            captured["args"] = args
            captured["manager"] = self._mcp_manager
            return {"ok": True, "task_id": "T1", "sid": "task-T1"}

        monkeypatch.setattr(TaskLocalHost, "_run_once", fake_run_once)
        out = api.submit_background_task("研究", "請整理 README", True)
        assert out["ok"] is True
        assert out["task_id"] == "T1"
        # v0.9.132.3 — the optional origin chat-session binding rides
        # the same args dict (None when the caller omits chat_sid).
        assert captured["args"] == {
            "title": "研究", "prompt": "請整理 README",
            "use_web_search": True, "origin_sid": None,
        }
        # The background host shares the MAIN agent's McpManager.
        assert captured["manager"] == "SENTINEL-MANAGER"

    def test_agent_failure_degrades_to_error_dict(self, monkeypatch):
        from gui.api import Api

        api = Api.__new__(Api)

        def boom():
            raise RuntimeError("agent init failed")

        monkeypatch.setattr(api, "_get_agent", boom)
        out = api.submit_background_task("t", "p")
        assert "error" in out
        assert "agent init failed" in out["error"]


class TestGetStatusRunningTasks:
    def test_field_present_and_reflects_registry(self, monkeypatch):
        from src.agent import task_runner
        from gui.api import Api

        api = Api.__new__(Api)
        api._qdrant_last_ok = True

        def fail_get_store(self):
            raise RuntimeError("qdrant down")

        def fail_get_agent(self):
            raise RuntimeError("no agent in unit test")

        monkeypatch.setattr(Api, "_get_store", fail_get_store)
        monkeypatch.setattr(Api, "_get_agent", fail_get_agent)
        out = api.get_status()
        assert out["running_tasks"] == 0
        task_runner.begin_task("task", "bg")
        out = api.get_status()
        assert out["running_tasks"] == 1


# ─── frontend source guards ──────────────────────────────────


class TestTaskLogToggle:
    """v0.9.133.10 — the ⏳ task rows gain an inline ⚙ execution-log
    expander: running tasks poll `poll_turn(turn_id, cursor)` LIVE,
    terminal rows replay the snapshot, ingest tasks (no turn) show an
    honest note. The registry carries ``turn_id`` via
    ``task_runner.attach_turn`` (bound at detach time)."""

    def test_python_binding_helpers_exist(self):
        src = (Path(__file__).resolve().parents[1] / "src" /
               "agent" / "task_runner.py").read_text(encoding="utf-8")
        assert "def attach_turn(" in src
        api = (Path(__file__).resolve().parents[1] / "gui" /
               "api.py").read_text(encoding="utf-8")
        assert "task_runner.attach_turn(task_id, turn_id)" in api
        # finalize persists the log into the assistant message
        assert 'sess.messages[-1]["turnLog"] = turn_log' in api

    def test_frontend_guards(self):
        js = _read("app.js")
        assert "function _taskLogRefresh(" in js
        assert "const _taskLogOpen = new Set()" in js
        assert "_taskLogCursor" in js
        assert "api.call('poll_turn', tid," in js
        assert "taskLogNoteIngest" in js
        assert "chat-task-row .task-log-lines" in _read("style.css")

    def test_i18n_four_locales(self):
        import re

        js = _read("app.js")
        for key in ("taskLogShow:", "taskLogNoteIngest:",
                    "taskLogNoteNone:"):
            assert len(re.findall(r"\n\s*" + re.escape(key), js)) == 4, key

    def test_registry_attach_roundtrip(self):
        from src.agent import task_runner

        tid = task_runner.begin_task("ingest", "fake")
        task_runner.attach_turn(tid, "turn-abc")
        assert task_runner.get_task(tid)["turn_id"] == "turn-abc"
        task_runner.attach_turn(tid, "  ")
        assert task_runner.get_task(tid)["turn_id"] is None
        task_runner.attach_turn("nope", "turn-x")  # never raises


class TestFrontendWiring:
    def test_index_html_has_tasks_button_and_panel_and_bg_toggle(self):
        html = _read("index.html")
        assert 'id="chat-tasks-btn"' in html
        assert 'id="chat-tasks-panel"' in html
        assert 'id="chat-tasks-list"' in html
        assert 'id="chat-tasks-refresh"' in html
        assert 'id="chat-bg-toggle"' in html

    def test_app_js_has_panel_functions(self):
        js = _read("app.js")
        for name in (
            "function updateTaskBadge(",
            "function _renderTaskRow(",
            "async function renderTaskPanel(",
            "async function toggleTaskPanel(",
        ):
            assert name in js, f"app.js missing {name!r}"

    def test_app_js_binds_panel_events(self):
        js = _read("app.js")
        assert (
            "document.getElementById('chat-tasks-btn')\n"
            "      .addEventListener('click', toggleTaskPanel);" in js
        )
        assert (
            "tasksRefresh.addEventListener('click', renderTaskPanel);"
            in js
        )

    def test_sendchat_background_branch(self):
        js = _read("app.js")
        idx = js.find("async function sendChat")
        assert idx != -1
        # v0.9.132.1 — 4000 → 8000: the v0.9.127.x send-safety wrapper
        # moved the bg branch into _sendChatCore, pushing the literals
        # past the old 4000-char window (pre-existing drift).
        body = js[idx:idx + 8000]
        assert "submit_background_task" in body
        assert "chat-bg-toggle" in body
        assert "taskSubmitted" in body
        # The branch must keep the real-time save chain intact.
        assert "autoSaveChat();" in body

    def test_refresh_status_paints_badge(self):
        js = _read("app.js")
        idx = js.find("async function refreshStatus()")
        assert idx != -1
        body = js[idx:idx + 1400]
        assert "running_tasks" in body
        assert "updateTaskBadge(" in body

    def test_sendbutton_released_in_background_branch(self):
        """The bg branch must re-enable the send button on BOTH the
        success and error paths (a stuck disabled Send button was the
        v0.9.x-era UX trap this mirrors). The slice is bounded by the
        branch terminator so the NORMAL path's release is excluded."""
        js = _read("app.js")
        idx = js.find("if (bgRun) {")
        assert idx != -1
        end = js.find("// v0.8.5+: forward", idx)
        assert end != -1
        body = js[idx:end]
        assert body.count("sendBtn.disabled = false;") == 1
        assert "sendBtn.textContent = t('send');" in body
        assert "autoSaveChat();" in body
        assert "return;" in body

    @pytest.mark.parametrize("locale", ["zh-TW", "zh-CN", "en", "ja"])
    def test_i18n_keys_in_all_locales(self, locale):
        js = _read("app.js")
        assert f"chatTasks: '" in js  # sanity: key exists
        # Each locale block carries the full key set (the locale
        # markers appear exactly once each as dict heads).
        heads = js.count(f"'{locale}': {{")
        assert heads == 1
        # Grab the slice from this locale head to the next head.
        start = js.find(f"'{locale}': {{")
        nxt = js.find(f"'{locale}'", start + 10)
        block = js[start:nxt if nxt != -1 else len(js)]
        for key in (
            "chatTasks:", "taskRefresh:", "taskNone:", "taskRunning:",
            "taskDone:", "taskFailed:", "bgRun:", "taskSubmitted:",
        ):
            assert key in block, (
                f"{locale} missing i18n key {key!r}"
            )

    def test_css_has_panel_and_status_dots(self):
        css = _read("style.css")
        assert "#chat-tasks-panel {" in css
        assert "#chat-tasks-panel[hidden] { display: none; }" in css
        assert ".chat-task-running .task-dot" in css
        assert ".chat-task-done .task-dot" in css
        assert ".chat-task-failed .task-dot" in css
        assert "#chat-tasks-btn.chat-tasks-active" in css

    def test_app_js_still_parses_with_node(self):
        import shutil
        import subprocess

        node = shutil.which("node")
        if not node:
            pytest.skip("node not available")
        subprocess.run(
            ["node", "--check", str(_ASSETS / "app.js")],
            check=True, capture_output=True,
        )


class TestBgResultWriteback:
    """v0.9.132.3 — background tasks submitted from the chat UI write
    their finished result back into the ORIGIN chat session.

    * ``task_runner`` records carry ``origin_sid`` (+ ``set_origin``).
    * ``TaskLocalHost._run_once`` accepts ``origin_sid``.
    * ``Api.submit_background_task`` forwards ``chat_sid``.
    * Frontend: bgWatch poller + reconcile hooks + i18n ×4.
    """

    def test_task_runner_origin_field_and_set_origin(self):
        from src.agent import task_runner

        tid = task_runner.begin_task("task", "t")
        assert task_runner.get_task(tid)["origin_sid"] is None
        task_runner.set_origin(tid, "chat-9")
        assert task_runner.get_task(tid)["origin_sid"] == "chat-9"
        # never raises on unknown id; falsy origin is a no-op
        task_runner.set_origin("nope", "chat-9")
        task_runner.set_origin(tid, "")
        assert task_runner.get_task(tid)["origin_sid"] == "chat-9"

    def test_submit_background_task_passes_origin(self, monkeypatch):
        import types

        import gui.api as gam

        seen = {}

        class _FakeHost:
            def __init__(self, mcp_manager=None):
                pass

            def _run_once(self, args):
                seen.update(args)
                return {"task_id": "t1", "sid": "task-t1"}

        monkeypatch.setattr(
            "src.mcp_client.host.TaskLocalHost", _FakeHost)
        g = gam.Api.__new__(gam.Api)
        g._get_agent = lambda: types.SimpleNamespace(mcp_manager=object())
        out = g.submit_background_task("t", "p", False, "chat-9")
        assert out == {"task_id": "t1", "sid": "task-t1"}
        assert seen["origin_sid"] == "chat-9"
        seen.clear()
        g.submit_background_task("t", "p", False)
        assert not seen.get("origin_sid")

    def test_run_once_records_origin(self):
        import time
        import types

        from src.mcp_client.host import TaskLocalHost
        from src.agent import task_runner

        host = TaskLocalHost(mcp_manager=MagicMock())
        fake = types.SimpleNamespace()
        fake.chat_turn = lambda **kw: ("背景答覆", "ctx", [])
        host._fresh_agent = lambda llm=None: fake
        out = host._run_once({
            "title": "t", "prompt": "p", "use_web_search": False,
            "origin_sid": "chat-9",
        })
        assert out.get("task_id")
        # v0.9.133.18 — the registry MUST carry the sid right away:
        # get_background_task reads the answer preview from it.
        from src.agent import task_runner as _tr
        rec = _tr.get_task(out["task_id"])
        assert rec["sid"] == out["sid"] == f"task-{out['task_id']}"
        rec = None
        deadline = time.time() + 5
        while time.time() < deadline:
            rec = task_runner.get_task(out["task_id"])
            if rec and rec["status"] != "running":
                break
            time.sleep(0.02)
        assert rec is not None and rec["status"] == "done"
        assert rec["origin_sid"] == "chat-9"

    def test_app_js_bg_watch_machinery(self):
        js = _read("app.js")
        for tok in (
            "function watchBgTask(",
            "async function bgWatchTick(",
            "async function reconcileBgTasks(",
            "'get_background_task', tid, 4000",
            "m.taskId === tid",
            "msgs[msgs.length - 1].taskId = tid",
            "watchBgTask(r.task_id)",
            "'list_background_tasks', null, 50",
            "'submit_background_task', text.slice(0, 40), text, false,",
            "(chatSession && chatSession.id) || null,",
        ):
            assert tok in js, tok

    def test_reconcile_hooks_cover_every_session_bind(self):
        js = _read("app.js")
        # applyRestore / newSession / loadSessionById / saveSession
        assert js.count("try { reconcileBgTasks(); } catch (_) {}") == 4

    def test_bg_result_back_i18n_four_locales(self):
        assert _read("app.js").count("bgResultBack:") == 4