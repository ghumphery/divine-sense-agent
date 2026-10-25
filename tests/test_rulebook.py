# -*- coding: utf-8 -*-
"""v0.9.124.0 — agent RULE BOOK (``src/agent/rulebook.py`` +
``RulebookLocalHost`` + stdio mirror + cli ``rules``) 的契約測試。

借鏡鏈（self-evolving-agent 譜系報告）：
* Memento 3 (arXiv 2610.11794) — rulebook工件：可修訂規則檔 + prediction
  error 觸發修訂（hint 每回合一次）+ underspecified 標記。
* Evo² (arXiv 2610.13025) — 證據門：revise 必附證據（fail-closed）。
* 建議④ — zero-dep snapshot ring（history/diff/rollback，非真 git）。

全部互動打 tmp QDRANT_RULEBOOK_FILE — 不碰真 sandbox。
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.agent.rulebook import (
    DEFAULT_BASENAME,
    hint_on_failure,
    load_rulebook,
    parse_rules,
    reset_rulebook_cache,
    rules_add,
    rules_history,
    rules_revise,
    rules_rollback,
)


@pytest.fixture()
def rb_env(tmp_path, monkeypatch):
    """Isolated rulebook: tmp file + clean gates."""
    f = tmp_path / DEFAULT_BASENAME
    monkeypatch.setenv("QDRANT_RULEBOOK_FILE", str(f))
    monkeypatch.delenv("QDRANT_RULEBOOK_ENABLED", raising=False)
    monkeypatch.delenv("QDRANT_RULEBOOK_SUGGEST", raising=False)
    monkeypatch.delenv("QDRANT_RULEBOOK_MAX_CHARS", raising=False)
    monkeypatch.delenv("QDRANT_RULEBOOK_HISTORY_MAX", raising=False)
    monkeypatch.delenv("QDRANT_RULEBOOK_MEMORY", raising=False)
    monkeypatch.setenv("QDRANT_FILESYSTEM_READ_ROOT", str(tmp_path))
    reset_rulebook_cache()
    yield tmp_path
    reset_rulebook_cache()


class TestCore:
    def test_add_creates_and_formats(self, rb_env):
        f = rb_env / DEFAULT_BASENAME
        out = rules_add("x.com 抓法", "web_fetch 打 x.com 會失敗，"
                        "改用 get_page_html",
                        evidence="2026-10-14 web_fetch(x.com) → 404")
        assert out.get("ok") and out["add"] == "r1"
        text = f.read_text(encoding="utf-8")
        assert text.startswith("# Agent Rulebook")
        assert "### [r1] x.com 抓法" in text
        assert "- evidence: 2026-10-14 web_fetch(x.com) → 404" in text
        assert "- updated:  " in text

    def test_add_without_evidence_ok(self, rb_env):
        out = rules_add("t2", "body text")
        assert out.get("ok") and out["add"] == "r1"

    def test_validation_failures(self, rb_env):
        assert "required" in rules_add("", "b")["error"]
        assert "required" in rules_add("t", "")["error"]
        assert "required" in rules_revise("", "x", "good evidence")["error"]

    def test_revise_requires_evidence_fail_closed(self, rb_env):
        rules_add("被修規則", "舊內文")
        bad = rules_revise("r1", "新內文", "!")
        assert not bad.get("ok") and "evidence is REQUIRED" in bad["error"]
        assert "舊內文" in load_rulebook()

    def test_revise_replaces_body_keeps_title(self, rb_env):
        rules_add("被修規則", "舊內文")
        ok = rules_revise("r1", "修訂新內文",
                          evidence="2026-10-15 回執：get_page_html → OK")
        assert ok.get("ok") and ok["revise"] == "r1"
        rules = parse_rules(load_rulebook())
        assert rules[0]["body"] == "修訂新內文"
        assert rules[0]["evidence"].startswith("2026-10-15")
        assert rules[0]["updated"], "updated stamp refresh"

    def test_revise_unknown_id(self, rb_env):
        rules_add("存在", "x")
        out = rules_revise("r99", "n", "evidence present here")
        assert "not found" in out["error"]
        assert "r1" in out["known_ids"]

    def test_id_autoincrements(self, rb_env):
        assert rules_add("a", "1")["add"] == "r1"
        assert rules_add("b", "2")["add"] == "r2"
        assert rules_add("c", "3")["add"] == "r3"

    def test_snapshot_ring_and_rollback(self, rb_env):
        rules_add("規則", "第一版內文")
        rules_revise("r1", "第二版內文", evidence="roll test evidence 1")
        h = rules_history()
        assert h["ok"] and h["snapshots"] == 1
        ro = rules_rollback(1)
        assert ro.get("ok") and ro["steps"] == 1
        assert "第一版內文" in ro["diff"]
        assert "第二版內文" not in load_rulebook()

    def test_rollback_no_snapshots(self, rb_env):
        assert "no snapshots" in rules_rollback(1)["error"]

    def test_history_lists_ring(self, rb_env):
        rules_add("a", "1")
        rules_revise("r1", "2", evidence="history listing evidence 3")
        h = rules_history(limit=5)
        assert len(h["entries"]) >= 2
        assert h["latest_diff"]
    def test_ring_prune(self, rb_env, monkeypatch):
        monkeypatch.setenv("QDRANT_RULEBOOK_HISTORY_MAX", "2")
        rules_add("prune", "v0")
        for i in range(5):
            rules_revise("r1", f"v{i + 1}",
                         evidence=f"ring prune test evidence {i}")
        snaps = list((rb_env / "rulebook_history").glob("*.md"))
        assert len(snaps) == 2, snaps

    def test_cap_truncates_prompt_view(self, rb_env, monkeypatch):
        monkeypatch.setenv("QDRANT_RULEBOOK_MAX_CHARS", "60")
        reset_rulebook_cache()
        rules_add("很長的規則標題", "R" * 200)
        view = load_rulebook()
        assert len(view) < 200 + 200
        assert "rulebook truncated at 60 chars" in view

    def test_enabled_off_refuses(self, rb_env, monkeypatch):
        monkeypatch.setenv("QDRANT_RULEBOOK_ENABLED", "off")
        reset_rulebook_cache()
        assert "disabled" in rules_add("t", "b")["error"]
        assert hint_on_failure() is None

    def test_never_raises_on_broken_file(self, rb_env):
        f = rb_env / DEFAULT_BASENAME
        f.write_bytes(b"\xff\xfe\x00binary")  # errors=replace-safe
        reset_rulebook_cache()
        assert isinstance(load_rulebook(), str)
        assert isinstance(parse_rules(load_rulebook()), list)

    def test_hint_gate(self, rb_env, monkeypatch):
        assert "PREDICTION ERROR" in hint_on_failure()
        monkeypatch.setenv("QDRANT_RULEBOOK_SUGGEST", "off")
        assert hint_on_failure() is None
        monkeypatch.delenv("QDRANT_RULEBOOK_SUGGEST")
        monkeypatch.setenv("QDRANT_RULEBOOK_ENABLED", "off")
        assert hint_on_failure() is None


class TestHostSurface:
    def _host(self):
        from src.mcp_client.host import RulebookLocalHost

        return RulebookLocalHost()

    @staticmethod
    def _payload(res):
        """LocalTool.__call__ returns McpCallResult — unwrap the JSON
        (error dicts arrive as is_error=True; both carry JSON text)."""
        import json as _json

        return _json.loads(res.text)

    def test_tool_names_and_required_fields(self, rb_env):
        h = self._host()
        names = [t.name for t in h.list_tools()]
        assert names == [
            "rules_list", "rules_add", "rules_revise",
            "rules_rollback", "rules_history",
            "toolroute_override_add", "toolroute_override_list",
        ]
        add = next(t for t in h.list_tools() if t.name == "rules_add")
        assert add.input_schema.get("required") == ["title", "text"]
        rev = next(t for t in h.list_tools() if t.name == "rules_revise")
        assert rev.input_schema.get("required") == [
            "rule_id", "text", "evidence",
        ]

    def test_confirm_gate_all_writes(self, rb_env):
        h = self._host()
        tools = {t.name: t for t in h.list_tools()}
        assert self._payload(tools["rules_add"](
            {"title": "t", "text": "b"}))["error"].startswith("confirm=")
        assert self._payload(tools["rules_revise"](
            {"rule_id": "r1", "text": "t",
             "evidence": "ev here"}))["error"].startswith("confirm=")
        assert self._payload(tools["rules_rollback"](
            {}))["error"].startswith("confirm=")

    def test_end_to_end_dispatch(self, rb_env):
        h = self._host()
        tools = {t.name: t for t in h.list_tools()}
        self._payload(tools["rules_add"]({
            "title": "派發測試", "text": "dispatch body",
            "evidence": "dispatch evidence 2026",
            "confirm": True,
        }))
        listed = self._payload(tools["rules_list"]({}))
        assert listed["ok"] and listed["count"] == 1
        assert listed["rules"][0]["title"] == "派發測試"

    def test_list_empty_rulebook(self, rb_env):
        out = self._host()._op_list({})  # direct handler call → dict
        assert out["ok"] and out["count"] == 0
        assert "rules_add starts it" in out["note"]


class TestManagerWiring:
    def test_manager_actually_seeds_the_host(self):
        """Behavioral registration (the v0.9.116.1 NameError lesson —
        not a source grep): from_config(path=bogus) = zero network."""
        from src.mcp_client.manager import McpManager

        mgr = McpManager.from_config(path="C:/definitely/not/here.json")
        names = sorted(t.name for t in mgr.list_tools()
                       if t.server_name == "rulebook-local")
        assert names == ["rules_add", "rules_history", "rules_list",
                         "rules_revise", "rules_rollback",
                         "toolroute_override_add", "toolroute_override_list"]
        assert "rulebook-local" not in (mgr.failed or {})
        assert getattr(mgr, "_rulebook_local_host", None) is not None

    def test_core_surface_membership(self):
        from src.agent import agent as A

        assert "rulebook-local" in A._TOOLROUTE_CORE_SERVERS


class TestPromptInjection:
    def _agent_stub(self):
        from src.agent.agent import Agent

        return Agent.__new__(Agent)

    def test_empty_file_byte_identical(self, rb_env):
        from src.agent.rulebook import reset_rulebook_cache

        reset_rulebook_cache()
        s = self._agent_stub()
        from src.agent.agent import Agent as _A

        p = _A._system_prompt(s, tools_enabled=True)
        assert "AGENT RULEBOOK" not in p

    def test_block_present_both_flavours(self, rb_env):
        rules_add("x.com 要用 viewer", "web_fetch 打 x.com 失敗 → get_page_html",
                  evidence="2026-10-14 回執 404")
        from src.agent.rulebook import reset_rulebook_cache

        reset_rulebook_cache()
        from src.agent.agent import Agent as _A

        s = self._agent_stub()
        p1 = _A._system_prompt(s, tools_enabled=True)
        assert "AGENT RULEBOOK" in p1
        assert "x.com 要用 viewer" in p1
        assert "rules_revise" in p1
        p2 = _A._system_prompt(s, tools_enabled=False)
        assert "AGENT RULEBOOK" in p2


class TestPredictionErrorHint:
    _FakeCall = None

    def _setup(self):
        from src.agent.agent import Agent

        class _FakeRes:
            is_error = True
            text = "tool blew up"

            def to_message_text(self):
                return "tool blew up"

        class _FakeCall:
            def __init__(self):
                self.function = type(
                    "F", (), {"name": "web_search",
                              "arguments": "{}"},
                )()
                self.id = "call-1"

        agent = Agent.__new__(Agent)
        agent._rulebook_hint_shown = False
        fake_mgr = MagicMock()
        fake_mgr.call.return_value = _FakeRes()
        agent._mcp_manager = fake_mgr
        return agent, _FakeCall()

    def test_once_per_turn_then_silent(self, rb_env):
        agent, call = self._setup()
        from src.agent.agent import Agent as _A

        out1 = _A._run_tool_call(agent, call)
        assert out1["content"].count("[rules]") == 1
        out2 = _A._run_tool_call(agent, call)
        assert out2["content"].count("[rules]") == 0

    def test_gate_off_no_hint(self, rb_env, monkeypatch):
        agent, call = self._setup()
        monkeypatch.setenv("QDRANT_RULEBOOK_SUGGEST", "off")
        from src.agent.agent import Agent as _A

        out = _A._run_tool_call(agent, call)
        assert out["content"].count("[rules]") == 0

    def test_success_no_hint(self, rb_env):
        from src.agent.agent import Agent as _A

        agent, _call = self._setup()

        class _OkRes:
            is_error = False
            text = '{"ok": true}'

            def to_message_text(self):
                return '{"ok": true}'

        agent._mcp_manager.call.return_value = _OkRes()
        good = _FakeCall = type("C", (), {
            "function": type("F", (), {
                "name": "web_search", "arguments": "{}"})(),
            "id": "c2",
        })()
        out = _A._run_tool_call(agent, good)
        assert out["content"].count("[rules]") == 0


class TestStdioParity:
    def test_surface_matches(self, rb_env):
        from src.mcp_server.rulebook_server import RulebookMCPServer

        srv = RulebookMCPServer()
        spec_names = [t.name for t in srv._tool_specs()]
        from src.mcp_client.host import RulebookLocalHost

        host_names = [t.name for t in
                      RulebookLocalHost().list_tools()]
        assert spec_names == host_names == [
            "rules_list", "rules_add", "rules_revise",
            "rules_rollback", "rules_history",
            "toolroute_override_add", "toolroute_override_list",
        ]


class TestCliWiring:
    def test_cli_source_guards(self):
        src = Path("cli.py").read_text(encoding="utf-8")
        assert '"rules", parents=[common]' in src
        assert '"rules_cmd"' in src
        assert 'args.command == "rules"' in src
        assert "_handle_rules(args)" in src