# -*- coding: utf-8 -*-
"""v0.9.107.1 — telegram turn-level HARD timeout tests."""
import time

import pytest


@pytest.fixture()
def tb():
    import src.agent.telegram_bot as m
    return m


class TestResolver:
    def test_unset_default_1800(self, monkeypatch, tb):
        monkeypatch.delenv("QDRANT_TURN_HARD_TIMEOUT_S", raising=False)
        assert tb._turn_hard_timeout_s() == 1800

    def test_invalid_falls_back_to_default(self, monkeypatch, tb):
        monkeypatch.setenv("QDRANT_TURN_HARD_TIMEOUT_S", "abc")
        assert tb._turn_hard_timeout_s() == 1800

    @pytest.mark.parametrize("bad", ["-5"])
    def test_nonpositive_clamped_off(self, monkeypatch, tb, bad):
        # a negative watchdog must never exist.
        monkeypatch.setenv("QDRANT_TURN_HARD_TIMEOUT_S", bad)
        assert tb._turn_hard_timeout_s() == 0

    def test_zero_off(self, monkeypatch, tb):
        monkeypatch.setenv("QDRANT_TURN_HARD_TIMEOUT_S", "0")
        assert tb._turn_hard_timeout_s() == 0

    def test_positive_honoured(self, monkeypatch, tb):
        monkeypatch.setenv("QDRANT_TURN_HARD_TIMEOUT_S", "45")
        assert tb._turn_hard_timeout_s() == 45


class _Agent:
    def __init__(self, delay=0.0, result="ok", exc=None):
        self.delay = delay
        self.result = result
        self.exc = exc
        self.calls = 0

    def chat_turn(self, **kw):
        self.calls += 1
        time.sleep(self.delay)
        if self.exc is not None:
            raise self.exc
        return self.result


class TestGuard:
    def test_fast_call_passthrough(self, monkeypatch, tb):
        monkeypatch.setenv("QDRANT_TURN_HARD_TIMEOUT_S", "10")
        a = _Agent(0.05, result="hello")
        r, timed_out = tb._chat_turn_guarded(a, 10, question="q")
        assert r == "hello" and timed_out is False

    def test_timeout_zero_is_plain_call(self, monkeypatch, tb):
        monkeypatch.setenv("QDRANT_TURN_HARD_TIMEOUT_S", "0")
        a = _Agent(0.05, result="legacy")
        r, timed_out = tb._chat_turn_guarded(a, 0, question="q")
        assert r == "legacy" and timed_out is False

    def test_hung_turn_abandoned(self, monkeypatch, tb):
        monkeypatch.setenv("QDRANT_TURN_HARD_TIMEOUT_S", "1")
        a = _Agent(delay=8.0, result="never")
        t0 = time.monotonic()
        r, timed_out = tb._chat_turn_guarded(a, 1, question="q")
        took = time.monotonic() - t0
        assert r is None and timed_out is True
        assert took < 5.0

    def test_exception_propagates(self, monkeypatch, tb):
        monkeypatch.setenv("QDRANT_TURN_HARD_TIMEOUT_S", "10")
        a = _Agent(0.01, exc=ValueError("boom"))
        with pytest.raises(ValueError):
            tb._chat_turn_guarded(a, 10, question="q")

    def test_reply_template_mentions_seconds(self, tb):
        s = tb._TURN_TIMEOUT_REPLY.format(secs=1800)
        assert "1800" in s and "超時" in s


class TestHandleWiring:
    def test_handle_uses_guard_and_timeout_row(self, monkeypatch, tmp_path,
                                               tb):
        monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path))
        monkeypatch.setenv("QDRANT_TURN_HARD_TIMEOUT_S", "1")
        import src.agent.telegram_bot as m

        class HangAgent:
            def chat_turn(self, **kw):
                time.sleep(6.0)

        factory = lambda: HangAgent()  # noqa: E731
        handle = m.make_telegram_on_message(factory)
        reply = handle(<YOUR_TELEGRAM_CHAT_ID>, "hi")
        assert "超時" in reply and "1" in reply

    def test_handle_normal_path_still_works(self, monkeypatch, tmp_path,
                                            tb):
        monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path))
        monkeypatch.setenv("QDRANT_TURN_HARD_TIMEOUT_S", "30")
        import src.agent.telegram_bot as m

        class OkAgent:
            def chat_turn(self, **kw):
                return ("answer-text", "", [])

        handle = m.make_telegram_on_message(lambda: OkAgent())
        reply = handle(<YOUR_TELEGRAM_CHAT_ID>, "hi")
        assert reply == "answer-text"


class _WorkAgent:
    """Silent or dispatching chat_turn stub (v0.9.118 liveness
    evidence source)."""

    def __init__(self, delay=0.0, dispatch_every=0.0):
        self.delay = delay
        self.dispatch_every = dispatch_every
        self.last_tool_dispatches = []
        self.last_usage = {"calls": 0}

    def chat_turn(self, **kw):
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


class TestExtension:
    """v0.9.118.0 — the Telegram turn guard shares the arbiter."""

    def test_resolver_default_3(self, monkeypatch, tb):
        monkeypatch.delenv("QDRANT_TURN_HARD_TIMEOUT_EXTENSIONS",
                           raising=False)
        assert tb._turn_timeout_extensions() == 3

    def test_resolver_honoured(self, monkeypatch, tb):
        monkeypatch.setenv("QDRANT_TURN_HARD_TIMEOUT_EXTENSIONS", "5")
        assert tb._turn_timeout_extensions() == 5

    def test_wrapper_gate_off(self, monkeypatch, tb):
        monkeypatch.setenv("QDRANT_SKILL_TIMEOUT_JEV", "off")
        monkeypatch.delenv("QDRANT_TIMEOUT_JEV", raising=False)
        assert tb._turn_jev_verdict("e") is None

    def test_liveness_extends_and_completes(self, monkeypatch, tb):
        monkeypatch.setenv("QDRANT_TURN_HARD_TIMEOUT_S", "2")
        monkeypatch.delenv("QDRANT_SKILL_TIMEOUT_JEV", raising=False)
        monkeypatch.delenv("QDRANT_TIMEOUT_JEV", raising=False)
        a = _WorkAgent(delay=6.5, dispatch_every=0.3)
        t0 = time.monotonic()
        r, timed_out = tb._chat_turn_guarded(a, 2, question="巨型工作")
        assert timed_out is False and r[0] == "ok"
        assert time.monotonic() - t0 >= 6

    def test_jev_verdict_extends_silent(self, monkeypatch, tb):
        monkeypatch.setenv("QDRANT_TURN_HARD_TIMEOUT_S", "2")
        calls = {"n": 0}
        monkeypatch.setattr(
            tb, "_turn_jev_verdict",
            lambda t: (calls.__setitem__("n", calls["n"] + 1)
                       or "extend"))
        a = _WorkAgent(delay=6.5)
        r, timed_out = tb._chat_turn_guarded(a, 2, question="q")
        assert timed_out is False
        assert calls["n"] >= 1

    def test_abnormal_kills_unchanged(self, monkeypatch, tb):
        monkeypatch.setenv("QDRANT_TURN_HARD_TIMEOUT_S", "1")
        monkeypatch.setattr(tb, "_turn_jev_verdict", lambda t: None)
        a = _Agent(delay=3.0)
        t0 = time.monotonic()
        r, timed_out = tb._chat_turn_guarded(a, 1, question="q")
        assert r is None and timed_out is True
        assert time.monotonic() - t0 < 3

    def test_handle_long_work_survives_via_liveness(self, monkeypatch,
                                                    tmp_path, tb):
        """The 22:48 incident shape — a Telegram BIG-job request now
        survives past ONE window via the arbiter instead of dying at
        the turn hard timeout."""
        monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path))
        monkeypatch.setenv("QDRANT_TURN_HARD_TIMEOUT_S", "2")
        monkeypatch.setenv("QDRANT_TURN_HARD_TIMEOUT_EXTENSIONS", "3")
        import src.agent.telegram_bot as m

        class BigJobAgent(_WorkAgent):
            def __init__(self):
                super().__init__(delay=7.0, dispatch_every=0.3)

        handle = m.make_telegram_on_message(lambda: BigJobAgent())
        reply = handle(<YOUR_TELEGRAM_CHAT_ID>, "把這 16 章全部下載")
        assert reply == "ok" or "超時" not in reply
