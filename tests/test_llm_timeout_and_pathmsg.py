"""Tests for the v0.9.63.1 stuck-turn hotfix.

1. ``QDRANT_LLM_TIMEOUT_S`` — the per-request ceiling on the chat /
   vision OpenAI clients (primary + failover legs). Live diagnosis
   2026-09-16 10:08: a telegram file turn whose prompt carried a huge
   vault read-back hung 8+ minutes with NO timeout (SDK default
   600 s/attempt × built-in retries).
2. ``_fs_resolve_path(must_be_file)`` message split — a MISSING path
   must say ``path not found: <posix>`` (the old message「is not a
   regular file.」conflated nonexistent with existing-but-special and
   sent the LLM down workaround paths instead of fixing the path).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.agent.agent import _LLM_TIMEOUT_DEFAULT_S, _llm_timeout_s
from src.mcp_client.host import _fs_resolve_path


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("QDRANT_LLM_TIMEOUT_S", raising=False)


class TestLlmTimeoutResolver:
    def test_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("QDRANT_LLM_TIMEOUT_S", raising=False)
        assert _llm_timeout_s() == _LLM_TIMEOUT_DEFAULT_S == 300.0

    def test_valid_env_value_honored(self, monkeypatch):
        monkeypatch.setenv("QDRANT_LLM_TIMEOUT_S", "45.5")
        assert _llm_timeout_s() == pytest.approx(45.5)

    def test_invalid_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("QDRANT_LLM_TIMEOUT_S", "abc")
        assert _llm_timeout_s() == 300.0

    def test_below_floor_clamped_up(self, monkeypatch):
        monkeypatch.setenv("QDRANT_LLM_TIMEOUT_S", "5")
        assert _llm_timeout_s() == 30.0

    def test_above_ceiling_clamped_down(self, monkeypatch):
        monkeypatch.setenv("QDRANT_LLM_TIMEOUT_S", "99999")
        assert _llm_timeout_s() == 1200.0

    def test_whitespace_stripped(self, monkeypatch):
        monkeypatch.setenv("QDRANT_LLM_TIMEOUT_S", " 90 ")
        assert _llm_timeout_s() == 90.0


class TestClientConstructionSourceGuard:
    def test_primary_client_carries_timeout(self):
        src = Path("src/agent/agent.py").read_text(encoding="utf-8")
        assert "timeout=_llm_timeout_s()," in src
        # both construction sites (primary + failover) pass it
        assert src.count("timeout=_llm_timeout_s()") == 2

    def test_failover_candidates_carry_timeout(self):
        """Source guard: the failover-walk construction site passes
        the same timeout."""
        src = Path("src/agent/agent.py").read_text(encoding="utf-8")
        marker = src[src.index("out.append((name, model, OpenAI("):]
        marker = marker[: marker.index("))") + 2]
        assert "timeout=_llm_timeout_s()" in marker


class TestFsResolvePathMessages:
    def _read_root(self, tmp_path: Path) -> Path:
        root = tmp_path / "sandbox"
        root.mkdir(exist_ok=True)
        return root

    def test_missing_relative_path_says_not_found(self, tmp_path):
        root = self._read_root(tmp_path)
        with pytest.raises(ValueError) as exc:
            _fs_resolve_path(
                "ghost.md", root, allow_relative=True, must_be_file=True,
            )
        assert "path not found" in str(exc.value)
        assert "not a regular file" not in str(exc.value)

    def test_missing_absolute_path_says_not_found(self, tmp_path):
        root = self._read_root(tmp_path)
        ghost = root / "nope.md"
        with pytest.raises(ValueError) as exc:
            _fs_resolve_path(
                ghost.as_posix(), root,
                allow_relative=True, must_be_file=True,
            )
        assert "path not found" in str(exc.value)
        assert ghost.as_posix() in str(exc.value)

    def test_directory_keeps_not_a_regular_file(self, tmp_path):
        root = self._read_root(tmp_path)
        with pytest.raises(ValueError) as exc:
            _fs_resolve_path(
                root.as_posix(), root,
                allow_relative=True, must_be_file=True,
            )
        assert "not a regular file" in str(exc.value)

    def test_must_exist_missing_raises_filenotfound(self, tmp_path):
        root = self._read_root(tmp_path)
        with pytest.raises(FileNotFoundError) as exc:
            _fs_resolve_path(
                "ghost.md", root,
                allow_relative=True, must_exist=True, must_be_file=True,
            )
        assert "path not found" in str(exc.value)

    def test_must_be_dir_missing_message(self, tmp_path):
        root = self._read_root(tmp_path)
        with pytest.raises(ValueError) as exc:
            _fs_resolve_path(
                "ghostdir", root,
                allow_relative=True, must_be_dir=True,
            )
        assert "is not a directory" in str(exc.value)