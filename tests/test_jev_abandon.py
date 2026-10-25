"""v0.9.85.0 — Jev abandon gate (探索放棄閘) tests.

The tool loop now tracks CONSECUTIVE empty/error tool results; at the
streak threshold ONE jev_noul decision decides whether the model
should stop trying variants (dead mirror sites) and answer from what
it already has. off (default) / shadow (log only) / on (inject a
stop-exploring system note when p ≥ prob). Every failure fails open.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest


@pytest.fixture
def agent(tmp_path, monkeypatch):
    monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path))
    from src.agent.agent import Agent

    from src.mcp_client.base import McpCallResult

    a = Agent()
    a._provider = "stub"
    a._model = "stub-model"
    mgr = MagicMock()
    mgr.has_tools.return_value = True
    mgr.call.return_value = McpCallResult(
        text='{"url": "https://x.example/", "content": ""}',
        is_error=False,
    )
    a._mcp_manager = mgr
    return a


def _resp(content="", finish="stop", tool_name=None):
    r = MagicMock()
    msg = MagicMock(content=content, finish_reason=finish)
    if tool_name:
        tc = MagicMock()
        tc.id = f"call-{tool_name}"
        tc.function = MagicMock(name=tool_name, arguments="{}")
        msg.tool_calls = [tc]
    else:
        msg.tool_calls = None
    r.choices = [MagicMock(message=msg, finish_reason=finish)]
    return r


def _wire(agent, outcomes):
    fake = MagicMock()
    fake.chat.completions.create.side_effect = list(outcomes)
    agent._client = fake
    return fake


TOOLS = [{"type": "function", "function": {
    "name": "web_fetch", "parameters": {}}}]

EMPTY_RESULT = '{"url": "https://x.example/", "content": ""}'


class TestResolvers:
    def test_mode_default_off_and_invalid(self, monkeypatch):
        from src.agent.agent import _jev_abandon_mode

        monkeypatch.delenv("QDRANT_JEV_ABANDON", raising=False)
        assert _jev_abandon_mode() == "off"
        monkeypatch.setenv("QDRANT_JEV_ABANDON", "SHADOW")
        assert _jev_abandon_mode() == "shadow"
        monkeypatch.setenv("QDRANT_JEV_ABANDON", "on")
        assert _jev_abandon_mode() == "on"
        monkeypatch.setenv("QDRANT_JEV_ABANDON", "wat")
        assert _jev_abandon_mode() == "off"

    def test_prob_default_and_clamp(self, monkeypatch):
        from src.agent.agent import _jev_abandon_prob

        monkeypatch.delenv("QDRANT_JEV_ABANDON_PROB", raising=False)
        assert _jev_abandon_prob() == 0.75
        monkeypatch.setenv("QDRANT_JEV_ABANDON_PROB", "0.9")
        assert _jev_abandon_prob() == 0.9
        monkeypatch.setenv("QDRANT_JEV_ABANDON_PROB", "3")
        assert _jev_abandon_prob() == 0.75
        monkeypatch.setenv("QDRANT_JEV_ABANDON_PROB", "abc")
        assert _jev_abandon_prob() == 0.75

    def test_streak_default_and_clamp(self, monkeypatch):
        from src.agent.agent import _jev_abandon_streak

        monkeypatch.delenv("QDRANT_JEV_ABANDON_STREAK", raising=False)
        assert _jev_abandon_streak() == 3
        monkeypatch.setenv("QDRANT_JEV_ABANDON_STREAK", "1")
        assert _jev_abandon_streak() == 2   # clamp low
        monkeypatch.setenv("QDRANT_JEV_ABANDON_STREAK", "99")
        assert _jev_abandon_streak() == 10  # clamp high
        monkeypatch.setenv("QDRANT_JEV_ABANDON_STREAK", "abc")
        assert _jev_abandon_streak() == 3

    def test_result_failed_heuristic(self):
        from src.agent.agent import _tool_result_failed

        assert _tool_result_failed("")
        assert _tool_result_failed(EMPTY_RESULT)
        assert _tool_result_failed(
            "TypeError: ...（參數定義：url:string）")
        assert not _tool_result_failed("ok content " * 50)


class TestGate:
    def _three_failing(self):
        return [
            _resp("", finish="tool_calls", tool_name="web_fetch"),
            _resp("", finish="tool_calls", tool_name="web_fetch"),
            _resp("", finish="tool_calls", tool_name="web_fetch"),
            _resp("已用現有資料作答"),
        ]

    @staticmethod
    def _no_note(fake) -> bool:
        for c in fake.chat.completions.create.call_args_list:
            for m in (c.kwargs.get("messages") or []):
                if (m.get("role") == "system"
                        and "工具迴圈提醒" in str(m.get("content") or "")):
                    return False
        return True

    def test_off_never_asks_jev(self, agent, monkeypatch):
        monkeypatch.delenv("QDRANT_JEV_ABANDON", raising=False)

        def _boom(*a, **k):
            raise AssertionError("jev must not run when off")

        monkeypatch.setattr("src.agent.agent._jev_abandon_check", _boom)
        _wire(agent, self._three_failing())
        out = agent._tool_loop(
            messages=[{"role": "user", "content": "q"}],
            tools=TOOLS, temperature=0.3, max_iterations=6,
        )
        assert out == "已用現有資料作答"

    def test_shadow_logs_without_note(self, agent, monkeypatch):
        monkeypatch.setenv("QDRANT_JEV_ABANDON", "shadow")
        monkeypatch.setattr("src.agent.agent._jev_abandon_check",
                            lambda *a, **k: (0.95, {"elapsed_s": 0.2}))
        fake = _wire(agent, self._three_failing())
        agent._tool_loop(
            messages=[{"role": "user", "content": "q"}],
            tools=TOOLS, temperature=0.3, max_iterations=6,
        )
        assert self._no_note(fake)

    def test_on_injects_note(self, agent, monkeypatch):
        monkeypatch.setenv("QDRANT_JEV_ABANDON", "on")
        monkeypatch.setenv("QDRANT_JEV_ABANDON_PROB", "0.75")
        monkeypatch.setattr("src.agent.agent._jev_abandon_check",
                            lambda *a, **k: (0.95, {"elapsed_s": 0.2}))
        fake = _wire(agent, self._three_failing())
        agent._tool_loop(
            messages=[{"role": "user", "content": "q"}],
            tools=TOOLS, temperature=0.3, max_iterations=6,
        )
        assert not self._no_note(fake)

    def test_on_low_confidence_no_note(self, agent, monkeypatch):
        monkeypatch.setenv("QDRANT_JEV_ABANDON", "on")
        monkeypatch.setattr("src.agent.agent._jev_abandon_check",
                            lambda *a, **k: (0.4, {"elapsed_s": 0.2}))
        fake = _wire(agent, self._three_failing())
        agent._tool_loop(
            messages=[{"role": "user", "content": "q"}],
            tools=TOOLS, temperature=0.3, max_iterations=6,
        )
        assert self._no_note(fake)

    def test_jev_failure_fails_open(self, agent, monkeypatch):
        monkeypatch.setenv("QDRANT_JEV_ABANDON", "on")
        monkeypatch.setattr("src.agent.agent._jev_abandon_check",
                            lambda *a, **k: (None, {"error": "402"}))
        fake = _wire(agent, self._three_failing())
        out = agent._tool_loop(
            messages=[{"role": "user", "content": "q"}],
            tools=TOOLS, temperature=0.3, max_iterations=6,
        )
        assert out == "已用現有資料作答"
        assert self._no_note(fake)

    def test_success_resets_streak(self, agent, monkeypatch):
        """2 failures → 1 success → the streak resets; the gate (3) is
        never reached even though the turn continues."""
        monkeypatch.setenv("QDRANT_JEV_ABANDON", "on")

        def _boom(*a, **k):
            raise AssertionError("streak should never reach 3")

        monkeypatch.setattr("src.agent.agent._jev_abandon_check", _boom)
        from src.mcp_client.base import McpCallResult

        _wire(agent, [
            _resp("", finish="tool_calls", tool_name="web_fetch"),
            _resp("", finish="tool_calls", tool_name="web_fetch"),
            _resp("", finish="tool_calls", tool_name="get_current_time"),
            _resp("done"),
        ])
        agent.mcp_manager.call.side_effect = [
            McpCallResult(text=EMPTY_RESULT, is_error=False),
            McpCallResult(text=EMPTY_RESULT, is_error=False),
            McpCallResult(text='{"local": {"date": "2026-09-22"}}',
                          is_error=False),
        ]
        agent._tool_loop(
            messages=[{"role": "user", "content": "q"}],
            tools=TOOLS, temperature=0.3, max_iterations=6,
        )

    def test_once_per_turn(self, agent, monkeypatch):
        monkeypatch.setenv("QDRANT_JEV_ABANDON", "on")
        calls = {"n": 0}

        def _check(*a, **k):
            calls["n"] += 1
            return (0.95, {"elapsed_s": 0.2})

        monkeypatch.setattr("src.agent.agent._jev_abandon_check", _check)
        outcomes = [
            _resp("", finish="tool_calls", tool_name="web_fetch"),
            _resp("", finish="tool_calls", tool_name="web_fetch"),
            _resp("", finish="tool_calls", tool_name="web_fetch"),
            _resp("", finish="tool_calls", tool_name="web_fetch"),
            _resp("", finish="tool_calls", tool_name="web_fetch"),
            _resp("done"),
        ]
        _wire(agent, outcomes)
        agent._tool_loop(
            messages=[{"role": "user", "content": "q"}],
            tools=TOOLS, temperature=0.3, max_iterations=6,
        )
        assert calls["n"] == 1  # ONE gate decision per turn