"""v0.9.14.1 — MAX_TOOL_ITERATIONS budget + windows-mcp prompt block.

Regression for the 「開啟小畫家畫小狗 → 6 次就斷」 report: the tool
loop's 6-iteration default aborted every desktop-automation task
mid-drawing, and the forced bail-out message made the LLM answer
「由於我無法再執行後續的滑鼠繪製動作」+ a manual how-to. Locks down:

* ``_resolve_max_tool_iterations()`` — unset / valid / whitespace /
  invalid / non-positive.
* ``DEFAULT_MAX_TOOL_ITERATIONS`` bumped 6 → 20.
* ``ask()`` / ``chat_turn()`` default None → env-resolved per call;
  an explicit int still wins.
* ``_tool_loop`` bail-out message asks for progress + 「繼續」.
* ``Agent._system_prompt(tools_enabled=True)`` teaches the windows-mcp
  launch ladder, the scripted System.Drawing route, and budget
  discipline; ``tools_enabled=False`` stays free of it.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def stub_agent(tmp_path, monkeypatch):
    """Build an Agent with everything stubbed so we can inspect
    the prompt without touching the network / Qdrant."""
    monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path))
    from src.agent.agent import Agent
    a = Agent()
    a._provider = "stub"
    a._model = "stub-model"
    a._mcp_manager = None
    return a


# ─── resolver + default ─────────────────────────────────────


class TestResolveMaxToolIterations:
    def test_default_bumped_to_20(self):
        from src.agent.agent import Agent
        assert Agent.DEFAULT_MAX_TOOL_ITERATIONS == 20

    def test_unset_returns_default(self, monkeypatch):
        monkeypatch.delenv("MAX_TOOL_ITERATIONS", raising=False)
        from src.agent.agent import Agent, _resolve_max_tool_iterations
        assert _resolve_max_tool_iterations() == Agent.DEFAULT_MAX_TOOL_ITERATIONS

    def test_valid_env_value_honored(self, monkeypatch):
        monkeypatch.setenv("MAX_TOOL_ITERATIONS", "31")
        from src.agent.agent import _resolve_max_tool_iterations
        assert _resolve_max_tool_iterations() == 31

    def test_whitespace_stripped(self, monkeypatch):
        monkeypatch.setenv("MAX_TOOL_ITERATIONS", " 7 ")
        from src.agent.agent import _resolve_max_tool_iterations
        assert _resolve_max_tool_iterations() == 7

    def test_invalid_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("MAX_TOOL_ITERATIONS", "abc")
        from src.agent.agent import Agent, _resolve_max_tool_iterations
        assert _resolve_max_tool_iterations() == Agent.DEFAULT_MAX_TOOL_ITERATIONS

    def test_non_positive_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("MAX_TOOL_ITERATIONS", "0")
        from src.agent.agent import Agent, _resolve_max_tool_iterations
        assert _resolve_max_tool_iterations() == Agent.DEFAULT_MAX_TOOL_ITERATIONS


# ─── ask / chat_turn resolve per call ───────────────────────


def _tool_agent(tmp_path, monkeypatch):
    """Agent whose manager has tools and whose OpenAI client is a
    MagicMock answering 'done' on every create."""
    monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path))
    from src.agent.agent import Agent
    a = Agent()
    a._provider = "stub"
    a._model = "stub-model"
    mgr = MagicMock()
    mgr.has_tools.return_value = True
    a._mcp_manager = mgr

    fake_openai = MagicMock()
    fake_resp = MagicMock()
    fake_resp.choices = [
        MagicMock(message=MagicMock(content="done"), finish_reason="stop")
    ]
    fake_openai.chat.completions.create.return_value = fake_resp
    a._client = fake_openai
    return a


class TestAskChatTurnBudgetResolution:
    def _spy_tool_loop(self, agent, seen):
        orig = agent._tool_loop

        def spy(*, messages, tools, temperature, max_iterations):
            seen["n"] = max_iterations
            return orig(
                messages=messages, tools=tools,
                temperature=temperature, max_iterations=max_iterations,
            )

        agent._tool_loop = spy

    def test_ask_none_uses_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MAX_TOOL_ITERATIONS", "9")
        a = _tool_agent(tmp_path, monkeypatch)
        seen: dict = {}
        self._spy_tool_loop(a, seen)
        a.ask("q", use_tools=True)
        assert seen["n"] == 9

    def test_chat_turn_none_uses_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MAX_TOOL_ITERATIONS", "11")
        a = _tool_agent(tmp_path, monkeypatch)
        seen: dict = {}
        self._spy_tool_loop(a, seen)
        a.chat_turn("q", history=[], use_tools=True)
        assert seen["n"] == 11

    def test_explicit_int_overrides_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MAX_TOOL_ITERATIONS", "9")
        a = _tool_agent(tmp_path, monkeypatch)
        seen: dict = {}
        self._spy_tool_loop(a, seen)
        a.ask("q", use_tools=True, max_tool_iterations=3)
        assert seen["n"] == 3

    def test_default_is_20_when_unset(self, tmp_path, monkeypatch):
        monkeypatch.delenv("MAX_TOOL_ITERATIONS", raising=False)
        a = _tool_agent(tmp_path, monkeypatch)
        seen: dict = {}
        self._spy_tool_loop(a, seen)
        a.ask("q", use_tools=True)
        assert seen["n"] == 20


# ─── bail-out message shape ─────────────────────────────────


class _FakeChoice:
    def __init__(self, message, finish="stop"):
        self.message = message
        self.finish_reason = finish


class _FakeMessage:
    def __init__(self, content="", tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []


class _FakeToolCall:
    def __init__(self, id, name, arguments):
        self.id = id
        self.function = MagicMock(name=name, arguments=arguments)


class _FakeResp:
    def __init__(self, choices):
        self.choices = choices


class TestBailOutMessage:
    def test_bailout_asks_progress_and_continue(self, monkeypatch):
        # live .env leak (v0.9.107.0): QDRANT_ROUTE=on greeting
        # fast-path intercepts "hi" before the tool loop — wipe
        # the route env for this unit test.
        for var in ("QDRANT_ROUTE", "QDRANT_TIME_ROUTE",
                    "QDRANT_TOOLROUTE"):
            monkeypatch.delenv(var, raising=False)
        """When the loop hits the budget, the injected user message
        must ask for step-by-step progress + 「繼續」 — not the old
        bare 'provide your final answer now' that read like a hard
        capability limit."""
        from src.agent.agent import Agent
        from src.mcp_client.manager import McpManager
        from src.mcp_client.host import LocalTool

        mgr = McpManager()
        mgr.register_local(LocalTool(
            name="loop", description="loop",
            handler=lambda **_: {"x": 1},
            server_name="t",
        ))
        tool_call = _FakeToolCall(id="x", name="loop", arguments=json.dumps({}))
        tool_msg = _FakeMessage(content="still going", tool_calls=[tool_call])
        final_msg = _FakeMessage(content="wrap-up")

        fake_openai = MagicMock()
        fake_openai.chat.completions.create.side_effect = (
            [_FakeResp([_FakeChoice(tool_msg, finish="tool_calls")])] * 2
            + [_FakeResp([_FakeChoice(final_msg, finish="stop")])]
        )
        a = Agent(mcp_manager=mgr)
        a._client = fake_openai
        a._model = "t"

        answer = a.ask("hi", use_tools=True, max_tool_iterations=2)
        assert answer == "wrap-up"

        # 3rd create = the bail-out call; its last message must be the
        # injected user turn asking for progress + 繼續.
        third = fake_openai.chat.completions.create.call_args_list[2]
        msgs = third.kwargs["messages"]
        assert msgs[-1]["role"] == "user"
        assert "unfinished" in msgs[-1]["content"]
        assert "繼續" in msgs[-1]["content"]
        assert "Do NOT claim unfinished work is done" in msgs[-1]["content"]


# ─── system prompt block ────────────────────────────────────


class TestWindowsAutomationPromptBlock:
    def test_block_header_present(self, stub_agent):
        prompt = stub_agent._system_prompt(tools_enabled=True)
        assert "Windows desktop automation (windows-mcp, v0.9.14.1+)" in prompt

    def test_launch_ladder_documented(self, stub_agent):
        prompt = stub_agent._system_prompt(tools_enabled=True)
        assert "not found in start menu" in prompt
        assert "mspaint.exe" in prompt
        assert "NEVER omit the extension" in prompt
        assert "Start-Process mspaint" in prompt

    def test_scripted_drawing_preferred(self, stub_agent):
        prompt = stub_agent._system_prompt(tools_enabled=True)
        assert "System.Drawing" in prompt
        assert "用滑鼠畫" in prompt

    def test_budget_discipline_documented(self, stub_agent):
        prompt = stub_agent._system_prompt(tools_enabled=True)
        assert "MAX_TOOL_ITERATIONS" in prompt
        assert "繼續" in prompt

    def test_legacy_prompt_unaffected(self, stub_agent):
        prompt = stub_agent._system_prompt(tools_enabled=False)
        assert "Windows desktop automation" not in prompt
        assert "MAX_TOOL_ITERATIONS" not in prompt
