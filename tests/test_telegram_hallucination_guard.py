"""v0.9.57.1 — Telegram photo-hallucination guard.

Live diagnosis 2026-09-15 00:03 (user: 「沒畫面」): glm-5.3-flash
intermittently answers camera-grab requests by FABRICATING a success
reply (「✅ 已截取並發送到你的 Telegram（訊息 #219）」) with ZERO tool
dispatches — the claimed file did not exist and no photo was ever
sent. app.log proved the turn started → done with no `[agent] tool`
line between; the uploads/ dir had no such file. The guard detects
the deterministic shape (delivery claim + no send_telegram_photo
dispatched) and re-prompts once with a corrective instruction.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.agent.telegram_bot import (  # noqa: E402
    _PHOTO_RETRY_PROMPT,
    _needs_photo_hallucination_retry,
    make_telegram_on_message,
)


@pytest.fixture()
def tg_home(tmp_path, monkeypatch):
    """Isolated QDRANT_RAG_HOME — sessions land in a tmp dir
    (same fixture shape as tests/test_telegram_bot.py)."""
    monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path / "home"))
    return tmp_path

# The EXACT fabricated reply from the 2026-09-15 00:03 live turn.
_FABRICATED_ANSWER = (
    "✅ 已截取並發送到你的 Telegram（訊息 #219）！\n\n"
    "- **檔案**：`uploads\\maindoor_20260915_0902.jpg`（704×576）\n"
    "- **拍攝時間**：2026/09/15 09:02（白天畫面）\n\n"
    "還需要其他操作嗎？"
)
_CAMERA_REQUEST = "抓五股大門camera畫面"


class TestGuardDetection:
    def test_live_fabricated_turn_matches(self):
        assert _needs_photo_hallucination_retry(
            _CAMERA_REQUEST, _FABRICATED_ANSWER, [],
        )

    def test_legit_send_suppresses(self):
        assert not _needs_photo_hallucination_retry(
            _CAMERA_REQUEST, _FABRICATED_ANSWER,
            ["onvif_get_snapshot", "send_telegram_photo"],
        )

    def test_onvif_only_does_not_suppress_delivery_claim(self):
        """A snapshot WITHOUT send_telegram_photo never delivered
        anything — a 「已發送」 claim on top of it is still fake."""
        assert _needs_photo_hallucination_retry(
            _CAMERA_REQUEST,
            "已發送到你的 Telegram（訊息 #200）",
            ["onvif_get_snapshot", "describe_image"],
        )

    def test_no_claim_no_retry(self):
        assert not _needs_photo_hallucination_retry(
            _CAMERA_REQUEST, "請說明要哪一台攝影機？", [],
        )

    def test_non_camera_request_no_retry(self):
        assert not _needs_photo_hallucination_retry(
            "現在幾點", "✅ 已發送（訊息 #219）", [],
        )

    def test_english_shapes(self):
        assert _needs_photo_hallucination_retry(
            "show main gate camera", "✅ Sent to Telegram "
            "(message_id: 219)", [],
        )
        assert not _needs_photo_hallucination_retry(
            "show main gate camera", "✅ Sent to Telegram "
            "(message_id: 219)", ["send_telegram_photo"],
        )

    def test_empty_and_none_safe(self):
        assert not _needs_photo_hallucination_retry(None, None, None)
        assert not _needs_photo_hallucination_retry("", "", [])


class TestAgentDispatchAudit:
    """`last_tool_dispatches` — appended in _run_tool_call, reset at
    ask/chat_turn entry."""

    def _agent(self):
        from src.agent.agent import Agent

        return Agent.__new__(Agent)

    def _tool_call(self, name, args='{"x": 1}'):
        from types import SimpleNamespace

        return SimpleNamespace(
            id="c1",
            function=SimpleNamespace(name=name, arguments=args),
        )

    def test_run_tool_call_appends(self):
        from src.mcp_client.base import McpCallResult

        a = self._agent()
        mgr = MagicMock()
        mgr.call.return_value = McpCallResult(
            text='{"ok": true}', is_error=False,
        )
        a._mcp_manager = mgr
        a.last_tool_dispatches = []
        msg = a._run_tool_call(self._tool_call("onvif_get_snapshot"))
        assert a.last_tool_dispatches == ["onvif_get_snapshot"]
        assert msg["role"] == "tool"

    def test_appends_across_multiple_calls(self):
        from src.mcp_client.base import McpCallResult

        a = self._agent()
        mgr = MagicMock()
        mgr.call.return_value = McpCallResult(text="ok", is_error=False)
        a._mcp_manager = mgr
        a.last_tool_dispatches = []
        a._run_tool_call(self._tool_call("onvif_get_snapshot"))
        a._run_tool_call(self._tool_call("send_telegram_photo"))
        assert a.last_tool_dispatches == [
            "onvif_get_snapshot", "send_telegram_photo",
        ]

    def test_missing_attr_recovers(self):
        """A stub Agent without the attr must not crash the dispatch."""
        from src.mcp_client.base import McpCallResult

        a = self._agent()
        mgr = MagicMock()
        mgr.call.return_value = McpCallResult(text="ok", is_error=False)
        a._mcp_manager = mgr
        assert not hasattr(a, "last_tool_dispatches")
        a._run_tool_call(self._tool_call("get_current_time", "{}"))
        assert a.last_tool_dispatches == ["get_current_time"]

    def test_resets_present_at_both_entries(self):
        src = Path("src/agent/agent.py").read_text(encoding="utf-8")
        assert src.count("self.last_tool_dispatches = []") >= 2
        assert "self.last_tool_dispatches: list[str] = []" in src


class _GuardAgent:
    """Fake Agent: scripted answers + fixed dispatch audit list."""

    def __init__(self, answers, dispatches=None):
        self._answers = list(answers)
        self.calls = []
        self.last_tool_dispatches = list(dispatches or [])

    def chat_turn(self, question, history=None, **kw):
        self.calls.append(question)
        return (self._answers.pop(0), "ctx", [])


class TestHandleRetryFlow:
    def test_fabricated_reply_retried_once(self, tg_home):
        agent = _GuardAgent(
            [_FABRICATED_ANSWER, "✅ 已真的發送（訊息 #220）"],
            dispatches=["list_chat_sessions"],
        )
        handle = make_telegram_on_message(lambda: agent)
        reply = handle(111, _CAMERA_REQUEST)
        # The corrective turn ran and ITS answer is delivered.
        assert len(agent.calls) == 2
        assert agent.calls[1] == _PHOTO_RETRY_PROMPT
        assert reply == "✅ 已真的發送（訊息 #220）"
        # Session audit: user / fabricated(flagged) / corrective / final.
        from src.agent.session import SessionManager

        sess = SessionManager().load("tgbot-111")
        assert [m["role"] for m in sess.messages] == [
            "user", "assistant", "user", "assistant",
        ]
        assert sess.messages[1]["hallucination_guarded"] is True
        assert sess.messages[1]["content"] == _FABRICATED_ANSWER
        assert sess.messages[2]["content"] == _PHOTO_RETRY_PROMPT
        assert sess.messages[3]["content"] == reply

    def test_legit_send_not_retried(self, tg_home):
        agent = _GuardAgent(
            [_FABRICATED_ANSWER],
            dispatches=["onvif_get_snapshot", "send_telegram_photo"],
        )
        handle = make_telegram_on_message(lambda: agent)
        reply = handle(111, _CAMERA_REQUEST)
        assert len(agent.calls) == 1
        assert reply == _FABRICATED_ANSWER

    def test_retry_failure_delivers_original(self, tg_home):
        class _BoomRetry:
            def __init__(self):
                self.calls = 0
                self.last_tool_dispatches = []

            def chat_turn(self, question, history=None, **kw):
                self.calls += 1
                if self.calls == 1:
                    return (_FABRICATED_ANSWER, "ctx", [])
                raise RuntimeError("provider down")

        agent = _BoomRetry()
        handle = make_telegram_on_message(lambda: agent)
        reply = handle(111, _CAMERA_REQUEST)
        assert agent.calls == 2
        assert reply == _FABRICATED_ANSWER

    def test_retry_empty_answer_keeps_original(self, tg_home):
        agent = _GuardAgent([_FABRICATED_ANSWER, "   "], dispatches=[])
        handle = make_telegram_on_message(lambda: agent)
        reply = handle(111, _CAMERA_REQUEST)
        assert len(agent.calls) == 2
        assert reply == _FABRICATED_ANSWER

    def test_non_camera_or_claimless_no_retry(self, tg_home):
        agent = _GuardAgent(["ok"], dispatches=[])
        handle = make_telegram_on_message(lambda: agent)
        reply = handle(111, "hello")
        assert len(agent.calls) == 1  # no corrective turn
        assert reply == "ok"


class TestPromptRule:
    def _prompt(self, tools_enabled=True):
        from src.agent.agent import Agent

        return Agent.__new__(Agent)._system_prompt(
            tools_enabled=tools_enabled,
        )

    def test_anti_fabrication_rule_present(self):
        p = self._prompt()
        assert "必須真的呼叫工具" in p
        assert "絕對禁止" in p
        assert "onvif_rtsp_snapshot" in p
        assert "send_telegram_photo" in p

    def test_rule_absent_in_legacy_prompt(self):
        p = self._prompt(tools_enabled=False)
        assert "必須真的呼叫工具" not in p
        assert "絕對禁止" not in p

    def test_retry_prompt_constant_shape(self):
        assert "onvif_get_snapshot" in _PHOTO_RETRY_PROMPT
        assert "send_telegram_photo" in _PHOTO_RETRY_PROMPT
        assert "系統偵測" in _PHOTO_RETRY_PROMPT

    def test_handle_source_wires_guard(self):
        src = Path("src/agent/telegram_bot.py").read_text(
            encoding="utf-8",
        )
        assert "_needs_photo_hallucination_retry(text, answer_text," \
            in src
        assert "photo-hallucination guard fired" in src
        assert "hallucination_guarded" in src
        assert "corrective retry failed" in src