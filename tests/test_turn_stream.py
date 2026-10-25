# -*- coding: utf-8 -*-
"""v0.9.123.0 — the Agent turn-stream hooks + Api.start_turn/poll_turn
two-phase contract (live execution log for the web/GUI chat)."""
import sys
import threading
import time
import types
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, ".")


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
    mgr.call.return_value = McpCallResult(text="tool-result", is_error=False)
    a._mcp_manager = mgr
    return a


def _resp(content="", finish="stop", tool_name=None):
    r = MagicMock()
    msg = MagicMock(content=content, finish_reason=finish)
    if tool_name:
        tc = MagicMock()
        tc.id = "call-1"
        fn = MagicMock()
        fn.name = tool_name  # NOT the MagicMock(name=…) kwarg — that
        fn.arguments = "{}"  # sets the mock's repr name, not .name!
        tc.function = fn
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


TOOLS = [{"type": "function", "function": {"name": "get_current_time",
          "parameters": {}}}]

from src.agent.agent import TURN_STOPPED_TEXT  # noqa: E402


class TestAgentHooks:
    def test_stream_emits_round_and_tool_events(self, agent):
        evs = []
        agent.begin_turn_stream("t1", lambda k, x: evs.append((k, x)))
        _wire(agent, [
            _resp("", finish="tool_calls", tool_name="get_current_time"),
            _resp("final"),
        ])
        out = agent._tool_loop(
            messages=[{"role": "user", "content": "q"}],
            tools=TOOLS, temperature=0.3, max_iterations=5,
        )
        assert out == "final"
        kinds = [k for k, _ in evs]
        assert kinds == ["llm_round", "tool_call", "tool_result",
                         "llm_round"]
        assert "get_current_time" in evs[1][1]
        assert evs[2][1].startswith("ok get_current_time")
        agent.end_turn_stream("t1")

    def test_no_stream_is_zero_cost(self, agent):
        _wire(agent, [_resp("final")])
        out = agent._tool_loop(
            messages=[{"role": "user", "content": "q"}],
            tools=TOOLS, temperature=0.3, max_iterations=5,
        )
        assert out == "final"  # loop ran fine without any stream

    def test_scoped_stop_event_ends_turn(self, agent):
        evs = []
        agent.begin_turn_stream("t2", lambda k, x: evs.append((k, x)))
        _wire(agent, [_resp("", finish="tool_calls",
                            tool_name="get_current_time")])
        assert agent.signal_turn_stop("t2") is True
        out = agent._tool_loop(
            messages=[{"role": "user", "content": "q"}],
            tools=TOOLS, temperature=0.3, max_iterations=5,
        )
        assert out == TURN_STOPPED_TEXT
        assert agent.last_tool_stopped is True
        assert evs[-1][0] == "stop"
        agent.end_turn_stream("t2")

    def test_signal_unknown_turn_is_false(self, agent):
        assert agent.signal_turn_stop("nope") is False

    def test_thread_isolation(self, agent):
        evs_a, evs_b = [], []
        agent.begin_turn_stream("mA", lambda k, x: evs_a.append((k, x)))

        def _other():
            agent.begin_turn_stream("mB", lambda k, x: evs_b.append((k, x)))
            agent._emit_turn_event("note", "from-b")
            agent.end_turn_stream("mB")

        th = threading.Thread(target=_other)
        th.start()
        th.join()
        agent._emit_turn_event("note", "from-a")
        agent.end_turn_stream("mA")
        assert evs_b == [("note", "from-b")]
        assert evs_a == [("note", "from-a")]

    def test_end_turn_stream_detaches(self, agent):
        evs = []
        agent.begin_turn_stream("t3", lambda k, x: evs.append((k, x)))
        agent.end_turn_stream("t3")
        assert agent.signal_turn_stop("t3") is False
        agent._emit_turn_event("note", "x")
        assert evs == []


class TestApiTwoPhase:
    @pytest.fixture
    def fake_agent(self):
        a = types.SimpleNamespace()
        a.last_usage = {"total_tokens": 5, "calls": 1}
        a.last_tool_dispatches = ["get_current_time"]
        a.last_tool_stopped = False
        a.begin_turn_stream = lambda tid, cb: (
            setattr(a, "cb", cb), threading.Event())[-1]
        a.end_turn_stream = lambda tid: setattr(a, "cb", None)
        a.signal_turn_stop = lambda tid: False

        def _chat(**kw):
            cb = getattr(a, "cb", None)
            if cb:
                cb("llm_round", "fake round 1")
                cb("tool_result", "ok fake · 5ms · 9 chars")
            return ("整合答覆", "ctx", ["f.md"])
        a.chat_turn = _chat
        return a

    @pytest.fixture
    def api(self, fake_agent):
        import gui.api as gam

        g = gam.Api.__new__(gam.Api)
        g._get_agent = lambda: fake_agent
        g._wire_openwith_diagrams_opener = lambda ag: None
        return g

    @pytest.fixture
    def te(self):
        from src.agent import turn_events as m

        m.reset_for_tests()
        yield m
        m.reset_for_tests()

    def _wait_terminal(self, te, turn_id, tries=200):
        for _ in range(tries):
            s = te.snapshot(turn_id)
            if s["status"] != "running":
                return s
            time.sleep(0.05)
        return te.snapshot(turn_id)

    def test_start_poll_roundtrip_done(self, api, te, fake_agent):
        r0 = api.start_turn("你好", history=[])
        tid = r0["turn_id"]
        assert tid
        final = self._wait_terminal(te, tid)
        assert final["status"] == "done"
        res = final["result"]
        assert res["answer"] == "整合答覆"
        assert res["sources"] == ["f.md"]
        assert res["usage"]["total_tokens"] == 5
        assert res["tools_used"] == ["get_current_time"]
        kinds = [e["kind"] for e in final["events"]]
        assert "llm_round" in kinds and "tool_result" in kinds
        poll = api.poll_turn(tid, final["next_cursor"])
        assert poll["events"] == []

    def test_error_path_is_honest(self, api, te, fake_agent):
        def _boom(**kw):
            raise RuntimeError("qdrant down")
        fake_agent.chat_turn = _boom
        r0 = api.start_turn("q")
        final = self._wait_terminal(te, r0["turn_id"])
        assert final["status"] == "error"
        assert "qdrant down" in final["result"]["error"]

    def test_poll_unknown_turn(self, api, te):
        s = api.poll_turn("nope")
        assert s["found"] is False and s["status"] == "unknown"

    def test_stop_turn_shapes(self, api, fake_agent):
        r = api.stop_turn("nope")
        assert r["ok"] is False and r.get("error")
        fake_agent.signal_turn_stop = lambda tid: tid == "s1"
        assert api.stop_turn("s1")["ok"] is True
        fake_agent.signal_turn_stop = lambda tid: False

    def test_sync_chat_turn_contract_untouched(self, api, fake_agent):
        out = api.chat_turn("q", history=[])
        assert out["answer"] == "整合答覆"
        assert out["tools_used"] == ["get_current_time"]


def test_web_bridge_envelope():
    from gui.web_server import call_api_method

    stub = types.SimpleNamespace(
        start_turn=lambda *a: {"turn_id": "abc123"},
        poll_turn=lambda *a: {"found": False, "status": "unknown",
                              "events": [], "result": None,
                              "next_cursor": 0},
        _secret=lambda *a: {"nope": True},
    )
    ok = call_api_method(stub, "start_turn", ["q"])
    assert ok["ok"] is True and ok["result"]["turn_id"] == "abc123"
    ok2 = call_api_method(stub, "poll_turn", ["abc123", 0])
    assert ok2["ok"] is True and ok2["result"]["found"] is False
    assert call_api_method(stub, "_secret", [])["ok"] is False
    assert call_api_method(stub, "unknown_method", [])["ok"] is False