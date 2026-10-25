"""v0.9.104.3 — the ABSOLUTE per-tool-result cap + the「on」alias.

Live incident (2026-10-01 17:17): the firefox get_page_html tool
returned a full Gmail HTML page (~6 MB) → the tool message rode INLINE
into the prompt at 1,751,656 tokens → OpenRouter 404 (context filter),
ollama 400 (prompt too long), openai-compat timeout → the whole turn
collapsed (157 s, no email sent).

Two fixes under test:
1. ``QDRANT_TOOL_RESULT_MAX_CHARS`` — an ABSOLUTE cap enforced in
   ``_run_tool_call`` REGARDLESS of the compressor (the offload is
   best-effort; a failed write can no longer blow the context).
   0 = unlimited (MCP_SURFACE_MAX_TOOLS convention).
2. ``CHAT_CONTEXT_COMPRESSION=on`` — the value the live .env has used
   since v0.9.82.5 — is now an alias of ``auto`` instead of falling
   back to a silent ``off`` (which is exactly why the offload missed).
All mocks — no network, no real tools.
"""
from __future__ import annotations

import json
import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))


@pytest.fixture()
def _env(monkeypatch, tmp_path):
    read_root = tmp_path / "read"
    read_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("QDRANT_FILESYSTEM_READ_ROOT", str(read_root))
    monkeypatch.chdir(tmp_path)
    for var in ("QDRANT_TOOL_RESULT_MAX_CHARS",
                "CHAT_CONTEXT_COMPRESSION",
                "CHAT_OFFLOAD_THRESHOLD_CHARS",
                "QDRANT_OFFLOAD_DIR"):
        monkeypatch.delenv(var, raising=False)
    yield tmp_path


def _agent(monkeypatch):
    from src.agent.agent import Agent
    from src.agent.context_compress import ContextCompressor
    from src.mcp_client.base import McpCallResult

    a = Agent.__new__(Agent)
    # mcp_manager is a no-setter PROPERTY over the `_mcp_manager`
    # backing attr — inject the mock there (test_toolroute 同款).
    a._mcp_manager = MagicMock()
    a.compressor = ContextCompressor()
    a._turn_result_refs = {}
    a.last_tool_dispatches = []
    payloads = {}

    def _call(name, args, **_):
        return McpCallResult(text=payloads["text"],
                             is_error=payloads.get("err", False))

    a.mcp_manager.call = _call
    monkeypatch.setattr(
        "src.agent.agent._distinctive_tokens", lambda *_: [])
    a.payloads = payloads
    return a


def _call_obj(name="get_page_html", args=None):
    return SimpleNamespace(
        id="call-1",
        function=SimpleNamespace(
            name=name, arguments=json.dumps(args or {"url": "x"})))


class TestCapResolver:
    def test_default_51200(self, _env):
        from src.agent.agent import _tool_result_cap

        assert _tool_result_cap() == 51200

    def test_env_override_and_clamps(self, _env, monkeypatch):
        from src.agent.agent import _tool_result_cap

        monkeypatch.setenv("QDRANT_TOOL_RESULT_MAX_CHARS", "1024")
        assert _tool_result_cap() == 1024
        monkeypatch.setenv("QDRANT_TOOL_RESULT_MAX_CHARS", "bogus")
        assert _tool_result_cap() == 51200
        monkeypatch.setenv("QDRANT_TOOL_RESULT_MAX_CHARS", "-5")
        assert _tool_result_cap() == 51200

    def test_zero_means_unlimited(self, _env, monkeypatch):
        from src.agent.agent import _tool_result_cap

        monkeypatch.setenv("QDRANT_TOOL_RESULT_MAX_CHARS", "0")
        assert _tool_result_cap() == 0


class TestRunToolCallCap:
    def test_oversized_result_truncated_with_notice(self, _env,
                                                    monkeypatch):
        from src.agent.agent import _TOOL_RESULT_CAP_DEFAULT

        a = _agent(monkeypatch)
        a.payloads["text"] = "X" * 60000
        msg = a._run_tool_call(_call_obj())
        assert msg["role"] == "tool"
        body = msg["content"]
        assert len(body) < 60000
        assert body.startswith("X")
        assert "tool result truncated" in body
        assert str(_TOOL_RESULT_CAP_DEFAULT) in body
        assert "read_file" in body  # the pointer tells the model how

    def test_small_result_untouched(self, _env, monkeypatch):
        a = _agent(monkeypatch)
        a.payloads["text"] = "hello world"
        msg = a._run_tool_call(_call_obj())
        assert msg["content"] == "hello world"

    def test_env_cap_enforced(self, _env, monkeypatch):
        monkeypatch.setenv("QDRANT_TOOL_RESULT_MAX_CHARS", "100")
        a = _agent(monkeypatch)
        a.payloads["text"] = "A" * 5000
        msg = a._run_tool_call(_call_obj())
        body = msg["content"]
        assert body.startswith("A" * 100)
        assert "truncated: 4900 chars" in body

    def test_zero_env_means_no_cap(self, _env, monkeypatch):
        monkeypatch.setenv("QDRANT_TOOL_RESULT_MAX_CHARS", "0")
        a = _agent(monkeypatch)
        a.payloads["text"] = "B" * 20000
        msg = a._run_tool_call(_call_obj())
        assert len(msg["content"]) == 20000

    def test_error_results_also_capped(self, _env, monkeypatch):
        """The cap protects ANY result shape — even a (rare) oversized
        error text is a context bomb; normal errors (a line or two)
        are far below the cap and stay verbatim."""
        a = _agent(monkeypatch)
        a.payloads["text"] = "E" * 60000
        a.payloads["err"] = True
        msg = a._run_tool_call(_call_obj())
        body = msg["content"]
        assert body.startswith("[tool error]")
        assert "tool result truncated" in body
        # small errors stay verbatim (no truncation marker on them)
        a.payloads["text"] = "boom"
        msg2 = a._run_tool_call(_call_obj())
        assert msg2["content"] == "[tool error] boom"


class TestOnAlias:
    def test_on_now_means_auto(self, _env, monkeypatch):
        from src.agent.context_compress import ContextCompressor

        for v in ("on", "yes", "true", "1", "ON"):
            monkeypatch.setenv("CHAT_CONTEXT_COMPRESSION", v)
            assert ContextCompressor._cfg().mode == "auto", v

    def test_off_and_bogus_stay_off(self, _env, monkeypatch):
        from src.agent.context_compress import ContextCompressor

        monkeypatch.setenv("CHAT_CONTEXT_COMPRESSION", "off")
        assert ContextCompressor._cfg().mode == "off"
        monkeypatch.setenv("CHAT_CONTEXT_COMPRESSION", "maybe")
        assert ContextCompressor._cfg().mode == "off"

    def test_on_alias_enables_offload(self, _env, monkeypatch):
        """The live-incident shape: CHAT_CONTEXT_COMPRESSION=on + a fat
        get_page_html result → OFFLOAD fires (pointer + preview), NOT
        a 1.7M-token inline blob."""
        monkeypatch.setenv("CHAT_CONTEXT_COMPRESSION", "on")
        monkeypatch.setenv("CHAT_OFFLOAD_THRESHOLD_CHARS", "500")
        a = _agent(monkeypatch)
        a.payloads["text"] = "<html>" + "Q" * 5000 + "</html>"
        msg = a._run_tool_call(_call_obj())
        body = msg["content"]
        assert "[offloaded]" in body           # pointer + preview
        assert "read_file" in body
        assert len(body) < 1500                # NOT the 5000+ blob
        # the full text landed in the read sandbox
        d = _env / "read" / "chat_offloads"
        files = list(d.glob("*.txt"))
        assert files and files[0].read_text(
            encoding="utf-8").startswith("<html>")

    def test_off_mode_still_hard_capped(self, _env, monkeypatch):
        """Compressor off → no offload — but the ABSOLUTE cap still
        protects (defense in depth)."""
        monkeypatch.setenv("CHAT_CONTEXT_COMPRESSION", "off")
        monkeypatch.setenv("QDRANT_TOOL_RESULT_MAX_CHARS", "300")
        a = _agent(monkeypatch)
        a.payloads["text"] = "<html>" + "Z" * 3000 + "</html>"
        msg = a._run_tool_call(_call_obj())
        body = msg["content"]
        assert "[offloaded]" not in body
        assert "tool result truncated" in body
        assert len(body) < 600