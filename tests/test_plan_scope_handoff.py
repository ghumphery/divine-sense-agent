# -*- coding: utf-8 -*-
"""v0.9.111.0 — plan/stage 工具作用域 (tools_allow) + 機械交接檔
(out/<stage_id>.json) + plan 終局記憶 sink + host schema 接線。

鎖定：
* compile 端 fail-closed 驗證（非 list/空 list/超量 → PlanError）。
* _run_stage 把 tools_allow 經 chat_turn(active_tool_names=…)
  （v0.9.14.0 per-TOOL 閘）機制化強制 — 無 allow 時不帶參數
  （byte-identical legacy）。
* 交接約定：驅動器在 stage 終局「之前」寫 out/<sid>.json；下游
  stage 的 compiled_prompt 注入上游交接檔的絕對路徑。
* plan 終局 kind=task-result sink（store-NOT-recall）。
"""
from __future__ import annotations

import json
import time

import pytest


@pytest.fixture()
def core(tmp_path, monkeypatch):
    monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path))
    monkeypatch.setenv("QDRANT_FILESYSTEM_WRITE_ROOT", str(tmp_path))
    import src.agent.skill_compiler as sc

    sc._reset_for_tests()
    sc.set_agent_factory(None)
    sc.set_plan_notify_callback(None)
    yield sc
    sc._reset_for_tests()
    sc.set_agent_factory(None)
    sc.set_plan_notify_callback(None)


class RecAgent:
    """Fake Agent that records the chat_turn kwargs."""

    def __init__(self, answer="done"):
        self.answer = answer
        self.kwargs = None

    def chat_turn(self, **kw):
        self.kwargs = kw
        return (self.answer, "", [])


def _wait_plan(core, pid, status, timeout_s=15.0):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        rec = core.get_plan_status(pid)
        if rec is not None and rec["status"] == status:
            return rec
        time.sleep(0.15)
    return core.get_plan_status(pid)


# ─── compile: tools_allow validation ─────────────────────────────

class TestToolsAllowValidation:
    def test_plan_level_ok(self, core):
        rec = core.compile_plan(
            name="sa", tools_allow=["read_file", "web_search"],
            stages=[{"title": "a", "prompt": "pa"}],
        )
        assert rec["tools_allow"] == ["read_file", "web_search"]
        assert rec["stages"][0]["tools_allow"] == [
            "read_file", "web_search",
        ]

    def test_stage_overrides_plan(self, core):
        rec = core.compile_plan(
            name="sb", tools_allow=["read_file"],
            stages=[{"title": "a", "prompt": "pa",
                     "tools_allow": ["get_current_time"]}],
        )
        assert rec["stages"][0]["tools_allow"] == ["get_current_time"]

    def test_tuple_accepted_and_deduped(self, core):
        rec = core.compile_plan(
            name="sc", tools_allow=("read_file", "read_file"),
            stages=[{"title": "a", "prompt": "pa"}],
        )
        assert rec["tools_allow"] == ["read_file"]

    def test_string_rejected(self, core):
        with pytest.raises(core.PlanError,
                           match="list of MCP tool names"):
            core.compile_plan(
                name="sd", tools_allow="read_file",
                stages=[{"title": "a", "prompt": "pa"}],
            )
    def test_empty_list_rejected(self, core):
        with pytest.raises(core.PlanError, match="empty"):
            core.compile_plan(
                name="se", tools_allow=[],
                stages=[{"title": "a", "prompt": "pa"}],
            )

    def test_oversized_rejected(self, core):
        with pytest.raises(core.PlanError, match="at most 60"):
            core.compile_plan(
                name="sf", tools_allow=[f"t{i}" for i in range(61)],
                stages=[{"title": "a", "prompt": "pa"}],
            )

    def test_absent_is_none_full_surface(self, core):
        rec = core.compile_plan(
            name="sg", stages=[{"title": "a", "prompt": "pa"}],
        )
        assert rec["tools_allow"] is None
        assert rec["stages"][0]["tools_allow"] is None


# ─── run: scope is MECHANICAL via chat_turn ──────────────────────

class TestRunStageScope:
    def test_scope_threaded_to_chat_turn(self, core):
        agent = RecAgent()
        core.set_agent_factory(lambda llm=None: agent)
        rec = core.compile_plan(
            name="r1",
            stages=[{"title": "做查詢", "prompt": "pa",
                     "tools_allow": ["read_file", "web_search"]}],
        )
        core.run_plan(rec["id"])
        final = _wait_plan(core, rec["id"], "done")
        assert final is not None and final["status"] == "done"
        assert agent.kwargs.get("active_tool_names") == [
            "read_file", "web_search",
        ]

    def test_no_scope_kwarg_when_unset(self, core):
        agent = RecAgent()
        core.set_agent_factory(lambda llm=None: agent)
        rec = core.compile_plan(
            name="r2", stages=[{"title": "a", "prompt": "pa"}],
        )
        core.run_plan(rec["id"])
        _wait_plan(core, rec["id"], "done")
        assert "active_tool_names" not in agent.kwargs

    def test_stage_level_beats_plan_level_at_runtime(self, core):
        agent = RecAgent()
        core.set_agent_factory(lambda llm=None: agent)
        rec = core.compile_plan(
            name="r3", tools_allow=["web_search"],
            stages=[{"title": "a", "prompt": "pa",
                     "tools_allow": ["run_grep"]}],
        )
        core.run_plan(rec["id"])
        _wait_plan(core, rec["id"], "done")
        assert agent.kwargs.get("active_tool_names") == ["run_grep"]
# ─── handoff convention: compile-time injection + driver write ───

class TestHandoffConvention:
    def test_compile_injects_dep_handoff_paths(self, core):
        rec = core.compile_plan(
            name="h1",
            stages=[
                {"title": "抓資料", "prompt": "p1"},
                {"title": "算指標", "prompt": "p2",
                 "depends_on": ["抓資料"]},
            ],
        )
        import src.agent.skill_compiler as sc

        a, b = rec["stages"][0], rec["stages"][1]
        # every stage records its OWN handoff path
        assert a["handoff_path"] == f"out/{a['id']}.json"
        assert b["handoff_path"] == f"out/{b['id']}.json"
        # the dependent's prompt names the upstream handoff (abs path)
        assert "上游交接檔" in b["compiled_prompt"]
        assert sc.handoff_abs(a["id"]) in b["compiled_prompt"]
        assert a["title"] in b["compiled_prompt"]
        # the upstream stage (no deps) does NOT get the read rule
        assert "上游交接檔" not in a["compiled_prompt"]


class TestHandoffDriverWrite:
    def test_driver_writes_both_handoff_files(self, core, tmp_path):
        core.set_agent_factory(lambda llm=None: RecAgent())
        rec = core.compile_plan(
            name="hw",
            stages=[
                {"title": "抓資料", "prompt": "p1"},
                {"title": "算指標", "prompt": "p2",
                 "depends_on": ["抓資料"]},
            ],
        )
        core.run_plan(rec["id"])
        _wait_plan(core, rec["id"], "done")
        a = rec["stages"][0]
        fa = tmp_path / "out" / f"{a['id']}.json"
        fb = tmp_path / "out" / f"{rec['stages'][1]['id']}.json"
        assert fa.exists() and fb.exists()
        d = json.loads(fa.read_text(encoding="utf-8"))
        assert d["status"] == "done"
        assert d["plan_id"] == rec["id"]
        assert d["stage_id"] == a["id"]
        assert d["summary"] == "done"
        assert d["session"].startswith("plan-")
        assert d["artifacts"] == []
        db = json.loads(fb.read_text(encoding="utf-8"))
        assert db["status"] == "done"
    def test_verify_failure_handoff_is_honest(self, core, tmp_path):
        rec = core.compile_plan(
            name="hw2",
            stages=[{
                "title": "產報告", "prompt": "p",
                "verify": {"path": "out/missing_report.json"},
            }],
        )
        core.set_agent_factory(lambda llm=None: RecAgent("宣稱完成"))
        core.run_plan(rec["id"])
        _wait_plan(core, rec["id"], "failed")
        a = rec["stages"][0]
        d = json.loads(
            (tmp_path / "out" / f"{a['id']}.json").read_text(
                encoding="utf-8"))
        # verify FAILED → the handoff is honest: status=failed
        assert d["status"] == "failed"

    def test_failed_handoff_write_degrades(self, core, tmp_path,
                                           monkeypatch):
        """A handoff-write hiccup must NEVER break the stage run."""
        import src.agent.skill_compiler as sc

        blocker = tmp_path / "blocker"
        blocker.write_text("x", encoding="utf-8")
        monkeypatch.setattr(sc, "_handoff_root", lambda: blocker)
        core.set_agent_factory(lambda llm=None: RecAgent())
# ─── plan-terminal memory sink (store-NOT-recall) ────────────────

class TestPlanSink:
    def test_plan_terminal_sinks_task_result(self, core, monkeypatch):
        import src.agent.memory_store as ms

        seen = []
        monkeypatch.setattr(
            ms, "store_task_result",
            lambda **kw: seen.append(kw) or {"id": "m"},
        )
        monkeypatch.setattr(ms, "task_sink_enabled", lambda: True)
        rec = core.compile_plan(
            name="sk1", description="測試計畫",
            stages=[{"title": "a", "prompt": "p"}],
        )
        core.set_agent_factory(lambda llm=None: RecAgent())
        core.run_plan(rec["id"])
        _wait_plan(core, rec["id"], "done")
        deadline = time.monotonic() + 5.0
        while not seen and time.monotonic() < deadline:
            time.sleep(0.1)
        assert seen, "plan terminal must fire the memory sink"
        kw = seen[0]
        assert kw["source"] == f"plan:{rec['id']}"
        assert kw["title"].startswith("[plan] ")
        assert "-> done" in kw["title"]
        assert "[a] done" in kw["answer"]

    def test_sink_disabled_is_noop(self, core, monkeypatch):
        import src.agent.memory_store as ms

        seen = []

        class _NoStore:
            def remember(self, text, kind="", source="", session_id=""):
                seen.append({"kind": kind, "text": text})
                return {"id": "fake"}

        monkeypatch.setattr(ms, "_shared_store", lambda: _NoStore())
        monkeypatch.setenv("QDRANT_TASK_MEMORY_SINK", "off")
        rec = core.compile_plan(
            name="sk2", stages=[{"title": "a", "prompt": "p"}],
        )
        core.set_agent_factory(lambda llm=None: RecAgent())
        core.run_plan(rec["id"])
        _wait_plan(core, rec["id"], "done")
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            time.sleep(0.1)
        # store_task_result itself short-circuits → NO remember call
        assert seen == []


# ─── host compile schema wires tools_allow ───────────────────────

class TestHostCompileSchema:
    def test_host_compile_accepts_tools_allow(self, core):
        from src.mcp_client.host import TactiLocalHost

        h = TactiLocalHost()
        out = h._compile({
            "name": "host-sc",
            "tools_allow": ["read_file"],
            "stages": [{"title": "a", "prompt": "p"}],
        })
        assert out.get("ok") is True, out
        rec = core.get_plan_status(out["plan_id"])
        assert rec["stages"][0]["tools_allow"] == ["read_file"]

    def test_host_compile_rejects_bad_tools_allow(self, core):
        from src.mcp_client.host import TactiLocalHost

        h = TactiLocalHost()
        out = h._compile({
            "name": "host-bad",
            "tools_allow": "read_file",
            "stages": [{"title": "a", "prompt": "p"}],
        })
        assert "list of MCP tool names" in (out.get("error") or "")

    def test_stage_schema_has_tools_allow(self):
        from src.mcp_client.host import TactiLocalHost

        h = TactiLocalHost()
        comp = next(
            t for t in h.list_tools() if t.name == "compile_plan"
        )
        items = comp.input_schema["properties"]["stages"]["items"]
        assert "tools_allow" in items["properties"]
        assert "tools_allow" in comp.input_schema["properties"]