"""Regression for v0.8.6.2: schedule-fires-but-no-telegram-send bug.

User log (2026-08-29 / 2026-08-31):
  22:08:06 scheduler: fired 502ff22a4fdb -> sched-... (answer=0 chars)

The schedule's prompt was 'send hello to chat <YOUR_TELEGRAM_CHAT_ID> via telegram'.
The LLM fired the prompt but emitted ZERO characters of answer — meaning
the tool-calling loop returned immediately because the LLM didn't call
any tool. ``send_telegram_message`` was in the live tool surface
(verified via _smoke2) but the system prompt never mentioned Telegram
trigger words, so the LLM didn't know it should reach for it.

Fix: add a "Telegram / external messaging" trigger block to the
``tools_enabled`` branch of ``Agent._system_prompt()`` with a
specifically-named "Telegram + Schedule composition (the common trap)"
sub-block that calls out the exact scenario.
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def stub_agent(tmp_path, monkeypatch):
    """Build an Agent with everything stubbed so we can inspect the prompt."""
    monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path))
    from src.agent.agent import Agent
    a = Agent()
    a._provider = "stub"
    a._model = "stub-model"
    a._mcp_manager = None  # skip lazy from_config — we don't need tools
    return a


class TestTelegramTriggerBlock:
    def test_tools_enabled_prompt_mentions_telegram(self, stub_agent):
        prompt = stub_agent._system_prompt(tools_enabled=True)
        assert "Telegram" in prompt, "Telegram trigger header missing"
        assert "send_telegram_message" in prompt, (
            "send_telegram_message tool name missing from trigger block"
        )

    def test_telegram_trigger_block_mentions_send_and_markdown(self, stub_agent):
        prompt = stub_agent._system_prompt(tools_enabled=True)
        assert "send_telegram_message" in prompt
        assert "send_telegram_markdown" in prompt

    def test_telegram_trigger_block_mentions_allowed_chats(self, stub_agent):
        """Security model must be visible to the LLM so it doesn't
        try to send to chats outside the allowlist."""
        prompt = stub_agent._system_prompt(tools_enabled=True)
        assert "TELEGRAM_ALLOWED_CHAT_IDS" in prompt
        assert "list_allowed_chats" in prompt

    def test_telegram_trigger_block_mentions_common_keywords(self, stub_agent):
        """Chinese + English + romanised trigger phrases must all
        be present so the LLM matches regardless of how the user
        phrases the request."""
        prompt = stub_agent._system_prompt(tools_enabled=True)
        # Chinese
        assert "用 telegram 發" in prompt
        assert "傳到 telegram" in prompt
        assert "發訊息給 chat_id" in prompt
        # English
        assert "send via telegram" in prompt
        assert "notify me on telegram" in prompt

    def test_composition_trap_is_called_out(self, stub_agent):
        """The most important addition: an explicit 'schedule fires
        you with a prompt that says to send a telegram → you MUST
        call the telegram tool' hint."""
        prompt = stub_agent._system_prompt(tools_enabled=True)
        # The whole point of v0.8.6.2.
        assert "Telegram + Schedule composition" in prompt
        assert "common trap" in prompt
        # Make sure the hint names the right tool.
        assert "send_telegram_message" in prompt
        assert "send_telegram_markdown" in prompt

    def test_telegram_block_does_not_leak_into_legacy_mode(self, stub_agent):
        """Legacy tools_enabled=False path must not include the
        Telegram block — that mode cannot reach the tool anyway."""
        legacy = stub_agent._system_prompt(tools_enabled=False)
        assert "send_telegram_message" not in legacy
        assert "Telegram + Schedule composition" not in legacy

    def test_telegram_block_composes_with_response_language(self, stub_agent):
        """Language directive must not displace the Telegram block."""
        prompt = stub_agent._system_prompt(
            tools_enabled=True, response_language="zh-TW",
        )
        assert "send_telegram_message" in prompt
        assert "LANGUAGE DIRECTIVE" in prompt
        assert "繁體中文" in prompt

    def test_telegram_block_does_not_collide_with_scheduler_triggers(
        self, stub_agent,
    ):
        """Adding the Telegram block must not displace the existing
        Schedule-management trigger block."""
        prompt = stub_agent._system_prompt(tools_enabled=True)
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
            assert tool in prompt, f"{tool} missing from prompt"
