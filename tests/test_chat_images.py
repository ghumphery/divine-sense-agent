"""Tests for the chat inline-image display feature (v0.9.30.0+).

Python side: ``Api.read_sandbox_image`` — reads an image INSIDE one
of the allowed sandbox roots (import ∪ filesystem READ ∪ WRITE) and
returns base64 + mime for the frontend to render as a ``data:``
URL. Extension whitelist + magic-byte check + 20 MB cap; refuses
anything outside the roots (never an arbitrary file read).

JS side: source guards for ``renderSandboxImages`` /
``_loadSandboxImage`` / mark-line scan wiring in app.js.
"""
from __future__ import annotations

import base64
import io

import pytest

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
JPG_MAGIC = b"\xff\xd8\xff\xe0" + b"\0" * 16
GIF_MAGIC = b"GIF89a" + b"\0" * 8
WEBP_MAGIC = b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\0" * 8
BMP_MAGIC = b"BM" + b"\0" * 16


@pytest.fixture()
def paste_env(tmp_path, monkeypatch):
    monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("QDRANT_FILESYSTEM_WRITE_ROOT",
                       str(tmp_path / "write"))
    return tmp_path


@pytest.fixture()
def api():
    from gui.api import Api

    return Api()


def _write_img(tmp_path, name, magic):
    uploads = tmp_path / "write" / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    p = uploads / name
    p.write_bytes(magic + b"payload")
    return p.as_posix()


class TestReadSandboxImage:
    def test_png_in_uploads_ok(self, paste_env, api):
        p = _write_img(paste_env, "shot.png", PNG_MAGIC)
        out = api.read_sandbox_image(p)
        assert out["ok"] is True
        assert out["mime"] == "image/png"
        assert base64.b64decode(out["data_base64"]).startswith(PNG_MAGIC)

    def test_jpeg_ok(self, paste_env, api):
        p = _write_img(paste_env, "cam.jpg", JPG_MAGIC)
        out = api.read_sandbox_image(p)
        assert out["ok"] is True and out["mime"] == "image/jpeg"

    def test_gif_webp_bmp_ok(self, paste_env, api):
        for name, magic, mime in (
            ("a.gif", GIF_MAGIC, "image/gif"),
            ("a.webp", WEBP_MAGIC, "image/webp"),
            ("a.bmp", BMP_MAGIC, "image/bmp"),
        ):
            p = _write_img(paste_env, name, magic)
            out = api.read_sandbox_image(p)
            assert out["ok"] is True, name
            assert out["mime"] == mime

    def test_txt_refused(self, paste_env, api):
        p = paste_env / "write" / "notes.txt"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("hello", encoding="utf-8")
        out = api.read_sandbox_image(p.as_posix())
        assert out["ok"] is False
        assert "extension" in out["error"]

    def test_png_magic_mismatch_refused(self, paste_env, api):
        p = paste_env / "write" / "fake.png"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"not an image at all")
        out = api.read_sandbox_image(p.as_posix())
        assert out["ok"] is False
        assert "valid image" in out["error"]

    def test_outside_roots_refused(self, tmp_path, paste_env, api,
                                   monkeypatch):
        import src.mcp_client.host as host_mod

        def narrow_roots():
            return [tmp_path / "write"]

        monkeypatch.setattr(host_mod, "_allowed_file_roots", narrow_roots)
        outside = tmp_path.parent / "elsewhere.png"
        outside.write_bytes(PNG_MAGIC + b"x")
        out = api.read_sandbox_image(outside.as_posix())
        assert out["ok"] is False
        assert "sandbox roots" in out["error"]

    def test_missing_file(self, paste_env, api):
        p = paste_env / "write" / "ghost.png"
        out = api.read_sandbox_image(p.as_posix())
        assert out["ok"] is False
        assert "not found" in out["error"]

    def test_oversize_refused(self, paste_env, api, monkeypatch):
        import gui.api as api_mod

        monkeypatch.setattr(api_mod, "_READ_IMAGE_MAX_BYTES", 8)
        p = _write_img(paste_env, "big.png", PNG_MAGIC)
        out = api.read_sandbox_image(p)
        assert out["ok"] is False
        assert "too large" in out["error"]

    def test_empty_path_refused(self, paste_env, api):
        assert api.read_sandbox_image("")["ok"] is False

    def test_file_uri_prefix_accepted(self, paste_env, api):
        p = _write_img(paste_env, "uri.png", PNG_MAGIC)
        out = api.read_sandbox_image("file:///" + p.lstrip("/"))
        # On Windows the drive survives; on any failure the error must
        # NOT be the sandbox refusal for a legitimately-sandboxed file.
        if out["ok"] is False:
            assert "sandbox roots" not in out["error"]


class TestRelativePaths:
    def test_relative_uploads_path_ok(self, paste_env, api):
        _write_img(paste_env, "rel.png", PNG_MAGIC)
        out = api.read_sandbox_image("uploads/rel.png")
        assert out["ok"] is True
        assert out["path"].endswith("uploads/rel.png")

    def test_relative_escape_refused(self, tmp_path, paste_env, api,
                                     monkeypatch):
        import src.mcp_client.host as host_mod

        def narrow_roots():
            return [tmp_path / "write"]

        monkeypatch.setattr(host_mod, "_allowed_file_roots", narrow_roots)
        out = api.read_sandbox_image("uploads/../../escape.png")
        assert out["ok"] is False


class TestPromptImageEcho:
    @pytest.fixture()
    def stub_agent(self, tmp_path, monkeypatch):
        monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path))
        from src.agent.agent import Agent

        a = Agent()
        a._provider = "stub"
        a._model = "stub-model"
        a._mcp_manager = None
        return a

    def test_tools_prompt_mentions_inline_images(self, stub_agent):
        prompt = stub_agent._system_prompt(tools_enabled=True)
        assert "Show images in the chat" in prompt
        assert "![alt]" in prompt
        assert "NEVER describe an image in prose" in prompt
        # v0.9.30.2+ — generated images MUST be saved inside the
        # write sandbox; Desktop / OneDrive paths cannot render.
        assert "write sandbox" in prompt
        assert "Desktop" in prompt or "OneDrive" in prompt

    def test_prompt_names_literal_write_sandbox(self, stub_agent,
                                                monkeypatch, tmp_path):
        # v0.9.30.3+ — the LLM cannot see env vars; the prompt must
        # name the ACTUAL write-sandbox directory.
        monkeypatch.setenv("QDRANT_FILESYSTEM_WRITE_ROOT", str(tmp_path))
        prompt = stub_agent._system_prompt(tools_enabled=True)
        assert str(tmp_path) in prompt

    def test_legacy_prompt_excluded(self, stub_agent):
        prompt = stub_agent._system_prompt(tools_enabled=False)
        assert "Show images in the chat" not in prompt


class TestJsWiring:
    def _js(self):
        return io.open("gui/assets/app.js", encoding="utf-8").read()

    def test_functions_exist(self):
        js = self._js()
        assert "function renderSandboxImages(" in js
        assert "function _loadSandboxImage(" in js
        assert "function _isLocalImgPath(" in js
        assert "_SANDBOX_IMG_CACHE_MAX" in js

    def test_renderchat_calls_pass(self):
        js = self._js()
        assert "renderSandboxImages();" in js

    def test_bridge_call_and_data_url(self):
        js = self._js()
        assert "read_sandbox_image(path)" in js
        assert "'data:' + r.mime + ';base64,' + r.data_base64" in js

    def test_mark_line_scan(self):
        js = self._js()
        assert "querySelectorAll('.chat-msg')" in js
        assert "chat-inline-image" in js
        assert "png|jpe?g|gif|webp|bmp" in js

    def test_failure_and_cache(self):
        js = self._js()
        assert "chat-image-failed" in js
        assert "_sandboxImgCache.set(path, url);" in js

    def test_remote_src_untouched(self):
        js = self._js()
        assert "^(data:|https?:|blob:)/i.test(src)" in js

    def test_css_rules_present(self):
        css = io.open("gui/assets/style.css", encoding="utf-8").read()
        assert ".chat-inline-image" in css
        assert ".chat-image-failed" in css

    def test_bare_and_relative_path_regex(self):
        js = self._js()
        # v0.9.30.1+ — bare absolute paths + uploads/-relative paths
        assert "uploads[\\\\/]" in js
        assert "(?![\\w.\\/\\\\])" in js
        assert "existing[p.toLowerCase()]" in js
        # v0.9.30.2+ — preceding-char validation (CJK punctuation
        # glued onto the path must still match)
        assert "txt.charAt(m.index - 1)" in js
        assert "/[\\w/\\\\]/.test(before)" in js

    def test_failure_alt_carries_error(self):
        js = self._js()
        assert "img.alt = '⚠ ' + why" in js

    def test_is_local_img_path_accepts_uploads(self):
        js = self._js()
        assert "indexOf('uploads/') === 0" in js


class TestLightbox:
    """v0.9.33.0+ — click an inline image to enlarge it."""

    def _js(self):
        return io.open("gui/assets/app.js", encoding="utf-8").read()

    def test_lightbox_functions_exist(self):
        js = self._js()
        for name in ("_makeChatImgClickable", "_openChatLightbox",
                     "_closeChatLightbox", "_chatLightboxEsc"):
            assert f"function {name}(" in js

    def test_both_image_sources_are_clickable(self):
        js = self._js()
        assert js.count("_makeChatImgClickable(img, src);") == 1
        assert js.count("_makeChatImgClickable(img, p);") == 1

    def test_esc_and_overlay_click_close(self):
        js = self._js()
        assert "e.key === 'Escape'" in js
        assert "document.removeEventListener('keydown', _chatLightboxEsc)" in js
        assert "overlay.addEventListener('click', function () {" in js

    def test_caption_textcontent_only(self):
        js = self._js()
        body = js[js.index("function _openChatLightbox("):
                  js.index("function _chatLightboxEsc(")]
        assert "cap.textContent = caption || '';" in body
        assert "innerHTML" not in body

    def test_css_lightbox_rules(self):
        css = io.open("gui/assets/style.css", encoding="utf-8").read()
        assert ".chat-lightbox {" in css
        assert ".chat-msg .chat-clickable" in css
        assert "cursor: zoom-in" in css
        assert "cursor: zoom-out" in css
        assert "z-index: 9999" in css