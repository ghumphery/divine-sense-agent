"""Tests for v0.9.11.0+ DateTimeLocalHost — the get_current_time
core tool (LOCAL + UTC).

Coverage:

* Tool surface — one zero-arg tool advertised under the
  ``datetime-local`` server name, schema without required keys.
* Handler output — local/UTC ISO strings parse, UTC consistency
  (local rendered back to UTC equals the utc section within a 2 s
  two-clock-read skew), ISO 8601 offset format, weekday consistency,
  epoch close to the wall clock, the ``_fmt_utc_offset`` helper.
* LocalTool round trip — v0.8.6.1 argument coercion (bare empty /
  wrapper key / None) reaches the handler cleanly.
* Manager registration — ``from_config`` registers the tool, keeps
  the host reference, and dispatch reaches the handler end-to-end;
  MetaLocalHost's catalog lists it.
* stdio server — tool specs match the host surface; the handler
  path returns JSON text content.
* System prompt — ``get_current_time`` mentioned in tools mode
  (with zh trigger words), absent in legacy mode.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_WEEKDAYS = {
    "Monday": 1, "Tuesday": 2, "Wednesday": 3, "Thursday": 4,
    "Friday": 5, "Saturday": 6, "Sunday": 7,
}


@pytest.fixture()
def roots(tmp_path, monkeypatch):
    """Pin the filesystem sandboxes at a per-test tmp dir so the
    from_config local-host seeding never touches the real cwd."""
    r = tmp_path / "read"
    r.mkdir()
    w = tmp_path / "write"
    w.mkdir()
    monkeypatch.setenv("QDRANT_FILESYSTEM_READ_ROOT", str(r))
    monkeypatch.setenv("QDRANT_FILESYSTEM_WRITE_ROOT", str(w))
    monkeypatch.setenv("WATCH_DIR", str(tmp_path / "watch"))
    return tmp_path


# ─── tool surface ────────────────────────────────────────────


class TestHostSurface:
    def test_one_tool_advertised(self):
        from src.mcp_client.host import DateTimeLocalHost

        host = DateTimeLocalHost()
        tools = host.list_tools()
        assert len(tools) == 1
        tool = tools[0]
        assert tool.name == "get_current_time"
        assert tool.server_name == "datetime-local"
        assert tool.input_schema["type"] == "object"
        # zero-argument tool — no required keys in the schema
        assert not tool.input_schema.get("required")
        assert "time" in tool.description.lower()

    def test_server_name_constant(self):
        from src.mcp_client.host import DateTimeLocalHost

        assert DateTimeLocalHost.SERVER_NAME == "datetime-local"


# ─── handler output ──────────────────────────────────────────


class TestHandlerOutput:
    def _now(self):
        from src.mcp_client.host import DateTimeLocalHost

        return DateTimeLocalHost()._now({})

    def test_sections_present(self):
        out = self._now()
        assert set(out) >= {"local", "utc", "epoch_seconds", "note"}
        for section in ("local", "utc"):
            keys = out[section]
            assert keys["weekday_en"] in _WEEKDAYS
            assert keys["isoweekday"] == _WEEKDAYS[keys["weekday_en"]]
            # structured date/time fields agree with the date string
            assert keys["date"] == (
                f"{keys['year']:04d}-{keys['month']:02d}-{keys['day']:02d}"
            )
            assert keys["time"] == (
                f"{keys['hour']:02d}:{keys['minute']:02d}"
                f":{keys['second']:02d}"
            )

    def test_iso_strings_parse_and_utc_consistency(self):
        out = self._now()
        local = datetime.fromisoformat(out["local"]["iso"])
        utc = datetime.fromisoformat(out["utc"]["iso"])
        # the utc section is pinned to UTC+0
        assert utc.utcoffset() == timedelta(0)
        assert out["utc"]["utc_offset"] == "+00:00"
        # local rendered back to UTC must equal the utc section —
        # two separate now() calls → allow ≤ 2 s skew
        skew = abs((local.astimezone(timezone.utc) - utc).total_seconds())
        assert skew <= 2
        # the offset text matches the parsed ISO offset
        assert out["local"]["utc_offset"] in local.isoformat()

    def test_utc_offset_format(self):
        out = self._now()
        assert re.fullmatch(
            r"[+-]\d{2}:\d{2}", out["local"]["utc_offset"]
        )
        assert out["utc"]["utc_offset"] == "+00:00"

    def test_epoch_close_to_wall_clock(self):
        out = self._now()
        assert abs(out["epoch_seconds"] - time.time()) <= 5

    def test_offset_helper(self):
        from src.mcp_client.host import _fmt_utc_offset

        assert _fmt_utc_offset(None) == "+00:00"
        assert _fmt_utc_offset(timedelta(0)) == "+00:00"
        assert _fmt_utc_offset(timedelta(hours=8)) == "+08:00"
        assert _fmt_utc_offset(timedelta(hours=5, minutes=30)) == "+05:30"
        assert _fmt_utc_offset(
            timedelta(hours=-5, minutes=-30)
        ) == "-05:30"

# ─── LocalTool argument-coercion round trip ──────────────────


class TestLocalToolRoundTrip:
    def test_bare_empty_args(self):
        from src.mcp_client.host import DateTimeLocalHost

        tool = DateTimeLocalHost().list_tools()[0]
        result = tool({})
        assert not result.is_error
        payload = json.loads(result.text)
        assert "local" in payload and "utc" in payload

    def test_wrapper_key_args(self):
        """v0.8.6.1 coercion — the {'args': {...}} wrapper shape some
        providers emit must reach the handler, not crash it."""
        from src.mcp_client.host import DateTimeLocalHost

        tool = DateTimeLocalHost().list_tools()[0]
        result = tool({"args": {}})
        assert not result.is_error

    def test_none_arguments(self):
        from src.mcp_client.host import DateTimeLocalHost

        tool = DateTimeLocalHost().list_tools()[0]
        result = tool(None)
        assert not result.is_error


# ─── manager registration ────────────────────────────────────


class TestManagerRegistration:
    def test_from_config_registers_datetime_tool(self, roots):
        from src.mcp_client.manager import McpManager

        mgr = McpManager.from_config(path=None, include_local_hosts=True)
        names = {t.name for t in mgr.list_tools()}
        assert "get_current_time" in names
        assert mgr._datetime_local_host is not None
        assert "datetime-local" not in mgr.failed

    def test_dispatch_reaches_host(self, roots):
        from src.mcp_client.manager import McpManager

        mgr = McpManager.from_config(path=None, include_local_hosts=True)
        result = mgr.call("get_current_time", {})
        assert not result.is_error
        payload = json.loads(result.text)
        assert "local" in payload and "utc" in payload

    def test_meta_catalog_lists_datetime_tool(self, roots):
        from src.mcp_client.host import MetaLocalHost
        from src.mcp_client.manager import McpManager

        mgr = McpManager.from_config(path=None, include_local_hosts=True)
        meta = MetaLocalHost()
        meta.attach_manager(mgr)
        result = meta._list()
        assert "get_current_time" in json.dumps(result)


# ─── stdio server ────────────────────────────────────────────


class TestStdioServer:
    def test_tool_specs_match_host_surface(self):
        from src.mcp_client.host import DateTimeLocalHost
        from src.mcp_server.time_server import _build_tool_specs

        spec_names = {s.name for s in _build_tool_specs()}
        host_names = {t.name for t in DateTimeLocalHost().list_tools()}
        assert spec_names == host_names

    def test_specs_have_no_required_params(self):
        from src.mcp_server.time_server import _build_tool_specs

        for spec in _build_tool_specs():
            assert not spec.inputSchema.get("required")

    def test_server_handler_path(self):
        from src.mcp_server.time_server import TimeMCPServer

        server = TimeMCPServer()
        assert len(server._tool_specs()) == 1
        blocks = server._do_now({})
        payload = json.loads(blocks[0].text)
        assert "local" in payload and "utc" in payload


# ─── system prompt ───────────────────────────────────────────


class TestSystemPrompt:
    def _agent(self):
        from src.agent.agent import Agent

        a = Agent()
        a._client = MagicMock()
        a._model = "mock-model"
        a._provider = "mock"
        return a

    def test_tools_mode_mentions_get_current_time(self):
        a = self._agent()
        prompt = a._system_prompt(tools_enabled=True)
        assert "get_current_time" in prompt
        assert "現在幾點" in prompt  # zh trigger words wired

    def test_legacy_mode_excluded(self):
        a = self._agent()
        prompt = a._system_prompt(tools_enabled=False)
        assert "get_current_time" not in prompt
