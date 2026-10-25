"""Tests for v0.8.7+ Schedule progress / lifecycle triggers in the
Agent system prompt. Locks down each clause + the legacy
``tools_enabled=False`` exclusion + the ``response_language``
composition (carried over from v0.8.6.2)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _agent():
    """Minimal Agent stub — we only call ``_system_prompt`` so we
    short-circuit the heavy machinery."""
    from src.agent.agent import Agent
    return Agent.__new__(Agent)


def test_schedule_progress_block_present():
    sp = _agent()._system_prompt(tools_enabled=True)
    # Trigger phrases
    assert "跑幾次了" in sp
    assert "還會跑幾次" in sp
    assert "還能跑幾次" in sp
    assert "過期了嗎" in sp
    assert "上次排程什麼時候跑的" in sp
    assert "這個排程上次跑得怎樣" in sp
    assert "show me the last" in sp
    assert "fires remaining" in sp.lower() or "fires_remaining" in sp
    # Tool names referenced
    assert "get_scheduled_task_status" in sp
    assert "list_scheduled_task_runs" in sp


def test_schedule_progress_block_omitted_without_tools():
    sp = _agent()._system_prompt(tools_enabled=False)
    # The legacy short-form system prompt must NOT carry the new
    # trigger block (no MCP tool surface at all).
    assert "get_scheduled_task_status" not in sp
    assert "list_scheduled_task_runs" not in sp


def test_schedule_progress_coexists_with_response_language():
    """v0.8.7 prompt composition with v0.8.5 response_language
    directive (no collision)."""
    sp = _agent()._system_prompt(
        tools_enabled=True, response_language="zh-TW",
    )
    assert "get_scheduled_task_status" in sp
    # The LANGUAGE DIRECTIVE block is appended; the schedule
    # progress block remains intact.
    assert "LANGUAGE DIRECTIVE" in sp


def test_schedule_progress_no_collide_with_telegram_block():
    """v0.8.7 progress triggers must not shadow the v0.8.6.2
    Telegram trigger block — both stay readable."""
    sp = _agent()._system_prompt(tools_enabled=True)
    assert "send_telegram_message" in sp  # v0.8.6.2
    assert "get_scheduled_task_status" in sp  # v0.8.7


def test_schedule_progress_distinguishes_status_and_runs():
    """The trigger block must clearly direct the LLM to two
    distinct tools — status (current lifecycle) vs) runs (past
    fire records)."""
    sp = _agent()._system_prompt(tools_enabled=True)
    # ``status`` mentioned in the context of "current state".
    status_idx = sp.find("get_scheduled_task_status")
    runs_idx = sp.find("list_scheduled_task_runs")
    # Both present, in the schedule progress block, before any
    # unrelated section.
    assert status_idx != -1
    assert runs_idx != -1
    # The LLM should see both distinct descriptions.
    assert "lifecycle snapshot" in sp.lower() or "lifecycle" in sp
    assert "history" in sp.lower() or "history.jsonl" in sp