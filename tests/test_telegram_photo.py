"""Tests for the Telegram sendPhoto tool (v0.9.31.0+).

``send_telegram_photo(chat_id, path, caption?)`` uploads a LOCAL
image file as a Telegram photo. Security mirrors the inline-image
bridge: the file MUST live inside the sandbox roots (import ∪
filesystem WRITE ∪ READ), only image extensions are sendable,
magic bytes are verified and the 10 MB Bot API cap is enforced
BEFORE any HTTP request. Mock-driven — no real network traffic.
"""
from __future__ import annotations

import asyncio
import io
import json
from unittest.mock import patch

import pytest

from src.mcp_server.telegram_server import (
    MAX_CAPTION_LEN,
    TelegramMCPServer,
    _image_magic_ok,
    _post_bot_multipart,
    _send_photo,
    _telegram_allowed_roots,
)

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
JPG_BYTES = b"\xff\xd8\xff\xe0" + b"\0" * 8


@pytest.fixture()
def tg_env(tmp_path, monkeypatch):
    """Hermetic sandbox env: the three roots point INSIDE tmp_path
    only, so live .env values (C:\\Users\\humphery) can't widen the
    gates."""
    for var in (
        "QDRANT_IMPORT_ALLOWED_ROOT",
        "QDRANT_FILESYSTEM_WRITE_ROOT",
        "QDRANT_FILESYSTEM_READ_ROOT",
        "WATCH_DIR",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("QDRANT_FILESYSTEM_WRITE_ROOT", str(tmp_path))
    uploads = tmp_path / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    return uploads


def _write_png(uploads, name="pic.png"):
    p = uploads / name
    p.write_bytes(PNG_MAGIC + b"payload")
    return p


class _CM:
    """Context-manager fake response for urlopen()."""

    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_open(captured: dict, message_id: int = 5):
    body = json.dumps({
        "ok": True,
        "result": {
            "message_id": message_id,
            "photo": [{"width": 160}, {"width": 800, "height": 600}],
        },
    }).encode("utf-8")

    def fake_open(req, timeout=None):
        captured["req"] = req
        return _CM(body)

    return fake_open


def _srv():
    return TelegramMCPServer(token="TOK", allowed_chat_ids=[100])


def _text(result):
    return result[0].text


# ──────────────────────────────────────────────────────────────
# multipart framing
# ──────────────────────────────────────────────────────────────


class TestMultipartFraming:
    def test_url_method_content_type(self):
        captured = {}

        def fake_open(req, timeout=None):
            captured["req"] = req
            return _CM(json.dumps(
                {"ok": True, "result": {"message_id": 1}},
            ).encode())

        with patch(
            "src.mcp_server.telegram_server.urllib.request.urlopen",
            side_effect=fake_open,
        ):
            _post_bot_multipart(
                "TOK", "sendPhoto", {"chat_id": "1"},
                "photo", "x.png", "image/png", PNG_MAGIC,
            )
        req = captured["req"]
        assert req.get_full_url().endswith("/botTOK/sendPhoto")
        assert req.get_method() == "POST"
        ct = req.get_header("Content-type") or ""
        assert ct.startswith("multipart/form-data; boundary=")

    def test_body_contains_fields_and_file(self):
        captured = {}

        def fake_open(req, timeout=None):
            captured["req"] = req
            return _CM(json.dumps(
                {"ok": True, "result": {"message_id": 1}},
            ).encode())

        with patch(
            "src.mcp_server.telegram_server.urllib.request.urlopen",
            side_effect=fake_open,
        ):
            _post_bot_multipart(
                "TOK", "sendPhoto",
                {"chat_id": "100", "caption": "hello"},
                "photo", "bear.png", "image/png", PNG_MAGIC + b"x",
            )
        body = captured["req"].data
        assert b'name="chat_id"' in body
        assert b'name="caption"' in body
        assert b'filename="bear.png"' in body
        assert b"Content-Type: image/png" in body
        assert PNG_MAGIC in body
        assert body.rstrip(b"\r\n").endswith(b"--")


# ──────────────────────────────────────────────────────────────
# sandbox gate + validation
# ──────────────────────────────────────────────────────────────


class TestSendPhotoValidation:
    def test_magic_helpers(self):
        assert _image_magic_ok(PNG_MAGIC + b"x", "image/png") is True
        assert _image_magic_ok(b"nope", "image/png") is False
        assert _image_magic_ok(JPG_BYTES, "image/jpeg") is True

    def test_allowed_roots_mirror_env(self, tg_env, tmp_path):
        assert tmp_path in _telegram_allowed_roots()

    def test_send_photo_happy_path(self, tg_env, monkeypatch):
        p = _write_png(tg_env)
        captured = {}
        monkeypatch.setattr(
            "src.mcp_server.telegram_server.urllib.request.urlopen",
            _fake_open(captured, message_id=5),
        )
        result = _send_photo("TOK", 100, p.as_posix(), caption="hi")
        assert result["message_id"] == 5
        req = captured["req"]
        assert req.get_full_url().endswith("/botTOK/sendPhoto")
        assert PNG_MAGIC in req.data
        assert b'name="caption"' in req.data

    def test_relative_uploads_path(self, tg_env, monkeypatch):
        _write_png(tg_env, "rel.png")
        captured = {}
        monkeypatch.setattr(
            "src.mcp_server.telegram_server.urllib.request.urlopen",
            _fake_open(captured, message_id=6),
        )
        result = _send_photo("TOK", 100, "uploads/rel.png")
        assert result["message_id"] == 6
        assert b'filename="rel.png"' in captured["req"].data

    def test_outside_roots_refused(self, tg_env, tmp_path):
        outside = tmp_path.parent / "outside.png"
        outside.write_bytes(PNG_MAGIC + b"x")
        with pytest.raises(RuntimeError) as exc:
            _send_photo("TOK", 100, outside.as_posix())
        assert "outside the allowed sandbox roots" in str(exc.value)

    def test_relative_escape_refused(self, tg_env):
        with pytest.raises(RuntimeError) as exc:
            _send_photo("TOK", 100, "uploads/../../escape.png")
        assert "outside the allowed sandbox roots" in str(exc.value)

    def test_non_image_ext_refused(self, tg_env):
        p = tg_env / "notes.txt"
        p.write_text("hello", encoding="utf-8")
        with pytest.raises(RuntimeError) as exc:
            _send_photo("TOK", 100, p.as_posix())
        assert "image extension" in str(exc.value)

    def test_magic_mismatch_refused(self, tg_env):
        p = tg_env / "fake.png"
        p.write_bytes(b"plain text pretending")
        with pytest.raises(RuntimeError) as exc:
            _send_photo("TOK", 100, p.as_posix())
        assert "valid image" in str(exc.value)

    def test_missing_file_refused(self, tg_env):
        with pytest.raises(RuntimeError) as exc:
            _send_photo("TOK", 100, (tg_env / "ghost.png").as_posix())
        assert "file not found" in str(exc.value)

    def test_oversize_downgrades_to_document(self, tg_env, monkeypatch):
        """v0.9.57.0: >10 MB images are no longer refused — they are
        downgraded to sendDocument (Bot API cap 50 MB) so oversized
        snapshots still reach the chat."""
        import src.mcp_server.telegram_server as ts

        monkeypatch.setattr(ts, "TELEGRAM_PHOTO_MAX_BYTES", 8)
        p = _write_png(tg_env, "big.png")
        captured = {}

        def fake_open(req, timeout=None):
            captured["url"] = req.get_full_url()
            return _CM(json.dumps({
                "ok": True,
                "result": {"message_id": 9, "document": {
                    "file_name": "big.png",
                }},
            }).encode())

        with patch(
            "src.mcp_server.telegram_server.urllib.request.urlopen",
            side_effect=fake_open,
        ):
            result = _send_photo("TOK", 100, p.as_posix())
        assert captured["url"].endswith("/botTOK/sendDocument")
        assert result["_sent_as"] == "document"

    def test_oversize_beyond_doc_cap_refused(
        self, tg_env, monkeypatch,
    ):
        import src.mcp_server.telegram_server as ts

        monkeypatch.setattr(ts, "TELEGRAM_PHOTO_MAX_BYTES", 8)
        monkeypatch.setattr(ts, "TELEGRAM_DOC_MAX_BYTES", 16)
        p = _write_png(tg_env, "big.png")
        p.write_bytes(PNG_MAGIC + b"payload way beyond the doc cap")
        with pytest.raises(RuntimeError) as exc:
            _send_photo("TOK", 100, p.as_posix())
        assert "too large" in str(exc.value)

    def test_missing_path_raises(self, tg_env):
        with pytest.raises(RuntimeError) as exc:
            _send_photo("TOK", 100, "")
        assert "'path' is required" in str(exc.value)


# ──────────────────────────────────────────────────────────────
# tool handler (_do_send_photo)
# ──────────────────────────────────────────────────────────────


class TestDoSendPhoto:
    def test_token_missing(self):
        s = TelegramMCPServer(token="", allowed_chat_ids=[1])
        out = _text(s._do_send_photo({"chat_id": 1, "path": "x.png"}))
        assert "TELEGRAM_BOT_TOKEN is not configured" in out

    def test_allowlist_empty(self):
        s = TelegramMCPServer(token="TOK", allowed_chat_ids=[])
        out = _text(s._do_send_photo({"chat_id": 1, "path": "x.png"}))
        assert "TELEGRAM_ALLOWED_CHAT_IDS is empty" in out

    def test_chat_id_not_in_allowlist_rejected_before_http(
        self, tg_env, monkeypatch,
    ):
        _write_png(tg_env)
        fired = []

        def fake_open(req, timeout=None):
            fired.append(req)
            return _CM(b"{}")

        monkeypatch.setattr(
            "src.mcp_server.telegram_server.urllib.request.urlopen",
            fake_open,
        )
        s = _srv()
        out = _text(s._do_send_photo(
            {"chat_id": 999, "path": "uploads/pic.png"},
        ))
        assert "not in the allowlist" in out
        assert not fired

    def test_caption_too_long(self):
        s = _srv()
        out = _text(s._do_send_photo({
            "chat_id": 100, "path": "x.png",
            "caption": "c" * (MAX_CAPTION_LEN + 1),
        }))
        assert "caption" in out and "1024" in out

    def test_caption_must_be_string(self):
        s = _srv()
        out = _text(s._do_send_photo({
            "chat_id": 100, "path": "x.png", "caption": 5,
        }))
        assert "'caption' must be a string" in out

    def test_path_must_be_string(self):
        s = _srv()
        out = _text(s._do_send_photo({"chat_id": 100, "path": ""}))
        assert "'path' must be a non-empty string" in out

    def test_send_ok_reports_dimensions(self, tg_env, monkeypatch):
        _write_png(tg_env, "pic.png")
        captured = {}
        monkeypatch.setattr(
            "src.mcp_server.telegram_server.urllib.request.urlopen",
            _fake_open(captured, message_id=42),
        )
        s = _srv()
        out = _text(s._do_send_photo({
            "chat_id": 100, "path": "uploads/pic.png",
            "caption": "小熊",
        }))
        data = json.loads(out)
        assert data["ok"] is True
        assert data["message_id"] == 42
        assert data["file"] == "pic.png"
        assert data["photo_width"] == 800
        assert data["caption"] == "小熊"

    def test_dispatch_via_call_tool(self, tg_env, monkeypatch):
        """``_call`` routes send_telegram_photo to the photo handler."""
        _write_png(tg_env, "x.png")
        monkeypatch.setattr(
            "src.mcp_server.telegram_server.urllib.request.urlopen",
            _fake_open({}, message_id=7),
        )
        s = _srv()
        handler = getattr(s, "_handle_photo", None)
        assert handler is not None
        result = asyncio.run(handler(
            {"chat_id": 100, "path": "uploads/x.png"},
        ))
        assert json.loads(result[0].text)["message_id"] == 7


class TestPhotoToolRegistered:
    def test_tool_registered_and_dispatched(self):
        import io as _io

        src = _io.open(
            "src/mcp_server/telegram_server.py", encoding="utf-8",
        ).read()
        assert 'name="send_telegram_photo"' in src
        assert 'if name == "send_telegram_photo"' in src
        assert "_handle_photo" in src


class TestPromptMentionsPhoto:
    @pytest.fixture()
    def stub_agent(self, tmp_path, monkeypatch):
        monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path))
        from src.agent.agent import Agent

        a = Agent()
        a._provider = "stub"
        a._model = "stub-model"
        a._mcp_manager = None
        return a

    def test_tools_prompt(self, stub_agent):
        prompt = stub_agent._system_prompt(tools_enabled=True)
        assert "send_telegram_photo" in prompt
        assert "caption" in prompt

    def test_legacy_prompt_excluded(self, stub_agent):
        prompt = stub_agent._system_prompt(tools_enabled=False)
        assert "send_telegram_photo" not in prompt