"""v0.9.55.0 — the Admin-tab (Qdrant-RAG 管理) LLM card REMOVED.

User request: 「將 Qdrant-RAG 管理 的 LLM 方塊移除，因為沒有用處」 —
the card only echoed get_status's llm_provider / llm_model, which the
footer status bar already shows. Inverted guards lock the removal:
index.html must NOT carry the card, app.js must NOT fill it, and the
now-unused llmInfo i18n keys must be gone from all 4 locales. The
get_status API fields stay (the footer consumes them).
"""
import shutil
import subprocess
from pathlib import Path

import pytest

_ASSETS = Path(__file__).resolve().parents[1] / "gui" / "assets"


def _read(name: str) -> str:
    return (_ASSETS / name).read_text(encoding="utf-8")


class TestAdminLlmCardRemoved:
    def test_index_html_has_no_llm_card(self):
        html = _read("index.html")
        assert 'id="admin-llm"' not in html
        assert 'data-i18n="llmInfo"' not in html

    def test_app_js_has_no_llm_card_fill(self):
        js = _read("app.js")
        assert "admin-llm" not in js
        assert "llmInfo" not in js

    def test_get_status_fields_still_available(self):
        """The API fields stay — the footer status bar consumes them."""
        import inspect

        from gui.api import Api
        src = inspect.getsource(Api.get_status)
        assert "llm_provider" in src
        assert "llm_model" in src

    def test_app_js_still_parses_with_node(self):
        node = shutil.which("node")
        if not node:
            pytest.skip("node not available")
        subprocess.run(
            ["node", "--check", str(_ASSETS / "app.js")],
            check=True, capture_output=True,
        )