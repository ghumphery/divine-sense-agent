"""v0.9.84.1 — Jev time route tests (plan §5.1 slice): off/shadow/on
modes + the deterministic get_current_time direct answer. All HTTP is
stubbed via monkeypatched jev_noul; the manager is a MagicMock."""
from unittest.mock import MagicMock

import pytest

import src.agent.agent as ag
from src.mcp_client.base import McpCallResult

_ENV_VARS = ("QDRANT_TIME_ROUTE", "QDRANT_TIME_ROUTE_PROB")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    import src.config  # noqa: F401 — .env loads BEFORE the wipe
    for name in _ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def _time_result(date="2026-09-21", t="18:30:00",
                 wd="Monday", off="+8:00"):
    r = MagicMock()
    r.is_error = False
    r.to_message_text.return_value = (
        '{"local": {"iso": "' + date + "T" + t + '+08:00", "date": "'
        + date + '", "time": "' + t + '", "weekday_en": "' + wd
        + '", "utc_offset": "' + off + '"}, "utc": {}}'
    )
    return r


class TestModeResolver:
    def test_off_by_default(self):
        assert ag._time_route_mode() == "off"

    @pytest.mark.parametrize("v,m", [("shadow", "shadow"), ("ON", "on")])
    def test_modes(self, monkeypatch, v, m):
        monkeypatch.setenv("QDRANT_TIME_ROUTE", v)
        assert ag._time_route_mode() == m

    def test_garbage_falls_back(self, monkeypatch):
        monkeypatch.setenv("QDRANT_TIME_ROUTE", "wat")
        assert ag._time_route_mode() == "off"

    def test_prob_resolver(self, monkeypatch):
        assert ag._time_route_prob() == 0.85
        monkeypatch.setenv("QDRANT_TIME_ROUTE_PROB", "0.9")
        assert ag._time_route_prob() == 0.9
        monkeypatch.setenv("QDRANT_TIME_ROUTE_PROB", "abc")
        assert ag._time_route_prob() == 0.85
        monkeypatch.setenv("QDRANT_TIME_ROUTE_PROB", "7")
        assert ag._time_route_prob() == 0.85
        monkeypatch.setenv("QDRANT_TIME_ROUTE_PROB", "1")
        assert ag._time_route_prob() == 1.0


class TestRoute:
    def _mgr(self):
        mgr = MagicMock()
        mgr.call.return_value = _time_result()
        return mgr

    def test_off_never_calls_jev(self, monkeypatch):
        mgr = self._mgr()
        monkeypatch.setattr(
            "src.agent.rerank.jev_noul",
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("called")))
        direct, p = ag._jev_time_route(mgr, "今天是幾月幾日")
        assert direct is None and p is None
        assert mgr.call.called is False

    def test_shadow_logs_only(self, monkeypatch, caplog):
        import logging

        monkeypatch.setenv("QDRANT_TIME_ROUTE", "shadow")
        monkeypatch.setattr("src.agent.rerank.jev_noul",
                            lambda *a, **k: (0.97, {"elapsed_s": 0.2}))
        mgr = self._mgr()
        with caplog.at_level(logging.INFO):
            direct, p = ag._jev_time_route(mgr, "今天是幾月幾日")
        assert direct is None and p == 0.97
        assert mgr.call.called is False  # shadow never acts
        assert "TimeRoute" in caplog.text

    def test_on_direct_answer(self, monkeypatch):
        monkeypatch.setenv("QDRANT_TIME_ROUTE", "on")
        monkeypatch.setattr("src.agent.rerank.jev_noul",
                            lambda *a, **k: (0.97, {"elapsed_s": 0.2}))
        mgr = self._mgr()
        direct, p = ag._jev_time_route(mgr, "今天是幾月幾日")
        assert direct and "2026-09-21" in direct
        assert "星期一" in direct and "18:30:00" in direct
        assert p == 0.97
        assert mgr.call.call_args[0][0] == "get_current_time"

    def test_on_low_confidence_runs_llm(self, monkeypatch):
        monkeypatch.setenv("QDRANT_TIME_ROUTE", "on")
        monkeypatch.setattr("src.agent.rerank.jev_noul",
                            lambda *a, **k: (0.4, {"elapsed_s": 0.2}))
        mgr = self._mgr()
        direct, p = ag._jev_time_route(mgr, "今天是幾月幾日")
        assert direct is None and p == 0.4
        assert mgr.call.called is False

    def test_jev_failure_falls_back(self, monkeypatch):
        monkeypatch.setenv("QDRANT_TIME_ROUTE", "on")
        monkeypatch.setattr("src.agent.rerank.jev_noul",
                            lambda *a, **k: (None, {"error": "HTTP 402"}))
        mgr = self._mgr()
        direct, p = ag._jev_time_route(mgr, "q")
        assert direct is None and p is None
        assert mgr.call.called is False

    def test_tool_error_fails_closed(self, monkeypatch):
        monkeypatch.setenv("QDRANT_TIME_ROUTE", "on")
        monkeypatch.setattr("src.agent.rerank.jev_noul",
                            lambda *a, **k: (0.99, {"elapsed_s": 0.2}))
        mgr = MagicMock()
        r = MagicMock()
        r.is_error = True
        mgr.call.return_value = r
        direct, p = ag._jev_time_route(mgr, "今天是幾月幾日")
        assert direct is None and p == 0.99

    def test_agent_integrates_route(self):
        """source guard: _chat_turn_impl consults the route BEFORE the
        LLM tool loop and returns the direct answer."""
        t = open("src/agent/agent.py", encoding="utf-8").read()
        call_idx = t.find("_jev_time_route(mgr, question)")
        assert call_idx > 0
        ret_idx = t.find("return (direct, \"\", [])", call_idx)
        assert ret_idx > call_idx
        # ...and the LLM loop only starts after the route check
        loop_idx = t.find("msgs = self._messages_with_user(", call_idx)
        assert loop_idx > ret_idx