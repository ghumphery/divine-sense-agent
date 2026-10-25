"""v0.9.23.0 — _tool_loop empty-final-answer wrap-up.

A reasoning model can emit content="" with finish_reason="stop"
right after a tool round — surfacing as answer=0 chars (the
14:55 schedule fire did exactly that: get_current_time +
web_search ×2 + list_mcp_tools, then an empty summary). When at
least one tool round ran, _tool_loop now does ONE no-tools
wrap-up create asking the model to output the final answer from
the tool results already in the message list.
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
        text="tool-result", is_error=False,
    )
    a._mcp_manager = mgr
    return a


def _resp(content="", finish="stop", tool_name=None):
    r = MagicMock()
    msg = MagicMock(content=content, finish_reason=finish)
    if tool_name:
        tc = MagicMock()
        tc.id = "call-1"
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


TOOLS = [{"type": "function", "function": {"name": "get_current_time",
          "parameters": {}}}]


def test_wrap_up_after_empty_final_answer(agent):
    """Round 1: tool call; round 2: EMPTY content + stop → the
    no-tools wrap-up create must run and its summary is returned."""
    fake = _wire(agent, [
        _resp("", finish="tool_calls", tool_name="get_current_time"),
        _resp(""),            # empty final answer (the bug shape)
        _resp("總結內容"),      # wrap-up
    ])
    out = agent._tool_loop(
        messages=[{"role": "user", "content": "q"}],
        tools=TOOLS, temperature=0.3, max_iterations=5,
    )
    assert out == "總結內容"
    calls = fake.chat.completions.create.call_args_list
    # the wrap-up call must NOT carry the tools kwarg
    assert "tools" not in calls[2].kwargs
    assert any(
        "最終答案" in str(c.kwargs.get("messages", []))
        for c in calls[2:]
    )


def test_non_empty_final_answer_skips_wrap_up(agent):
    """Normal path: a non-empty final answer returns directly —
    exactly two creates, no wrap-up."""
    fake = _wire(agent, [
        _resp("", finish="tool_calls", tool_name="get_current_time"),
        _resp("完整總結"),
    ])
    out = agent._tool_loop(
        messages=[{"role": "user", "content": "q"}],
        tools=TOOLS, temperature=0.3, max_iterations=5,
    )
    assert out == "完整總結"
    assert fake.chat.completions.create.call_count == 2


def test_empty_first_round_skips_wrap_up(agent):
    """An empty answer on round 1 (no tool rounds ran) returns as-is
    — no wrap-up create is spent."""
    fake = _wire(agent, [_resp("")])
    out = agent._tool_loop(
        messages=[{"role": "user", "content": "q"}],
        tools=TOOLS, temperature=0.3, max_iterations=5,
    )
    assert out == ""
    assert fake.chat.completions.create.call_count == 1


def test_wrap_up_failure_returns_empty_gracefully(agent):
    """If the wrap-up create raises, the loop returns the empty
    content instead of crashing."""
    fake = _wire(agent, [
        _resp("", finish="tool_calls", tool_name="get_current_time"),
        _resp(""),
    ])
    fake.chat.completions.create.side_effect = [
        _resp("", finish="tool_calls", tool_name="get_current_time"),
        _resp(""),
        RuntimeError("wrap-up down"),
    ]
    out = agent._tool_loop(
        messages=[{"role": "user", "content": "q"}],
        tools=TOOLS, temperature=0.3, max_iterations=5,
    )
    assert out == ""