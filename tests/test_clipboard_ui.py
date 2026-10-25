# -*- coding: utf-8 -*-
"""v0.9.132.9 — clipboard buttons across the web UI:
* chat input: 📋 copy the draft / 📥 paste clipboard text at the caret;
* chat history bubbles (assistant AND user): per-bubble 📋 copy;
* notes document body (文件框): 📋 copy / ✂️ cut / 📥 paste.
Write = navigator.clipboard + hidden-textarea execCommand fallback;
read = navigator.clipboard only (honest fail toast otherwise)."""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "gui" / "assets"


def _read(name: str) -> str:
    return (ASSETS / name).read_text(encoding="utf-8")


KEYS = ("clipCopyMsg", "clipCopyInput", "clipPasteInput",
        "clipCopySel", "clipCutSel", "clipPasteSel",
        "clipCopied", "clipPasted", "clipPasteFail", "clipEmpty")


class TestClipboardUi:
    def test_index_buttons(self):
        html = _read("index.html")
        for tok in ('id="chat-clip-copy"', 'id="chat-clip-paste"',
                    'id="ne-clip-copy"', 'id="ne-clip-cut"',
                    'id="ne-clip-paste"',
                    'data-i18n-title="clipCopyInput"',
                    'data-i18n-title="clipPasteInput"',
                    'data-i18n-title="clipCopySel"',
                    'data-i18n-title="clipCutSel"',
                    'data-i18n-title="clipPasteSel"',
                    'class="ne-clip-row"'):
            assert tok in html, tok
        # the paste buttons must come after the snippet button, the
        # notes row between the body textarea and the preview
        assert html.index('id="chat-snips-btn"') < html.index(
            'id="chat-clip-copy"')
        ne_body = html.index('id="ne-body"')
        ne_row = html.index('class="ne-clip-row"')
        ne_prev = html.index('id="ne-preview"')
        assert ne_body < ne_row < ne_prev

    def test_app_js_helpers_and_binding(self):
        js = _read("app.js")
        for tok in (
            "async function _clipWrite(text)",
            "async function _clipRead()",
            "function _bubbleCopyBtn(text)",
            "function _insertAtCaret(el, text)",
            "function bindClipboard()",
            "navigator.clipboard.writeText(text || '')",
            "document.execCommand('copy')",
            "navigator.clipboard.readText()",
            "div.appendChild(_bubbleCopyBtn(m.content));",
            "bindClipboard();",
            "bindClipboard._done",
            "id=\"ne-body\"" if False else "getElementById('ne-body')",
            "'ne-clip-copy', 'copy'", "'ne-clip-cut', 'cut'",
            "'ne-clip-paste', 'paste'",
            "dispatchEvent(new Event('input', { bubbles: true }))",
        ):
            assert tok in js, tok
        # wired from the early binder group inside bindEvents
        seg = js[js.index("function bindEvents()"):]
        assert "bindClipboard();" in seg[:1400]

    def test_i18n_four_locales(self):
        js = _read("app.js")
        for key in KEYS:
            assert js.count(key + ":") == 4, key

    def test_css_blocks(self):
        css = _read("style.css")
        for frag in (".chat-msg .btn-msg-copy {",
                     ".chat-msg .btn-msg-copy:hover {",
                     ".notes-editor .ne-clip-row {"):
            assert frag in css, frag

    def test_node_parses(self):
        import shutil
        import subprocess

        node = shutil.which("node")
        if not node:
            pytest.skip("node not installed")
        subprocess.run(
            ["node", "--check", str(ASSETS / "app.js")],
            check=True, capture_output=True,
        )