"""Tests for the Telegram bidirectional bot (v0.9.27.0+).

The daemon long-polls ``getUpdates`` (pure OUTBOUND HTTPS) and feeds
whitelisted chats' messages into ``Agent.chat_turn``; replies are
auto-delivered via ``sendMessage``. All tests inject a FAKE client —
no real network call is ever made.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_ALLOWED_CHAT_IDS", raising=False)
    monkeypatch.delenv("QDRANT_TELEGRAM_BOT_POLLING", raising=False)
    monkeypatch.delenv("QDRANT_TELEGRAM_BOT_POLL_INTERVAL",
                       raising=False)


@pytest.fixture()
def tg_home(tmp_path, monkeypatch):
    monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path / "home"))
    return tmp_path


def _update(update_id: int, chat_id: int, text: str) -> dict:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id,
            "chat": {"id": chat_id, "type": "private"},
            "text": text,
        },
    }


class _FakeClient:
    """In-memory stand-in for TelegramBotClient."""

    def __init__(self, updates=None):
        self.updates = list(updates or [])
        self.sent: list[tuple[int, str]] = []
        self.get_offsets: list[int] = []

    def get_updates(self, offset: int, wait: float = 10.0) -> list:
        self.get_offsets.append(offset)
        return self.updates

    def send_message(self, chat_id: int, text: str):
        self.sent.append((chat_id, text))
        return {"message_id": 1}


class TestPollOnce:
    def test_whitelisted_message_handled_and_replied(self, tg_home,
                                                     monkeypatch):
        monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "111,222")
        from src.agent.telegram_bot import TelegramBotDaemon

        sent_by_handler = []

        def on_message(chat_id, text):
            sent_by_handler.append((chat_id, text))
            return f"echo:{text}"

        client = _FakeClient([_update(5, 111, "你好")])
        d = TelegramBotDaemon(client=client, on_message=on_message,
                              poll_interval=0.1)
        handled = d.poll_once()
        assert handled == 1
        assert sent_by_handler == [(111, "你好")]
        assert client.sent == [(111, "echo:你好")]  # reply delivered
        assert d._offset == 6                       # offset acked
        assert client.get_offsets == [0]

    def test_non_whitelisted_ignored_silently(self, tg_home,
                                              monkeypatch):
        monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "111")
        from src.agent.telegram_bot import TelegramBotDaemon

        client = _FakeClient([_update(5, 999, "hello stranger")])
        d = TelegramBotDaemon(client=client,
                              on_message=MagicMock(),
                              poll_interval=0.1)
        d.poll_once()
        assert d.on_message.call_count == 0  # no LLM call
        assert client.sent == []             # no reply
        assert d._offset == 6                # acked anyway

    def test_empty_allowlist_fails_closed(self, tg_home, monkeypatch):
        monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "")
        from src.agent.telegram_bot import TelegramBotDaemon

        client = _FakeClient([_update(5, 111, "hi")])
        d = TelegramBotDaemon(client=client, on_message=MagicMock(),
                              poll_interval=0.1)
        d.poll_once()
        assert d.on_message.call_count == 0
        assert client.sent == []

    def test_offset_acks_across_batches(self, tg_home, monkeypatch):
        monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "111")
        from src.agent.telegram_bot import TelegramBotDaemon

        client = _FakeClient([
            _update(10, 111, "one"),
            _update(11, 111, "two"),
        ])
        d = TelegramBotDaemon(client=client,
                              on_message=lambda cid, t: f"r:{t}",
                              poll_interval=0.1)
        d.poll_once()
        assert d._offset == 12
        client.updates = []  # NEXT poll carries the advanced offset
        d.poll_once()
        assert client.get_offsets == [0, 12]

    def test_non_text_message_ignored(self, tg_home, monkeypatch):
        monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "111")
        from src.agent.telegram_bot import TelegramBotDaemon

        client = _FakeClient([
            {"update_id": 7, "message": {"chat": {"id": 111}}},
            {"update_id": 8},  # no message at all
        ])
        d = TelegramBotDaemon(client=client, on_message=MagicMock(),
                              poll_interval=0.1)
        d.poll_once()
        assert d.on_message.call_count == 0


class _FakeAgent:
    def __init__(self, answer="ok"):
        self.answer = answer

    def chat_turn(self, question, history=None, **kw):
        if self.answer == "boom":
            raise RuntimeError("qdrant down")
        return (f"answer:{question.splitlines()[1]}", "ctx", [])


class TestOnMessageHandler:
    def test_persistent_session_and_reply(self, tg_home):
        from src.agent.session import SessionManager
        from src.agent.telegram_bot import make_telegram_on_message

        handle = make_telegram_on_message(lambda: _FakeAgent())
        reply1 = handle(111, "你好")
        assert reply1.startswith("answer:你好")
        sm = SessionManager()
        sess = sm.load("tgbot-111")
        assert sess is not None
        assert len(sess.messages) == 2  # user + assistant
        # second turn keeps history (multi-turn)
        reply2 = handle(111, "再問一次")
        sess2 = sm.load("tgbot-111")
        assert len(sess2.messages) == 4
        assert reply2.startswith("answer:再問")

    def test_framing_anti_double_send(self, tg_home):
        from src.agent.telegram_bot import make_telegram_on_message

        questions = []

        class RecordingAgent:
            def chat_turn(self, question, history=None, **kw):
                questions.append(question)
                return ("ok", "ctx", [])

        handle = make_telegram_on_message(lambda: RecordingAgent())
        handle(111, "test")
        assert "[Telegram message from chat 111]" in questions[0]
        assert "不要自行呼叫 send_telegram_message" in questions[0]

    def test_chat_turn_failure_degrades_to_error_reply(self, tg_home):
        from src.agent.telegram_bot import make_telegram_on_message

        handle = make_telegram_on_message(
            lambda: _FakeAgent(answer="boom"),
        )
        reply = handle(111, "hi")
        assert reply.startswith("[內部錯誤]")
        assert "RuntimeError" in reply

    def test_empty_answer_fallback(self, tg_home):
        from src.agent.telegram_bot import make_telegram_on_message

        class EmptyAgent:
            def chat_turn(self, question, history=None, **kw):
                return ("", "ctx", [])

        reply = make_telegram_on_message(lambda: EmptyAgent())(
            111, "hi",
        )
        assert reply == "(無回覆 — 模型未產生內容)"


# ─── Agent wiring + boot resume ──────────────────────────────


class TestAgentWiring:
    def test_agent_property_and_start_stop(self, tg_home):
        from unittest.mock import MagicMock

        from src.agent.agent import Agent

        a = Agent()
        a._client = MagicMock()
        a._model = "mock-model"
        a._provider = "mock"
        bot = a.telegram_bot
        assert bot is a.telegram_bot  # cached
        bot.start()
        assert bot.is_running()
        a.start_telegram_bot()  # idempotent
        a.stop_telegram_bot()
        assert not bot.is_running()


class TestBootResume:
    def _api(self):
        from gui.api import Api

        return Api()

    def _agent(self):
        a = MagicMock()
        return a

    def test_resume_starts_when_ready(self, tg_home, monkeypatch):
        import gui.api as api

        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "T")
        monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "111")
        monkeypatch.setenv("QDRANT_TELEGRAM_BOT_POLLING", "on")
        a = self._agent()
        api.Api._resume_telegram_bot(api, a)
        a.start_telegram_bot.assert_called_once()

    def test_resume_skipped_env_off(self, tg_home, monkeypatch):
        import gui.api as api

        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "T")
        monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "111")
        monkeypatch.setenv("QDRANT_TELEGRAM_BOT_POLLING", "off")
        a = self._agent()
        api.Api._resume_telegram_bot(api, a)
        a.start_telegram_bot.assert_not_called()

    def test_resume_skipped_missing_token(self, tg_home, monkeypatch):
        import gui.api as api

        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "111")
        a = self._agent()
        api.Api._resume_telegram_bot(api, a)
        a.start_telegram_bot.assert_not_called()

    def test_resume_skipped_empty_allowlist(self, tg_home,
                                             monkeypatch):
        import gui.api as api

        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "T")
        monkeypatch.delenv("TELEGRAM_ALLOWED_CHAT_IDS", raising=False)
        a = self._agent()
        api.Api._resume_telegram_bot(api, a)
        a.start_telegram_bot.assert_not_called()

    def test_resume_failure_swallowed(self, tg_home, monkeypatch):
        import gui.api as api

        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "T")
        monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "111")
        a = self._agent()
        a.start_telegram_bot = MagicMock(
            side_effect=RuntimeError("boom"),
        )
        api.Api._resume_telegram_bot(api, a)  # must not raise

    def test_prewarm_calls_resume(self, tg_home, monkeypatch):
        import gui.api as api

        calls = []
    def test_prewarm_calls_resume(self, tg_home, monkeypatch):
        import gui.api as api

        calls = []
        monkeypatch.setattr(
            api.Api, "_resume_telegram_bot",
            # staticmethod (the call site is `self._resume_telegram_
            # bot(agent)` — a staticmethod receives only `agent`)
            staticmethod(lambda agent: calls.append(agent)),
        )
        monkeypatch.setattr(
            api.Api, "_get_agent",
            lambda self: MagicMock(),
        )
        a = self._api()._prewarm_agent()
        assert calls  # resume invoked during prewarm


# ─── Stock order-approval gate (v0.9.28.0) ───────────────────


@pytest.fixture(autouse=True)
def _clean_approvals():
    from src.agent.telegram_bot import _APPROVALS

    _APPROVALS.clear()
    yield
    _APPROVALS.clear()


class TestApprovalRegistry:
    def test_request_resolves_approved(self, tg_home, monkeypatch):
        import threading

        import src.agent.telegram_bot as tb

        sent = []
        monkeypatch.setattr(tb, "_last_poll_ts", 9_999_999_999.0)

        def fake_send(chat_id, text):
            sent.append((chat_id, text))
            code = text.split("#")[1].split("）")[0]
            assert tb._try_resolve_approval(111, f"同意 {code}")
            return {"message_id": 1}

        holder = {}

        def run_gate():
            holder["outcome"] = tb.request_order_approval(
                111, "測試下單", timeout_s=2.0, client=type(
                    "C", (), {"send_message": staticmethod(fake_send)},
                )(),
            )

        t = threading.Thread(target=run_gate)
        t.start()
        t.join(timeout=5)
        assert holder["outcome"] == "approved"
        assert sent and sent[0][0] == 111
        assert "同意" in sent[0][1]

    def test_denied_via_reject_word(self, tg_home, monkeypatch):
        import threading

        import src.agent.telegram_bot as tb

        monkeypatch.setattr(tb, "_last_poll_ts", 9_999_999_999.0)
        holder = {}

        def fake_send(chat_id, text):
            code = text.split("#")[1].split("）")[0]
            tb._try_resolve_approval(111, f"拒絕 {code}")
            return {}

        def run():
            holder["o"] = tb.request_order_approval(
                111, "測試", timeout_s=2.0, client=type(
                    "C", (), {"send_message": staticmethod(fake_send)},
                )(),
            )

        t = threading.Thread(target=run)
        t.start()
        t.join(timeout=5)
        assert holder["o"] == "denied"

    def test_bare_code_approved_ambiguous_denied(self, tg_home):
        from src.agent.telegram_bot import (
            _PendingApproval, _try_resolve_approval,
        )

        a = _PendingApproval(code="STK-AAAA", chat_id=111, summary="s")
        from src.agent.telegram_bot import _APPROVALS

        _APPROVALS["STK-AAAA"] = a
        assert _try_resolve_approval(111, "同意 STK-AAAA") is True
        assert a.outcome == "approved"

        b = _PendingApproval(code="STK-BBBB", chat_id=111)
        _APPROVALS["STK-BBBB"] = b
        assert _try_resolve_approval(111, "STK-BBBB") is True
        assert b.outcome == "approved"

        c = _PendingApproval(code="STK-CCCC", chat_id=111)
        _APPROVALS["STK-CCCC"] = c
        assert _try_resolve_approval(
            111, "STK-CCCC 但我不確定",
        ) is True
        assert c.outcome == "denied"  # ambiguous → deny

    def test_other_chat_not_consumed(self, tg_home):
        from src.agent.telegram_bot import (
            _PendingApproval, _try_resolve_approval,
        )

        a = _PendingApproval(code="STK-DDDD", chat_id=222)
        from src.agent.telegram_bot import _APPROVALS

        _APPROVALS["STK-DDDD"] = a
        assert _try_resolve_approval(111, "同意 STK-DDDD") is False
        assert a.outcome == ""  # untouched

    def test_daemon_consumes_approval_reply_before_chat(
        self, tg_home, monkeypatch,
    ):
        monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "111")
        from src.agent.telegram_bot import (
            TelegramBotDaemon, _PendingApproval,
        )

        entry = _PendingApproval(code="STK-EEEE", chat_id=111)
        from src.agent.telegram_bot import _APPROVALS

        _APPROVALS["STK-EEEE"] = entry
        client = _FakeClient([_update(5, 111, "同意 STK-EEEE")])
        d = TelegramBotDaemon(client=client, on_message=MagicMock(),
                              poll_interval=0.1)
        d.poll_once()
        # consumed: approval resolved, LLM handler NOT called, no reply
        assert entry.outcome == "approved"
        assert entry.event.is_set()
        assert d.on_message.call_count == 0
        assert client.sent == []

    def test_no_daemon_heartbeat_raises(self, tg_home, monkeypatch):
        import src.agent.telegram_bot as tb

        monkeypatch.setattr(tb, "_last_poll_ts", None)
        with pytest.raises(RuntimeError) as ei:
            tb.request_order_approval(
                111, "s", timeout_s=1.0, client=_FakeClient(),
            )
        assert "QDRANT_TELEGRAM_BOT_POLLING" in str(ei.value)


class TestEngineConsentGate:
    def _live_engine(self, monkeypatch, **kw):
        from src.mega_stock_tools import MegaStockEngine

        monkeypatch.setenv("MEGA_STOCK_MODE", "live")
        monkeypatch.setenv("MEGA_STOCK_TRADING_ENABLED", "on")
        monkeypatch.setenv("MEGA_STOCK_ID", "A123456789")
        monkeypatch.setenv("MEGA_STOCK_PASSWORD", "x")
        monkeypatch.setenv("MEGA_STOCK_ACCOUNT", "1673976")
        monkeypatch.setenv("MEGA_STOCK_BRANCH", "7000")
        return MegaStockEngine()

    def test_consent_denied_blocks_order(self, tg_home, monkeypatch):
        import src.agent.telegram_bot as tb

        e = self._live_engine(monkeypatch)
        monkeypatch.setattr(tb, "_last_poll_ts", 9_999_999_999.0)
        monkeypatch.setattr(
            tb, "request_order_approval",
            lambda *a, **kw: "denied",
        )
        out = e.place_order("2330", "buy", 1000, price=1000.0,
                            confirm=True)
        assert "error" in out
        assert "已取消送單" in out["error"]
        assert out["consent"] == "denied"

    def test_consent_approved_proceeds_to_broker(self, tg_home,
                                                 monkeypatch):
        e = self._live_engine(monkeypatch)
        monkeypatch.setattr(
            "src.agent.telegram_bot._last_poll_ts", 9_999_999_999.0,
        )
        monkeypatch.setattr(
            "src.agent.telegram_bot.request_order_approval",
            lambda *a, **kw: "approved",
        )
        out = e.place_order("2330", "buy", 1000, price=1000.0,
                            confirm=True)
        # approval passed → the REAL broker path runs (connect fails
        # pre-authorization) — assert it got PAST the consent gate
        assert "已取消送單" not in out.get("error", "")
        assert out.get("consent") is None or "error" in out

    def test_consent_off_skips_gate(self, tg_home, monkeypatch):
        monkeypatch.setenv("MEGA_STOCK_ORDER_APPROVAL", "off")
        e = self._live_engine(monkeypatch)
        called = []
        import src.agent.telegram_bot as tb

        monkeypatch.setattr(
            tb, "request_order_approval",
            lambda *a, **kw: called.append(1) or "approved",
        )
        out = e.place_order("2330", "buy", 1000, price=1000.0,
                            confirm=True)
        assert not called  # gate skipped entirely

    def test_default_chat_id_fallback(self, tg_home, monkeypatch):
        # no MEGA_STOCK_ORDER_APPROVAL_CHAT_ID + empty allowlist →
        # the documented default <YOUR_TELEGRAM_CHAT_ID>
        import src.agent.telegram_bot as tb

        e = self._live_engine(monkeypatch)
        monkeypatch.setenv("MEGA_STOCK_ORDER_APPROVAL_TIMEOUT", "10")
        captured = {}

        def fake_req(chat_id, summary, timeout_s=180.0, client=None):
            captured["chat_id"] = chat_id
            return "denied"

        monkeypatch.setattr(tb, "_last_poll_ts", 9_999_999_999.0)
        monkeypatch.setattr(
            tb, "request_order_approval", fake_req,
        )
        out = e.place_order("2330", "buy", 1000, price=1000.0,
                            confirm=True)
        assert captured["chat_id"] == <YOUR_TELEGRAM_CHAT_ID>
        assert "consent" in out