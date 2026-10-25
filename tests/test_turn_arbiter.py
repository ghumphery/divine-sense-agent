# -*- coding: utf-8 -*-
"""v0.9.118.0 — SHARED hard-timeout arbiter (turn_arbiter.py) tests.

Covers: extension-budget resolver, gate env alias semantics (the
117.0 skill knob aliases the shared knob), prob-floor alias, evidence
text subject parameterization, guarded_run_join loop behaviors
(fast-pass / liveness extend / verdict extend / stop / worker raise /
extensions exhausted = fail-closed kill) via real threads.
"""
from __future__ import annotations

import os
import threading
import time

import pytest

import src.agent.turn_arbiter as ta


class _Agent:
    """Stub with steady optional dispatches (liveness evidence)."""

    def __init__(self, delay=0.0, dispatch_every=0.0):
        self.delay = delay
        self.dispatch_every = dispatch_every
        self.last_tool_dispatches = []
        self.last_usage = {"calls": 0}

    def chat_turn(self):
        end = time.monotonic() + self.delay
        if self.dispatch_every <= 0:
            time.sleep(self.delay)
            return "ok", [], []
        while time.monotonic() < end:
            time.sleep(min(self.dispatch_every,
                           max(0.05, end - time.monotonic())))
            self.last_tool_dispatches.append("run_bash")
            self.last_usage["calls"] = len(self.last_tool_dispatches)
        return "ok", [], []


def _guarded(agent, window_s, *, verdict="extend", verdict_calls,
             extensions_env="QDRANT_SKILL_STAGE_TIMEOUT_EXTENSIONS",
             extensions_default=3, env_overrides=None):
    """Thread + shared guarded_run_join with an always-same verdict;
    env_overrides cleaned in finally."""
    env_overrides = env_overrides or {}
    for k, v in env_overrides.items():
        os.environ[k] = v
    try:
        box: dict = {}

        def _run():
            try:
                box["result"] = agent.chat_turn()
            except BaseException as e:  # noqa: BLE001
                box["error"] = e

        w = threading.Thread(target=_run, daemon=True)
        w.start()
        return ta.guarded_run_join(
            w, box, agent, window_s=window_s,
            log_label="[probe] test", subject="背景 stage ",
            meta={"title": "t", "question": "q"},
            extensions_env=extensions_env,
            extensions_default=extensions_default,
            verdict_fn=lambda text: (verdict_calls.__setitem__(
                "n", verdict_calls["n"] + 1) or verdict),
        )
    finally:
        for k in env_overrides:
            os.environ.pop(k, None)


class TestResolver:
    def test_unset_default_3(self, monkeypatch):
        monkeypatch.delenv("QDRANT_TURN_HARD_TIMEOUT_EXTENSIONS",
                           raising=False)
        assert ta.resolve_extensions(
            "QDRANT_TURN_HARD_TIMEOUT_EXTENSIONS", 3) == 3

    def test_invalid_falls_back(self, monkeypatch):
        monkeypatch.setenv("QDRANT_TURN_HARD_TIMEOUT_EXTENSIONS", "abc")
        assert ta.resolve_extensions(
            "QDRANT_TURN_HARD_TIMEOUT_EXTENSIONS", 3) == 3

    def test_zero_off(self, monkeypatch):
        monkeypatch.setenv("QDRANT_SKILL_STAGE_TIMEOUT_EXTENSIONS", "0")
        assert ta.resolve_extensions(
            "QDRANT_SKILL_STAGE_TIMEOUT_EXTENSIONS", 3) == 0

    def test_positive(self, monkeypatch):
        monkeypatch.setenv("QDRANT_TURN_HARD_TIMEOUT_EXTENSIONS", "7")
        assert ta.resolve_extensions(
            "QDRANT_TURN_HARD_TIMEOUT_EXTENSIONS", 3) == 7


class TestGateAliases:
    def test_both_unset_gate_on(self, monkeypatch):
        monkeypatch.delenv("QDRANT_SKILL_TIMEOUT_JEV", raising=False)
        monkeypatch.delenv("QDRANT_TIMEOUT_JEV", raising=False)
        assert ta._gate_env() is True

    def test_skill_alias_off(self, monkeypatch):
        monkeypatch.setenv("QDRANT_SKILL_TIMEOUT_JEV", "off")
        monkeypatch.delenv("QDRANT_TIMEOUT_JEV", raising=False)
        assert ta._gate_env() is False

    def test_shared_knob_off(self, monkeypatch):
        monkeypatch.delenv("QDRANT_SKILL_TIMEOUT_JEV", raising=False)
        monkeypatch.setenv("QDRANT_TIMEOUT_JEV", "0")
        assert ta._gate_env() is False

    def test_skill_alias_wins(self, monkeypatch):
        monkeypatch.setenv("QDRANT_SKILL_TIMEOUT_JEV", "on")
        monkeypatch.setenv("QDRANT_TIMEOUT_JEV", "off")
        assert ta._gate_env() is True

    def test_prob_alias(self, monkeypatch):
        monkeypatch.delenv("QDRANT_SKILL_TIMEOUT_JEV_MIN_PROB",
                           raising=False)
        monkeypatch.setenv("QDRANT_TIMEOUT_JEV_MIN_PROB", "0.35")
        assert ta._min_prob() == pytest.approx(0.35)
        monkeypatch.setenv("QDRANT_SKILL_TIMEOUT_JEV_MIN_PROB", "0.8")
        assert ta._min_prob() == pytest.approx(0.8)
        monkeypatch.setenv("QDRANT_SKILL_TIMEOUT_JEV_MIN_PROB", "99")
        assert ta._min_prob() == pytest.approx(0.99)

    def test_shared_verdict_gate_off_kills(self, monkeypatch):
        monkeypatch.setenv("QDRANT_TIMEOUT_JEV", "off")
        monkeypatch.setattr("src.jev_tools.JevClassifierEngine", None)
        assert ta.jev_verdict("evidence") is None


class TestEvidenceText:
    def test_subject_parameterized(self):
        a = _Agent(0.2, dispatch_every=0.05)
        a.chat_turn()
        s1 = ta.evidence_text(a, 1800, ta.evidence_snapshot(a),
                              "背景 stage ", {"title": "下載",
                                              "question": "q"})
        s2 = ta.evidence_text(a, 1800, ta.evidence_snapshot(a),
                              "Telegram 回合 ",
                              {"title": "下載", "question": "q"})
        assert s1.startswith("背景 stage 已連續執行")
        assert s2.startswith("Telegram 回合 已連續執行")
        assert "30.0 分鐘" in s1 and "30.0 分鐘" in s2
        assert "run_bash" in s1 and "下載" in s2
        assert "normal_long_work" in s1 and "abnormal" in s1


class TestGuardedRunJoin:
    def test_fast_pass(self):
        r, timed_out, used = _guarded(_Agent(0.05), 5,
                                      verdict_calls={"n": 0})
        assert r[0] == "ok" and timed_out is False and used == 0

    def test_worker_raise_propagates(self):
        class Boom(_Agent):
            def chat_turn(self):
                time.sleep(0.05)
                raise ValueError("boom")

        with pytest.raises(ValueError):
            _guarded(Boom(), 5, verdict_calls={"n": 0})

    def test_liveness_extends_then_completes(self):
        calls = {"n": 0}
        r, timed_out, used = _guarded(
            _Agent(2.2, dispatch_every=0.2), 1, verdict_calls=calls)
        assert timed_out is False and r[0] == "ok"
        assert used >= 1 and calls["n"] == 0  # liveness = free

    def test_verdict_extends_silent_run(self):
        calls = {"n": 0}
        r, timed_out, used = _guarded(
            _Agent(2.2), 1, verdict="extend", verdict_calls=calls)
        assert timed_out is False and used >= 1
        assert calls["n"] >= 1

    def test_kill_when_verdict_none(self):
        calls = {"n": 0}
        t0 = time.monotonic()
        r, timed_out, used = _guarded(
            _Agent(8.0), 1, verdict=None, verdict_calls=calls)
        assert r is None and timed_out is True and used == 0
        assert calls["n"] == 1
        assert time.monotonic() - t0 < 4

    def test_exhausted_is_fail_closed(self):
        calls = {"n": 0}
        r, timed_out, used = _guarded(
            _Agent(9.0), 1, verdict="extend", verdict_calls=calls,
            extensions_env="QDRANT_SKILL_STAGE_TIMEOUT_EXTENSIONS",
            extensions_default=2)
        assert timed_out is True and used == 2
        assert calls["n"] == 3  # 2 grants + 1 final consultation

    def test_owner_stop_abandons_immediately(self):
        a = _Agent(9.0)
        box: dict = {}

        def _run():
            box["result"] = a.chat_turn()

        w = threading.Thread(target=_run, daemon=True)
        w.start()
        r, timed_out, used = ta.guarded_run_join(
            w, box, a, window_s=1, log_label="[probe] stop",
            subject="背景 stage ", meta=None,
            extensions_env="QDRANT_SKILL_STAGE_TIMEOUT_EXTENSIONS",
            extensions_default=3,
            verdict_fn=lambda t: None, stopped=lambda: True)
        assert r is None and timed_out is True and used == 0