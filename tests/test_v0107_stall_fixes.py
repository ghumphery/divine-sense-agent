# -*- coding: utf-8 -*-
"""v0.9.107.0 — stall-fix suite.

Covers the three root-cause fixes for the 2026-10-03/04 telegram
stalls (read_file x62/x118 storms -> max_iterations=50, plus the
~58-minute silent hang on one never-returning tool call):

1. read_file/read_multiple are EXEMPT from context offloading
   (the pointer-chain onion is structurally impossible now).
2. Per-tool-call watchdog: hung dispatch -> normal tool error.
3. _fs_walk never descends into _FS_SKIP_DIRS (.cache/node_modules/…).
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from src.mcp_client.base import McpCallResult


# ─── 1. offload exemption ──────────────────────────────────────────

class TestOffloadExempt:
    @pytest.fixture()
    def compressor(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CHAT_CONTEXT_COMPRESSION", "auto")
        monkeypatch.setenv("CHAT_OFFLOAD_THRESHOLD_CHARS", "500")
        monkeypatch.setenv("QDRANT_OFFLOAD_DIR", str(tmp_path / "od"))
        from src.agent.context_compress import ContextCompressor

        return ContextCompressor()

    def test_read_file_big_result_never_offloaded(self, compressor):
        big = "X" * 3000
        inline, path = compressor.offload_tool_result("read_file", big)
        assert path is None
        assert inline == big

    def test_read_multiple_exempt_too(self, compressor):
        big = "Y" * 3000
        inline, path = compressor.offload_tool_result("read_multiple", big)
        assert path is None
        assert inline == big

    def test_other_tools_still_offload(self, compressor):
        big = "Z" * 3000
        inline, path = compressor.offload_tool_result("web_search", big)
        assert path is not None
        assert "[offloaded]" in inline
        assert Path(path).exists()

    def test_offload_file_content_is_verbatim(self, compressor):
        # the saved file must be the RAW tool result — NOT another
        # envelope wrapped around it (the old onion shape).
        big = "payload-body-" + "Q" * 3000
        _inline, path = compressor.offload_tool_result("run_python", big)
        assert path is not None
        assert Path(path).read_text(encoding="utf-8") == big


# ─── 2. per-tool-call watchdog ─────────────────────────────────────

class _SlowMgr:
    def __init__(self, delay: float, result=None, exc: Exception | None = None):
        self.delay = delay
        self.result = result
        self.exc = exc

    def call(self, name, args):
        time.sleep(self.delay)
        if self.exc is not None:
            raise self.exc
        return self.result


def _bare_agent(mgr):
    import src.agent.agent as A

    agent = object.__new__(A.Agent)
    agent._mcp_manager = mgr
    return agent


class TestToolWatchdog:
    def test_hung_call_returns_watchdog_error(self, monkeypatch):
        monkeypatch.setenv("QDRANT_TOOL_CALL_TIMEOUT_S", "1")
        a = _bare_agent(_SlowMgr(5.0, McpCallResult(text="ok")))
        t0 = time.monotonic()
        r = a._dispatch_with_watchdog("sleepy_tool", {})
        took = time.monotonic() - t0
        assert isinstance(r, McpCallResult)
        assert r.is_error
        assert "watchdog" in r.text.lower()
        assert took < 4.0  # not 5s — the watchdog fired first

    def test_timeout_zero_is_legacy_passthrough(self, monkeypatch):
        monkeypatch.setenv("QDRANT_TOOL_CALL_TIMEOUT_S", "0")
        ok = McpCallResult(text="fine")
        a = _bare_agent(_SlowMgr(0.05, ok))
        assert a._dispatch_with_watchdog("t", {}) is ok

    def test_exception_propagates(self, monkeypatch):
        monkeypatch.setenv("QDRANT_TOOL_CALL_TIMEOUT_S", "5")
        a = _bare_agent(_SlowMgr(0.01, exc=ValueError("boom")))
        with pytest.raises(ValueError):
            a._dispatch_with_watchdog("t", {})

    def test_fast_call_within_budget_unchanged(self, monkeypatch):
        monkeypatch.setenv("QDRANT_TOOL_CALL_TIMEOUT_S", "10")
        ok = McpCallResult(text="quick")
        a = _bare_agent(_SlowMgr(0.05, ok))
        r = a._dispatch_with_watchdog("t", {})
        assert r is ok and not r.is_error


class TestTimeoutResolver:
    def test_unset_defaults_off(self, monkeypatch):
        monkeypatch.delenv("QDRANT_TOOL_CALL_TIMEOUT_S", raising=False)
        from src.agent.agent import _tool_call_timeout_s

        assert _tool_call_timeout_s() == 0

    @pytest.mark.parametrize("bad", ["abc", "-3", " "])
    def test_invalid_falls_back_off(self, monkeypatch, bad):
        monkeypatch.setenv("QDRANT_TOOL_CALL_TIMEOUT_S", bad)
        from src.agent.agent import _tool_call_timeout_s

        assert _tool_call_timeout_s() == 0

    def test_positive_value_honoured(self, monkeypatch):
        monkeypatch.setenv("QDRANT_TOOL_CALL_TIMEOUT_S", "45")
        from src.agent.agent import _tool_call_timeout_s

        assert _tool_call_timeout_s() == 45


# ─── 3. _fs_walk exclusions ────────────────────────────────────────

class TestFsWalkSkips:
    @pytest.fixture()
    def tree(self, tmp_path: Path) -> Path:
        base = tmp_path / "root"
        (base / "node_modules" / "pkg").mkdir(parents=True)
        (base / ".cache" / "huggingface").mkdir(parents=True)
        (base / "sub").mkdir()
        (base / "a.txt").write_text("a", encoding="utf-8")
        (base / "node_modules" / "x.js").write_text("x", encoding="utf-8")
        (base / ".cache" / "y.bin").write_text("y", encoding="utf-8")
        (base / "sub" / "z.txt").write_text("z", encoding="utf-8")
        return base

    def test_files_under_skip_dirs_never_collected(self, tree):
        from src.mcp_client.host import _fs_walk

        paths, _trunc = _fs_walk(
            tree, max_entries=500, max_depth=9,
            include_dirs=False, include_files=True,
        )
        rels = {p.relative_to(tree).as_posix() for p in paths}
        assert rels == {"a.txt", "sub/z.txt"}

    def test_skip_dirs_listed_but_not_descended(self, tree):
        from src.mcp_client.host import _fs_walk

        paths, _trunc = _fs_walk(
            tree, max_entries=500, max_depth=9,
            include_dirs=True, include_files=True,
        )
        names = {p.name for p in paths}
        assert "node_modules" in names and ".cache" in names
        assert "x.js" not in names and "y.bin" not in names

    def test_search_files_end_to_end_skips(self, tree, monkeypatch):
        # through the LocalHost tool: search_files must not return
        # matches from inside skip dirs.
        monkeypatch.setenv("QDRANT_FILESYSTEM_READ_ROOT", str(tree))
        from src.mcp_client.host import FilesystemLocalHost

        host = FilesystemLocalHost()
        out = host._search_files({
            "path": str(tree), "pattern": "*", "recursive": True,
            "max_results": 500,
        })
        matched = {Path(m["path"]).name for m in out["matches"]}
        assert "x.js" not in matched and "y.bin" not in matched
        assert "a.txt" in matched and "z.txt" in matched
