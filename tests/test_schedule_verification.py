"""v0.9.106.0 — project_improvement_plan_v2 P0/P1/P2 tests.

P0 驗證前置化（「硬擋」取代「自律」）:
* fire prompt 注入 VERIFICATION DIRECTIVE（verify_required=True 預設；
  QDRANT_SCHEDULE_VERIFY=off 全域退出；per-schedule verify_required=False）;
* 事後機械守衛 — 宣稱成功但零驗證證據 → ONE 糾正補驗證回合；
  糾正失敗/空回覆 → 退回原回覆，verified=False 誠實記錄；
* history.jsonl / session 記錄三態 verified 旗標；
* get_scheduled_task_status 呈現 verify_required / last_verified /
  unverified_recent。

P1 獨立評估器（機械式檢查 ⭐ 優先 — 零 LLM、零幻覺）:
* verify_artifact — 沙盒路徑解析 / exists / min_bytes / JSON 欄位 /
  巢狀 dotted path / 未知 check type fail-closed；
* SchedulerLocalHost + stdio scheduler_server 雙面同工具；
* 對「假 findings.json」（缺欄位/缺檔）必判 FAIL（計畫驗收標準）。

P2 檢查點續跑:
* 系統提示含「先讀 findings.json，已完成階段跳過」的 checkpoint 規則
  （純提示層 — 排程 + 任務樹已有依賴圖能力，不引入新框架）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


# ─── fixtures / helpers ──────────────────────────────────────


@pytest.fixture()
def tmp_home(tmp_path, monkeypatch):
    monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path))
    monkeypatch.delenv("QDRANT_SCHEDULE_FIRE_HISTORY", raising=False)
    monkeypatch.delenv("QDRANT_SCHEDULE_VERIFY", raising=False)
    # Pin every filesystem sandbox at the tmp dir so verify_artifact's
    # path resolution is hermetic.
    monkeypatch.setenv("QDRANT_FILESYSTEM_WRITE_ROOT", str(tmp_path))
    monkeypatch.setenv("QDRANT_FILESYSTEM_READ_ROOT", str(tmp_path))
    monkeypatch.setenv("WATCH_DIR", str(tmp_path))
    yield tmp_path


class _Agent:
    """Scripted chat_turn fake recording every call."""

    def __init__(self, turns):
        self._turns = list(turns)
        self.n = 0
        self.questions: list[str] = []
        self.last_tool_dispatches: list = []

    def chat_turn(self, question, history=None, **kw):
        self.questions.append(question)
        turn = self._turns[min(self.n, len(self._turns) - 1)]
        self.n += 1
        dispatches, answer = turn
        self.last_tool_dispatches = list(dispatches)
        return (answer, "", [])


def _schedule(prompt="請分析並將結果發送到 Telegram chat_id 111",
              sid="verify", verify_required=True):
    from src.agent.scheduler import Schedule

    return Schedule(
        id=sid, prompt=prompt, schedule_type="cron",
        cron="0 14 * * *",
        next_fire_at="2026-01-01T00:00:00", enabled=True,
        verify_required=verify_required,
    )


def _last_row(home: Path, sid: str) -> dict:
    hist = Path(home) / "schedules" / f"{sid}.history.jsonl"
    rows = [json.loads(x) for x in
            hist.read_text(encoding="utf-8").strip().splitlines()]
    return rows[-1]


def _last_session(home: Path, sid: str):
    from src.agent.session import SessionManager

    rows = _last_row(home, sid)
    return SessionManager().load(rows["session_id"])


# ─── P0: verification directive ──────────────────────────────


class TestVerificationDirective:
    def test_directive_appended_by_default(self, tmp_home):
        from src.agent.scheduler import ScheduleManager, make_agent_on_fire

        agent = _Agent([(["verify_artifact"], "驗證通過，分析完成")])
        mgr = ScheduleManager()
        mgr.save(_schedule(sid="vd1"))
        make_agent_on_fire(agent_factory=lambda: agent)(mgr.get("vd1"))
        assert "VERIFICATION DIRECTIVE" in agent.questions[0]
        assert "verify_artifact" in agent.questions[0]
        assert "驗證失敗" in agent.questions[0]

    def test_directive_names_schedule_id(self, tmp_home):
        from src.agent.scheduler import (
            ScheduleManager, _verification_directive, make_agent_on_fire,
        )

        assert "id=abc123" in _verification_directive("abc123")
        agent = _Agent([([], "答案")])
        mgr = ScheduleManager()
        mgr.save(_schedule(sid="vd2"))
        make_agent_on_fire(agent_factory=lambda: agent)(mgr.get("vd2"))
        assert "id=vd2" in agent.questions[0]

    def test_env_off_restores_legacy_prompt(self, tmp_home, monkeypatch):
        from src.agent.scheduler import ScheduleManager, make_agent_on_fire

        monkeypatch.setenv("QDRANT_SCHEDULE_VERIFY", "off")
        agent = _Agent([([], "分析完成")])
        mgr = ScheduleManager()
        mgr.save(_schedule(sid="vd3"))
        make_agent_on_fire(agent_factory=lambda: agent)(mgr.get("vd3"))
        assert "VERIFICATION DIRECTIVE" not in agent.questions[0]

    def test_per_schedule_opt_out(self, tmp_home):
        from src.agent.scheduler import ScheduleManager, make_agent_on_fire

        agent = _Agent([([], "分析完成")])
        mgr = ScheduleManager()
        mgr.save(_schedule(sid="vd4", verify_required=False))
        make_agent_on_fire(agent_factory=lambda: agent)(mgr.get("vd4"))
        assert "VERIFICATION DIRECTIVE" not in agent.questions[0]

    def test_session_prompt_carries_directive(self, tmp_home):
        """The persisted session user message must contain the
        directive (the session is the audit trail)."""
        from src.agent.scheduler import ScheduleManager, make_agent_on_fire

        agent = _Agent([([], "驗證結果：通過")])
        mgr = ScheduleManager()
        mgr.save(_schedule(sid="vd5"))
        make_agent_on_fire(agent_factory=lambda: agent)(mgr.get("vd5"))
        sess = _last_session(tmp_home, "vd5")
        assert "VERIFICATION DIRECTIVE" in sess.messages[0]["content"]


# ─── P0: post-fire machine guard ─────────────────────────────


class TestVerificationGuard:
    def test_unverified_success_triggers_corrective_turn(
        self, tmp_home, monkeypatch,
    ):
        """Plan acceptance: an LLM that skips the verification step
        (claims 成功 with zero evidence) is caught and forced to
        verify."""
        monkeypatch.setenv("QDRANT_SCHEDULE_FIRE_HISTORY", "-1")
        from src.agent.scheduler import ScheduleManager, make_agent_on_fire

        agent = _Agent([
            # turn 1: success claim, NO verification, no dispatches
            ([], "分析已成功完成，結果已存檔 findings.json"),
            # turn 2 (corrective): verified
            (["verify_artifact"], "驗證結果：verify_artifact ok=True，"
             "已驗證 findings.json 欄位齊全"),
        ])
        mgr = ScheduleManager()
        mgr.save(_schedule(sid="vg1"))
        make_agent_on_fire(agent_factory=lambda: agent)(mgr.get("vg1"))
        assert len(agent.questions) == 2
        assert "系統驗證守衛" in agent.questions[1]
        assert "verify_artifact" in agent.questions[1]
        # the corrective answer is adopted
        row = _last_row(tmp_home, "vg1")
        assert "驗證結果" in row["answer"]
        assert row["verified"] is True

    def test_verified_reply_skips_corrective_turn(self, tmp_home):
        from src.agent.scheduler import ScheduleManager, make_agent_on_fire

        agent = _Agent([
            (["verify_artifact"],
             "驗證通過（verify_artifact ok），分析完成"),
        ])
        mgr = ScheduleManager()
        mgr.save(_schedule(sid="vg2"))
        make_agent_on_fire(agent_factory=lambda: agent)(mgr.get("vg2"))
        assert len(agent.questions) == 1
        row = _last_row(tmp_home, "vg2")
        assert row["verified"] is True

    def test_honest_failure_needs_no_corrective_turn(self, tmp_home):
        from src.agent.scheduler import ScheduleManager, make_agent_on_fire

        agent = _Agent([
            ([], "執行失敗：web_search 逾時，無法取得資料"),
        ])
        mgr = ScheduleManager()
        mgr.save(_schedule(sid="vg3"))
        make_agent_on_fire(agent_factory=lambda: agent)(mgr.get("vg3"))
        assert len(agent.questions) == 1
        row = _last_row(tmp_home, "vg3")
        assert row["verified"] is True  # honest failure = verified

    def test_failed_correction_keeps_verified_false(self, tmp_home):
        """The corrective turn itself produced another unverified
        success claim — the row must stay verified=False (honest)."""
        from src.agent.scheduler import ScheduleManager, make_agent_on_fire

        agent = _Agent([
            ([], "已成功完成所有步驟"),
            ([], "已成功完成驗證"),  # still claims, still no evidence
        ])
        mgr = ScheduleManager()
        mgr.save(_schedule(sid="vg4"))
        make_agent_on_fire(agent_factory=lambda: agent)(mgr.get("vg4"))
        assert len(agent.questions) == 2
        row = _last_row(tmp_home, "vg4")
        assert row["verified"] is False

    def test_disabled_verify_no_flag_no_guard(self, tmp_home):
        from src.agent.scheduler import ScheduleManager, make_agent_on_fire

        agent = _Agent([([], "已成功完成")])
        mgr = ScheduleManager()
        mgr.save(_schedule(sid="vg5", verify_required=False))
        make_agent_on_fire(agent_factory=lambda: agent)(mgr.get("vg5"))
        assert len(agent.questions) == 1
        row = _last_row(tmp_home, "vg5")
        assert "verified" not in row  # legacy: flag absent

    def test_neutral_reply_flag_true(self, tmp_home):
        from src.agent.scheduler import ScheduleManager, make_agent_on_fire

        agent = _Agent([([], "本期無異常事項。")])
        mgr = ScheduleManager()
        mgr.save(_schedule(sid="vg6"))
        make_agent_on_fire(agent_factory=lambda: agent)(mgr.get("vg6"))
        row = _last_row(tmp_home, "vg6")
        assert row["verified"] is True

    def test_verify_artifact_dispatch_counts_as_evidence(self, tmp_home):
        """The dispatch list is the strongest evidence — prose may
        omit the word 驗證 entirely."""
        from src.agent.scheduler import (
            ScheduleManager, _verified_flag, make_agent_on_fire,
        )

        agent = _Agent([
            (["verify_artifact"], "所有檢查皆為綠色。"),
        ])
        mgr = ScheduleManager()
        mgr.save(_schedule(sid="vg7"))
        make_agent_on_fire(agent_factory=lambda: agent)(mgr.get("vg7"))
        row = _last_row(tmp_home, "vg7")
        assert row["verified"] is True
        # unit-level: dispatch alone flips the flag
        assert _verified_flag(
            "全數通過。", None, True,
            agent=agent,
        ) is True


# ─── P0: status/runs surface ─────────────────────────────────


class TestVerifiedSurface:
    def test_history_row_and_progress_carry_flags(self, tmp_home):
        from src.agent.scheduler import ScheduleManager, make_agent_on_fire

        agent = _Agent([
            ([], "分析已成功完成"),
            (["verify_artifact"], "驗證結果：已驗證 ok"),
        ])
        mgr = ScheduleManager()
        mgr.save(_schedule(sid="vs1"))
        fire = make_agent_on_fire(agent_factory=lambda: agent)
        fire(mgr.get("vs1"))
        # fire 1: unverified success claim → ONE corrective turn ran
        # and the corrective reply carried verification evidence, so
        # the row ends verified=True (the claim WAS verified in the
        # end). The guard is visible in the question log.
        assert len(agent.questions) == 2
        fire(mgr.get("vs1"))
        # fire 2: the LLM verified proactively — single turn.
        assert len(agent.questions) == 3
        runs = mgr.list_runs("vs1")
        assert [r.get("verified") for r in runs] == [True, True]
        prog = mgr.get_progress("vs1")
        assert prog["verify_required"] is True
        assert prog["last_verified"] is True
        # no UNVERIFIED success remains on record
        assert prog["unverified_recent"] == 0

    def test_flag_absent_when_verification_disabled(self, tmp_home):
        from src.agent.scheduler import ScheduleManager, make_agent_on_fire

        agent = _Agent([([], "ok")])
        mgr = ScheduleManager()
        mgr.save(_schedule(sid="vs2", verify_required=False))
        make_agent_on_fire(agent_factory=lambda: agent)(mgr.get("vs2"))
        assert all("verified" not in r for r in mgr.list_runs("vs2"))
        assert mgr.get_progress("vs2")["verify_required"] is False


# ─── P0: classifier units ────────────────────────────────────


class TestClassifiers:
    def test_success_claim_shapes(self):
        from src.agent.scheduler import _has_success_claim

        for text in (
            "已成功發送至 Telegram",
            "報告已完成並存檔",  # 已存檔 = concrete receipt claim
            "全部成功",
            "sent successfully to telegram",
            "任務已順利達成",  # 已達成
            "成功完成所有步驟",
        ):
            assert _has_success_claim(text), text

    def test_weak_neutral_shapes_are_not_claims(self):
        """「分析完成。」/「本期無異常」 — honest summary lines are
        NOT receipt claims (no corrective turn for those)."""
        from src.agent.scheduler import _has_success_claim

        for text in (
            "分析完成（本地任務）。",
            "本期無異常事項。",
            "報告已完成。",  # 完成 alone without a concrete receipt
        ):
            assert not _has_success_claim(text), text

    def test_failure_reply_is_not_success_claim(self):
        from src.agent.scheduler import _has_success_claim

        for text in (
            "驗證失敗：findings.json 缺少 confidence 欄位",
            "執行失敗，錯誤原因：API 401",
            "無法完成：Qdrant 連線被拒",
            "task failed with error 500",
        ):
            assert not _has_success_claim(text), text

    def test_verification_evidence_shapes(self):
        from src.agent.scheduler import _has_verification_evidence

        for text in (
            "已呼叫 verify_artifact，ok=true",
            "驗證結果：通過",
            "驗證失敗：缺欄位",  # failure evidence also counts
            "以 get_chat_session 回讀比對工具回執",
            "verification passed: size=1234",
            "回執比對一致",
        ):
            assert _has_verification_evidence(text), text

    def test_unverified_success_gate(self):
        from src.agent.scheduler import _unverified_success

        assert _unverified_success("已成功完成")
        assert not _unverified_success("驗證通過，成功完成")
        assert not _unverified_success("執行失敗")

    def test_flag_three_states(self):
        from src.agent.scheduler import _verified_flag

        assert _verified_flag(None, "boom", True) is True
        assert _verified_flag(None, None, False) is None
        assert _verified_flag("", None, True) is False
        assert _verified_flag("已成功", None, True) is False
        assert _verified_flag("驗證通過", None, True) is True
        assert _verified_flag("中性摘要", None, True) is True


# ─── P1: verify_artifact engine ──────────────────────────────


class TestVerifyArtifact:
    def test_missing_file_fails(self, tmp_home):
        from src.agent.scheduler import verify_artifact

        r = verify_artifact("ghost.json")
        assert r["ok"] is False
        assert "does not exist" in r["errors"][0]

    def test_exists_and_min_bytes(self, tmp_home):
        from src.agent.scheduler import verify_artifact

        p = tmp_home / "a.txt"
        p.write_text("x" * 200, encoding="utf-8")
        assert verify_artifact(str(p))["ok"] is True
        r = verify_artifact(
            str(p), [{"type": "min_bytes", "value": 500}],
        )
        assert r["ok"] is False
        assert "min_bytes" in r["errors"][0]
        r2 = verify_artifact(
            str(p), [{"type": "min_bytes", "value": 100}],
        )
        assert r2["ok"] is True

    def test_fake_findings_json_fails(self, tmp_home):
        """Plan acceptance P1: the checker must judge a FAKE
        findings.json as FAIL (missing fields)."""
        from src.agent.scheduler import verify_artifact

        fake = tmp_home / "findings.json"
        fake.write_text(
            json.dumps({"stage1": "done"}), encoding="utf-8",
        )
        r = verify_artifact(
            "findings.json",
            checks=[
                {"field": "summary.status", "type": "nonempty"},
                {"field": "summary.confidence", "type": "json_number",
                 "min": 0, "max": 1},
                {"field": "signals", "type": "json_array",
                 "min_length": 3},
                {"field": "telegram_message_id", "type": "nonempty"},
            ],
        )
        assert r["ok"] is False
        assert len(r["errors"]) == 4

    def test_good_findings_json_passes(self, tmp_home):
        from src.agent.scheduler import verify_artifact

        good = tmp_home / "findings.json"
        good.write_text(json.dumps({
            "summary": {"status": "ok", "confidence": 0.87},
            "signals": ["a", "b", "c"],
            "telegram_message_id": "4242",
            "snapshot": "snapshot.jpg",
        }), encoding="utf-8")
        (tmp_home / "snapshot.jpg").write_bytes(b"\xff\xd8\xff\xe0")
        r = verify_artifact(
            "findings.json",
            checks=[
                {"type": "min_bytes", "value": 50},
                {"field": "summary.status", "type": "nonempty"},
                {"field": "summary.confidence", "type": "json_number",
                 "min": 0, "max": 1},
                {"field": "signals", "type": "json_array",
                 "min_length": 3},
                {"field": "telegram_message_id", "type": "nonempty"},
                {"field": "snapshot", "type": "file_exists"},
            ],
        )
        assert r["ok"] is True, r["errors"]
        assert r["check_count"] == 6

    def test_nested_dotted_and_index_paths(self, tmp_home):
        from src.agent.scheduler import verify_artifact

        f = tmp_home / "nested.json"
        f.write_text(json.dumps({
            "stages": [{"name": "s1", "status": "completed"}],
        }), encoding="utf-8")
        r = verify_artifact(
            "nested.json",
            checks=[
                {"field": "stages[0].status", "type": "exists"},
                {"field": "stages[0].name", "type": "json_string",
                 "pattern": "^s\\d$"},
            ],
        )
        assert r["ok"] is True, r["errors"]

    def test_unknown_check_type_fails_closed(self, tmp_home):
        from src.agent.scheduler import verify_artifact

        f = tmp_home / "x.json"
        f.write_text("{}", encoding="utf-8")
        r = verify_artifact(
            "x.json", [{"field": "a", "type": "mystical_check"}],
        )
        assert r["ok"] is False
        assert "unknown check type" in r["errors"][0]

    def test_outside_sandbox_refused(self, tmp_home):
        from src.agent.scheduler import verify_artifact

        r = verify_artifact("C:/Windows/win.ini")
        assert r["ok"] is False
        assert "outside" in r["errors"][0]

    def test_non_json_content_with_json_checks(self, tmp_home):
        from src.agent.scheduler import verify_artifact

        f = tmp_home / "notjson.json"
        f.write_text("<html>oops</html>", encoding="utf-8")
        r = verify_artifact(
            "notjson.json",
            checks=[{"field": "summary", "type": "exists"}],
        )
        assert r["ok"] is False
        assert "JSON parse failed" in r["errors"][0]

    def test_empty_checks_exists_only(self, tmp_home):
        from src.agent.scheduler import verify_artifact

        f = tmp_home / "plain.md"
        f.write_text("# hello", encoding="utf-8")
        r = verify_artifact("plain.md")
        assert r["ok"] is True
        assert r["passed"] == ["artifact exists"]

    def test_relative_path_anchors_write_root(self, tmp_home):
        from src.agent.scheduler import verify_artifact

        (tmp_home / "rel.json").write_text("{}", encoding="utf-8")
        r = verify_artifact("rel.json")
        assert r["ok"] is True
        assert r["resolved_path"].endswith("rel.json")


# ─── P1: tool surface (in-process + stdio parity) ────────────


class TestVerifyArtifactSurface:
    def test_local_host_has_verify_artifact(self, tmp_home):
        from src.mcp_client.host import SchedulerLocalHost

        names = {t.name for t in SchedulerLocalHost().list_tools()}
        assert "verify_artifact" in names

    def test_host_dispatch_ok(self, tmp_home):
        from src.mcp_client.host import SchedulerLocalHost

        host = SchedulerLocalHost()
        (tmp_home / "d.json").write_text(
            json.dumps({"a": 1}), encoding="utf-8",
        )
        tools = {t.name: t for t in host.list_tools()}
        r = tools["verify_artifact"]({"path": "d.json"})
        assert not r.is_error
        payload = json.loads(r.text)
        assert payload["ok"] is True

    def test_host_dispatch_bad_args(self, tmp_home):
        from src.mcp_client.host import SchedulerLocalHost

        host = SchedulerLocalHost()
        tools = {t.name: t for t in host.list_tools()}
        # The codebase convention: handler-level errors come back as
        # {"error": ...} in a normal (is_error=False) result — the
        # LLM reads the error key. Only handler CRASHES become
        # is_error=True.
        r1 = tools["verify_artifact"]({})
        assert not r1.is_error
        assert "error" in json.loads(r1.text)
        r2 = tools["verify_artifact"](
            {"path": "x", "checks": "not-a-list"},
        )
        assert not r2.is_error
        assert "error" in json.loads(r2.text)

    def test_stdio_parity(self, tmp_home):
        from src.mcp_client.host import SchedulerLocalHost
        from src.mcp_server.scheduler_server import _build_tool_specs

        spec_names = {t.name for t in _build_tool_specs()}
        host_names = {t.name for t in SchedulerLocalHost().list_tools()}
        assert spec_names == host_names
        assert "verify_artifact" in spec_names

    def test_stdio_server_dispatch(self, tmp_home):
        import asyncio

        from src.mcp_server.scheduler_server import SchedulerMCPServer

        (tmp_home / "s.json").write_text(
            json.dumps({"k": "v"}), encoding="utf-8",
        )
        srv = SchedulerMCPServer(schedules_root=str(tmp_home / "sched"))
        out = asyncio.run(
            srv._handle_verify_artifact({"path": "s.json"}),
        )
        assert "ok" in out[0].text
        out2 = asyncio.run(srv._handle_verify_artifact({}))
        assert out2[0].text.startswith("Error")

    def test_stdio_add_accepts_verify_required(self, tmp_home):
        import asyncio

        from src.mcp_server.scheduler_server import SchedulerMCPServer

        srv = SchedulerMCPServer(schedules_root=str(tmp_home / "sched"))
        out = asyncio.run(srv._handle_add({
            "prompt": "x", "schedule_type": "interval_seconds",
            "interval_seconds": 60, "verify_required": False,
        }))
        payload = json.loads(out[0].text)
        s = srv.mgr.get(payload["id"])
        assert s.verify_required is False


# ─── P2: checkpoint-resume prompt rule ───────────────────────


class TestCheckpointPromptRule:
    def test_prompt_block_present(self):
        from src.agent.agent import Agent

        prompt = Agent.__new__(Agent)._system_prompt(tools_enabled=True)
        assert "Long-running job CHECKPOINTS" in prompt
        assert "findings.json" in prompt
        assert "verify_artifact" in prompt
        assert "add_task_dependency" in prompt

    def test_prompt_block_absent_in_legacy_mode(self):
        from src.agent.agent import Agent

        prompt = Agent.__new__(Agent)._system_prompt(
            tools_enabled=False,
        )
        assert "Long-running job CHECKPOINTS" not in prompt

    def test_schedule_verification_block_present(self):
        from src.agent.agent import Agent

        prompt = Agent.__new__(Agent)._system_prompt(tools_enabled=True)
        assert "Schedule VERIFICATION" in prompt
        assert "verified" in prompt

    def test_existing_blocks_unchanged(self):
        from src.agent.agent import Agent

        prompt = Agent.__new__(Agent)._system_prompt(tools_enabled=True)
        # coexistence with the neighbouring blocks
        assert "add_scheduled_task" in prompt
        assert "send_telegram_message" in prompt
        assert "render_task_tree" in prompt


# ─── P0: existing fire semantics preserved ───────────────────


class TestFireSemanticsUnchanged:
    def test_tuple_result_recorded(self, tmp_home):
        from src.agent.scheduler import ScheduleManager, make_agent_on_fire

        agent = _Agent([([], "驗證結果：通過，分析完成")])
        mgr = ScheduleManager()
        mgr.save(_schedule(sid="fs1"))
        make_agent_on_fire(agent_factory=lambda: agent)(mgr.get("fs1"))
        row = _last_row(tmp_home, "fs1")
        assert row["answer"] == "驗證結果：通過，分析完成"
        assert row["error"] is None

    def test_chat_exception_still_raises_and_stamps(self, tmp_home):
        from src.agent.scheduler import ScheduleManager, make_agent_on_fire

        class _BoomAgent:
            last_tool_dispatches: list = []

            def chat_turn(self, **kw):
                raise RuntimeError("qdrant down")

        mgr = ScheduleManager()
        mgr.save(_schedule(sid="fs2"))
        with pytest.raises(RuntimeError):
            make_agent_on_fire(
                agent_factory=lambda: _BoomAgent(),
            )(mgr.get("fs2"))
        s = mgr.get("fs2")
        assert s.status == "failed"
        assert "qdrant down" in (s.last_error or "")
        row = _last_row(tmp_home, "fs2")
        assert row["error"] is not None
        assert row["verified"] is True  # honest error row

    def test_schedule_from_dict_defaults_verify_required(self, tmp_home):
        """Old on-disk schedule JSON files (no verify_required key)
        load with the verification flow ON."""
        from src.agent.scheduler import Schedule

        s = Schedule.from_dict({
            "id": "legacy1", "prompt": "p",
            "schedule_type": "interval_seconds",
            "interval_seconds": 60,
        })
        assert s.verify_required is True





