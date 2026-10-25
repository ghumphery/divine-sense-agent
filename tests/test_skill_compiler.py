# -*- coding: utf-8 -*-
"""v0.9.109.0 — Skill/Workflow compiler + plan executor tests.

Covers: compile validation (empty/unknown deps/cycles/dedupe/wave
packing/budget/verify+notify injection), persistence, run_plan wave
driving (fake agent factory), mechanical verify node (pass+fail),
stop_plan, run_plan_stage retry, host surface + E2E, stdio parity,
prompt-block/toolroute source guards.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest


@pytest.fixture()
def core(tmp_path, monkeypatch):
    monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path))
    import src.agent.skill_compiler as sc

    sc._reset_for_tests()
    sc.set_agent_factory(None)
    sc.set_plan_notify_callback(None)
    yield sc
    sc._reset_for_tests()
    sc.set_agent_factory(None)
    sc.set_plan_notify_callback(None)


class FakeAgent:
    def __init__(self, answer="done", delay=0.0):
        self.answer = answer
        self.delay = delay

    def chat_turn(self, **kw):
        time.sleep(self.delay)
        return (self.answer, "", [])


def _wire(core, answer="done", delay=0.0):
    core.set_agent_factory(lambda: FakeAgent(answer, delay))


# ─── compile ──────────────────────────────────────────────────────

class TestCompileValidation:
    def test_minimal_ok(self, core):
        rec = core.compile_plan(
            name="t1", stages=[{"title": "s1", "prompt": "do X"}],
        )
        assert rec["status"] == "compiled"
        assert len(rec["waves"]) == 1
        assert rec["stages"][0]["budget"] >= 1
        assert "執行規則" in rec["stages"][0]["compiled_prompt"]

    def test_missing_name_or_prompt(self, core):
        with pytest.raises(core.PlanError):
            core.compile_plan(name="", stages=[{"title": "a",
                                                "prompt": "p"}])
        with pytest.raises(core.PlanError):
            core.compile_plan(name="x", stages=[])
        with pytest.raises(core.PlanError):
            core.compile_plan(
                name="x", stages=[{"title": "a", "prompt": ""}],
            )

    def test_dependency_by_title_and_waves(self, core):
        rec = core.compile_plan(
            name="wf", stages=[
                {"title": "下載檔案", "prompt": "A"},
                {"title": "轉檔", "prompt": "B", "depends_on": ["下載檔案"]},
            ],
        )
        w0, w1 = rec["waves"]
        # dependency → wave 2 for 轉檔；下載檔案 alone in wave 1
        assert len(w0) == 1 and len(w1) == 1
        dep_stage = next(s for s in rec["stages"] if s["title"] == "轉檔")
        assert dep_stage["wave"] == 1

    def test_unknown_dependency(self, core):
        with pytest.raises(core.PlanError):
            core.compile_plan(
                name="x", stages=[
                    {"title": "a", "prompt": "p",
                     "depends_on": ["不存在的"]},
                ],
            )

    def test_cycle_detected(self, core):
        with pytest.raises(core.PlanError):
            core.compile_plan(
                name="x", stages=[
                    {"title": "a", "prompt": "p", "depends_on": ["b"]},
                    {"title": "b", "prompt": "p", "depends_on": ["a"]},
                ],
            )

    def test_wave_cap_packing(self, core):
        rec = core.compile_plan(
            name="many",
            stages=[{"title": f"s{i}", "prompt": "p"}
                    for i in range(5)],
            wave_cap=2,
        )
        assert rec["waves"] == [["s0-1", "s1-2"], ["s2-3", "s3-4"],
                                ["s4-5"]]

    def test_dedupe_identical_stage(self, core):
        rec = core.compile_plan(
            name="dup",
            stages=[
                {"title": "same", "prompt": "p1"},
                {"title": "other", "prompt": "p2",
                 "depends_on": ["same"]},
                {"title": "same", "prompt": "p1"},
            ],
        )
        assert len(rec["stages"]) == 2
        assert rec["stages"][0].get("deduped") is True

    def test_dedupe_with_extra_deps_rejected(self, core):
        # a duplicate carrying depends_on would manufacture a reversed
        # edge (same→other→same cycle) — fail-closed PlanError.
        with pytest.raises(core.PlanError):
            core.compile_plan(
                name="dupcycle",
                stages=[
                    {"title": "same", "prompt": "p1"},
                    {"title": "other", "prompt": "p2",
                     "depends_on": ["same"]},
                    {"title": "same", "prompt": "p1",
                     "depends_on": ["other"]},
                ],
            )

    def test_budget_env_resolution(self, core, monkeypatch):
        monkeypatch.setenv("MAX_TOOL_ITERATIONS", "31")
        rec = core.compile_plan(
            name="b", stages=[{"title": "a", "prompt": "p"}],
        )
        assert rec["stages"][0]["budget"] == 31

    def test_verify_and_notify_injection(self, core):
        rec = core.compile_plan(
            name="v",
            stages=[{
                "title": "產出報告", "prompt": "p",
                "verify": {"path": "out/report.json"},
            }],
            notify_chat_id=<YOUR_TELEGRAM_CHAT_ID>,
        )
        cp = rec["stages"][0]["compiled_prompt"]
        assert "verify_artifact" in cp
        assert "out/report.json" in cp
        assert "send_telegram_message" in cp
        assert "<YOUR_TELEGRAM_CHAT_ID>" in cp

    def test_max_stages_cap(self, core):
        with pytest.raises(core.PlanError):
            core.compile_plan(
                name="big",
                stages=[{"title": f"s{i}", "prompt": "p"}
                        for i in range(21)],
            )


class TestPersistence:
    def test_round_trip_and_list(self, core):
        rec = core.compile_plan(
            name="lib", stages=[{"title": "a", "prompt": "p"}],
        )
        other = core.load_plan(rec["id"])
        assert other["id"] == rec["id"]
        listed = core.list_plans()
        assert listed["plans"][0]["name"] == "lib"
        assert listed["plans"][0]["status"] == "compiled"

    def test_status_and_missing(self, core):
        assert core.get_plan_status("nope-xxx") is None
        rec = core.compile_plan(
            name="st", stages=[{"title": "a", "prompt": "p"}],
        )
        st = core.get_plan_status(rec["id"])
        assert st["status"] == "compiled"
        assert st["stages"][0]["status"] == "pending"


# ─── execution ────────────────────────────────────────────────────

def _wait_plan(core, plan_id, status, timeout=15.0):
    dl = time.monotonic() + timeout
    last = None
    while time.monotonic() < dl:
        time.sleep(0.3)
        last = core.get_plan_status(plan_id)
        if last["status"] == status:
            return last
    return last


class TestRunPlan:
    def test_two_waves_driven_to_done(self, core):
        rec = core.compile_plan(
            name="e2e",
            stages=[
                {"title": "抓資料", "prompt": "p1"},
                {"title": "算指標", "prompt": "p2",
                 "depends_on": ["抓資料"]},
            ],
        )
        _wire(core)
        out = core.run_plan(rec["id"])
        assert out["ok"] is True
        final = _wait_plan(core, rec["id"], "done")
        assert final is not None and final["status"] == "done"
        assert all(s["status"] == "done" for s in final["stages"])

    def test_verify_fail_fails_stage(self, core):
        rec = core.compile_plan(
            name="vfail",
            stages=[{
                "title": "產報告", "prompt": "p",
                "verify": {"path": "out/missing_report.json"},
            }],
        )
        _wire(core, "claimed done")
        core.run_plan(rec["id"])
        final = _wait_plan(core, rec["id"], "failed")
        st = final["stages"][0]
        assert st["status"] == "failed"
        assert st["verify_ok"] is False
        assert "does not exist" in (st["error"] or "")

    def test_verify_pass_ok(self, core, tmp_path):
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        (out_dir / "report.json").write_text('{"k": "v"}',
                                             encoding="utf-8")
        rec = core.compile_plan(
            name="vpass",
            stages=[{
                "title": "產報告", "prompt": "p",
                "verify": {
                    "path": str(out_dir / "report.json"),
                    "checks": [{"type": "min_bytes", "value": 5}],
                },
            }],
        )
        _wire(core)
        core.run_plan(rec["id"])
        final = _wait_plan(core, rec["id"], "done")
        assert final["stages"][0]["verify_ok"] is True

    def test_stop_policy_skips_later_waves(self, core):
        rec = core.compile_plan(
            name="stoppl",
            stages=[
                {"title": "w1工作", "prompt": "p",
                 "verify": {"path": "nope/never.json"}},
                {"title": "w2工作", "prompt": "p2",
                 "depends_on": ["w1工作"]},
            ],
        )
        _wire(core)
        core.run_plan(rec["id"])
        final = _wait_plan(core, rec["id"], "failed")
        st2 = next(s for s in final["stages"]
                   if s["title"] == "w2工作")
        assert st2["status"] == "skipped"

    def test_stage_timeout(self, core, monkeypatch):
        # kills-path contract (v0.9.107.1 exact) — isolate from the
        # live-.env JEV_MODE=live leak (src.config load_dotenv runs
        # when ANY import pulls it in): the arbiter would otherwise
        # make a real OpenRouter call (~8s) inside this timing test.
        monkeypatch.setenv("QDRANT_SKILL_TIMEOUT_JEV", "off")
        rec = core.compile_plan(
            name="slow",
            stages=[{"title": "慢", "prompt": "p"}],
            stage_timeout_s=1,
        )
        _wire(core, delay=6.0)
        t0 = time.monotonic()
        core.run_plan(rec["id"])
        final = _wait_plan(core, rec["id"], "failed", timeout=10)
        assert time.monotonic() - t0 < 8
        assert "timeout" in (final["stages"][0]["error"] or "").lower()

    def test_stop_plan(self, core):
        rec = core.compile_plan(
            name="stopme",
            stages=[{"title": "長工", "prompt": "p"}],
            stage_timeout_s=30,
        )
        _wire(core, delay=8.0)
        core.run_plan(rec["id"])
        time.sleep(0.8)
        assert core.stop_plan(rec["id"])["ok"] is True
        final = _wait_plan(core, rec["id"], "stopped", timeout=15)
        assert final is not None
        assert final["stages"][0]["status"] in ("failed", "skipped")

    def test_plan_notify_callback(self, core):
        seen = []
        core.set_plan_notify_callback(lambda ev: seen.append(ev))
        rec = core.compile_plan(
            name="nfy", stages=[{"title": "a", "prompt": "p"}],
        )
        _wire(core)
        core.run_plan(rec["id"])
        _wait_plan(core, rec["id"], "done")
        plan_ev = [e for e in seen if e.get("kind") == "plan"]
        assert plan_ev and plan_ev[0]["status"] == "done"


class TestResumeAndRetry:
    def test_retry_then_resume_full_loop(self, core, tmp_path):
        artifact = tmp_path / "artifact.json"
        rec = core.compile_plan(
            name="retryw",
            stages=[
                {"title": "產報告", "prompt": "p",
                 "verify": {"path": str(artifact)}},
                {"title": "後續", "prompt": "p2",
                 "depends_on": ["產報告"]},
            ],
        )
        _wire(core, "claimed done (artifact missing)")
        core.run_plan(rec["id"])
        failed = _wait_plan(core, rec["id"], "failed")
        st1 = next(s for s in failed["stages"]
                   if s["title"] == "產報告")
        assert st1["status"] == "failed"
        st2 = next(s for s in failed["stages"]
                   if s["title"] == "後續")
        assert st2["status"] == "skipped"   # stop policy
        # retry the failed stage — artifact now exists → verify passes
        artifact.write_text('{"ok": 1}', encoding="utf-8")
        out = core.run_plan_stage(rec["id"], st1["id"])
        assert out.get("ok") is True, out
        status = _wait_plan_stage(core, rec["id"], st1["id"], "done")
        assert status == "done"
        # resume dependent waves via run_plan (done skips)
        core.run_plan(rec["id"])
        final = _wait_plan(core, rec["id"], "done")
        assert final["progress"] == "2/2"

    def test_done_stage_skipped_on_run_plan(self, core):
        rec = core.compile_plan(
            name="resu",
            stages=[
                {"title": "第一步", "prompt": "p1"},
                {"title": "第二步", "prompt": "p2",
                 "depends_on": ["第一步"]},
            ],
        )
        _wire(core)
        core.run_plan(rec["id"])
        _wait_plan(core, rec["id"], "done")
        # re-run = full reset (skill re-run)
        core.run_plan(rec["id"])
        final = _wait_plan(core, rec["id"], "done")
        assert final["progress"] == "2/2"


def _wait_plan_stage(core, plan_id, stage_id, status, timeout=15.0):
    dl = time.monotonic() + timeout
    while time.monotonic() < dl:
        time.sleep(0.3)
        st = core.get_plan_status(plan_id)
        for s in st["stages"]:
            if s["id"] == stage_id and s["status"] == status:
                return status
    return None


# ─── host surface + E2E ───────────────────────────────────────────

class TestHostSurface:
    def _host(self):
        from src.mcp_client.host import TactiLocalHost

        return TactiLocalHost(mcp_manager=None)

    def test_surfaces(self, core):
        h = self._host()
        names = [t.name for t in h.list_tools()]
        assert names == ["compile_plan", "run_plan", "get_plan_status",
                         "list_plans", "run_plan_stage", "stop_plan",
                         "delete_plan"]
        for t in h.list_tools():
            assert t.server_name == "tacti-local"
            if t.name != "list_plans":      # limit-only, no required
                assert "required" in t.input_schema
            else:
                assert "limit" in t.input_schema["properties"]

    def test_host_e2e_compile_run_status(self, core):
        h = self._host()
        _wire(core)
        out = h._compile({
            "name": "hostE2E",
            "stages": [{"title": "分析", "prompt": "p"}],
        })
        assert out.get("ok") is True
        pid = out["plan_id"]
        r = h._run({"plan_id": pid})
        assert r["ok"] is True
        st = h._status({"plan_id": pid})
        final = _wait_plan(core, pid, "done")
        assert final["progress"] == "1/1"
        lst = h._list({})
        assert any(p["id"] == pid for p in lst["plans"])

    def test_host_compile_bad_plan_error(self, core):
        h = self._host()
        out = h._compile({"name": "bad", "stages": [{"title": "a"}]})
        assert "error" in out


def _make_host():
    from src.mcp_client.host import TactiLocalHost

    return TactiLocalHost(mcp_manager=None)


class TestStdioParity:
    def test_surface_matches(self, core):
        from src.mcp_server.tacti_server import TactiMCPServer

        srv = TactiMCPServer()
        names = [t.name for t in srv._tool_specs()]
        assert names == [t.name for t in _make_host().list_tools()]


class TestSourceGuards:
    def test_agent_prompt_block(self):
        from pathlib import Path

        src = Path("src/agent/agent.py").read_text(encoding="utf-8")
        assert "Tacti plan compiler (v0.9.109+" in src
        assert "compile_plan" in src

    def test_toolroute_trigger(self):
        from pathlib import Path

        src = Path("src/agent/agent.py").read_text(encoding="utf-8")
        assert '"tacti-local"' in src
        assert "戰術" in src
        # v0.9.120.3 — the plan-compiler prompt block is TACTI-branded
        assert "Tacti plan compiler (v0.9.109+" in src

    def test_env_example_block(self):
        src = Path(".env.example").read_text(encoding="utf-8")
        assert "QDRANT_SKILL_STAGE_TIMEOUT_S" in src
        assert "QDRANT_SKILL_WAVE_CAP" in src


class WorkAgent(FakeAgent):
    """FakeAgent + steady tool-dispatch activity (last_tool_dispatches
    / last_usage — the v0.9.117.0 liveness evidence source)."""

    def __init__(self, answer="done", delay=0.0, dispatch_every=0.0):
        super().__init__(answer, delay)
        self.last_tool_dispatches = []
        self.last_usage = {"calls": 0}
        self.dispatch_every = dispatch_every

    def chat_turn(self, **kw):
        assert "meta" not in kw  # guard metadata never reaches chat_turn
        end = time.monotonic() + self.delay
        if self.dispatch_every <= 0:
            time.sleep(max(0.0, end - time.monotonic()))
            return (self.answer, "", [])
        while time.monotonic() < end:
            time.sleep(min(self.dispatch_every,
                           max(0.05, end - time.monotonic())))
            self.last_tool_dispatches.append("run_bash")
            self.last_usage["calls"] = len(self.last_tool_dispatches)
        return (self.answer, "", [])


class TestTimeoutExtension:
    """v0.9.117.0 — stage hard-timeout arbiter: liveness growth /
    live-Jev verdict grant extra windows; abnormal / exhausted /
    gate-off kill (v0.9.107.1 exact message preserved)."""

    def test_liveness_extends_and_completes(self, core):
        rec = core.compile_plan(
            name="big", stages=[{"title": "巨量下載", "prompt": "p"}],
            stage_timeout_s=2,
        )
        core.set_agent_factory(lambda: WorkAgent(delay=7,
                                                 dispatch_every=0.3))
        core.run_plan(rec["id"])
        final = _wait_plan(core, rec["id"], "done", timeout=20)
        st = final["stages"][0]
        assert st["status"] == "done", st.get("error")
        assert core._extensions_for(rec["id"], st["id"]) >= 1

    def test_jev_verdict_extends_silent_stage(self, core, monkeypatch):
        calls = {"n": 0}

        def _v(text):
            calls["n"] += 1
            return "extend"

        monkeypatch.setattr(core, "_jev_timeout_verdict", _v)
        rec = core.compile_plan(
            name="silent", stages=[{"title": "轉檔", "prompt": "p"}],
            stage_timeout_s=2,
        )
        core.set_agent_factory(lambda: WorkAgent(delay=7))
        core.run_plan(rec["id"])
        final = _wait_plan(core, rec["id"], "done", timeout=20)
        assert final["stages"][0]["status"] == "done"
        assert calls["n"] >= 1

    def test_jev_abnormal_fails_v09107_exact(self, core, monkeypatch):
        monkeypatch.setattr(core, "_jev_timeout_verdict", lambda t: None)
        rec = core.compile_plan(
            name="hang", stages=[{"title": "掛死", "prompt": "p"}],
            stage_timeout_s=1,
        )
        core.set_agent_factory(lambda: WorkAgent(delay=3.5))
        t0 = time.monotonic()
        core.run_plan(rec["id"])
        final = _wait_plan(core, rec["id"], "failed", timeout=10)
        st = final["stages"][0]
        assert st["status"] == "failed"
        assert st["error"] == "stage hard timeout after 1s"
        assert time.monotonic() - t0 < 6

    def test_exhausted_extensions_fail_with_suffix(self, core,
                                                   monkeypatch):
        monkeypatch.setenv("QDRANT_SKILL_STAGE_TIMEOUT_EXTENSIONS", "2")
        monkeypatch.setattr(core, "_jev_timeout_verdict",
                            lambda t: "extend")
        rec = core.compile_plan(
            name="cap", stages=[{"title": "超長", "prompt": "p"}],
            stage_timeout_s=1,
        )
        core.set_agent_factory(lambda: WorkAgent(delay=6))
        t0 = time.monotonic()
        core.run_plan(rec["id"])
        final = _wait_plan(core, rec["id"], "failed", timeout=15)
        st = final["stages"][0]
        assert st["status"] == "failed"
        assert st["error"].startswith(
            "stage hard timeout after 1s (+2 extension(s")
        assert time.monotonic() - t0 < 8

    def test_gate_off_never_extends(self, core, monkeypatch):
        monkeypatch.setenv("QDRANT_SKILL_TIMEOUT_JEV", "off")
        monkeypatch.setenv("QDRANT_SKILL_STAGE_TIMEOUT_EXTENSIONS", "0")
        rec = core.compile_plan(
            name="off", stages=[{"title": "長", "prompt": "p"}],
            stage_timeout_s=1,
        )
        core.set_agent_factory(lambda: WorkAgent(delay=3.5,
                                                 dispatch_every=0.2))
        t0 = time.monotonic()
        core.run_plan(rec["id"])
        final = _wait_plan(core, rec["id"], "failed", timeout=10)
        st = final["stages"][0]
        assert st["status"] == "failed"
        assert st["error"] == "stage hard timeout after 1s"
        assert time.monotonic() - t0 < 4


class TestTimeoutArbiterUnits:
    """Pure helpers — verdict prob floor / mock+gate skip / deadline
    math / evidence text."""

    class FakeEng:
        mode = "live"
        is_mock = False

        def __init__(self, choice="normal_long_work", prob=0.6):
            self._choice = choice
            self._prob = prob

        def classify_batch(self, **kw):
            return {"results": [{"id": "stage",
                                 "choice": self._choice,
                                 "prob": self._prob}]}

    def test_verdict_prob_floor(self, core, monkeypatch):
        monkeypatch.setenv("JEV_MODE", "live")
        monkeypatch.setattr(
            "src.jev_tools.JevClassifierEngine",
            lambda: self.FakeEng("normal_long_work", 0.42))
        assert core._jev_timeout_verdict("evidence") is None
        monkeypatch.setattr(
            "src.jev_tools.JevClassifierEngine",
            lambda: self.FakeEng("normal_long_work", 0.60))
        assert core._jev_timeout_verdict("evidence") == "extend"
        monkeypatch.setattr(
            "src.jev_tools.JevClassifierEngine",
            lambda: self.FakeEng("abnormal", 0.9))
        assert core._jev_timeout_verdict("evidence") is None

    def test_verdict_prob_env_override(self, core, monkeypatch):
        monkeypatch.setenv("JEV_MODE", "live")
        monkeypatch.setenv("QDRANT_SKILL_TIMEOUT_JEV_MIN_PROB", "0.3")
        monkeypatch.setattr(
            "src.jev_tools.JevClassifierEngine",
            lambda: self.FakeEng("normal_long_work", 0.42))
        assert core._jev_timeout_verdict("evidence") == "extend"

    def test_verdict_skips_mock_mode(self, core, monkeypatch):
        monkeypatch.setenv("JEV_MODE", "mock")

        class Boom:
            def __init__(self):
                pass

            def classify_batch(self, **kw):
                raise AssertionError("mock must never classify")

        monkeypatch.setattr(
            "src.jev_tools.JevClassifierEngine", Boom)
        assert core._jev_timeout_verdict("evidence") is None

    def test_verdict_gate_off(self, core, monkeypatch):
        monkeypatch.setenv("QDRANT_SKILL_TIMEOUT_JEV", "off")
        monkeypatch.setattr(
            "src.jev_tools.JevClassifierEngine", None)
        assert core._jev_timeout_verdict("evidence") is None

    def test_jev_error_falls_back_to_kill(self, core, monkeypatch):
        monkeypatch.setenv("JEV_MODE", "live")

        class ErrEng:
            mode = "live"

            def classify_batch(self, **kw):
                return {"results": [{"id": "stage",
                                     "error": "503"}]}

        monkeypatch.setattr(
            "src.jev_tools.JevClassifierEngine", lambda: ErrEng())
        assert core._jev_timeout_verdict("evidence") is None

    def test_wave_deadline_math(self, core):
        f = core._wave_deadline
        assert f(100.0, 1800, 0) == pytest.approx(100 + 1860)
        assert f(100.0, 1800, 1) == pytest.approx(100 + 3660)
        assert f(100.0, 1, 0) == pytest.approx(100 + 61)
        assert f(100.0, 1, 3) == pytest.approx(100 + 64)

    def test_evidence_text_contains_activity(self, core):
        a = WorkAgent(delay=0.2, dispatch_every=0.05)
        a.chat_turn()
        text = core._timeout_evidence_text(
            a, 1800, core._evidence_snapshot(a),
            {"title": "下載小說", "question": "q"})
        assert "下載小說" in text and "run_bash" in text
        assert "30.0 分鐘" in text
        assert "normal_long_work" in text and "abnormal" in text

    def test_extensions_budget_reset_on_rerun(self, core):
        core._grant_extension("plan-x", "s1")
        assert core._extensions_for("plan-x", "s1") == 1
        rec = core.compile_plan(
            name="plan-x", stages=[{"title": "s1", "prompt": "p"}],
        )
        core.run_plan(rec["id"])  # no factory → stages fail fast
        assert core._extensions_for(rec["id"], "s1") == 0

# -- v0.9.121.2 -- delete_plan (user: provide tacti delete feature) --
class TestDeletePlan:
    def test_confirm_gate_fail_closed(self, core):
        rec = core.compile_plan(
            name="del1", stages=[{"title": "s1", "prompt": "p"}],
        )
        out = core.delete_plan(rec["id"])
        assert "error" in out and "confirm" in out["error"]
        assert core.load_plan(rec["id"]) is not None  # survivors

    def test_delete_compiled_plan_removes_file(self, core):
        rec = core.compile_plan(
            name="del2", stages=[{"title": "s1", "prompt": "p"}],
        )
        assert core.plan_file(rec["id"]).exists()
        out = core.delete_plan(rec["id"], confirm=True)
        assert out["ok"] is True and out["deleted"] == rec["id"]
        assert not core.plan_file(rec["id"]).exists()
        assert core.load_plan(rec["id"]) is None
        assert all(p["id"] != rec["id"]
                   for p in core.list_plans()["plans"])

    def test_delete_unknown_plan(self, core):
        out = core.delete_plan("nope", confirm=True)
        assert "error" in out and "not found" in out["error"]

    def test_running_refused_then_force(self, core, monkeypatch):
        from src.agent import task_runner as tr

        monkeypatch.setattr(tr, "request_stop",
                            lambda tid: True, raising=False)
        _wire(core, answer="ok", delay=0.0)
        rec = core.compile_plan(
            name="del3", stages=[{"title": "s1", "prompt": "p"}],
        )
        core.run_plan(rec["id"])
        out = core.delete_plan(rec["id"], confirm=True)
        assert "error" in out and "RUNNING" in out["error"]
        out2 = core.delete_plan(rec["id"], confirm=True, force=True)
        assert out2["ok"] is True
        assert core.load_plan(rec["id"]) is None
        assert not core.plan_file(rec["id"]).exists()

    def test_zombie_driver_cannot_resurrect(self, core, monkeypatch):
        from src.agent import task_runner as tr

        monkeypatch.setattr(tr, "request_stop",
                            lambda tid: True, raising=False)
        rec = core.compile_plan(
            name="del4", stages=[{"title": "s1", "prompt": "p"}],
        )
        core.delete_plan(rec["id"], confirm=True)
        # a zombie driver's late save is a NO-OP and the file stays
        core.save_plan(core.load_plan(rec["id"]) or dict(rec))
        assert not core.plan_file(rec["id"]).exists()
        assert core.load_plan(rec["id"]) is None

    def test_host_tool_delete_gate_and_stdio_parity(self, core):
        from src.mcp_client.host import TactiLocalHost
        from src.mcp_server.tacti_server import TactiMCPServer

        h = TactiLocalHost(mcp_manager=None)
        names = [t.name for t in h.list_tools()]
        assert names == [
            "compile_plan", "run_plan", "get_plan_status",
            "list_plans", "run_plan_stage", "stop_plan",
            "delete_plan",
        ]
        tool = next(t for t in h.list_tools()
                    if t.name == "delete_plan")
        assert tool.input_schema["required"] == ["plan_id", "confirm"]
        assert tool.server_name == h.SERVER_NAME

        srv = TactiMCPServer()
        assert [t.name for t in srv._tool_specs()] == names

        rec = core.compile_plan(
            name="del5", stages=[{"title": "s1", "prompt": "p"}],
        )
        out = h._delete(
            {"plan_id": rec["id"], "confirm": True})
        assert out["ok"] is True
        assert core.load_plan(rec["id"]) is None
