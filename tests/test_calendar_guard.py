"""Tests for the calendar action-integrity guard (v0.9.91.0).

User report (v0.9.90.0 live telegram turn): 「沒有移成功」 after the
model answered 1202 chars with ZERO calendar write dispatches — the
move was never performed. The guard fires ONE corrective turn (tools
enabled) whenever the question carries a calendar-action intent but
the turn dispatched none of the calendar write tools.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path))
    monkeypatch.delenv("QDRANT_CALENDAR_GUARD", raising=False)
    monkeypatch.delenv("KEEP_MODE", raising=False)
    yield


def _agent(monkeypatch, dispatches=None, on=True):
    if on:
        monkeypatch.setenv("QDRANT_CALENDAR_GUARD", "on")
    from src.agent.agent import Agent

    a = Agent()
    a._client = MagicMock()
    a._model = "stub-model"
    a._provider = "mock"
    a._mcp_manager = MagicMock()
    a.last_tool_dispatches = list(dispatches or [])
    a._last_turn_question = ""
    a._calendar_guard_ran = False
    a._last_memory_hits = []
    return a


class TestIntentDetection:
    def test_move_intent_fires(self, monkeypatch):
        a = _agent(monkeypatch, dispatches=[])
        a._tool_loop = MagicMock(return_value="已移動：event id abc")
        out = a._calendar_action_guard(
            "把豐德醫院的會議移到下午三點", "好的，我已經調整好了。",
            [], 0.3, 20)
        assert a._calendar_guard_ran is True  # corrective ran
        a._tool_loop.assert_called_once()
        assert out != "好的，我已經調整好了。"

    def test_delete_intent_fires(self, monkeypatch):
        a = _agent(monkeypatch, dispatches=[])
        a._tool_loop = MagicMock(return_value="已刪除：event id xyz")
        out = a._calendar_action_guard(
            "刪除那個會議", "好的，已刪除。", [], 0.3, 20)
        assert a._calendar_guard_ran is True
        a._tool_loop.assert_called_once()
        assert out == "已刪除：event id xyz"

    def test_question_mark_skips(self, monkeypatch):
        a = _agent(monkeypatch, dispatches=[])
        out = a._calendar_action_guard(
            "可以幫我把會議改到四點嗎?", "可以，告訴我哪一個。",
            [], 0.3, 20)
        assert out == "可以，告訴我哪一個。"

    def test_no_noun_skips(self, monkeypatch):
        a = _agent(monkeypatch, dispatches=[])
        out = a._calendar_action_guard(
            "把這段話刪掉重寫", "已重寫。", [], 0.3, 20)
        assert out == "已重寫。"

    def test_write_dispatch_present_skips(self, monkeypatch):
        a = _agent(monkeypatch, dispatches=["update-event"])
        out = a._calendar_action_guard(
            "把會議移到三點", "已移動完成。", [], 0.3, 20)
        assert out == "已移動完成。"


class TestGuardBehavior:
    def test_corrective_answer_replaces_original(self, monkeypatch):
        a = _agent(monkeypatch, dispatches=[])
        a._last_turn_question = "把會議移到三點"
        a._tool_loop = MagicMock(
            return_value="已移動：event id abc（真實工具結果）")
        out = a._calendar_action_guard(
            "把會議移到三點", "已移動好了。", [], 0.3, 20)
        assert "真實工具結果" in out
        a._tool_loop.assert_called_once()
        # the directive rode along as the last user message
        sent = a._tool_loop.call_args.kwargs["messages"][-1]["content"]
        assert "update-event" in sent and "把會議移到三點" in sent

    def test_corrective_failure_appends_honest_note(self, monkeypatch):
        a = _agent(monkeypatch, dispatches=[])
        a._last_turn_question = "把會議移到三點"
        a._tool_loop = MagicMock(side_effect=RuntimeError("boom"))
        original = "已移動好了。"
        out = a._calendar_action_guard(
            "把會議移到三點", original, [], 0.3, 20)
        assert original in out
        assert "未實際執行" in out

    def test_once_per_turn(self, monkeypatch):
        a = _agent(monkeypatch, dispatches=[])
        a._last_turn_question = "把會議移到三點"
        a._tool_loop = MagicMock(return_value="真實結果")
        first = a._calendar_action_guard(
            "把會議移到三點", "好了。", [], 0.3, 20)
        second = a._calendar_action_guard(
            "把會議移到三點", "好了。", [], 0.3, 20)
        assert first == "真實結果"  # corrective answer replaces original
        assert second == "好了。"  # once per turn
        assert a._tool_loop.call_count == 1

    def test_env_off_never_fires(self, monkeypatch):
        a = _agent(monkeypatch, dispatches=[], on=False)
        out = a._calendar_action_guard(
            "把會議移到三點", "已移動。", [], 0.3, 20)
        assert out == "已移動。"

    def test_chat_turn_impl_resets_guard(self):
        from pathlib import Path

        src = Path("src/agent/agent.py").read_text(encoding="utf-8")
        assert src.count("self._calendar_guard_ran = False") >= 2
        assert "_calendar_action_guard(" in src