"""v0.9.67.1 — schedule-fire history cap + telegram-send integrity
guard tests.

Live log 2026-09-16 15:50: manual trigger of cron schedule
「下午2點股市異常分析」(9e924b90ac9c) — the reused session had
accumulated 14 fires (30 messages); the LLM skipped EVERY tool call
and fabricated "✅ 已成功發送至 Telegram (訊息長度 1,024 字元)" with
zero dispatches; no Telegram message ever arrived. Fixes locked:

* ``QDRANT_SCHEDULE_FIRE_HISTORY`` caps the history carried into a
  fire (default 0 = independent fires; 2 = one prior Q/A; -1 =
  legacy full history);
* a fire whose prompt asks for a Telegram send, whose reply claims
  the send, but with no ``send_telegram_*`` dispatch runs a
  corrective turn that forces the tool call and adopts its answer;
  no corrective when the send was dispatched / prompt has no
  telegram intent / corrective failed (original answer recorded).

v0.9.103.2 — ``_claims_telegram_send`` gains the future/announce
shapes (the live 2026-09-29 14:00 fire said 「整理分析後發送
Telegram」 / 「現在發送 Telegram」 and EVADED the old completed-tense
regex → no corrective → nothing sent) + a negative list for honest
no-send replies.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture()
def tmp_home(tmp_path, monkeypatch):
    monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path))
    monkeypatch.delenv("QDRANT_SCHEDULE_FIRE_HISTORY", raising=False)
    yield tmp_path


class _Agent:
    def __init__(self, turns):
        self._turns = list(turns)
        self.n = 0
        self.questions: list[str] = []
        self.histories: list[list] = []
        self.kwargs: list[dict] = []
        self.last_tool_dispatches: list = []

    def chat_turn(self, question, history=None, **kw):
        self.questions.append(question)
        self.histories.append(list(history or []))
        self.kwargs.append(dict(kw))
        turn = self._turns[min(self.n, len(self._turns) - 1)]
        self.n += 1
        dispatches, answer = turn
        self.last_tool_dispatches = list(dispatches)
        return (answer, "", [])


def _schedule(prompt="請分析並將結果發送到 Telegram chat_id 111",
              sid="tgguard"):
    from src.agent.scheduler import Schedule

    return Schedule(
        id=sid, prompt=prompt, schedule_type="cron",
        cron="0 14 * * *",
        next_fire_at="2026-01-01T00:00:00", enabled=True,
    )


def _seed_session(home: Path, sid: str, pairs: int) -> None:
    from src.agent.session import Session, SessionManager

    sess = Session(sid=sid, title="[schedule] test")
    for i in range(pairs):
        sess.messages.append(
            {"role": "user", "content": f"u{i}", "ts": "t"})
        sess.messages.append(
            {"role": "assistant", "content": f"a{i}", "ts": "t"})
    SessionManager().save(sess)
    return sess


def _last_answer_row(home: Path, sid: str) -> dict:
    hist = Path(home) / "schedules" / f"{sid}.history.jsonl"
    rows = [json.loads(x) for x in
            hist.read_text(encoding="utf-8").strip().splitlines()]
    return rows[-1]


# ─── fire history cap ────────────────────────────────────────


class TestFireHistoryCap:
    def test_default_zero_independent_fire(self, tmp_home, monkeypatch):
        from src.agent.scheduler import ScheduleManager, make_agent_on_fire

        agent = _Agent([([],"分析答案")])
        mgr = ScheduleManager()
        s = _schedule(sid="histcap")
        s.last_session_id = "sched-histcap-old"
        mgr.save(s)
        home = Path(tmp_home)
        _seed_session(home, "sched-histcap-old", pairs=5)
        on_fire = make_agent_on_fire(agent_factory=lambda: agent)
        on_fire(s)
        # default cap 0 — the fire carried NO prior history
        assert agent.histories[-1] == []

    def test_env_two_keeps_tail(self, tmp_home, monkeypatch):
        from src.agent.scheduler import ScheduleManager, make_agent_on_fire

        monkeypatch.setenv("QDRANT_SCHEDULE_FIRE_HISTORY", "2")
        from src.agent.session import Session, SessionManager

        mgr = ScheduleManager()
        s = _schedule(sid="histcap2")
        s.last_session_id = "sched-histcap-old2"
        mgr.save(s)
        _seed_session(Path(tmp_home), "sched-histcap-old2", pairs=3)
        agent = _Agent([([], "答案")])
        on_fire = make_agent_on_fire(agent_factory=lambda: agent)
        on_fire(s)
        got = agent.histories[-1]
        assert [m.get("content") for m in got] == ["u2", "a2"]

    def test_minus_one_legacy_full(self, tmp_home, monkeypatch):
        from src.agent.scheduler import ScheduleManager, make_agent_on_fire

        monkeypatch.setenv("QDRANT_SCHEDULE_FIRE_HISTORY", "-1")
        mgr = ScheduleManager()
        s = _schedule(sid="histcap")
        s.last_session_id = "sched-histcap-old3"
        mgr.save(s)
        _seed_session(Path(tmp_home), "sched-histcap-old3", pairs=4)
        agent = _Agent([([], "答案")])
        on_fire = make_agent_on_fire(agent_factory=lambda: agent)
        on_fire(s)
        assert len(agent.histories[-1]) == 8

    def test_resolver_values(self, monkeypatch):
        from src.agent.scheduler import _fire_history_limit

        monkeypatch.delenv(
            "QDRANT_SCHEDULE_FIRE_HISTORY", raising=False)
        assert _fire_history_limit() == 0
        monkeypatch.setenv("QDRANT_SCHEDULE_FIRE_HISTORY", "4")
        assert _fire_history_limit() == 4
        monkeypatch.setenv("QDRANT_SCHEDULE_FIRE_HISTORY", "-1")
        assert _fire_history_limit() == -1
        monkeypatch.setenv("QDRANT_SCHEDULE_FIRE_HISTORY", "abc")
        assert _fire_history_limit() == 0


# __PART_GUARD__


# ─── v0.9.67.2 — dispatch-shape fix + fire memory switch ────────


class TestDispatchShapeFix:
    """``last_tool_dispatches`` on the REAL Agent is a plain
    ``list[str]`` (v0.9.57.1: ``_run_tool_call`` appends the name).
    The v0.9.67.1 guard parsed dict/object shapes only → a fire that
    HAD dispatched send_telegram_message was misjudged as fabricated
    → a false corrective turn + a DUPLICATE Telegram send (live log
    16:46:52 send OK → 16:46:58 corrective trigger → 16:47:18 second
    send). Locks the string shape first."""

    def test_string_entries_match(self):
        from src.agent.scheduler import _dispatched_telegram_send

        assert _dispatched_telegram_send(type(
            "A", (), {"last_tool_dispatches": [
                "get_current_time", "web_search",
                "send_telegram_message"]})())
        assert _dispatched_telegram_send(type(
            "A", (), {"last_tool_dispatches": [
                "send_telegram_markdown"]})())

    def test_string_entries_no_match(self):
        from src.agent.scheduler import _dispatched_telegram_send

        assert not _dispatched_telegram_send(type(
            "A", (), {"last_tool_dispatches": [
                "get_current_time", "web_search"]})())
        assert not _dispatched_telegram_send(type(
            "A", (), {})())

    def test_dict_and_object_shapes_still_tolerated(self):
        from src.agent.scheduler import _dispatched_telegram_send

        assert _dispatched_telegram_send(type(
            "A", (), {"last_tool_dispatches": [
                {"name": "send_telegram_message"}]})())

        class _D:
            name = "send_telegram_message"

        assert _dispatched_telegram_send(type(
            "A", (), {"last_tool_dispatches": [_D()]})())

    def test_fire_dispatching_send_gets_no_corrective(self, tmp_home):
        """Live regression: real-Agent string dispatches → the guard
        must NOT trigger a corrective turn (no duplicate send)."""
        from src.agent.scheduler import ScheduleManager, make_agent_on_fire

        agent = _Agent([
            (["get_current_time", "web_search",
              "send_telegram_message"],
             "✅ 已完成分析並成功將結果發送至 Telegram。"),
        ])
        mgr = ScheduleManager()
        s = _schedule()
        mgr.save(s)
        on_fire = make_agent_on_fire(agent_factory=lambda: agent)
        on_fire(s)
        assert agent.n == 1  # single turn — no corrective, no re-send


class TestFireMemorySwitch:
    def test_fire_passes_use_memory_false_by_default(
        self, tmp_home, monkeypatch,
    ):
        monkeypatch.delenv("QDRANT_SCHEDULE_FIRE_MEMORY", raising=False)
        from src.agent.scheduler import ScheduleManager, make_agent_on_fire

        agent = _Agent([([], "分析答案")])
        mgr = ScheduleManager()
        s = _schedule()
        mgr.save(s)
        on_fire = make_agent_on_fire(agent_factory=lambda: agent)
        on_fire(s)
        assert agent.kwargs[-1].get("use_memory") is False

    def test_env_on_restores_memory_recall(self, tmp_home, monkeypatch):
        monkeypatch.setenv("QDRANT_SCHEDULE_FIRE_MEMORY", "on")
        from src.agent.scheduler import ScheduleManager, make_agent_on_fire

        agent = _Agent([([], "分析答案")])
        mgr = ScheduleManager()
        s = _schedule()
        mgr.save(s)
        on_fire = make_agent_on_fire(agent_factory=lambda: agent)
        on_fire(s)
        assert agent.kwargs[-1].get("use_memory") is True

    def test_corrective_turn_also_threaded(self, tmp_home):
        from src.agent.scheduler import ScheduleManager, make_agent_on_fire

        agent = _Agent([
            ([], "✅ 已成功發送至 Telegram。"),
            (["send_telegram_message"], "已透過工具完成發送。"),
        ])
        mgr = ScheduleManager()
        s = _schedule()
        mgr.save(s)
        on_fire = make_agent_on_fire(agent_factory=lambda: agent)
        on_fire(s)
        assert agent.n == 2
        # BOTH turns carry the memory switch (default off)
        assert agent.kwargs[0].get("use_memory") is False
        assert agent.kwargs[1].get("use_memory") is False

    def test_resolver_values(self, monkeypatch):
        from src.agent.scheduler import _fire_memory_enabled

        monkeypatch.delenv("QDRANT_SCHEDULE_FIRE_MEMORY", raising=False)
        assert _fire_memory_enabled() is False
        for v in ("on", "ON", "true", "1", "yes"):
            monkeypatch.setenv("QDRANT_SCHEDULE_FIRE_MEMORY", v)
            assert _fire_memory_enabled() is True
        monkeypatch.setenv("QDRANT_SCHEDULE_FIRE_MEMORY", "off")
        assert _fire_memory_enabled() is False
        monkeypatch.setenv("QDRANT_SCHEDULE_FIRE_MEMORY", "")
        assert _fire_memory_enabled() is False


class TestFutureTenseClaims1032:
    """v0.9.103.2 — the live 2026-09-29 14:00 fire answers were
    FUTURE/announce shapes and evaded the old completed-tense regex;
    they must now count as a send claim (guard fires the corrective).
    Honest no-send replies keep matching nothing."""

    def test_live_2026_09_29_shapes_now_match(self):
        from src.agent.scheduler import _claims_telegram_send

        # fire#1 (36 chars) — 「...整理分析後發送 Telegram。」
        assert _claims_telegram_send(
            "已取得今日（9/29）盤面資料與新聞，整理分析後發送 Telegram。")
        # fire#2 (42 chars) — 「...現在整理分析並發送 Telegram。」
        assert _claims_telegram_send(
            "我已掌握今日（2026/09/29）盤面資訊，現在整理分析並"
            "發送 Telegram。")
        # 9/28 shape — 「...現在發送 Telegram：」
        assert _claims_telegram_send(
            "今天是 2026-09-28（週一），台股因中秋連假休市。我已彙整"
            "行情與異常分析，現在發送 Telegram：")

    def test_completed_shapes_still_match(self):
        from src.agent.scheduler import _claims_telegram_send

        assert _claims_telegram_send("✅ 已成功發送至 Telegram（1,024 字元）")
        assert _claims_telegram_send("sent via Telegram to chat 123")
        assert not _claims_telegram_send("")
        assert not _claims_telegram_send(None)
        # telegram never mentioned
        assert not _claims_telegram_send("已發送到 email")

    def test_explicit_no_send_refused(self):
        from src.agent.scheduler import _claims_telegram_send

        assert not _claims_telegram_send(
            "目前無異常，因此不發送 Telegram。")
        assert not _claims_telegram_send(
            "資料不足，無需發送 Telegram 通知。")
        assert not _claims_telegram_send(
            "已分析完成，但未發送 Telegram。")


# ─── telegram-send integrity guard ───────────────────────────


class TestTelegramSendGuard:
    def test_claim_without_dispatch_triggers_corrective(
        self, tmp_home,
    ):
        from src.agent.scheduler import ScheduleManager, make_agent_on_fire

        agent = _Agent([
            ([], "✅ 已完成分析並成功將結果發送至 Telegram。"),   # fabricated
            ([{"name": "send_telegram_message"}], "已呼叫工具發送。"),
        ])
        mgr = ScheduleManager()
        s = _schedule()
        mgr.save(s)
        on_fire = make_agent_on_fire(agent_factory=lambda: agent)
        on_fire(s)
        assert agent.n == 2  # original + corrective
        # the corrective prompt carries the forced tool-call directive
        assert "[系統守衛]" in agent.questions[-1]
        assert "send_telegram_message" in agent.questions[-1]
        # history.jsonl records the CORRECTIVE answer
        row = _last_answer_row(tmp_home, "tgguard")
        assert "已呼叫" in str(row.get("answer") or "")

    def test_no_corrective_when_send_dispatched(self, tmp_home):
        from src.agent.scheduler import ScheduleManager, make_agent_on_fire

        agent = _Agent([
            ([{"name": "send_telegram_message"}],
             "✅ 已發送至 Telegram。"),
        ])
        mgr = ScheduleManager()
        s = _schedule()
        mgr.save(s)
        on_fire = make_agent_on_fire(agent_factory=lambda: agent)
        on_fire(s)
        assert agent.n == 1  # single turn — no corrective

    def test_no_corrective_when_prompt_has_no_telegram(self, tmp_home):
        from src.agent.scheduler import ScheduleManager, make_agent_on_fire

        agent = _Agent([([], "分析完成（本地任務）。")])
        mgr = ScheduleManager()
        s = _schedule(prompt="純本地分析任務", sid="tgguard2")
        mgr.save(s)
        on_fire = make_agent_on_fire(agent_factory=lambda: agent)
        on_fire(s)
        assert agent.n == 1

    def test_corrective_failure_degrades_gracefully(self, tmp_home):
        from src.agent.scheduler import ScheduleManager, make_agent_on_fire

        class _FailAgent:
            def __init__(self):
                self.n = 0
                self.last_tool_dispatches: list = []

            def chat_turn(self, question, history=None, **kw):
                self.n += 1
                if self.n == 1:
                    return ("✅ 已發送至 Telegram。", "", [])
                raise RuntimeError("corrective failed")

        mgr = ScheduleManager()
        s = _schedule()
        mgr.save(s)
        on_fire = make_agent_on_fire(agent_factory=lambda: _FailAgent())
        on_fire(s)  # must not raise
        row = _last_answer_row(tmp_home, "tgguard")
        # the ORIGINAL (fabricated) answer is still recorded — but the
        # corrective attempt is logged
        assert "已發送" in str(row.get("answer") or "")

    def test_guard_helpers_unit(self):
        from src.agent.scheduler import (
            _claims_telegram_send,
            _dispatched_telegram_send,
            _telegram_intent,
        )

        assert _telegram_intent("send to Telegram now")
        assert not _telegram_intent("no cloud here")
        assert _claims_telegram_send(
            "✅ 已成功將分析結果發送至 Telegram。")
        assert _claims_telegram_send(
            "訊息已傳送至 telegram (chat_id: 111)")
        assert not _claims_telegram_send("分析完成。")
        assert not _claims_telegram_send(
            "telegram 相關但未宣稱發送")
        assert _dispatched_telegram_send(type(
            "A", (), {"last_tool_dispatches": [
                {"name": "send_telegram_message"}]})())
        assert not _dispatched_telegram_send(type(
            "A", (), {"last_tool_dispatches": []})())

    def test_session_records_corrective_answer(self, tmp_home):
        from src.agent.scheduler import ScheduleManager, make_agent_on_fire
        from src.agent.session import SessionManager

        agent = _Agent([
            ([], "✅ 已成功發送至 Telegram。"),
            ([{"name": "send_telegram_message"}],
             "已透過工具完成發送。"),
        ])
        mgr = ScheduleManager()
        s = _schedule()
        mgr.save(s)
        on_fire = make_agent_on_fire(agent_factory=lambda: agent)
        on_fire(s)
        sess_path = (Path(tmp_home) / "sessions" /
                     f"{s.last_session_id}.json")
        assert sess_path.exists()
        sess = json.loads(sess_path.read_text(encoding="utf-8"))
        # the LAST assistant row carries the corrective (real) answer
        last_a = [m for m in sess["messages"]
                  if m.get("role") == "assistant"][-1]
        assert "已透過工具" in str(last_a.get("content") or "")