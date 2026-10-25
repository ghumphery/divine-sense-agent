# -*- coding: utf-8 -*-
"""v0.9.119.0 — polite scraping / download discipline prompt block.

Source + prompt-level contracts: the block teaches the LLM to pace
multi-page fetch scripts (2-5s fixed delay, 429/503 backoff with
Retry-After honouring, done-ID checkpoints, one self-contained script
per batch, sequential by default) and to never report a failed fetch
as success. Tools-flavour prompt only; legacy excludes it; composes
with response_language and the other trigger blocks.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _agent():
    from src.agent.agent import Agent

    return Agent.__new__(Agent)


def _prompt(tools_enabled=True, response_language=None):
    return _agent()._system_prompt(
        tools_enabled=tools_enabled,
        response_language=response_language,
    )


def test_block_present_when_tools_enabled():
    p = _prompt(tools_enabled=True)
    assert "Polite web scraping / download discipline" in p
    assert "v0.9.119.0+" in p


def test_key_rules_present():
    p = _prompt(tools_enabled=True)
    assert "429" in p
    assert "30+random×30s" in p or "back off 30" in p
    assert "Retry-After" in p
    assert "checkpoint" in p.lower()
    assert "2-5s" in p or "2-5 秒" in p


def test_teaches_429_is_not_threads():
    p = _prompt(tools_enabled=True)
    assert "fully sequential" in p.lower() or "FULLY SEQUENTIAL" in p


def test_never_report_failed_fetch_as_success():
    p = _prompt(tools_enabled=True)
    assert "NEVER claim a failed fetch" in p


def test_absent_in_legacy_prompt():
    p = _prompt(tools_enabled=False)
    assert "Polite web scraping" not in p


def test_composes_with_response_language():
    p = _prompt(tools_enabled=True, response_language="zh-TW")
    assert "Polite web scraping" in p
    assert "LANGUAGE DIRECTIVE" in p


def test_block_single_occurrence_in_source():
    src = Path("src/agent/agent.py").read_text(encoding="utf-8")
    assert src.count("Polite web scraping / download discipline") == 1


def test_coexists_with_other_blocks():
    p = _prompt(tools_enabled=True)
    # sibling trigger blocks untouched
    assert "Skill file library" in p
    assert "compile_plan" in p
    assert "Background tasks" in p or "run_task_once" in p