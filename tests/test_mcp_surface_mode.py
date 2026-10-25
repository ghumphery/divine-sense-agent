"""Tests for MCP_SURFACE_MODE = full | index (v0.9.81.0) — the
「精簡核心 + 按需展開」(lean core + on-demand expansion) strategy.

* index mode attaches LEAN schemas (parameter keys + types only;
  enums / property descriptions stripped) for all non-exempt
  servers — the initial tool index stays light on context.
* The 擴充層: a FAILED tool call carries the tool's full parameter
  definition (參數定義 hint) appended by manager.call, so the model
  can self-correct without a second discovery round.
* full mode (default) is byte-identical to the legacy surface.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SURFACE_ENV_VARS = ("MCP_SURFACE_MODE",)


@pytest.fixture()
def clean_env(monkeypatch):
    # setenv (not delenv): src.config's load_dotenv may run lazily and
    # re-inject MCP_SURFACE_MODE/MAX_TOOLS/ACTIVE_SERVERS from the live
    # .env — explicit defaults beat dotenv regardless of import order.
    # (v0.9.88.0: MCP_ACTIVE_SERVERS pinned empty too — the live .env
    # gate dropped teams-assist in v0.9.87.2, which made
    # last_hidden_servers=['teams-assist'] leak into the cap tests.)
    monkeypatch.setenv("MCP_SURFACE_MODE", "full")
    monkeypatch.setenv("MCP_SURFACE_MAX_TOOLS", "0")
    monkeypatch.setenv("MCP_ACTIVE_SERVERS", "")
    yield


# ─── resolver + lean schema ───────────────────────────────────────


class TestSurfaceMode:
    def test_resolver_default_full_and_invalid_falls_back(
        self, clean_env, monkeypatch,
    ):
        from src.mcp_client.manager import _surface_mode

        assert _surface_mode() == "full"
        monkeypatch.setenv("MCP_SURFACE_MODE", "index")
        assert _surface_mode() == "index"
        monkeypatch.setenv("MCP_SURFACE_MODE", "bogus")
        assert _surface_mode() == "full"

    def test_lean_schema_strips_details_keeps_types(
        self, clean_env,
    ):
        from src.mcp_client.manager import ToolSpec

        schema = {
            "type": "object",
            "properties": {
                "mode": {"type": "string",
                         "enum": ["mock", "live"],
                         "description": "模式的說明"},
                "n": {"type": "number"},
                "flag": {"description": "no type"},
            },
            "required": ["mode"],
        }
        lean = ToolSpec._lean_schema(schema)
        assert lean["properties"]["mode"] == {"type": "string"}
        assert "enum" not in lean["properties"]["mode"]
        assert "description" not in lean["properties"]["mode"]
        assert lean["properties"]["n"] == {"type": "number"}
        assert lean["properties"]["flag"] == {"type": "string"}
        assert lean["required"] == ["mode"]
        # non-dict schema → empty object
        assert ToolSpec._lean_schema(None) == {
            "type": "object", "properties": {},
        }

    def test_lean_description_truncated_to_60(self, clean_env):
        """v0.9.81.0: index mode shortens the description (first line,
        ≤60 chars) — the full wording stays available on demand."""
        from src.mcp_client.manager import ToolSpec

        long_desc = "第一行很長的說明" * 20 + "\n第二行"
        spec = ToolSpec(
            tool=MagicMock(
                name="t", server_name="s", description=long_desc,
                input_schema={"type": "object", "properties": {}},
            ),
            dispatch=("local", MagicMock()),
            lean=True,
        )
        desc = spec.to_openai()["function"]["description"]
        assert len(desc) == 60
        assert "\n" not in desc
        # full mode keeps the whole description
        spec.lean = False
        assert spec.to_openai()["function"][
            "description"] == long_desc

    def test_full_mode_byte_identical(self, clean_env):
        from src.mcp_client.manager import McpManager

        mgr = McpManager.from_config(path=None)
        specs = mgr.active_specs()
        assert specs and all(s.lean is False for s in specs)
        # a tool WITH an enum keeps it in full mode
        with_enum = [
            s for s in specs
            if any(
                isinstance(p, dict) and p.get("enum")
                for p in (s.tool.input_schema or {}).get(
                    "properties", {}
                ).values()
            )
        ]
        if with_enum:
            s = with_enum[0]
            fn = s.to_openai()["function"]
            props = fn["parameters"]["properties"]
            assert any(
                isinstance(p, dict) and "enum" in p
                for p in props.values()
            )

    def test_index_mode_lean_and_exempt_full(self, clean_env,
                                             monkeypatch):
        from src.mcp_client.manager import (
            _LEAN_EXEMPT_SERVERS, McpManager,
        )

        monkeypatch.setenv("MCP_SURFACE_MODE", "index")
        mgr = McpManager.from_config(path=None)
        specs = mgr.active_specs()
        assert specs, "local-host-only manager must expose tools"
        for s in specs:
            fn = s.to_openai()["function"]
            props = fn["parameters"].get("properties") or {}
            if s.tool.server_name in _LEAN_EXEMPT_SERVERS:
                assert s.lean is False
            else:
                assert s.lean is True
                # every property is a bare {"type": ...}
                for p in props.values():
                    assert set(p.keys()) <= {"type"}
        # the SAME spec list still dispatches end-to-end
        r = mgr.call("get_current_time", {})
        assert not r.is_error


# ─── 擴充層: error carries the full parameter definition ──────────


class TestErrorFeedback:
    def test_failed_call_appends_schema_hint(self, clean_env):
        from src.mcp_client.manager import McpManager

        mgr = McpManager.from_config(path=None)
        r = mgr.call("read_file", {})  # missing path → error envelope
        # the validation-error envelope keeps is_error=False (legacy
        # contract) BUT the hint is still attached (v0.9.81.0)
        assert "'path' is required" in r.text
        assert "參數定義：" in r.text
        # property keys of read_file surface in the hint
        props = {}
        for s in mgr.active_specs():
            if s.tool.name == "read_file":
                props = (s.tool.input_schema or {}).get(
                    "properties", {}
                )
        for key in props:
            assert key in r.text

    def test_successful_call_has_no_hint(self, clean_env):
        from src.mcp_client.manager import McpManager

        mgr = McpManager.from_config(path=None)
        r = mgr.call("get_current_time", {})
        assert not r.is_error
        assert "參數定義：" not in r.text

    def test_hint_not_duplicated(self, clean_env):
        from src.mcp_client import manager as m
        from src.mcp_client.base import McpCallResult

        mgr = m.McpManager.from_config(path=None)
        # fake a tool whose error text already contains the marker
        mgr._tool_index["fake_tool"] = m.ToolSpec(
            tool=MagicMock(
                name="fake_tool", server_name="fake",
                input_schema={"type": "object",
                              "properties": {"a": {"type": "string"}}},
                description="x",
            ),
            dispatch=("local", MagicMock(
                side_effect=lambda args: McpCallResult(
                    text="err（參數定義：a:string）", is_error=True,
                ),
            )),
        )
        r = mgr.call("fake_tool", {})
        assert r.text.count("參數定義：") == 1


# ─── agent integration ────────────────────────────────────────────


class TestAgentSurface:
    def test_chat_tools_honor_index_mode(self, clean_env, monkeypatch):
        from src.mcp_client.manager import McpManager

        monkeypatch.setenv("MCP_SURFACE_MODE", "index")
        mgr = McpManager.from_config(path=None)
        tools = [
            s.to_openai() for s in mgr.active_specs(
                tool_names=["get_current_time"],
            )
        ]
        assert len(tools) == 1
        params = tools[0]["function"]["parameters"]
        # zero-prop schema: nothing to lean, still valid
        assert params.get("type") == "object"


class TestToolCap:
    """v0.9.82.2 — MCP_SURFACE_MAX_TOOLS: providers cap the tools
    array (OpenAI: 128); the surface trims its TAIL and records the
    hidden servers."""

    def test_cap_trims_tail_and_records_hidden(self, clean_env,
                                                monkeypatch):
        from src.mcp_client.manager import McpManager

        monkeypatch.setenv("MCP_SURFACE_MAX_TOOLS", "40")
        mgr = McpManager.from_config(path=None)
        specs = mgr.active_specs()
        assert len(specs) == 40
        names = [s.tool.name for s in specs]
        assert len(set(names)) == 40  # unique
        # last_hidden_servers unions gate-hidden + cap-trimmed — must
        # be non-empty when trimming actually happened
        assert mgr.last_hidden_servers, "trim must record hidden"
        # dispatch still works for a kept tool
        assert not mgr.call("get_current_time", {}).is_error

    def test_cap_zero_is_unlimited(self, clean_env):
        from src.mcp_client.manager import McpManager

        mgr = McpManager.from_config(path=None)
        full = len(mgr.active_specs())
        assert full > 40  # the box really carries 200+ tools
        assert mgr.last_hidden_servers in ([], None)

    def test_cap_invalid_value_ignored(self, clean_env, monkeypatch):
        from src.mcp_client.manager import McpManager

        monkeypatch.setenv("MCP_SURFACE_MAX_TOOLS", "bogus")
        mgr = McpManager.from_config(path=None)
        assert len(mgr.active_specs()) > 40
