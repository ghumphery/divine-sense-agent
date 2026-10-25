"""Tests for the v0.8.6 system prompt additions.

Verifies the LLM-facing instruction block in
``Agent._system_prompt`` correctly mentions the scheduler
tools so the model knows when to invoke them.

* Tools-enabled block lists every scheduler tool name.
* Tools-enabled block mentions trigger phrases in zh-TW, zh-CN,
  en (covers the user demographics we've seen).
* Legacy / non-tools blocks don't include scheduler triggers
  (would be confusing noise).
* Composes cleanly with ``response_language`` — no overlap.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest


def _make_agent():
    """Build an Agent with stubbed OpenAI client / no real MCP."""
    from src.agent.agent import Agent
    agent = object.__new__(Agent)
    agent._client = MagicMock()
    agent._provider = "stub"
    agent._model = "stub-model"
    agent._mcp_manager = None
    return agent


class TestSystemPromptScheduler:
    def test_scheduler_tools_listed(self):
        agent = _make_agent()
        prompt = agent._system_prompt(tools_enabled=True)
        for tool in (
            "add_scheduled_task",
            "delete_scheduled_task",
            "list_scheduled_tasks",
            "get_scheduled_task",
            "enable_scheduled_task",
            "disable_scheduled_task",
            "run_scheduled_task_now",
            "get_scheduled_task_errors",
        ):
            assert tool in prompt, (
                f"tool {tool!r} missing from system prompt"
            )

    def test_scheduler_triggers_present(self):
        agent = _make_agent()
        prompt = agent._system_prompt(tools_enabled=True)
        # Triggers across the three locales we support.
        assert "排程" in prompt
        assert "schedule" in prompt.lower()
        assert "cron" in prompt.lower()
        assert "every day" in prompt.lower() or "every hour" in prompt.lower()

    def test_failure_handling_block_preserved(self):
        """Don't regress v0.8.0+ failure-handling rules."""
        agent = _make_agent()
        prompt = agent._system_prompt(tools_enabled=True)
        assert "STOP retrying" in prompt or "pivot" in prompt
        assert "Cite filenames" in prompt

    def test_legacy_mode_excludes_scheduler(self):
        """The non-tools prompt path is for raw RAG (pre-MCP) —
        scheduler triggers would be misleading there."""
        agent = _make_agent()
        legacy = agent._system_prompt(
            web_enabled=True, tools_enabled=False,
        )
        assert "add_scheduled_task" not in legacy

    def test_composes_with_response_language(self):
        agent = _make_agent()
        prompt = agent._system_prompt(
            tools_enabled=True, response_language="zh-TW",
        )
        # Scheduler triggers still present alongside the language directive.
        assert "add_scheduled_task" in prompt
        assert "LANGUAGE DIRECTIVE" in prompt
        assert "繁體中文" in prompt

    def test_response_language_does_not_collide(self):
        """zh-TW directive shouldn't translate the tool names —
        the carve-out from v0.8.5 should still apply."""
        agent = _make_agent()
        prompt = agent._system_prompt(
            tools_enabled=True, response_language="zh-CN",
        )
        # Tool names are ASCII code identifiers — must stay as-is.
        for tool in (
            "add_scheduled_task",
            "get_scheduled_task_errors",
        ):
            assert tool in prompt
