"""v0.9.102.0 — Telegram session context discipline.

Live report (user: 「telegram 的 session 不能更新和清除 導致 context
內容過多 llm 錯亂」): the per-chat session tgbot-<chat_id> grew
FOREVER and every turn fed the ENTIRE history to chat_turn — context
bloat made the LLM drift/confuse. This suite locks the three
controls: the TELEGRAM_HISTORY_CAP feed slice, the stored-session
ring trim (TELEGRAM_SESSION_MAX_MESSAGES), and the in-chat /new
clear command.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.agent.telegram_bot import (  # noqa: E402
    _SESSION_CLEAR_REPLY,
    _telegram_history_cap,
    _telegram_session_max_messages,
    make_telegram_on_message,
)
from src.agent.session import SessionManager  # noqa: E402


@pytest.fixture()
def tg_home(tmp_path, monkeypatch):
    monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path / "home"))
    return tmp_path


class _CapAgent:
    """Records the history lists it was fed; answers one-liners."""

    def __init__(self):
        self.history_lens = []

    def chat_turn(self, question, history=None, **kw):
        self.history_lens.append(len(history or []))
        return (f"答{len(self.history_lens)}", "ctx", [])


def _fill_session(chat_id, n_msgs):
    sm = SessionManager()
    sid = f"tgbot-{chat_id}"
    sess = sm.load(sid)
    if sess is None:
        from src.agent.session import Session

        sess = Session(sid=sid, title=f"[telegram bot] chat {chat_id}")
    for i in range(n_msgs):
        sess.messages.append({"role": "user" if i % 2 == 0 else
                              "assistant", "content": f"m{i}",
                              "ts": "2026-01-01T00:00:00"})
    sm.save(sess)
    return sid


class TestResolvers:
    def test_default_cap_is_12(self, monkeypatch):
        monkeypatch.delenv("TELEGRAM_HISTORY_CAP", raising=False)
        assert _telegram_history_cap() == 12

    def test_custom_cap_and_negative_is_full(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_HISTORY_CAP", "6")
        assert _telegram_history_cap() == 6
        monkeypatch.setenv("TELEGRAM_HISTORY_CAP", "-1")
        assert _telegram_history_cap() == -1
        monkeypatch.setenv("TELEGRAM_HISTORY_CAP", "junk")
        assert _telegram_history_cap() == 12

    def test_session_max_default_200(self, monkeypatch):
        monkeypatch.delenv("TELEGRAM_SESSION_MAX_MESSAGES",
                           raising=False)
        assert _telegram_session_max_messages() == 200
        monkeypatch.setenv("TELEGRAM_SESSION_MAX_MESSAGES", "0")
        assert _telegram_session_max_messages() == -1


class TestHistoryCap:
    def test_feed_is_sliced(self, tg_home, monkeypatch):
        monkeypatch.setenv("TELEGRAM_HISTORY_CAP", "4")
        _fill_session(201, 30)
        agent = _CapAgent()
        handle = make_telegram_on_message(lambda: agent)
        handle(201, "hello")
        assert agent.history_lens == [4]   # last 4 of the 30 prior

    def test_full_feed_legacy(self, tg_home, monkeypatch):
        monkeypatch.setenv("TELEGRAM_HISTORY_CAP", "-1")
        _fill_session(202, 30)
        agent = _CapAgent()
        handle = make_telegram_on_message(lambda: agent)
        handle(202, "hello")
        assert agent.history_lens == [30]

    def test_session_file_keeps_everything(self, tg_home, monkeypatch):
        monkeypatch.setenv("TELEGRAM_HISTORY_CAP", "4")
        _fill_session(203, 30)
        agent = _CapAgent()
        handle = make_telegram_on_message(lambda: agent)
        handle(203, "hello")
        sid = f"tgbot-203"
        sm = SessionManager()
        sess = sm.load(sid)
        # 30 old + framed user + assistant answer = 32
        assert len(sess.messages) == 32


class TestStoredRingTrim:
    def test_ring_trim_to_max(self, tg_home, monkeypatch):
        monkeypatch.setenv("TELEGRAM_SESSION_MAX_MESSAGES", "10")
        _fill_session(204, 20)
        agent = _CapAgent()
        handle = make_telegram_on_message(lambda: agent)
        handle(204, "trigger")
        sess = SessionManager().load("tgbot-204")
        # 20 old + framed user + answer, trimmed to the newest 10
        assert len(sess.messages) == 10
        assert sess.messages[-1]["role"] == "assistant"

    def test_no_trim_when_under_cap(self, tg_home):
        agent = _CapAgent()
        handle = make_telegram_on_message(lambda: agent)
        handle(205, "trigger")
        sess = SessionManager().load("tgbot-205")
        assert len(sess.messages) == 2   # user + assistant


class TestInChatClear:
    def _is_cleared_file(self, tmp, chat_id):
        f = (Path(tmp) / "home" / "sessions" / f"tgbot-{chat_id}.json")
        if not f.is_file():
            return None
        return json.loads(f.read_text(encoding="utf-8"))

    @pytest.mark.parametrize("cmd", ["/new", "/clear", "清除對話",
                                     "新對話"])
    def test_clear_command_resets_session(self, tg_home, cmd):
        _fill_session(206, 20)
        agent = _CapAgent()
        handle = make_telegram_on_message(lambda: agent)
        reply = handle(206, cmd)
        assert reply == _SESSION_CLEAR_REPLY
        data = self._is_cleared_file(tg_home, 206)
        assert data is not None
        assert len(data["messages"]) == 0
        agent.chat_turn  # never called — no LLM turn burned
        assert agent.history_lens == []

    def test_normal_message_not_treated_as_command(self, tg_home):
        agent = _CapAgent()
        handle = make_telegram_on_message(lambda: agent)
        reply = handle(207, "/news 上有一些好料")
        assert reply != _SESSION_CLEAR_REPLY
        assert agent.history_lens == [0]
        data = self._is_cleared_file(tg_home, 207)
        assert data is not None and len(data["messages"]) >= 2