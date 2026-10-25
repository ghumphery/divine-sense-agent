"""Tests for v0.9.12.0+ per-pair token usage plumbing.

Chain under test:

* ``Agent._extract_usage`` — pull prompt/completion/total/reasoning
  off a ``chat.completions`` response (None when usage omitted).
* ``Agent._accumulate_usage`` — sum across tool-loop iterations.
* ``Agent._tool_loop`` / legacy ``ask`` / ``chat_turn`` — set
  ``Agent.last_usage`` (aggregated for multi-call turns; reset to
  None at every turn entry so stale numbers never linger).
* ``gui/api.py::chat_turn`` — forward ``usage`` into the JS-bridge
  payload (None for MagicMock agents / legacy providers).
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _usage(prompt=100, completion=20, total=120, reasoning=0, cached=0):
    """OpenAI-shaped usage object incl. the reasoning details nest."""
    return SimpleNamespace(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=total,
        completion_tokens_details=SimpleNamespace(
            reasoning_tokens=reasoning,
        ),
        prompt_tokens_details=SimpleNamespace(
            cached_tokens=cached,
        ),
    )


def _resp(content="ok", usage=None, tool_calls=None, finish="stop"):
    msg = SimpleNamespace(content=content, tool_calls=tool_calls)
    choice = SimpleNamespace(message=msg, finish_reason=finish)
    return SimpleNamespace(choices=[choice], usage=usage)


def _agent_with_stubs():
    """Agent with a mocked OpenAI client and no MCP tools on the
    legacy path — no network anywhere."""
    from src.agent.agent import Agent

    a = Agent()
    a._client = MagicMock()
    a._model = "mock-model"
    a._provider = "mock"
    mgr = MagicMock()
    mgr.has_tools.return_value = False
    a._mcp_manager = mgr
    a._build_context = (
        lambda q, top_k, use_web_search, web_max_results: "CTX"
    )
    return a


def _tool_call(name="t", args_json="{}"):
    call = MagicMock()
    call.id = "call_1"
    call.function.name = name
    call.function.arguments = args_json
    return call


# ─── extract / accumulate ────────────────────────────────────


class TestExtractAccumulate:
    def test_extract_none_when_usage_omitted(self):
        from src.agent.agent import Agent

        a = Agent()
        assert a._extract_usage(_resp()) is None

    def test_extract_fields_including_reasoning(self):
        from src.agent.agent import Agent

        a = Agent()
        u = a._extract_usage(_resp(usage=_usage(100, 20, 120, 5, 88)))
        assert u == {
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "total_tokens": 120,
            "reasoning_tokens": 5,
            "cached_tokens": 88,
            "calls": 1,
        }

    def test_extract_tolerates_missing_details(self):
        from src.agent.agent import Agent

        a = Agent()
        bare = SimpleNamespace(
            prompt_tokens=1, completion_tokens=2, total_tokens=3,
        )
        u = a._extract_usage(_resp(usage=bare))
        assert u["reasoning_tokens"] == 0
        assert u["calls"] == 1

    def test_accumulate_sums_across_calls(self):
        from src.agent.agent import Agent

        a = Agent()
        turn = a._accumulate_usage(None, _resp(usage=_usage(100, 20, 120, 0, 100)))
        turn = a._accumulate_usage(
            turn, _resp(usage=_usage(50, 10, 60, 3, 40))
        )
        assert turn["prompt_tokens"] == 150
        assert turn["completion_tokens"] == 30
        assert turn["total_tokens"] == 180
        assert turn["reasoning_tokens"] == 3
        assert turn["calls"] == 2
        assert turn["cached_tokens"] == 140

    def test_accumulate_keeps_turn_when_usage_missing(self):
        from src.agent.agent import Agent

        a = Agent()
        turn = a._accumulate_usage(None, _resp(usage=_usage()))
        assert a._accumulate_usage(turn, _resp()) is turn


# ─── tool loop aggregation ───────────────────────────────────


class TestToolLoopUsage:
    def _mgr_with_tools(self):
        from src.mcp_client.base import McpCallResult

        mgr = MagicMock()
        mgr.has_tools.return_value = True
        mgr.openai_tools.return_value = []
        mgr.call.return_value = McpCallResult(text="{}", is_error=False)
        return mgr

    def test_tool_loop_aggregates_all_iterations(self):
        from src.agent.agent import Agent

        a = _agent_with_stubs()
        a._mcp_manager = self._mgr_with_tools()
        a._client.chat.completions.create.side_effect = [
            _resp(
                content="",
                tool_calls=[_tool_call()],
                finish="tool_calls",
                usage=_usage(1000, 50, 1050),
            ),
            _resp(content="done", usage=_usage(1200, 30, 1230, 7)),
        ]
        out = a._tool_loop(
            messages=[{"role": "user", "content": "q"}],
            tools=[{"type": "function", "function": {"name": "t"}}],
            temperature=0.3,
            max_iterations=3,
        )
        assert out == "done"
        assert a.last_usage == {
            "prompt_tokens": 2200,
            "completion_tokens": 80,
            "total_tokens": 2280,
            "reasoning_tokens": 7,
            "cached_tokens": 0,
            "calls": 2,
        }

    def test_tool_loop_sets_none_when_provider_omits_usage(self):
        a = _agent_with_stubs()
        a._mcp_manager = self._mgr_with_tools()
        a._client.chat.completions.create.side_effect = [
            _resp(content="", tool_calls=[_tool_call()],
                  finish="tool_calls"),
            _resp(content="done"),
        ]
        a._tool_loop(
            messages=[{"role": "user", "content": "q"}],
            tools=[], temperature=0.3, max_iterations=3,
        )
        assert a.last_usage is None


# ─── ask / chat_turn lifecycle ───────────────────────────────


class TestAskChatTurnLifecycle:
    def test_ask_legacy_sets_usage(self):
        a = _agent_with_stubs()
        a._client.chat.completions.create.return_value = _resp(
            usage=_usage(10, 5, 15)
        )
        a.ask("q", use_tools=False)
        assert a.last_usage["total_tokens"] == 15

    def test_chat_turn_legacy_sets_usage(self):
        a = _agent_with_stubs()
        a._client.chat.completions.create.return_value = _resp(
            usage=_usage(10, 5, 15)
        )
        a.chat_turn("q", history=[], use_tools=False)
        assert a.last_usage["total_tokens"] == 15

    def test_no_usage_resp_resets_stale_numbers(self):
        a = _agent_with_stubs()
        a.last_usage = {"prompt_tokens": 999}
        a._client.chat.completions.create.return_value = _resp()
        a.chat_turn("q", history=[], use_tools=False)
        assert a.last_usage is None


# ─── gui bridge payload ──────────────────────────────────────


class TestGuiWrapper:
    def test_chat_turn_payload_includes_usage(self):
        from gui.api import Api

        api = Api.__new__(Api)
        agent = MagicMock()
        agent.chat_turn.return_value = ("ans", "ctx", [])
        agent.last_usage = {
            "prompt_tokens": 10, "completion_tokens": 5,
            "total_tokens": 15, "reasoning_tokens": 0, "calls": 1,
        }
        api._get_agent = lambda: agent
        result = api.chat_turn("q", history=[], web_search=False)
        assert result["usage"]["total_tokens"] == 15

    def test_usage_none_for_non_dict(self):
        """MagicMock agents (existing tests) must not leak a
        non-serialisable object into the JS bridge."""
        from gui.api import Api

        api = Api.__new__(Api)
        agent = MagicMock()
        agent.chat_turn.return_value = ("ans", "ctx", [])
        api._get_agent = lambda: agent
        result = api.chat_turn("q", history=[], web_search=False)
        assert result["usage"] is None
