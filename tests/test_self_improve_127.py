# -*- coding: utf-8 -*-
"""v0.9.127.0 — self-improvement items ①②: plan→SKILL.md auto-draft +
rulebook per-rule failure telemetry."""
import json

import pytest

from src.agent import rulebook as rb


@pytest.fixture()
def rb_home(tmp_path, monkeypatch):
    monkeypatch.setenv("QDRANT_FILESYSTEM_READ_ROOT", str(tmp_path))
    monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path))
    monkeypatch.delenv("QDRANT_RULEBOOK_FILE", raising=False)
    monkeypatch.delenv("QDRANT_RULEBOOK_TELEMETRY", raising=False)
    monkeypatch.delenv("QDRANT_PLAN_SKILL_DRAFT", raising=False)
    monkeypatch.delenv("QDRANT_PLAN_SKILL_DRAFT_MIN_STAGES", raising=False)
    rb.reset_rulebook_cache()
    yield tmp_path
    rb.reset_rulebook_cache()


class TestRulebookTelemetry:
    def _two_rules(self):
        assert rb.rules_add("先查本機語料", "資料題先 search_documents",
                            "v0.9.53 教訓")["ok"]
        assert rb.rules_add("x.com 用 firefox", "x.com 一律走 firefox_craw",
                            "web_fetch 404")["ok"]

    def test_bump_counts_and_stamp(self, rb_home):
        self._two_rules()
        r1 = rb.note_tool_error()
        assert r1 and r1["ok"] and r1["rules_bumped"] == 2
        rb.note_tool_error()
        rules = rb.parse_rules(rb.load_rulebook())
        assert all(int(r["errors"]) == 2 for r in rules)
        assert all(r["last_error"] for r in rules)

    def test_no_ring_snapshot_for_bookkeeping(self, rb_home):
        self._two_rules()
        base = len(rb.rules_history()["entries"])
        rb.note_tool_error()
        rb.note_tool_error()
        assert len(rb.rules_history()["entries"]) == base

    def test_revise_resets_errors(self, rb_home):
        self._two_rules()
        rb.note_tool_error()
        assert rb.rules_revise("r1", "新內文", "鐵證 2026-10-16")["ok"]
        rules = rb.parse_rules(rb.load_rulebook())
        r1 = next(r for r in rules if r["id"] == "r1")
        r2 = next(r for r in rules if r["id"] == "r2")
        assert int(r1["errors"]) == 0 and int(r2["errors"]) == 1

    def test_gate_off_and_no_rules(self, rb_home, monkeypatch):
        monkeypatch.setenv("QDRANT_RULEBOOK_TELEMETRY", "off")
        assert rb.note_tool_error() is None
        monkeypatch.setenv("QDRANT_RULEBOOK_TELEMETRY", "on")
        assert rb.note_tool_error() is None  # rulebook file empty = None


class TestPlanSkillDraft:
    def _rec(self):
        return {
            "id": "abc123def4",
            "name": "每週 Crypto 摘要",
            "description": "抓新聞 → 摘要 → 發 Telegram",
            "stages": [
                {"title": "抓資料", "prompt": "抓 crypto 新聞 10 則",
                 "tools_allow": ["firefox_craw"]},
                {"title": "摘要", "prompt": "逐則摘要成三行"},
                {"title": "驗證與發送", "prompt": "verify_artifact 後發送",
                 "verify": {"path": "out/crypto.json"}},
            ],
        }

    def test_draft_shapes(self, rb_home):
        from src.skill_library import draft_from_plan

        out = draft_from_plan(self._rec())
        assert out.get("ok"), out
        assert out["name"].startswith("plan-")
        text = open(out["path"], encoding="utf-8").read()
        assert text.startswith("---")
        assert "### Step 1: 抓資料" in text
        assert "verify_artifact" in text and "skill_import" in text
        meta = json.load(open(out["folder"] + "/_skill_meta.json",
                              encoding="utf-8"))
        assert meta["status"] == "pending-confirm"
        assert meta["source"] == "auto-drafted-from-plan"

    def test_draft_never_raises_on_garbage(self, rb_home):
        from src.skill_library import draft_from_plan

        assert "error" in draft_from_plan({})
        assert "error" in draft_from_plan(None)

    def test_min_stages_env(self, rb_home, monkeypatch):
        from src.skill_library import plan_skill_draft_min_stages
        from src.agent.skill_compiler import _draft_min_stages

        assert _draft_min_stages() == 3
        monkeypatch.setenv("QDRANT_PLAN_SKILL_DRAFT_MIN_STAGES", "5")
        assert plan_skill_draft_min_stages() == 5
        assert _draft_min_stages() == 5

    def test_gate_respected(self, rb_home, monkeypatch):
        from src.skill_library import plan_skill_draft_enabled

        assert plan_skill_draft_enabled() is True
        monkeypatch.setenv("QDRANT_PLAN_SKILL_DRAFT", "off")
        assert plan_skill_draft_enabled() is False

    def test_prompt_mentions_fixation(self, rb_home):
        from src.agent.agent import Agent

        p = Agent.__new__(Agent)._system_prompt(tools_enabled=True)
        assert "skill_drafts" in p and "auto-drafted" in p