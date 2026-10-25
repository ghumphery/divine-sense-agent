# -*- coding: utf-8 -*-
"""v0.9.133.0 — bubble colour & transparency UI (歷史氣泡可調顏色/透明度):
* the chat toolbar gains 🎨 → a panel with a colour picker + opacity
  slider PER ROLE (assistant / user), persisted in localStorage
  ('chat-bubble-style' = {asst:{c,a}, user:{c,a}});
* live-applied via CSS custom props (--bub-*-bg / --bub-asst-border
  keyed on #chat-log) so every existing bubble restyles instantly;
* reset clears the override."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
ASSETS = Path(__file__).resolve().parents[1] / "gui" / "assets"


def _read(name: str) -> str:
    return (ASSETS / name).read_text(encoding="utf-8")


def test_panel_html(self=None):
    html = _read("index.html")
    for tok in ('id="chat-style-btn"', 'id="chat-style-panel"',
                'id="bub-asst-color"', 'id="bub-asst-alpha"',
                'id="bub-user-color"', 'id="bub-user-alpha"',
                'id="chat-style-reset"',
                'data-i18n="styleBtn"', 'data-i18n="styleAsst"',
                'data-i18n="styleUser"', 'data-i18n="styleReset"',
                'type="range" id="bub-asst-alpha"',
                'min="30" max="100"'):
        assert tok in html, tok
    # the button sits in the chat toolbar after the paste button
    assert html.index('id="chat-clip-paste"') < html.index(
        'id="chat-style-btn"')
    # the panel sits right after the tools panel
    assert html.index('id="chat-tools-panel"') < html.index(
        'id="chat-style-panel"')


def test_style_css_vars_and_panel():
    css = _read("style.css")
    # the consumed vars + fallback defaults
    assert ("    --bub-user-bg: var(--primary);" in css)
    assert "    --bub-asst-bg: var(--bg);" in css
    assert "    --bub-asst-border: var(--border);" in css
    assert ".chat-msg.user { background: var(--bub-user-bg);" in css
    assert (".chat-msg.assistant { background: var(--bub-asst-bg);"
            " border: 1px solid var(--bub-asst-border);" in css)
    # the panel itself
    assert "#chat-style-panel {" in css
    assert "#chat-style-panel[hidden] { display: none; }" in css
    # the old hardcoded backgrounds are gone from the base rules
    assert (".chat-msg.user { background: var(--primary);"
            not in css)
    assert (".chat-msg.assistant { background: var(--bg);"
            not in css)


def test_app_js_logic(self=None):
    js = _read("app.js")
    for tok in (
        "function _hexToRgba(c, alpha)",
        "/^#([0-9a-f]{6})$/i",
        "function _loadBubbleStyle()",
        "localStorage.getItem('chat-bubble-style')",
        "localStorage.setItem('chat-bubble-style'",
        "localStorage.removeItem('chat-bubble-style')",
        "function _applyBubbleStyle()",
        "'--bub-asst-bg'", "'--bub-user-bg'", "'--bub-asst-border'",
        "logEl.style.setProperty(bgVar, rgba)",
        "logEl.style.removeProperty(bgVar)",
        "function bindBubbleStyle()",
        "bindBubbleStyle();",
        "bindBubbleStyle._done",
        "panel.hidden = !panel.hidden;",
        "els[role].alpha.addEventListener(",
    ):
        assert tok in js, tok


def test_i18n_four_locales():
    js = _read("app.js")
    for key in ("styleBtn:", "styleAsst:", "styleUser:", "styleReset:"):
        assert js.count(key) == 4, key


def test_node_parses():
    import shutil
    import subprocess

    node = shutil.which("node")
    if not node:
        import pytest
        pytest.skip("node not installed")
    subprocess.run(
        ["node", "--check", str(ASSETS / "app.js")],
        check=True, capture_output=True,
    )