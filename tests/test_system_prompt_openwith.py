"""Regression tests for the v0.9.6.0 open-with trigger block.

``Agent._system_prompt(tools_enabled=True)`` must teach the LLM
about the Explorer-style artifact opener (``open_file`` /
``open_content`` / ``list_openable_types``): trigger phrases,
the write-then-open flow, and the executable refusal. Mirrors
``tests/test_system_prompt_diagrams.py``.
"""
from __future__ import annotations

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


class TestOpenWithTriggerBlock:
    def test_tools_enabled_prompt_names_the_tools(self, stub_agent):
        prompt = stub_agent._system_prompt(tools_enabled=True)
        assert "open_file" in prompt
        assert "open_content" in prompt
        assert "list_openable_types" in prompt

    def test_block_header_present(self, stub_agent):
        prompt = stub_agent._system_prompt(tools_enabled=True)
        assert "Open-with / artifact opener (v0.9.6.0+)" in prompt

    def test_prompt_lists_trigger_keywords(self, stub_agent):
        prompt = stub_agent._system_prompt(tools_enabled=True)
        # Chinese
        for kw in ("幫我開啟", "打開來看", "存檔後開給我看"):
            assert kw in prompt, f"missing zh trigger {kw!r}"
        # English
        for kw in ("open the file", "open the diagram"):
            assert kw in prompt, f"missing en trigger {kw!r}"

    def test_prompt_states_explorer_semantics(self, stub_agent):
        prompt = stub_agent._system_prompt(tools_enabled=True)
        assert "Windows-Explorer" in prompt
        assert "file association" in prompt or "associated" in prompt

    def test_prompt_documents_drawio_routing(self, stub_agent):
        prompt = stub_agent._system_prompt(tools_enabled=True)
        assert "Drawio Viewer" in prompt
        assert "auto-routed" in prompt

    def test_prompt_documents_security_refusal(self, stub_agent):
        prompt = stub_agent._system_prompt(tools_enabled=True)
        assert "sandbox-scoped" in prompt
        assert "refused" in prompt

    def test_block_not_in_legacy_mode(self, stub_agent):
        legacy = stub_agent._system_prompt(tools_enabled=False)
        assert "open_content" not in legacy
        assert "list_openable_types" not in legacy
        assert "Open-with / artifact opener" not in legacy

    def test_composes_with_response_language(self, stub_agent):
        prompt = stub_agent._system_prompt(
            tools_enabled=True, response_language="zh-TW",
        )
        assert "open_content" in prompt
        assert "LANGUAGE DIRECTIVE" in prompt

    def test_does_not_displace_other_blocks(self, stub_agent):
        """The new block must coexist with the drawio / telegram /
        schedule blocks already in the prompt."""
        prompt = stub_agent._system_prompt(tools_enabled=True)
        assert "create_diagram" in prompt
        assert "View in Diagrams" in prompt
        assert "send_telegram_message" in prompt
        assert "add_scheduled_task" in prompt
        assert "render_task_tree" in prompt