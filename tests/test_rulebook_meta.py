# -*- coding: utf-8 -*-
"""v0.9.124.0 — P2/P3 層測試：Evo² crossover handoff hint、
Reflector→Proposer post-mortem gate、prompt 層守衛（script 雙層分診 /
TACTI crossover / 多假設食譜）與 SKILL_GUIDE §2.5。

全部 mock — 不真跑 chat_turn（post-mortem 的 LLM 回合由 fake agent 承接）。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest


@pytest.fixture()
def po_env(tmp_path, monkeypatch):
    """Isolated write sandbox for handoff files + quiet post-mortem."""
    monkeypatch.setenv("QDRANT_FILESYSTEM_WRITE_ROOT", str(tmp_path))
    monkeypatch.setenv("QDRANT_RULEBOOK_FILE",
                       str(tmp_path / "rb.md"))
    monkeypatch.delenv("QDRANT_REFLECT_POSTMORTEM", raising=False)
    monkeypatch.delenv("QDRANT_RULEBOOK_ENABLED", raising=False)
    yield tmp_path
    from src.agent.rulebook import reset_rulebook_cache

    reset_rulebook_cache()


class TestHandoffRecoveryHint:
    def _write(self, status, **kw):
        from src.agent.skill_compiler import _write_handoff

        return _write_handoff("p1", "s1", status, "summary", **kw)

    def _read(self, po_env):
        f = po_env / "out" / "s1.json"
        return json.loads(f.read_text(encoding="utf-8"))

    def test_failed_hint_written_when_passed(self, po_env):
        """The _run_stage call site composes the hint (source-guard in
        TestPromptLayerGuards); this locks the pass-through behaviour."""
        assert self._write("failed", recovery_hint="skill_search('抓資料')")
        d = self._read(po_env)
        assert d["recovery_hint"].startswith("skill_search(")

    def test_done_stage_has_no_hint(self, po_env):
        assert self._write("done",
                           recovery_hint=None)
        assert "recovery_hint" not in self._read(po_env)

    def test_hint_capped(self, po_env):
        long = "x" * 900
        assert self._write("failed", recovery_hint=long)
        d = self._read(po_env)
        assert len(d["recovery_hint"]) <= 600


class TestPostMortem:
    def _rec(self, stages=None):
        return {
            "id": "plx", "name": "驗證計畫", "status": "failed",
            "default_llm": None,
            "stages": stages or [
                {"id": "a1", "title": "抓資料", "status": "failed",
                 "error": "boom", "sid": "plan-plx-a1"},
                {"id": "a2", "title": "後續", "status": "done"},
            ],
        }

    def test_gate_off_is_silent(self, po_env, monkeypatch):
        import threading as _th

        from src.agent import skill_compiler as sc

        calls = []
        monkeypatch.setattr(_th, "Thread",
                            lambda **kw: calls.append(kw))
        sc._reflect_postmortem(self._rec(), "failed", "plx")
        assert calls == []

    def test_gate_on_spawns_worker_with_roles(self, po_env, monkeypatch):
        import threading as _th

        from src.agent import skill_compiler as sc

        monkeypatch.setenv("QDRANT_REFLECT_POSTMORTEM", "on")
        fake_agent = MagicMock()
        fake_agent.chat_turn.return_value = (
            "複盤：環境假設錯 → rules_revise 已提（r1）", "", [])
        monkeypatch.setattr(sc, "_AGENT_FACTORY", lambda **kw: fake_agent)
        started = []

        class _T:
            def __init__(self, target=None, daemon=None, name=None):
                self.target = target
                started.append(self)

            def start(self):
                self.target()

        monkeypatch.setattr(sc.threading, "Thread", _T)
        sc._reflect_postmortem(self._rec(), "failed", "plx")
        assert len(started) == 1
        kw = fake_agent.chat_turn.call_args
        assert kw is not None
        prompt = kw.kwargs.get("question") or kw.args[0]
        assert "Reflector" in prompt and "Proposer" in prompt
        assert "規則簿修訂" in prompt
        assert "無證據" in prompt or "嚴禁" in prompt
        assert "a1" in prompt  # failed stage id carried

    def test_no_factory_is_silent(self, po_env):
        from src.agent import skill_compiler as sc

        monkey = None  # _AGENT_FACTORY restored per-module state
        saved = sc._AGENT_FACTORY
        sc._AGENT_FACTORY = None
        try:
            os.environ["QDRANT_REFLECT_POSTMORTEM"] = "on"
            sc._reflect_postmortem(self._rec(), "failed", "plx")
        finally:
            sc._AGENT_FACTORY = saved
            os.environ.pop("QDRANT_REFLECT_POSTMORTEM", None)

    def test_no_failed_stages_is_silent(self, po_env, monkeypatch):
        import threading as _th

        from src.agent import skill_compiler as sc

        monkeypatch.setenv("QDRANT_REFLECT_POSTMORTEM", "on")
        calls = []
        monkeypatch.setattr(_th, "Thread",
                            lambda **kw: calls.append(kw))
        rec = self._rec([{"id": "a1", "title": "x", "status": "done"}])
        sc._reflect_postmortem(rec, "done", "plx")
        assert calls == []

    def test_worker_crash_never_propagates(self, po_env, monkeypatch):
        from src.agent import skill_compiler as sc

        monkeypatch.setenv("QDRANT_REFLECT_POSTMORTEM", "on")
        monkeypatch.setattr(
            sc, "_AGENT_FACTORY",
            MagicMock(side_effect=RuntimeError("LLM down")),
        )

        class _T:
            def __init__(self, target=None, daemon=None, name=None):
                self.target = target

            def start(self):
                self.target()  # crash INSIDE the worker

        monkeypatch.setattr(sc.threading, "Thread", _T)
        sc._reflect_postmortem(self._rec(), "failed", "plx")  # no raise
class TestPromptLayerGuards:
    """P2 — script 雙層分診、TACTI crossover + 多假設食譜、skill
    recovery_hint（源碼守衛 + 行為驗證）。"""

    AGENT = Path("src/agent/agent.py")
    GUIDE = Path("SKILL_GUIDE.md")
    COMPILER = Path("src/agent/skill_compiler.py")

    def _text(self, p):
        return p.read_text(encoding="utf-8")

    def test_script_triage_block_present(self):
        t = self._text(self.AGENT)
        assert "Script FAILURE triage" in t
        assert "UNDERSTANDING of the environment was wrong" in t
        assert "rules_revise" in t

    def test_tacti_crossover_and_population_recipe(self):
        t = self._text(self.AGENT)
        assert "Crossover on failure (v0.9.124.0" in t
        assert "skill_search(" in t
        assert "Multi-hypothesis recipe" in t
        assert "picks the winner on shared evidence" in t

    def test_recovery_hint_wired_in_run_stage(self):
        t = self._text(self.COMPILER)
        assert "recovery_hint" in t
        assert "Evo" in t  # the crossover marker text (plain ASCII-safe)

    def test_postmortem_opt_in_env(self):
        t = self._text(self.COMPILER)
        assert "QDRANT_REFLECT_POSTMORTEM" in t
        assert "_reflect_postmortem" in t

    def test_skill_guide_recipe_section(self):
        g = self._text(self.GUIDE)
        assert "§2.5 自進化食譜" in g
        assert "多假設並存" in g
        assert "recovery_hint" in g
        assert "QDRANT_REFLECT_POSTMORTEM" in g

    def test_rulebook_prompt_note_mentions_underspecified(self):
        t = self._text(self.AGENT)
        assert "underspecified" in t

    def test_env_example_rulebook_block(self):
        e = self._text(Path(".env.example"))
        assert "QDRANT_RULEBOOK_ENABLED" in e
        assert "rulebook-local" in e
        assert "QDRANT_REFLECT_POSTMORTEM" in e