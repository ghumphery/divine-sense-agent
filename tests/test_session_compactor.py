# -*- coding: utf-8 -*-
"""v0.9.133.3 — T+1 session-summary compactor
(uploads/uniichat_memory_report.md §6 / P1+P3' plan ②).

Covers: session scanning (mtime window + machine-prefix exclusion +
corrupt-file skip), transcript compaction caps, the main batch loop
(idempotency / dry-run / per-session failure isolation / exact-payload
write verification + stopped-early), the sink gate, the default llm
pin, the MemoryLocalHost 7th tool surface + stdio mirror parity, the
gui boot-seed schedule (env cron gate + Schedule.llm pin + idempotency
+ swallow), and the _memory_block recall-note update (session-summary
recallable; task-result exclusion UNCHANGED).
"""
import json
import os
import time
import unittest
from unittest import mock


def _clean_env(monkeypatch_dict):
    """Wipe the compactor env knobs so host defaults rule."""
    for k in ("QDRANT_SESSION_SUMMARY_SINK",
              "QDRANT_SESSION_SUMMARY_LLM",
              "QDRANT_SESSION_SUMMARY_CRON"):
        os.environ.pop(k, None)


class FakeStore:
    """remember/recent/search with the MemoryStore payload shape."""

    def __init__(self, fail_write=False, hide_writes=False):
        self.rows = []
        self.fail_write = fail_write
        self.hide_writes = hide_writes

    def remember(self, text, kind="fact", source="", session_id="",
                 meta=None, ts_epoch=None):
        if self.fail_write:
            raise RuntimeError("qdrant down")
        row = {
            "id": f"m{len(self.rows) + 1}", "kind": kind,
            "source": source, "session_id": session_id,
            "content": text, "stored_at": "2026-10-20T10:00:00",
            "stored_at_epoch": 1.0,
        }
        if not self.hide_writes:
            self.rows.append(row)
        return {"id": row["id"], "stored_at": row["stored_at"],
                "stored_at_epoch": 1.0}

    def recent(self, limit=10, kind=None):
        rows = [r for r in self.rows if not kind or r["kind"] == kind]
        return rows[: max(1, int(limit))]

    def search(self, *a, **kw):
        return []


def _mk_session(tmp, sid, messages, *, mtime=None, title=""):
    """Drop a session JSON file into a tmp sessions root."""
    tmp.mkdir(parents=True, exist_ok=True)
    p = tmp / f"{sid}.json"
    d = {
        "id": sid, "title": title or sid,
        "created_at": "2026-10-19T10:00:00",
        "updated_at": "2026-10-20T10:00:00",
        "messages": messages,
    }
    p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    if mtime is not None:
        os.utime(p, (mtime, mtime))
    return p


def _fake_manager(tmp):
    """SessionManager double that just points at the tmp root."""

    class M:
        root = tmp

    return M()


A = "user 問了台積電現在多少錢，agent 用 stock_get_quote 查得 1020 元"
B = "討論了修 GUI 白屏問題，決定走 no-store + ETag 方案"


class TestScanning(unittest.TestCase):
    def _base(self, days=1.0, now=None):
        import tempfile
        from pathlib import Path

        tmp = Path(tempfile.mkdtemp(prefix="_sc_tests_"))
        now = now or time.time()
        _mk_session(tmp, "aaaa1111", [{"role": "user", "content": A},
                                       {"role": "assistant", "content": B}],
                    mtime=now - 3600)
        return tmp, now

    def test_machine_prefixes_excluded(self):
        import tempfile
        from pathlib import Path

        from src.agent.session_compactor import _iter_recent_sessions

        tmp = Path(tempfile.mkdtemp(prefix="_sc_tests_"))
        now = time.time()
        _mk_session(tmp, "aaaa1111", [{"role": "user", "content": "hi"}],
                    mtime=now - 60)
        _mk_session(tmp, "sched-x", [{"role": "user", "content": "s"}],
                    mtime=now - 60)
        _mk_session(tmp, "task-9a", [{"role": "user", "content": "s"}],
                    mtime=now - 60)
        _mk_session(tmp, "webhook-1", [{"role": "user", "content": "s"}],
                    mtime=now - 60)
        rows = _iter_recent_sessions(1.0, now=now, manager=_fake_manager(tmp))
        self.assertEqual([r["sid"] for r in rows], ["aaaa1111"])

    def test_window_and_corrupt_skip(self):
        import tempfile
        from pathlib import Path

        from src.agent.session_compactor import _iter_recent_sessions

        tmp = Path(tempfile.mkdtemp(prefix="_sc_tests_"))
        now = time.time()
        _mk_session(tmp, "recent1", [{"role": "user", "content": A}],
                    mtime=now - 60)
        _mk_session(tmp, "stale1", [{"role": "user", "content": "old"}],
                    mtime=now - 3 * 86400)
        (tmp / "corrupt1.json").write_text("{not json", encoding="utf-8")
        os.utime(tmp / "corrupt1.json", (now - 60, now - 60))
        rows = _iter_recent_sessions(1.0, now=now, manager=_fake_manager(tmp))
        self.assertEqual([r["sid"] for r in rows], ["recent1"])

    def test_oldest_first(self):
        import tempfile
        from pathlib import Path

        from src.agent.session_compactor import _iter_recent_sessions

        tmp = Path(tempfile.mkdtemp(prefix="_sc_tests_"))
        now = time.time()
        _mk_session(tmp, "newer", [{"role": "user", "content": "x"}],
                    mtime=now - 60)
        _mk_session(tmp, "older", [{"role": "user", "content": "x"}],
                    mtime=now - 7200)
        rows = _iter_recent_sessions(1.0, now=now, manager=_fake_manager(tmp))
        self.assertEqual([r["sid"] for r in rows], ["older", "newer"])


class TestTranscript(unittest.TestCase):
    def test_caps_and_error_rows(self):
        from src.agent.session_compactor import _transcript

        msgs = [
            {"role": "user", "content": "u" * 700},       # user cap 600
            {"role": "assistant",
             "content": "[error] something blew up: " + "e" * 500},
            {"role": "assistant", "content": "a" * 700},  # asst cap 400
        ]
        t = _transcript(msgs)
        self.assertIn("user: " + "u" * 600, t)
        self.assertNotIn("u" * 601, t)
        self.assertIn("assistant: [error] something blew up", t)
        self.assertIn("assistant: " + "a" * 400, t)
        self.assertNotIn("a" * 401, t)

    def test_total_cap_truncation_marker(self):
        from src.agent.session_compactor import _transcript

        msgs = [{"role": "user", "content": "x" * 30_000}]
        t = _transcript(msgs, total_cap=100, user_cap=600, asst_cap=400)
        self.assertIn("…[transcript truncated]", t)
        self.assertLess(len(t), 400)

    def test_skips_non_dicts_and_empty(self):
        from src.agent.session_compactor import _transcript

        t = _transcript([None, {}, "junk",
                         {"role": "user", "content": "ok"}])
        self.assertEqual(t, "user: ok")


class TestCompact(unittest.TestCase):
    def _compact(self, *, days=1.0, dry_run=False, store=None,
                 llm_call=None, now=None, tmp=None):
        import tempfile
        from pathlib import Path

        from src.agent.session_compactor import summarize_sessions

        tmp = tmp or Path(tempfile.mkdtemp(prefix="_sc_tests_"))
        now = now or time.time()
        # default seed: one qualifying session (unless already dropped)
        if not any(tmp.glob("*.json")):
            _mk_session(tmp, "aaaa1111",
                        [{"role": "user", "content": A},
                         {"role": "assistant", "content": B}],
                        mtime=now - 3600)
        return summarize_sessions(
            days=days, dry_run=dry_run, store=store,
            llm_call=llm_call, now=now,
            manager=_fake_manager(tmp),
        )

    def test_writes_payload_shape(self):
        store = FakeStore()
        r = self._compact(store=store, llm_call=lambda p: "第一行\n第二行")
        self.assertTrue(r["ok"])
        self.assertEqual(r["summarized"], 1)
        row = store.rows[0]
        self.assertEqual(row["kind"], "session-summary")
        self.assertEqual(row["source"], "aaaa1111")
        self.assertEqual(row["session_id"], "aaaa1111")
        self.assertIn("session aaaa1111「", row["content"])
        self.assertIn("第一行", row["content"])
        self.assertTrue(r.get("verified"))

    def test_idempotent_skip(self):
        store = FakeStore()
        r1 = self._compact(store=store, llm_call=lambda p: "s1")
        r2 = self._compact(store=store, llm_call=lambda p: "s1-again")
        self.assertEqual(r1["summarized"], 1)
        self.assertEqual(r2["summarized"], 0)
        self.assertEqual(r2["skipped_existing"], 1)
        self.assertEqual(len(store.rows), 1)  # no duplicate

    def test_dry_run_no_write(self):
        store = FakeStore()
        r = self._compact(store=store, dry_run=True,
                          llm_call=lambda p: "dry")
        self.assertNotIn("verified", r)
        self.assertEqual(store.rows, [])
        self.assertIn("would_write", r["summaries"][0])

    def test_llm_failure_isolated(self):
        import tempfile
        from pathlib import Path

        store = FakeStore()
        tmp = Path(tempfile.mkdtemp(prefix="_sc_tests_"))
        now = time.time()
        _mk_session(tmp, "sess0001", [{"role": "user", "content": A}],
                    mtime=now - 60)
        _mk_session(tmp, "sess0002", [{"role": "user", "content": B}],
                    mtime=now - 120)

        def bad_call(p):
            return "" if "sess0001" in p else "好的摘要"

        r = self._compact(store=store, llm_call=bad_call,
                          now=now, tmp=tmp)
        self.assertFalse(r["ok"])
        self.assertEqual(r["summarized"], 1)   # sess0002 still written
        self.assertEqual(r["failed"][0]["sid"], "sess0001")
        self.assertEqual(len(store.rows), 1)

    def test_write_failure_never_raises(self):
        store = FakeStore(fail_write=True)
        r = self._compact(store=store, llm_call=lambda p: "any")
        self.assertFalse(r["ok"])
        self.assertEqual(r["summarized"], 0)

    def test_silent_write_loss_stops_early(self):
        store = FakeStore(hide_writes=True)  # remember OK, recall sees none
        r = self._compact(store=store, llm_call=lambda p: "any")
        self.assertFalse(r["ok"])
        self.assertTrue(r["stopped_early"])
        self.assertIn("verification failed", r["error"])

    def test_gate_off(self):
        os.environ["QDRANT_SESSION_SUMMARY_SINK"] = "off"
        try:
            store = FakeStore()
            r = self._compact(store=store, llm_call=lambda p: "s")
            self.assertFalse(r["ok"])
            self.assertIn("disabled", r["error"])
            self.assertEqual(store.rows, [])
        finally:
            _clean_env(None)

    def test_llm_pin_default(self):
        import src.agent.session_compactor as sc

        self.assertEqual(sc.default_llm_pin(), "ollama:qwen2.5:7b")
        os.environ["QDRANT_SESSION_SUMMARY_LLM"] = "ollama:gpt-oss:20b"
        try:
            self.assertEqual(sc.default_llm_pin(), "ollama:gpt-oss:20b")
        finally:
            _clean_env(None)

    def test_llm_call_seam_receives_prompt(self):
        import tempfile
        from pathlib import Path

        store = FakeStore()
        tmp = Path(tempfile.mkdtemp(prefix="_sc_tests_"))
        now = time.time()
        _mk_session(tmp, "aaaa1111", [{"role": "user", "content": A}],
                    mtime=now - 60)
        seen = []
        self._compact(store=store, now=now, tmp=tmp,
                      llm_call=lambda p: seen.append(p) or "摘要")
        self.assertEqual(len(seen), 1)
        self.assertIn("aaaa1111", seen[0])
        self.assertIn("台積電", seen[0])


class TestHostTool(unittest.TestCase):
    """MemoryLocalHost surface: 7 tools + the T+1 dispatch."""

    def _host(self):
        from src.mcp_client.host import MemoryLocalHost

        return MemoryLocalHost(store=FakeStore())

    def test_tool_names(self):
        names = [t.name for t in self._host().list_tools()]
        self.assertIn("summarize_sessions", names)
        self.assertEqual(
            names,
            ["remember_info", "search_memory", "list_recent_memories",
             "forget_memory", "purge_memories", "suggest_memory_cleanup",
             "summarize_sessions"])

    def test_schema_fields(self):
        tools = {t.name: t for t in self._host().list_tools()}
        props = tools["summarize_sessions"].input_schema["properties"]
        self.assertEqual(
            sorted(props.keys()), ["days", "dry_run", "llm"])
        self.assertIsNone(
            tools["summarize_sessions"].input_schema.get("required"))

    def test_handler_delegation(self):
        import src.agent.session_compactor as sc

        received = {}

        def _stub(*pw, **kw):
            received.update(kw)
            return {"ok": True, "summarized": 2}

        h = self._host()
        with mock.patch.object(sc, "summarize_sessions", _stub):
            out = h._summarize_sessions({"days": 7, "dry_run": True})
        self.assertEqual(out, {"ok": True, "summarized": 2})
        self.assertEqual(received.get("days"), 7.0)
        self.assertIs(received.get("dry_run"), True)

    def test_handler_gate_off(self):
        os.environ["QDRANT_SESSION_SUMMARY_SINK"] = "off"
        try:
            out = self._host()._summarize_sessions({})
            self.assertIn("error", out)
        finally:
            _clean_env(None)

    def test_handler_never_raises(self):
        import src.agent.session_compactor as sc

        def _boom(*pw, **kw):
            raise RuntimeError("boom")

        h = self._host()
        with mock.patch.object(sc, "summarize_sessions", _boom):
            out = h._summarize_sessions({})
        self.assertIn("error", out)


class TestStdioMirror(unittest.TestCase):
    def test_dispatch_has_compactor(self):
        import src.mcp_server.memory_server as srv

        self.assertIn("summarize_sessions", srv._DISPATCH)
        self.assertIn("suggest_memory_cleanup", srv._DISPATCH)

    def test_surface_counts_match_host(self):
        import src.mcp_server.memory_server as srv

        names = [t.name for t in srv._host.list_tools()]
        self.assertEqual(len(set(names)), len(srv._DISPATCH))
        self.assertEqual(
            len(names), 7)


class TestBootSeed(unittest.TestCase):
    """gui/api.py::_ensure_session_summary_schedule (127.0 pattern)."""

    def setUp(self):
        import tempfile
        from pathlib import Path

        self._old_home = os.environ.get("QDRANT_RAG_HOME")
        self.home = Path(tempfile.mkdtemp(prefix="_sc_home_"))
        os.environ["QDRANT_RAG_HOME"] = str(self.home)

    def tearDown(self):
        if self._old_home is None:
            os.environ.pop("QDRANT_RAG_HOME", None)
        else:
            os.environ["QDRANT_RAG_HOME"] = self._old_home
        _clean_env(None)

    def _stub_agent(self, started):
        class S:
            def start_scheduler(self):
                started.append(True)

        return S()

    def test_creates_schedule_with_pin(self):
        import gui.api as gam
        from src.agent.scheduler import ScheduleManager
        from src.agent.session_compactor import SCHEDULE_PROMPT

        os.environ["QDRANT_SESSION_SUMMARY_CRON"] = "0 3 * * *"
        started = []
        api = gam.Api.__new__(gam.Api)
        api._ensure_session_summary_schedule(self._stub_agent(started))
        s = ScheduleManager().find_by_name("每日對話摘要沉澱（T+1）")
        self.assertIsNotNone(s)
        self.assertEqual(s.cron, "0 3 * * *")
        self.assertTrue(s.enabled)
        self.assertEqual(s.llm, "ollama:qwen2.5:7b")
        self.assertEqual(s.prompt, SCHEDULE_PROMPT)
        self.assertTrue(started)

    def test_idempotent(self):
        import gui.api as gam
        from src.agent.scheduler import ScheduleManager

        os.environ["QDRANT_SESSION_SUMMARY_CRON"] = "0 3 * * *"
        api = gam.Api.__new__(gam.Api)
        api._ensure_session_summary_schedule(self._stub_agent([]))
        first = ScheduleManager().find_by_name("每日對話摘要沉澱（T+1）")
        api._ensure_session_summary_schedule(self._stub_agent([]))
        second = ScheduleManager().find_by_name("每日對話摘要沉澱（T+1）")
        self.assertEqual(first.id, second.id)

    def test_gate_off_no_schedule(self):
        import gui.api as gam
        from src.agent.scheduler import ScheduleManager

        gam.Api.__new__(gam.Api)._ensure_session_summary_schedule(
            object())
        self.assertEqual(ScheduleManager().list(), [])

    def test_never_raises_on_save_failure(self):
        import gui.api as gam
        from src.agent.scheduler import ScheduleManager

        os.environ["QDRANT_SESSION_SUMMARY_CRON"] = "0 3 * * *"

        class BoomMgr(ScheduleManager):
            def save(self, s):
                raise RuntimeError("disk full")

        with mock.patch("src.agent.scheduler.ScheduleManager", BoomMgr):
            gam.Api.__new__(gam.Api)._ensure_session_summary_schedule(
                object())  # must not raise

    def test_prewarm_wiring(self):
        import io

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        src = io.open(os.path.join(root, "gui", "api.py"),
                      encoding="utf-8").read()
        self.assertIn("_ensure_session_summary_schedule(agent)", src)
        i_maint = src.index(
            "self._ensure_memory_maintenance_schedule(agent)")
        i_sess = src.index(
            "self._ensure_session_summary_schedule(agent)")
        self.assertGreater(i_sess, i_maint)


class TestAgentPrompt(unittest.TestCase):
    def test_session_summary_note_and_task_result_exclusion(self):
        import io

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        src = io.open(
            os.path.join(root, "src", "agent", "agent.py"),
            encoding="utf-8").read()
        self.assertIn('exclude_kinds=("task-result",)', src)
        self.assertIn("kind=session-summary 自動沉澱", src)
        self.assertIn("search_chat_history", src)
        # the v0.9.111.0 task-result note stays verbatim
        self.assertIn("不會自動出現在這裡", src)
        # module prompt seam feeds the boot-seeded schedule
        from src.agent.session_compactor import SCHEDULE_PROMPT

        self.assertIn("summarize_sessions", SCHEDULE_PROMPT)


class TestModuleMain(unittest.TestCase):
    def test_main_dry_run_contract(self):
        import contextlib
        import io

        from src.agent import session_compactor as sc

        buf = io.StringIO()
        with mock.patch.object(
            sc, "summarize_sessions",
            lambda **kw: {"ok": True, "dry_run": kw.get("dry_run"),
                          "days": kw.get("days")}), \
                contextlib.redirect_stdout(buf):
            rc = sc.main(["--dry-run", "--days", "7"])
        self.assertEqual(rc, 0)
        self.assertIn('"days": 7.0', buf.getvalue())
        self.assertIn('"dry_run": true', buf.getvalue())


class TestLLMCallRouting(unittest.TestCase):
    """_default_llm_call 的 pin 分流（v0.9.133.3 llama.cpp CPU 上線）。

    ``llamacpp:<model>`` 與 ``ollama:<model>`` 必須走各自的本機直連
    （agent 的 ollama 分支撞的是雲端 OLLAMA_BASE_URL）；其他 pin 走
    ``Agent(task_llm=)`` 鏈。"""

    def test_llamacpp_pin_hits_direct_call(self):
        from src.agent import session_compactor as sc

        calls = []
        with mock.patch.object(
            sc, "_llamacpp_direct_call",
            side_effect=lambda m, p: calls.append((m, p)) or "s1"), \
                mock.patch.object(sc, "Agent", create=True) as fake_agent:
            out = sc._default_llm_call("pp", "llamacpp:qwen2.5-7b-cpu")
        self.assertEqual(out, "s1")
        self.assertEqual(calls, [("qwen2.5-7b-cpu", "pp")])
        fake_agent.assert_not_called()

    def test_ollama_pin_hits_direct_call(self):
        from src.agent import session_compactor as sc

        calls = []
        with mock.patch.object(
            sc, "_ollama_direct_call",
            side_effect=lambda m, p: calls.append((m, p)) or "s2"), \
                mock.patch.object(sc, "Agent", create=True) as fake_agent:
            out = sc._default_llm_call("pp", "ollama:qwen2.5:7b")
        self.assertEqual(out, "s2")
        self.assertEqual(calls, [("qwen2.5:7b", "pp")])
        fake_agent.assert_not_called()

    def test_other_pin_rides_agent_chain(self):
        from src.agent import session_compactor as sc

        class FakeCompletion:
            class choices:  # noqa: N801 - mimic openai shape
                pass

        fake = mock.MagicMock()
        fake.choices[0].message.content = "s3"
        agent = mock.MagicMock()
        agent._create_validated.return_value = (fake, True)
        with mock.patch("src.agent.agent.Agent",
                        create=True, return_value=agent) as made, \
                mock.patch.object(
                    sc, "_llamacpp_direct_call") as lp, \
                mock.patch.object(
                    sc, "_ollama_direct_call") as ol:
            out = sc._default_llm_call("pp", "openrouter:z/ai/glm-5.3")
        self.assertEqual(out, "s3")
        made.assert_called_once_with(task_llm="openrouter:z/ai/glm-5.3")
        lp.assert_not_called()
        ol.assert_not_called()

    def test_llamacpp_url_default_and_env(self):
        from src.agent import session_compactor as sc

        os.environ.pop("QDRANT_SESSION_SUMMARY_LLAMACPP_URL", None)
        self.assertEqual(sc._local_llamacpp_url(),
                         "http://127.0.0.1:8090/v1")
        os.environ["QDRANT_SESSION_SUMMARY_LLAMACPP_URL"] = (
            "http://127.0.0.1:8091/v1/")
        try:
            self.assertEqual(sc._local_llamacpp_url(),
                             "http://127.0.0.1:8091/v1")
        finally:
            os.environ.pop("QDRANT_SESSION_SUMMARY_LLAMACPP_URL", None)

    def test_default_pin_still_ollama(self):
        from src.agent import session_compactor as sc

        os.environ.pop("QDRANT_SESSION_SUMMARY_LLM", None)
        self.assertEqual(sc.default_llm_pin(), "ollama:qwen2.5:7b")