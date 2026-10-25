"""Regression for v0.9.5.2: drawio XML not echoed into final message.

User log (2026-09-01 22:43+):
  The user asked the LLM to draw a "hello" block via drawio and show
  it. The agent did call ``create_diagram(['xml'])`` (visible in the
  log: ``tool create_diagram -> OK: '{"xml": "<mxGraphModel>..."}'``)
  but the assistant's FINAL response was pure prose:

      "已完成！✅ 我用 draw.io 繪製了一個 hello 方塊:形狀:圓角矩形
      (240 × 120)文字:hello(28px 粗體)配色:淡藍色背景..."

  No XML was embedded. The chat UI's ``_extractDiagramXml`` regex
  found nothing to attach a "View in Diagrams" button to. The user
  therefore saw only the prose — no clickable viewer link.

Fix: add a "Drawio / diagrams viewer" trigger block to the
``tools_enabled`` branch of ``Agent._system_prompt()`` with a
specifically-named sub-rule that forces the LLM to echo the XML
into its final message (in either JSON-envelope or fenced-xml
shape) so the chat UI regex can pick it up.
"""
from __future__ import annotations

import os
from pathlib import Path
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


class TestDiagramsTriggerBlock:
    def test_tools_enabled_prompt_mentions_drawio(self, stub_agent):
        prompt = stub_agent._system_prompt(tools_enabled=True)
        assert "Drawio" in prompt or "drawio" in prompt, (
            "drawio trigger header missing"
        )
        assert "create_diagram" in prompt, (
            "create_diagram tool name missing from trigger block"
        )

    def test_prompt_lists_common_trigger_keywords(self, stub_agent):
        """Both Chinese + English trigger phrases must be
        present so the LLM matches regardless of how the user
        phrases the request."""
        prompt = stub_agent._system_prompt(tools_enabled=True)
        # Chinese
        for kw in ("drawio", "繪制", "方塊", "流程圖", "圖表檢視器"):
            assert kw in prompt, (
                f"Chinese trigger keyword {kw!r} missing from prompt"
            )
        # English
        for kw in ("drawio", "draw.io", "diagram",
                   "flowchart", "diagrams viewer"):
            assert kw in prompt, (
                f"English trigger keyword {kw!r} missing from prompt"
            )

    def test_prompt_forbids_pure_prose_response(self, stub_agent):
        """The whole point of v0.9.5.2: the LLM MUST include
        the XML, not just describe the diagram in prose."""
        prompt = stub_agent._system_prompt(tools_enabled=True)
        # The exact bug wording should appear.
        assert "describe the diagram in prose" in prompt, (
            "LLM must be told explicitly that pure-prose "
            "descriptions produce no viewer button"
        )
        # And the viewer button mechanism must be named.
        assert "View in Diagrams" in prompt, (
            "prompt must name the chat UI button so the LLM "
            "knows what it's enabling"
        )

    def test_prompt_shows_both_xml_shapes(self, stub_agent):
        """The LLM needs both shapes available — JSON envelope
        AND bare fenced XML — so it can pick whichever fits
        the prose around it."""
        prompt = stub_agent._system_prompt(tools_enabled=True)
        assert '"xml"' in prompt, (
            "JSON-envelope shape {\"xml\": \"...\"} must be in prompt"
        )
        assert "mxGraphModel" in prompt, (
            "Bare XML block ```xml ... ``` must be in prompt"
        )

    def test_block_does_not_leak_into_legacy_mode(self, stub_agent):
        """Legacy tools_enabled=False path must not include the
        diagrams block — that mode cannot reach the tool anyway."""
        legacy = stub_agent._system_prompt(tools_enabled=False)
        assert "create_diagram" not in legacy, (
            "diagrams trigger block must not appear in legacy "
            "tools_enabled=False prompt"
        )
        assert "View in Diagrams" not in legacy

    def test_block_composes_with_response_language(self, stub_agent):
        """Language directive must not displace the diagrams block."""
        prompt = stub_agent._system_prompt(
            tools_enabled=True, response_language="zh-TW",
        )
        assert "create_diagram" in prompt
        assert "LANGUAGE DIRECTIVE" in prompt
        assert "繁體中文" in prompt

    def test_block_does_not_collide_with_telegram_or_schedule(
        self, stub_agent,
    ):
        """Adding the diagrams block must not displace the
        existing Telegram + Schedule trigger blocks."""
        prompt = stub_agent._system_prompt(tools_enabled=True)
        # Telegram
        assert "send_telegram_message" in prompt
        assert "Telegram + Schedule composition" in prompt
        # Schedule
        for tool in (
            "add_scheduled_task",
            "delete_scheduled_task",
            "list_scheduled_tasks",
        ):
            assert tool in prompt, f"{tool} missing from prompt"

    def test_block_warns_about_tool_failure(self, stub_agent):
        """If the drawio MCP tool fails (network / auth / quota),
        the LLM should surface the error instead of inventing a
        fake description."""
        prompt = stub_agent._system_prompt(tools_enabled=True)
        # Either of these phrasings should appear.
        assert (
            "drawio tool fails" in prompt
            or "tool fails" in prompt
            or "silently describing" in prompt
        )
