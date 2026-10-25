"""Tests for the v0.8.8+ todo plan / execute trigger block."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _agent():
    """Minimal Agent stub - we only call ``_system_prompt`` so we
    can skip ``__init__`` (which would otherwise try to import
    OpenAI / qdrant)."""
    from src.agent.agent import Agent
    return Agent.__new__(Agent)


def _system_prompt(tools_enabled: bool = True, response_language=None):
    return _agent()._system_prompt(
        tools_enabled=tools_enabled,
        response_language=response_language,
    )


def test_todo_trigger_block_present_when_tools_enabled():
    p = _system_prompt(tools_enabled=True)
    # Plan-mode trigger phrases
    assert "幫我規劃" in p
    assert "做個計畫" in p
    assert "拆解任務" in p
    assert "plan X" in p
    assert "step by step" in p
    # Tool names must be advertised in the trigger block
    for name in (
        "create_task", "add_subtask", "start_task",
        "complete_task", "get_todo_progress", "reorder_task",
        "add_task_dependency",
    ):
        assert name in p, f"{name!r} missing from system prompt"


def test_todo_trigger_block_absent_when_tools_disabled():
    """Legacy / no-tools prompt must NOT carry the todo
    triggers - the LLM has no tool surface to use them with."""
    p = _system_prompt(tools_enabled=False)
    assert "幫我規劃" not in p
    assert "create_task" not in p


def test_todo_trigger_block_coexists_with_response_language():
    """Adding a response_language directive must not strip the
    todo trigger block."""
    p = _system_prompt(tools_enabled=True, response_language="zh-TW")
    assert "幫我規劃" in p
    assert "create_task" in p
    # And the language directive itself is present
    assert "LANGUAGE DIRECTIVE" in p or "繁體中文" in p or "zh-TW" in p


def test_todo_trigger_block_coexists_with_schedule_block():
    """v0.8.7+ schedule progress block and v0.8.8+ todo block
    must both surface in the tools-enabled prompt - the LLM
    can drive both in the same session."""
    p = _system_prompt(tools_enabled=True)
    assert "Schedule management" in p or "排程" in p
    assert "Plan / multi-step" in p or "幫我規劃" in p


def test_todo_trigger_block_coexists_with_telegram_block():
    p = _system_prompt(tools_enabled=True)
    assert "Telegram" in p or "telegram" in p
    assert "Plan / multi-step" in p or "create_task" in p


def test_dependence_rule_advertised():
    """The prompt must teach the LLM that ``add_task_dependency``
    is the right way to express B-after-A, not narrative."""
    p = _system_prompt(tools_enabled=True)
    assert "Dependence rule" in p or "add_task_dependency" in p
    assert "depends_on" in p or "依賴" in p or "dependency graph" in p


def test_auto_complete_explained():
    """The LLM should know NOT to manually complete the parent
    when all children are terminal."""
    p = _system_prompt(tools_enabled=True)
    assert "auto" in p.lower()
    assert "Auto-completion" in p or "auto-completes" in p or "auto-flips" in p


def test_execution_model_explained():
    """The LLM should know it is BOTH planner AND executor
    (drives the plan turn by turn)."""
    p = _system_prompt(tools_enabled=True)
    assert "Execution model" in p or "planner AND" in p
    assert "scratchpad" in p or "across turns" in p


def test_skip_and_delete_advertised():
    p = _system_prompt(tools_enabled=True)
    assert "skip_task" in p
    assert "delete_task" in p


def test_render_and_journal_advertised():
    p = _system_prompt(tools_enabled=True)
    assert "render_task_tree" in p or "render" in p
    assert "list_task_journal" in p or "journal" in p
