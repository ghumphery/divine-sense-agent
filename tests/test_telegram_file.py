"""Tests for the Telegram sendDocument tool (v0.9.62.0+).

``send_telegram_file(chat_id, path, caption?)`` uploads a LOCAL
sandbox file of any non-executable type as a Telegram DOCUMENT
(Bot API sendDocument, 50 MB cap). Security mirrors the photo tool:
the file MUST live inside the sandbox roots (import ∪ filesystem
WRITE ∪ READ), dangerous executables (.exe/.bat/.ps1/...) are
refused outright, and the size cap is enforced BEFORE any HTTP
request leaves this process. Mock-driven — no real network traffic.
"""
from __future__ import annotations

import asyncio
import io
import json

import pytest

from src.mcp_server.telegram_server import (
    MAX_CAPTION_LEN,
    TELEGRAM_DOC_MAX_BYTES,
    TelegramMCPServer,
    _send_document,
)

DOC_RESPONSE = json.dumps({
    "ok": True,
    "result": {
        "message_id": 9,
        "document": {
            "file_name": "report.pdf",
            "mime_type": "application/pdf",
            "file_size": 12345,
        },
    },
}).encode("utf-8")

PLAIN_RESPONSE = json.dumps({
    "ok": True,
    "result": {
        "message_id": 10,
        "document": {
            "file_name": "notes.txt",
            "mime_type": "text/plain",
            "file_size": 6,
        },
    },
}).encode("utf-8")


@pytest.fixture()
def tg_env(tmp_path, monkeypatch):
    """Hermetic sandbox env: the three roots point INSIDE tmp_path
    only, so live .env values can't widen the gates."""
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


def _fake_open(captured: dict, body: bytes = DOC_RESPONSE):
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


class TestSendDocumentFraming:
    def test_url_method_and_content_type(self, tg_env, monkeypatch):
        p = tg_env / "report.pdf"
        p.write_bytes(b"%PDF-1.4 fake")
        captured = {}
        monkeypatch.setattr(
            "src.mcp_server.telegram_server.urllib.request.urlopen",
            _fake_open(captured),
        )
        _send_document("TOK", 100, p.as_posix())
        req = captured["req"]
        assert req.get_full_url().endswith("/botTOK/sendDocument")
        assert req.get_method() == "POST"
        ct = req.get_header("Content-type") or ""
        assert ct.startswith("multipart/form-data; boundary=")

    def test_disposition_carries_filename_and_mime(
        self, tg_env, monkeypatch,
    ):
        p = tg_env / "report.pdf"
        p.write_bytes(b"%PDF-1.4 fake")
        captured = {}
        monkeypatch.setattr(
            "src.mcp_server.telegram_server.urllib.request.urlopen",
            _fake_open(captured),
        )
        _send_document("TOK", 100, p.as_posix())
        body = captured["req"].data
        assert b'name="document"; filename="report.pdf"' in body
        assert b"Content-Type: application/pdf" in body
        assert b'name="chat_id"\r\n\r\n100\r\n' in body

    def test_mime_text_plain_and_fallback_octet_stream(
        self, tg_env, monkeypatch,
    ):
        txt = tg_env / "notes.txt"
        txt.write_bytes(b"hello\n")
        captured = {}
        monkeypatch.setattr(
            "src.mcp_server.telegram_server.urllib.request.urlopen",
            _fake_open(captured, PLAIN_RESPONSE),
        )
        _send_document("TOK", 100, txt.as_posix())
        assert b"Content-Type: text/plain" in captured["req"].data

        noext = tg_env / "noext"
        noext.write_bytes(b"data")
        captured2 = {}
        monkeypatch.setattr(
            "src.mcp_server.telegram_server.urllib.request.urlopen",
            _fake_open(captured2),
        )
        _send_document("TOK", 100, noext.as_posix())
        assert (
            b"Content-Type: application/octet-stream"
            in captured2["req"].data
        )

    def test_caption_truncated_at_1024(self, tg_env, monkeypatch):
        p = tg_env / "report.pdf"
        p.write_bytes(b"%PDF")
        captured = {}
        monkeypatch.setattr(
            "src.mcp_server.telegram_server.urllib.request.urlopen",
            _fake_open(captured),
        )
        _send_document("TOK", 100, p.as_posix(), caption="c" * 2000)
        body = captured["req"].data
        assert b"c" * MAX_CAPTION_LEN in body
        assert b"c" * (MAX_CAPTION_LEN + 1) not in body
# ──────────────────────────────────────────────────────────────
# sandbox / size / extension gates (no HTTP may fire)
# ──────────────────────────────────────────────────────────────


class TestSendDocumentGates:
    def test_happy_path_relative_anchored_at_write_root(
        self, tg_env, monkeypatch,
    ):
        p = tg_env / "report.pdf"
        p.write_bytes(b"%PDF-1.4 fake")
        captured = {}
        monkeypatch.setattr(
            "src.mcp_server.telegram_server.urllib.request.urlopen",
            _fake_open(captured),
        )
        result = _send_document("TOK", 100, "uploads/report.pdf")
        assert result["_sent_as"] == "document"
        assert captured["req"].data.count(b"%PDF-1.4 fake") == 1

    def test_outside_sandbox_refused_no_http(
        self, tg_env, tmp_path, monkeypatch,
    ):
        outside = tmp_path.parent / "outside.pdf"
        outside.write_bytes(b"%PDF")
        fired = []

        def fake_open(req, timeout=None):
            fired.append(req)
            return _CM(b"{}")

        monkeypatch.setattr(
            "src.mcp_server.telegram_server.urllib.request.urlopen",
            fake_open,
        )
        with pytest.raises(RuntimeError) as exc:
            _send_document("TOK", 100, outside.as_posix())
        assert "outside the allowed sandbox roots" in str(exc.value)
        assert not fired

    def test_relative_escape_refused(self, tg_env, monkeypatch):
        fired = []
        monkeypatch.setattr(
            "src.mcp_server.telegram_server.urllib.request.urlopen",
            lambda req, timeout=None: fired.append(req) or _CM(b"{}"),
        )
        with pytest.raises(RuntimeError) as exc:
            _send_document("TOK", 100, "uploads/../../escape.pdf")
        assert "outside the allowed sandbox roots" in str(exc.value)
        assert not fired

    def test_dangerous_extensions_refused_no_http(
        self, tg_env, monkeypatch,
    ):
        fired = []
        monkeypatch.setattr(
            "src.mcp_server.telegram_server.urllib.request.urlopen",
            lambda req, timeout=None: fired.append(req) or _CM(b"{}"),
        )
        for name in ("evil.exe", "evil.bat", "evil.ps1", "evil.cmd"):
            p = tg_env / name
            p.write_bytes(b"MZ")
            with pytest.raises(RuntimeError) as exc:
                _send_document("TOK", 100, p.as_posix())
            assert "dangerous executable" in str(exc.value)
        assert not fired

    def test_oversize_refused_no_http(self, tg_env, monkeypatch):
        fired = []
        monkeypatch.setattr(
            "src.mcp_server.telegram_server.urllib.request.urlopen",
            lambda req, timeout=None: fired.append(req) or _CM(b"{}"),
        )
        p = tg_env / "big.zip"
        with open(p, "wb") as f:
            f.truncate(TELEGRAM_DOC_MAX_BYTES + 1)  # sparse file
        with pytest.raises(RuntimeError) as exc:
            _send_document("TOK", 100, p.as_posix())
        assert "too large" in str(exc.value)
        assert "50 MB" in str(exc.value)
        assert not fired

    def test_file_not_found(self, tg_env):
        with pytest.raises(RuntimeError) as exc:
            _send_document(
                "TOK", 100, (tg_env / "ghost.pdf").as_posix(),
            )
        assert "file not found" in str(exc.value)

    def test_empty_path_refused(self):
        with pytest.raises(RuntimeError) as exc:
            _send_document("TOK", 100, "")
        assert "'path' is required" in str(exc.value)

    def test_relative_path_without_write_root(
        self, tmp_path, monkeypatch,
    ):
        for var in (
            "QDRANT_IMPORT_ALLOWED_ROOT",
            "QDRANT_FILESYSTEM_WRITE_ROOT",
            "QDRANT_FILESYSTEM_READ_ROOT",
            "WATCH_DIR",
        ):
            monkeypatch.delenv(var, raising=False)
        with pytest.raises(RuntimeError) as exc:
            _send_document("TOK", 100, "uploads/x.pdf")
        assert "QDRANT_FILESYSTEM_WRITE_ROOT" in str(exc.value)
# ──────────────────────────────────────────────────────────────
# tool handler validation
# ──────────────────────────────────────────────────────────────


class TestFileToolHandler:
    def test_token_not_configured(self):
        s = TelegramMCPServer(token="", allowed_chat_ids=[100])
        out = _text(s._do_send_file({"chat_id": 100, "path": "x.pdf"}))
        assert "TELEGRAM_BOT_TOKEN" in out

    def test_allowlist_empty(self):
        s = TelegramMCPServer(token="TOK", allowed_chat_ids=[])
        out = _text(s._do_send_file({"chat_id": 100, "path": "x.pdf"}))
        assert "TELEGRAM_ALLOWED_CHAT_IDS is empty" in out

    def test_chat_id_must_be_int(self):
        s = _srv()
        assert "'chat_id' must be an integer" in _text(
            s._do_send_file({"chat_id": "100", "path": "x.pdf"}),
        )
        assert "'chat_id' must be an integer" in _text(
            s._do_send_file({"chat_id": True, "path": "x.pdf"}),
        )

    def test_not_in_allowlist_no_http(self, tg_env, monkeypatch):
        fired = []
        monkeypatch.setattr(
            "src.mcp_server.telegram_server.urllib.request.urlopen",
            lambda req, timeout=None: fired.append(req) or _CM(b"{}"),
        )
        s = _srv()
        out = _text(s._do_send_file({
            "chat_id": 999, "path": "uploads/x.pdf",
        }))
        assert "not in the allowlist" in out
        assert not fired

    def test_path_must_be_string(self):
        s = _srv()
        out = _text(s._do_send_file({"chat_id": 100, "path": ""}))
        assert "'path' must be a non-empty string" in out

    def test_caption_must_be_string(self):
        s = _srv()
        out = _text(s._do_send_file({
            "chat_id": 100, "path": "x.pdf", "caption": 5,
        }))
        assert "'caption' must be a string" in out

    def test_caption_too_long(self):
        s = _srv()
        out = _text(s._do_send_file({
            "chat_id": 100, "path": "x.pdf",
            "caption": "c" * (MAX_CAPTION_LEN + 1),
        }))
        assert "caption" in out and "1024" in out

    def test_send_ok_reports_document_metadata(
        self, tg_env, monkeypatch,
    ):
        p = tg_env / "report.pdf"
        p.write_bytes(b"%PDF-1.4 fake")
        monkeypatch.setattr(
            "src.mcp_server.telegram_server.urllib.request.urlopen",
            _fake_open({}),
        )
        s = _srv()
        out = json.loads(_text(s._do_send_file({
            "chat_id": 100, "path": "uploads/report.pdf",
            "caption": "日報",
        })))
        assert out["ok"] is True
        assert out["message_id"] == 9
        assert out["file"] == "report.pdf"
        assert out["file_name"] == "report.pdf"
        assert out["mime_type"] == "application/pdf"
        assert out["size_bytes"] == 12345
        assert out["caption"] == "日報"
        assert out["sent_as"] == "document"

    def test_dispatch_via_handle_file(self, tg_env, monkeypatch):
        """``_call`` routes send_telegram_file to the file handler."""
        p = tg_env / "x.pdf"
        p.write_bytes(b"%PDF-1.4")
        monkeypatch.setattr(
            "src.mcp_server.telegram_server.urllib.request.urlopen",
            _fake_open({}),
        )
        s = _srv()
        handler = getattr(s, "_handle_file", None)
        assert handler is not None
        result = asyncio.run(handler(
            {"chat_id": 100, "path": "uploads/x.pdf"},
        ))
        assert json.loads(result[0].text)["message_id"] == 9
class TestFileToolRegistered:
    def test_tool_registered_and_dispatched(self):
        src = io.open(
            "src/mcp_server/telegram_server.py", encoding="utf-8",
        ).read()
        assert 'name="send_telegram_file"' in src
        assert 'if name == "send_telegram_file"' in src
        assert "_handle_file" in src
        assert "_send_document" in src
        assert '"sendDocument"' in src


class TestPromptMentionsFile:
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
        assert "send_telegram_file" in prompt
        assert "send_telegram_photo" in prompt

    def test_legacy_prompt_excluded(self, stub_agent):
        prompt = stub_agent._system_prompt(tools_enabled=False)
        assert "send_telegram_file" not in prompt