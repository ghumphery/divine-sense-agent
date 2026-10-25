"""Tests for v0.9.14.0+ — the chat-UI MCP tool picker.

Chain under test:

* ``gui/api.py::chat_turn`` forwards the picker's ``active_tools``
  list to ``Agent.chat_turn`` as ``active_tool_names`` (None when
  the picker selection is empty = everything enabled).
* ``Agent.chat_turn`` / ``Agent._chat_tools`` pass the per-TOOL
  allow-list down to ``McpManager.active_specs(tool_names)``.
* Source guards: index.html carries the picker panel, app.js wires
  the picker (localStorage-disabled-set semantics), style.css
  styles it, and all four locales carry the labels.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class TestGuiBridge:
    def test_chat_turn_forwards_active_tools(self):
        from gui.api import Api

        api = Api.__new__(Api)
        agent = MagicMock()
        agent.chat_turn.return_value = ("ans", "ctx", [])
        api._get_agent = lambda: agent
        api.chat_turn(
            "q", history=[], web_search=False,
            active_tools=["get_current_time"],
        )
        kwargs = agent.chat_turn.call_args.kwargs
        assert kwargs["active_tool_names"] == ["get_current_time"]

    def test_chat_turn_default_is_none(self):
        """No picker selection → active_tool_names None → the env
        gate (MCP_ACTIVE_SERVERS) applies."""
        from gui.api import Api

        api = Api.__new__(Api)
        agent = MagicMock()
        agent.chat_turn.return_value = ("ans", "ctx", [])
        api._get_agent = lambda: agent
        api.chat_turn("q", history=[], web_search=False)
        kwargs = agent.chat_turn.call_args.kwargs
        assert kwargs["active_tool_names"] is None


class TestAgentPassThrough:
    def test_chat_tools_passes_tool_names(self):
        from src.agent.agent import Agent

        a = Agent()
        mgr = MagicMock()
        mgr.active_specs.return_value = []
        a._mcp_manager = mgr
        a._chat_tools(["get_current_time"])
        mgr.active_specs.assert_called_once_with(["get_current_time"])

    def test_chat_tools_none_means_env_gate(self):
        from src.agent.agent import Agent

        a = Agent()
        mgr = MagicMock()
        mgr.active_specs.return_value = []
        a._mcp_manager = mgr
        a._chat_tools(None)
        mgr.active_specs.assert_called_once_with(None)


# ─── source guards (frontend wiring) ─────────────────────────


def _read(name: str) -> str:
    return (Path(__file__).resolve().parents[1]
            / "gui" / "assets" / name).read_text(encoding="utf-8")


class TestFrontendWiring:
    def test_index_html_has_picker(self):
        html = _read("index.html")
        assert 'id="chat-tools-panel"' in html
        assert 'id="chat-tools-btn"' in html
        assert 'id="chat-tools-list"' in html

    def test_app_js_picker_functions(self):
        js = _read("app.js")
        for name in (
            "toggleChatToolsPanel", "renderChatToolsPanel",
            "effectiveChatTools", "updateChatToolsBadge",
        ):
            assert f"function {name}(" in js, f"{name} missing"

    def test_app_js_disabled_set_persists(self):
        js = _read("app.js")
        assert "chat-disabled-tools" in js

    def test_app_js_boot_wipes_stale_disabled_set(self):
        """v0.9.83.8 — the boot migration wipes any stale persistent
        set once (the .83.7 root cause: it rode on every request)."""
        js = _read("app.js")
        assert "localStorage.removeItem('chat-disabled-tools')" in js

    def test_app_js_override_is_per_turn(self):
        """v0.9.83.8 — the disabled-set is reset to null right after
        the turn request completes (per-turn override only).
        v0.9.127.0 — the regex follows the v0.9.123.0 two-phase
        surface (sendChat awaits api.call('start_turn', …) inside
        pollTurnStream; the legacy one-shot 'chat_turn' call is gone
        from sendChat — test_turn_panel.py guards that)."""
        js = _read("app.js")
        import re

        m = re.search(r"await api\.call\(\s*'start_turn', text, history", js)
        assert m, "start_turn api.call not found"
        reset_idx = js.find("chatDisabledTools = null", m.start())
        assert reset_idx > m.start()
        # ...and the reset sits right after the SAME await
        assert "await api.call" in js[m.start():reset_idx]

    def test_app_js_disabled_set_never_persisted_again(self):
        """v0.9.83.8 — inverted guard: nothing writes the disabled set
        back to localStorage (that persistence was the root cause)."""
        js = _read("app.js")
        assert "JSON.stringify([...chatDisabledTools])" not in js

    def test_send_chat_passes_effective_tools(self):
        js = _read("app.js")
        assert "effectiveChatTools()" in js

    def test_style_css_has_panel(self):
        css = _read("style.css")
        assert "#chat-tools-panel {" in css
        assert "#chat-tools-panel[hidden] { display: none; }" in css

    @pytest.mark.parametrize("locale", ["zh-TW", "zh-CN", "en", "ja"])
    def test_all_four_locales_have_labels(self, locale):
        js = _read("app.js")
        # 4 locale dicts × 4 new keys — check per-key global count.
        for key in ("chatTools", "chatToolsAll", "chatToolsNone",
                    "chatToolsReset"):
            assert js.count(f"{key}:") == 4, key


# ─── v0.9.32.0+ — per-server select-all toggle (大項選取) ────────


class TestServerGroupToggle:
    def test_server_header_is_tri_state_checkbox(self):
        js = _read("app.js")
        assert "chat-tool-srv-cb" in js
        assert "srvCb.indeterminate = true;" in js
        assert "srvCb.dataset.srv = srv;" in js

    def test_tri_state_mapping(self):
        js = _read("app.js")
        assert "offCount === 0" in js
        assert "offCount === names.length" in js

    def test_server_click_flips_whole_group(self):
        js = _read("app.js")
        assert "const turnOn = srvCb.checked;" in js
        assert "names.forEach(function (n) {" in js

    def test_row_change_renders_panel(self):
        """Row toggle re-renders so the server tri-state stays fresh."""
        js = _read("app.js")
        body = js[js.index("function renderChatToolsPanel()"):
                  js.index("function toggleChatToolsPanel(")]
        assert body.count("renderChatToolsPanel();") >= 2

    def test_style_css_srv_checkbox(self):
        css = _read("style.css")
        assert ".chat-tool-srv-cb" in css
        assert "accent-color" in css

    def test_header_is_label_for_group_toggle(self):
        js = _read("app.js")
        body = js[js.index("function renderChatToolsPanel("):
                  js.index("function toggleChatToolsPanel(")]
        assert "head.className = 'chat-tool-srv';" in body
        assert "head.appendChild(srvCb);" in body