"""v0.9.84.2 — message router v2 tests: jev_choice client + the
greeting/status/weather/time handlers + shadow semantics. All HTTP is
stubbed via monkeypatched jev_choice; the manager is a MagicMock."""
from unittest.mock import MagicMock

import pytest

import src.agent.agent as ag

_ENV_VARS = ("QDRANT_ROUTE", "QDRANT_ROUTE_PROB", "QDRANT_ROUTE_CLASSES")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    import src.config  # noqa: F401 — .env loads BEFORE the wipe
    for name in _ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def _time_tool(date="2026-09-21", wd="Monday"):
    r = MagicMock()
    r.is_error = False
    r.to_message_text.return_value = (
        '{"local": {"date": "' + date + '", "time": "18:30:00", '
        '"weekday_en": "' + wd + '", "utc_offset": "+8:00"}}')
    return r


def _ok_tool(payload):
    r = MagicMock()
    r.is_error = False
    r.to_message_text.return_value = payload
    return r


class TestClient:
    def test_choice_parsed_with_probability(self, monkeypatch):
        import src.agent.rerank as rr

        monkeypatch.setenv("OPENROUTER_API_KEY", "k")
        body_seen = {}

        class _Ctx:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return ('{"answers": {"decision": {"type": "choice", '
                        '"choice": "time", '
                        '"probabilities": {"time": 0.93, "chat": 0.07}}}, '
                        '"usage": {"input_tokens": 500}}').encode()

        def fake(req, timeout=None):
            body_seen["body"] = json.loads(req.data.decode())
            return _Ctx()

        monkeypatch.setattr(rr.urllib.request, "urlopen", fake)
        choice, prob, meta = rr.jev_choice(
            "哪一類？", "今天是幾月幾日", ["time", "greeting", "chat"])
        assert choice == "time" and prob == 0.93
        q = body_seen["body"]["questions"]["decision"]
        assert q["type"] == "choice"
        assert q["criteria"] == {"time": None, "greeting": None,
                                 "chat": None}
        assert meta["usage"]["input_tokens"] == 500

    def test_choice_failure_returns_none(self, monkeypatch):
        import src.agent.rerank as rr

        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        choice, prob, meta = rr.jev_choice("q", "s", ["time", "chat"])
        assert choice is None and prob is None
        assert "OPENROUTER_API_KEY missing" in meta["error"]


import json  # noqa: E402  (used by the client test above)


class TestResolvers:
    def test_route_mode(self, monkeypatch):
        assert ag._route_mode() == "off"
        monkeypatch.setenv("QDRANT_ROUTE", "shadow")
        assert ag._route_mode() == "shadow"
        monkeypatch.setenv("QDRANT_ROUTE", "on")
        assert ag._route_mode() == "on"
        monkeypatch.setenv("QDRANT_ROUTE", "wat")
        assert ag._route_mode() == "off"

    def test_route_prob(self, monkeypatch):
        assert ag._route_prob() == 0.85
        monkeypatch.setenv("QDRANT_ROUTE_PROB", "0.95")
        assert ag._route_prob() == 0.95
        monkeypatch.setenv("QDRANT_ROUTE_PROB", "x")
        assert ag._route_prob() == 0.85

    def test_route_classes_default_and_filter(self, monkeypatch):
        assert ag._route_classes() == ["time", "greeting", "status",
                                       "weather"]
        monkeypatch.setenv("QDRANT_ROUTE_CLASSES", "time, greeting ,stock")
        assert ag._route_classes() == ["time", "greeting"]
        monkeypatch.setenv("QDRANT_ROUTE_CLASSES", "time")
        assert ag._route_classes() == ["time"]


class TestRoute:
    def _mgr(self):
        return MagicMock()

    def test_off_never_calls_jev(self, monkeypatch):
        mgr = self._mgr()
        monkeypatch.setattr(
            "src.agent.rerank.jev_choice",
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("called")))
        action, payload, p = ag._jev_class_route(mgr, "今天是幾月幾日")
        assert action is None and payload is None and p is None

    def test_shadow_logs_choice_only(self, monkeypatch, caplog):
        import logging

        monkeypatch.setenv("QDRANT_ROUTE", "shadow")
        monkeypatch.setattr("src.agent.rerank.jev_choice",
                            lambda *a, **k: ("greeting", 0.9,
                                             {"elapsed_s": 0.2}))
        mgr = self._mgr()
        with caplog.at_level(logging.INFO):
            action, cls, p = ag._jev_class_route(mgr, "你好", "zh-TW")
        assert action is None and cls == "greeting" and p == 0.9
        assert mgr.call.called is False  # handlers never act in shadow
        assert "Route" in caplog.text

    def test_greeting_direct_no_tool(self, monkeypatch):
        monkeypatch.setenv("QDRANT_ROUTE", "on")
        monkeypatch.setattr("src.agent.rerank.jev_choice",
                            lambda *a, **k: ("greeting", 0.95,
                                             {"elapsed_s": 0.2}))
        mgr = self._mgr()
        action, payload, p = ag._jev_class_route(mgr, "你好", "zh-TW")
        assert action == "direct"
        assert "你好" in payload and "幫忙" in payload
        assert mgr.call.called is False  # no tool needed at all

    def test_greeting_locale_template(self, monkeypatch):
        monkeypatch.setenv("QDRANT_ROUTE", "on")
        monkeypatch.setattr("src.agent.rerank.jev_choice",
                            lambda *a, **k: ("greeting", 0.95,
                                             {"elapsed_s": 0.2}))
        mgr = self._mgr()
        _, payload, _ = ag._jev_class_route(mgr, "hello", "en")
        assert "Hello" in payload
        _, payload, _ = ag._jev_class_route(mgr, "こんにちは", "ja")
        assert "こんにちは" in payload

    def test_time_direct_reuses_tool(self, monkeypatch):
        monkeypatch.setenv("QDRANT_ROUTE", "on")
        monkeypatch.setattr("src.agent.rerank.jev_choice",
                            lambda *a, **k: ("time", 0.95,
                                             {"elapsed_s": 0.2}))
        mgr = self._mgr()
        mgr.call.return_value = _time_tool()
        action, payload, p = ag._jev_class_route(mgr, "今天是幾月幾日", None)
        assert action == "direct" and "2026-09-21" in payload
        assert mgr.call.call_args[0][0] == "get_current_time"

    def test_status_formatted(self, monkeypatch):
        monkeypatch.setenv("QDRANT_ROUTE", "on")
        monkeypatch.setattr("src.agent.rerank.jev_choice",
                            lambda *a, **k: ("status", 0.9,
                                             {"elapsed_s": 0.2}))
        mgr = self._mgr()
        mgr.call.return_value = _ok_tool(
            '{"mode": "live", "auto": false, "paused": false, '
            '"gate": {"skipped": 1}, "watch": {"running": true}}')
        action, payload, _ = ag._jev_class_route(mgr, "teams 狀態", None)
        assert action == "direct"
        assert "執行中" in payload and "自動送出=off" in payload
        assert "省下 1 次誤報" in payload

    def test_weather_prefetch_marker(self, monkeypatch):
        monkeypatch.setenv("QDRANT_ROUTE", "on")
        monkeypatch.setattr("src.agent.rerank.jev_choice",
                            lambda *a, **k: ("weather", 0.9,
                                             {"elapsed_s": 0.2}))
        mgr = self._mgr()
        mgr.call.return_value = _ok_tool(
            '{"query": "…", "results": ["台北"]}')
        action, payload, _ = ag._jev_class_route(mgr, "今天天氣如何", None)
        assert action == "prefetch"
        assert "中央氣象署" in mgr.call.call_args[0][1]["query"]

    def test_low_confidence_runs_llm(self, monkeypatch):
        monkeypatch.setenv("QDRANT_ROUTE", "on")
        monkeypatch.setattr("src.agent.rerank.jev_choice",
                            lambda *a, **k: ("time", 0.4,
                                             {"elapsed_s": 0.2}))
        mgr = self._mgr()
        action, payload, p = ag._jev_class_route(mgr, "q", None)
        assert action is None and p == 0.4

    def test_class_not_enabled_runs_llm(self, monkeypatch):
        monkeypatch.setenv("QDRANT_ROUTE", "on")
        monkeypatch.setenv("QDRANT_ROUTE_CLASSES", "time")
        monkeypatch.setattr("src.agent.rerank.jev_choice",
                            lambda *a, **k: ("greeting", 0.99,
                                             {"elapsed_s": 0.2}))
        mgr = self._mgr()
        action, payload, _ = ag._jev_class_route(mgr, "你好", None)
        assert action is None  # greeting disabled → normal LLM path

    def test_chat_class_runs_llm(self, monkeypatch):
        monkeypatch.setenv("QDRANT_ROUTE", "on")
        monkeypatch.setattr("src.agent.rerank.jev_choice",
                            lambda *a, **k: ("chat", 0.95,
                                             {"elapsed_s": 0.2}))
        mgr = self._mgr()
        action, payload, _ = ag._jev_class_route(mgr, "幫我畫圖", None)
        assert action is None

    def test_jev_failure_falls_back(self, monkeypatch):
        monkeypatch.setenv("QDRANT_ROUTE", "on")
        monkeypatch.setattr("src.agent.rerank.jev_choice",
                            lambda *a, **k: (None, None,
                                             {"error": "HTTP 402"}))
        mgr = self._mgr()
        action, payload, p = ag._jev_class_route(mgr, "q", None)
        assert action is None and p is None

    def test_handler_tool_failure_falls_back(self, monkeypatch):
        monkeypatch.setenv("QDRANT_ROUTE", "on")
        monkeypatch.setattr("src.agent.rerank.jev_choice",
                            lambda *a, **k: ("status", 0.9,
                                             {"elapsed_s": 0.2}))
        mgr = MagicMock()
        bad = MagicMock()
        bad.is_error = True
        mgr.call.return_value = bad
        action, payload, _ = ag._jev_class_route(mgr, "teams 狀態", None)
        assert action is None


class TestIntegration:
    def test_wiring_v2_before_v1_and_tool_loop(self):
        t = open("src/agent/agent.py", encoding="utf-8").read()
        v2_idx = t.find("_jev_class_route(\n                    mgr")
        if v2_idx < 0:
            v2_idx = t.find("_jev_class_route(")
        assert v2_idx > 0
        v1_idx = t.find("_jev_time_route(mgr, question)", v2_idx)
        assert v1_idx > v2_idx
        loop_idx = t.find("msgs = self._messages_with_user(", v1_idx)
        assert loop_idx > v1_idx