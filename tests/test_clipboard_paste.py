"""Tests for the chat clipboard paste-image feature (v0.9.29.0+).

Python side: ``Api.save_pasted_image`` (the pywebview GUI bridge)
decodes a base64 clipboard image and writes it into the SAME
filesystem WRITE sandbox ``uploads/`` dir the web ``POST /upload``
endpoint uses — magic sniffing (PNG/JPEG), sanitised naming,
timestamp + collision suffix.
JS side: source guards for the paste handler wiring in app.js.
"""
from __future__ import annotations

import base64
import os

import pytest

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
JPG_MAGIC = b"\xff\xd8\xff\xe0" + b"\0" * 16


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


class TestSniffAndSanitize:
    def test_sniff_png_jpeg_none(self):
        from gui.api import _sniff_image

        assert _sniff_image(PNG_MAGIC + b"\0" * 8) == "png"
        assert _sniff_image(JPG_MAGIC) == "jpg"
        assert _sniff_image(b"GIF89a.....") is None
        assert _sniff_image(b"") is None

    def test_sanitize_forces_sniffed_ext(self):
        from gui.api import _sanitize_paste_name

        assert _sanitize_paste_name("shot.png", "png") == "shot.png"
        assert _sanitize_paste_name("shot", "png") == "shot.png"
        assert _sanitize_paste_name("photo", "jpg") == "photo.jpg"
        assert _sanitize_paste_name("x.txt", "jpg") == "x.txt.jpg"

    def test_sanitize_strips_traversal_and_weird_chars(self):
        from gui.api import _sanitize_paste_name

        out = _sanitize_paste_name("..\\evil/<img>.png", "png")
        assert "/" not in out and "\\" not in out
        assert ".." not in out
        assert out.endswith(".png")

    def test_sanitize_empty_defaults(self):
        from gui.api import _sanitize_paste_name

        out = _sanitize_paste_name("", "png")
        assert out.endswith(".png") and out.startswith("paste")


class TestSavePastedImage:
    def test_png_written_into_uploads(self, paste_env):
        from gui.api import Api

        api = Api()
        b64 = base64.b64encode(PNG_MAGIC + b"payload" * 10).decode()
        out = api.save_pasted_image(b64, "screenshot.png")
        assert out["ok"] is True
        assert out["path"].endswith(".png")
        assert "/uploads/" in out["path"]
        with open(out["path"], "rb") as f:
            assert f.read(8) == PNG_MAGIC

    def test_jpeg_accepted(self, paste_env):
        from gui.api import Api

        api = Api()
        b64 = base64.b64encode(JPG_MAGIC + b"jpegbody").decode()
        out = api.save_pasted_image(b64, "cam")
        assert out["ok"] is True
        assert out["path"].endswith(".jpg")

    def test_non_image_magic_refused(self, paste_env):
        from gui.api import Api

        api = Api()
        b64 = base64.b64encode(b"not an image at all").decode()
        out = api.save_pasted_image(b64, "x.png")
        assert out["ok"] is False
        assert "PNG/JPEG" in out["error"]

    def test_invalid_base64_refused(self, paste_env):
        from gui.api import Api

        api = Api()
        out = api.save_pasted_image("%%%not-base64%%%", "x.png")
        assert out["ok"] is False
        assert "base64" in out["error"]

    def test_empty_refused(self, paste_env):
        from gui.api import Api

        out = Api().save_pasted_image("", "x.png")
        assert out["ok"] is False

    def test_oversize_refused(self, paste_env, monkeypatch):
        import gui.api as gapi

        monkeypatch.setattr(gapi, "_PASTE_B64_MAX", 100)
        from gui.api import Api

        b64 = base64.b64encode(PNG_MAGIC + b"\0" * 200).decode()
        out = Api().save_pasted_image(b64, "big.png")
        assert out["ok"] is False
        assert "too large" in out["error"]

    def test_collision_suffix(self, paste_env):
        from gui.api import Api

        api = Api()
        b64 = base64.b64encode(PNG_MAGIC + b"\0" * 8).decode()
        out1 = api.save_pasted_image(b64, "same.png")
        out2 = api.save_pasted_image(b64, "same.png")
        assert out1["ok"] and out2["ok"]
        assert out1["path"] != out2["path"]
        assert os.path.exists(out1["path"])
        assert os.path.exists(out2["path"])

    def test_path_traversal_never_escapes_uploads(self, paste_env):
        from gui.api import Api

        api = Api()
        b64 = base64.b64encode(PNG_MAGIC + b"\0" * 8).decode()
        out = api.save_pasted_image(b64, "..\\..\\evil<img>.png")
        assert out["ok"] is True
        assert ".." not in out["path"]
        assert "/uploads/" in out["path"]


class TestJsWiring:
    def _js(self):
        import io

        return io.open("gui/assets/app.js", encoding="utf-8").read()

    def test_paste_handler_exists_and_filters_images(self):
        js = self._js()
        assert "function onChatPaste(" in js
        assert "clipboardData" in js
        # v0.9.120.1: images still collected by kind+type; non-image
        # file items are now routed to uploadFiles too.
        assert "it.kind !== 'file') continue;" in js
        assert "it.type.indexOf('image/') === 0" in js

    def test_plain_text_paste_untouched(self):
        js = self._js()
        assert "if (!imgs.length) return;" in js
        assert "e.preventDefault();" in js  # only when images found

    def test_web_branch_reuses_upload(self):
        js = self._js()
        assert "async function handlePastedImage(" in js
        assert "isWebMode()" in js
        assert "await uploadFiles([file]);" in js

    def test_gui_branch_uses_bridge(self):
        js = self._js()
        assert "save_pasted_image" in js
        assert "window.pywebview && window.pywebview.api" in js

    def test_bindpaste_wired_in_bindevents(self):
        js = self._js()
        assert "function bindPaste()" in js
        assert "bindPaste();" in js
        assert "addEventListener('paste', onChatPaste)" in js

    def test_paste_bound_to_chat_input(self):
        js = self._js()
        assert "getElementById('chat-input')" in js