"""v0.9.53.0 — search-first rule for the local corpus (qdrant-local).

The old rule (「Call search_documents first when the user asks a
question that MAY BE ANSWERED from their local knowledge base」) left
the LLM free to skip qdrant-local for stock/time/general questions.
The new rule makes search_documents the FIRST tool for any data /
knowledge / research question — before own-memory, before
web_search, before other tools — with explicit carve-outs for meta
queries and dedicated live-data triggers.
"""
from __future__ import annotations

import pytest


def _prompt_agent():
    from src.agent.agent import Agent

    a = Agent()
    a._provider = "stub"
    a._model = "stub-model"
    a._mcp_manager = None
    return a


class TestSearchFirstRule:
    def test_rule_present_when_tools_enabled(self):
        prompt = _prompt_agent()._system_prompt(tools_enabled=True)
        assert "Local corpus FIRST" in prompt
        assert "search_documents" in prompt
        # Explicit ordering constraints.
        assert "BEFORE" in prompt
        assert "web_search" in prompt
        # zh trigger words wired.
        assert "查詢" in prompt and "資料" in prompt

    def test_carve_outs_named(self):
        prompt = _prompt_agent()._system_prompt(tools_enabled=True)
        # Meta queries + live-data triggers must be listed as the
        # exceptions, so the rule stays deterministic.
        assert "get_current_time" in prompt
        assert "chat-history" in prompt
        assert "stock" in prompt

    def test_never_substitute_memory_clause(self):
        prompt = _prompt_agent()._system_prompt(tools_enabled=True)
        assert "never silently substitute" in prompt

    def test_legacy_mode_excluded(self):
        prompt = _prompt_agent()._system_prompt(tools_enabled=False)
        assert "Local corpus FIRST" not in prompt

    def test_coexists_with_response_language(self):
        prompt = _prompt_agent()._system_prompt(
            tools_enabled=True, response_language="zh-TW",
        )
        assert "Local corpus FIRST" in prompt
        assert "LANGUAGE DIRECTIVE" in prompt