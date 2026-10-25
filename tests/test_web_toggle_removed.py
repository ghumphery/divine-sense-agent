"""v0.9.133.12 — the inert web-search toggles are REMOVED.

Real web capability in the tool-loop chat path is chosen automatically
by ToolRoute/Jev (web class → my_web / firefox_craw tools) and the LLM
decides per-turn; the manual toggles only fed ``web_enabled`` into the
system-prompt builder whose TOOL flavour never reads that parameter
(its only consumers were the legacy no-tools prompt + legacy RAG
pre-bake, neither is the GUI chat path). Removed surfaces:

* chat toolbar  : ``#chat-web-toggle`` + ``data-i18n=webSearch``
* schedule edit : ``#schedule-edit-web-search`` (scheduleFieldWebSearch)
                  ``#schedule-edit-web-max``   (scheduleFieldWebMax)
* app.js        : the useWeb getter + i18n keys ×4 locales; the two
                  call sites now pass ``false`` (positional args kept).

The Background checkbox is deliberately KEPT (manual submit = the UI
never locks for the first 60 s, unlike the auto-detach threshold).
"""
from pathlib import Path

_ASSETS = Path(__file__).resolve().parents[1] / "gui" / "assets"


def _read(name: str) -> str:
    return (_ASSETS / name).read_text(encoding="utf-8")


class TestWebToggleRemovedV013312:
    def test_index_html_has_no_web_toggle(self):
        html = _read("index.html")
        for tok in ("chat-web-toggle", "schedule-edit-web-search",
                    "schedule-edit-web-max", "data-i18n=\"webSearch\"",
                    "scheduleFieldWebSearch", "scheduleFieldWebMax"):
            assert tok not in html, tok

    def test_app_js_has_no_useweb(self):
        js = _read("app.js")
        for tok in ("chat-web-toggle", "const useWeb",
                    "useWeb,", "webSearch:", "scheduleFieldWebSearch",
                    "scheduleFieldWebMax"):
            assert tok not in js, tok

    def test_call_sites_pass_false(self):
        js = _read("app.js")
        assert "'submit_background_task', text.slice(0, 40), text, false," \
            in js
        assert "'start_turn', text, history, false, 5, 5, lang," \
            " effectiveChatTools()," in js

    def test_background_checkbox_kept(self):
        html = _read("index.html")
        assert 'id="chat-bg-toggle"' in html
        js = _read("app.js")
        assert "chat-bg-toggle" in js