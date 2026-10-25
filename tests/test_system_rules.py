"""v0.9.104.6 — user-importable system rules tests.

* ``load_system_rules`` — default path (READ sandbox), env override,
  mtime hot-reload cache, cap truncation, never-raises.
* ``Agent._system_prompt`` — the USER SYSTEM RULES block appears in
  BOTH flavours (tools + legacy) and composes with the language
  directive; absent when no file.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.agent.system_rules import (  # noqa: E402
    DEFAULT_BASENAME,
    load_system_rules,
    reset_rules_cache,
)


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    monkeypatch.setenv("QDRANT_FILESYSTEM_READ_ROOT", str(tmp_path))
    monkeypatch.delenv("QDRANT_SYSTEM_RULES_FILE", raising=False)
    monkeypatch.delenv("QDRANT_SYSTEM_RULES_MAX_CHARS", raising=False)
    reset_rules_cache()
    yield
    reset_rules_cache()


def _agent():
    from src.agent.agent import Agent
    return Agent.__new__(Agent)


class TestLoadSystemRules:
    def test_default_path_in_read_sandbox(self, tmp_path):
        f = tmp_path / DEFAULT_BASENAME
        f.write_text("永遠用繁體中文回答", encoding="utf-8")
        assert load_system_rules() == "永遠用繁體中文回答"

    def test_no_file_means_empty(self, tmp_path):
        assert load_system_rules() == ""

    def test_env_override_path(self, tmp_path, monkeypatch):
        other = tmp_path / "my" / "rules.md"
        other.parent.mkdir()
        other.write_text("規則 A", encoding="utf-8")
        monkeypatch.setenv("QDRANT_SYSTEM_RULES_FILE", str(other))
        assert load_system_rules() == "規則 A"

    def test_tilde_expansion(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        home.mkdir()
        f = home / "r.md"
        f.write_text("tilde rules", encoding="utf-8")
        monkeypatch.setenv("QDRANT_SYSTEM_RULES_FILE", "~/r.md")
        monkeypatch.setenv("HOME", str(home))       # posix
        monkeypatch.setenv("USERPROFILE", str(home))  # windows
        assert load_system_rules() == "tilde rules"

    def test_mtime_hot_reload(self, tmp_path):
        f = tmp_path / DEFAULT_BASENAME
        f.write_text("v1", encoding="utf-8")
        assert load_system_rules() == "v1"
        # write v2 FIRST, then bump mtime (+10s) — write_text itself
        # resets mtime to "now", which on a fast box can alias the
        # cached value (same-second).
        import os
        f.write_text("v2", encoding="utf-8")
        st = os.stat(f)
        os.utime(f, (st.st_atime, st.st_mtime + 10))
        assert load_system_rules() == "v2"

    def test_cap_truncates(self, tmp_path, monkeypatch):
        monkeypatch.setenv("QDRANT_SYSTEM_RULES_MAX_CHARS", "50")
        f = tmp_path / DEFAULT_BASENAME
        f.write_text("R" * 500, encoding="utf-8")
        out = load_system_rules()
        assert len(out) < 500 + 200  # cap + notice
        assert "truncated at 50 chars" in out
        assert out.startswith("R" * 50)

    def test_directory_never_raises(self, tmp_path):
        d = tmp_path / DEFAULT_BASENAME
        d.mkdir()
        assert load_system_rules() == ""

    def test_env_pointing_at_directory_never_raises(
        self, tmp_path, monkeypatch,
    ):
        d = tmp_path / "adir"
        d.mkdir()
        monkeypatch.setenv("QDRANT_SYSTEM_RULES_FILE", str(d))
        assert load_system_rules() == ""


class TestPromptIntegration:
    def test_block_in_tools_prompt(self, tmp_path):
        (tmp_path / DEFAULT_BASENAME).write_text(
            "自訂規則一號", encoding="utf-8")
        p = _agent()._system_prompt(tools_enabled=True)
        assert "USER SYSTEM RULES" in p
        assert "自訂規則一號" in p

    def test_block_in_legacy_prompt(self, tmp_path):
        (tmp_path / DEFAULT_BASENAME).write_text(
            "legacy rule", encoding="utf-8")
        p = _agent()._system_prompt(tools_enabled=False)
        assert "USER SYSTEM RULES" in p
        assert "legacy rule" in p

    def test_absent_without_file(self):
        p = _agent()._system_prompt(tools_enabled=True)
        assert "USER SYSTEM RULES" not in p

    def test_composes_with_language_directive(self, tmp_path):
        (tmp_path / DEFAULT_BASENAME).write_text(
            "規則", encoding="utf-8")
        p = _agent()._system_prompt(
            tools_enabled=True, response_language="zh-TW")
        assert "USER SYSTEM RULES" in p
        assert "LANGUAGE DIRECTIVE" in p
        assert "規則" in p

    def test_rules_after_core_security_rules(self, tmp_path):
        (tmp_path / DEFAULT_BASENAME).write_text(
            "使用者規則", encoding="utf-8")
        p = _agent()._system_prompt(tools_enabled=True)
        # the never-fake security rule stays authoritative (earlier)
        assert p.index("NEVER fake a tool call") < p.index("使用者規則")