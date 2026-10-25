"""Tests for the Telegram MCP server (v0.8.1+).

Mock-driven: no real HTTP traffic. We monkeypatch the stdlib
``urllib.request.urlopen`` so we can simulate:

* Telegram returns ``{"ok": true, "result": {...}}``
* Telegram returns ``{"ok": false, "description": "..."}``
* Network errors (URLError)
* Non-JSON responses (HTTPError with garbage body)
* Timeouts
"""
from __future__ import annotations

import io
import json
import os
import sys
from typing import Any
from unittest.mock import patch

import pytest

from src.mcp_server.telegram_server import (
    MAX_MESSAGE_LEN,
    TELEGRAM_API_BASE,
    TelegramMCPServer,
    _is_allowed,
    _load_allowed_ids,
    _post_bot_method,
    _send_text,
)


# ──────────────────────────────────────────────────────────────
# helpers
# ──────────────────────────────────────────────────────────────


class _FakeResp:
    """Stand-in for ``urllib.addinfourl`` returned by urlopen()."""

    def __init__(self, body: bytes):
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _ok_result(message_id: int = 12345) -> bytes:
    return json.dumps({
        "ok": True,
        "result": {
            "message_id": message_id,
            "date": 1700000000,
            "chat": {"id": 100, "type": "private"},
            "text": "hi",
        },
    }).encode("utf-8")


def _err_result(code: int = 400, desc: str = "bad request") -> bytes:
    return json.dumps({
        "ok": False,
        "error_code": code,
        "description": desc,
    }).encode("utf-8")


def _http_error(code: int, body: bytes):
    from urllib.error import HTTPError
    return HTTPError(
        url="https://api.telegram.org/botTOK/sendMessage",
        code=code,
        msg="Bad Request",
        hdrs={},
        fp=io.BytesIO(body),
    )


# ──────────────────────────────────────────────────────────────
# allowlist helpers
# ──────────────────────────────────────────────────────────────


class TestLoadAllowedIds:
    def test_empty_when_unset(self, monkeypatch):
        monkeypatch.delenv("TELEGRAM_ALLOWED_CHAT_IDS", raising=False)
        assert _load_allowed_ids() == []

    def test_empty_when_blank(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "")
        assert _load_allowed_ids() == []

    def test_parses_single(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "123")
        assert _load_allowed_ids() == [123]

    def test_parses_comma_separated(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "1,2,3")
        assert _load_allowed_ids() == [1, 2, 3]

    def test_handles_whitespace(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", " 10 , 20 ,30 ")
        assert _load_allowed_ids() == [10, 20, 30]

    def test_skips_non_integer_tokens(self, monkeypatch):
        monkeypatch.setenv(
            "TELEGRAM_ALLOWED_CHAT_IDS", "1, not-a-number ,2, ",
        )
        assert _load_allowed_ids() == [1, 2]

    def test_negative_ids_allowed(self, monkeypatch):
        """Negative chat IDs are valid for group chats."""
        monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "-100123,42")
        assert _load_allowed_ids() == [-100123, 42]


class TestIsAllowed:
    def test_match(self):
        assert _is_allowed(42, [1, 42, 99]) is True

    def test_no_match(self):
        assert _is_allowed(42, [1, 2, 3]) is False

    def test_empty_allowlist(self):
        assert _is_allowed(42, []) is False


# ──────────────────────────────────────────────────────────────
# stdlib HTTP wrapper
# ──────────────────────────────────────────────────────────────


class TestPostBotMethod:
    def test_success_returns_result(self):
        with patch(
            "src.mcp_server.telegram_server.urllib.request.urlopen",
            return_value=_FakeResp(_ok_result(message_id=999)),
        ) as m:
            res = _post_bot_method(
                "TOK", "sendMessage", {"chat_id": 1, "text": "x"},
            )
        assert res["message_id"] == 999
        req = m.call_args.args[0]
        assert req.get_full_url() == f"{TELEGRAM_API_BASE}/botTOK/sendMessage"
        assert req.get_method() == "POST"
        assert (
            req.get_header("Content-type")
            == "application/x-www-form-urlencoded"
        )

    def test_http_error_with_json_body_raises(self):
        err = _http_error(400, b'{"ok":false,"description":"chat not found"}')
        with patch(
            "src.mcp_server.telegram_server.urllib.request.urlopen",
            side_effect=err,
        ):
            with pytest.raises(RuntimeError) as exc:
                _post_bot_method("TOK", "sendMessage", {})
        assert "HTTP 400" in str(exc.value)
        assert "chat not found" in str(exc.value)

    def test_url_error_raises(self):
        from urllib.error import URLError
        with patch(
            "src.mcp_server.telegram_server.urllib.request.urlopen",
            side_effect=URLError("dns down"),
        ):
            with pytest.raises(RuntimeError) as exc:
                _post_bot_method("TOK", "sendMessage", {})
        assert "network error" in str(exc.value).lower()

    def test_non_json_body_raises(self):
        with patch(
            "src.mcp_server.telegram_server.urllib.request.urlopen",
            return_value=_FakeResp(b"<html>500 server error</html>"),
        ):
            with pytest.raises(RuntimeError) as exc:
                _post_bot_method("TOK", "sendMessage", {})
        assert "non-JSON" in str(exc.value)

    def test_ok_false_payload_raises(self):
        with patch(
            "src.mcp_server.telegram_server.urllib.request.urlopen",
            return_value=_FakeResp(_err_result(403, "forbidden")),
        ):
            with pytest.raises(RuntimeError) as exc:
                _post_bot_method("TOK", "sendMessage", {})
        assert "403" in str(exc.value)
        assert "forbidden" in str(exc.value)

    def test_send_text_includes_parse_mode(self):
        captured: dict[str, Any] = {}

        def fake_open(req, timeout=None):
            captured["url"] = req.get_full_url()
            captured["body"] = req.data.decode("utf-8")
            return _FakeResp(_ok_result())

        with patch(
            "src.mcp_server.telegram_server.urllib.request.urlopen",
            side_effect=fake_open,
        ):
            _send_text("TOK", 1, "hi", parse_mode="MarkdownV2")
        assert "parse_mode=MarkdownV2" in captured["body"]
        assert "chat_id=1" in captured["body"]
        assert "text=hi" in captured["body"]


# ──────────────────────────────────────────────────────────────
# TelegramMCPServer construction
# ──────────────────────────────────────────────────────────────


class TestServerConstruction:
    def test_init_reads_env_token_and_allowlist(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "T")
        monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "1,2")
        s = TelegramMCPServer()
        assert s.token == "T"
        assert s.allowed == [1, 2]

    def test_init_uses_injected_args_over_env(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "from-env")
        s = TelegramMCPServer(
            token="from-arg", allowed_chat_ids=[99],
        )
        assert s.token == "from-arg"
        assert s.allowed == [99]

    def test_init_empty_defaults(self, monkeypatch):
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        monkeypatch.delenv("TELEGRAM_ALLOWED_CHAT_IDS", raising=False)
        s = TelegramMCPServer()
        assert s.token == ""
        assert s.allowed == []


# ──────────────────────────────────────────────────────────────
# Send paths (allowlist + validation + telegram API)
# ──────────────────────────────────────────────────────────────


class TestSendTelegramMessage:
    """Drive the synchronous ``_do_send`` directly so we don't need
    the MCP async plumbing — keeps tests fast and isolated."""

    def _srv(self, monkeypatch, allowed=(100,)):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "TOK")
        return TelegramMCPServer(
            token="TOK", allowed_chat_ids=list(allowed),
        )

    def test_sends_to_allowed_chat(self, monkeypatch):
        srv = self._srv(monkeypatch, allowed=[100])
        with patch(
            "src.mcp_server.telegram_server._send_text",
            return_value={"message": {"message_id": 777}},
        ) as send:
            res = srv._do_send(
                {"chat_id": 100, "text": "hi"}, parse_mode=None,
            )
        assert send.called
        assert send.call_args.kwargs["parse_mode"] is None
        body = json.loads(res[0].text)
        assert body["ok"] is True
        assert body["chat_id"] == 100
        assert body["message_id"] == 777

    def test_rejects_chat_not_in_allowlist(self, monkeypatch):
        srv = self._srv(monkeypatch, allowed=[100])
        with patch(
            "src.mcp_server.telegram_server._send_text",
        ) as send:
            res = srv._do_send(
                {"chat_id": 999, "text": "sneaky"}, parse_mode=None,
            )
        assert not send.called
        assert res[0].text.startswith("Error:")
        assert "999" in res[0].text
        assert "allowlist" in res[0].text

    def test_rejects_when_no_token(self, monkeypatch):
        srv = TelegramMCPServer(token="", allowed_chat_ids=[100])
        with patch(
            "src.mcp_server.telegram_server._send_text",
        ) as send:
            res = srv._do_send(
                {"chat_id": 100, "text": "x"}, parse_mode=None,
            )
        assert not send.called
        assert "TELEGRAM_BOT_TOKEN" in res[0].text



    def test_validates_chat_id_type(self, monkeypatch):
        srv = self._srv(monkeypatch)
        for bad in ("not-int", 1.5, None, [100]):
            res = srv._do_send(
                {"chat_id": bad, "text": "x"}, parse_mode=None,
            )
            assert res[0].text.startswith("Error:"), (
                f"chat_id={bad!r} should be rejected"
            )

    def test_rejects_bool_chat_id(self, monkeypatch):
        """``True`` is technically an int — we must exclude it."""
        srv = self._srv(monkeypatch)
        res = srv._do_send(
            {"chat_id": True, "text": "x"}, parse_mode=None,
        )
        assert res[0].text.startswith("Error:")
        assert "integer" in res[0].text

    def test_validates_text_must_be_nonempty_string(self, monkeypatch):
        srv = self._srv(monkeypatch)
        for bad in ("", "   ", None, 123, []):
            res = srv._do_send(
                {"chat_id": 100, "text": bad}, parse_mode=None,
            )
            assert "non-empty string" in res[0].text, (
                f"text={bad!r} should be rejected"
            )

    def test_validates_text_length(self, monkeypatch):
        srv = self._srv(monkeypatch)
        long_text = "x" * (MAX_MESSAGE_LEN + 1)
        res = srv._do_send(
            {"chat_id": 100, "text": long_text}, parse_mode=None,
        )
        assert "4096" in res[0].text or str(MAX_MESSAGE_LEN) in res[0].text

    def test_surfaces_api_error(self, monkeypatch):
        srv = self._srv(monkeypatch)
        with patch(
            "src.mcp_server.telegram_server._send_text",
            side_effect=RuntimeError("Telegram API error: 403 forbidden"),
        ):
            res = srv._do_send(
                {"chat_id": 100, "text": "x"}, parse_mode=None,
            )
        assert res[0].text.startswith("Error:")
        assert "403" in res[0].text

    def test_markdown_passes_parse_mode(self, monkeypatch):
        srv = self._srv(monkeypatch)
        with patch(
            "src.mcp_server.telegram_server._send_text",
            return_value={"message": {"message_id": 1}},
        ) as send:
            srv._do_send(
                {"chat_id": 100, "text": "*hi*"},
                parse_mode="MarkdownV2",
            )
        assert send.call_args.kwargs["parse_mode"] == "MarkdownV2"


# ──────────────────────────────────────────────────────────────
# list_allowed_chats
# ──────────────────────────────────────────────────────────────


class TestListAllowed:
    def test_returns_ids_and_bot_flag(self, monkeypatch):
        srv = TelegramMCPServer(
            token="TOK", allowed_chat_ids=[10, 20],
        )
        res = srv._do_list_allowed()
        body = json.loads(res[0].text)
        assert body["count"] == 2
        assert body["allowed_chat_ids"] == [10, 20]
        assert body["bot_configured"] is True

    def test_bot_unconfigured_reported(self, monkeypatch):
        srv = TelegramMCPServer(token="", allowed_chat_ids=[10])
        body = json.loads(srv._do_list_allowed()[0].text)
        assert body["bot_configured"] is False
        assert body["allowed_chat_ids"] == [10]

    def test_empty_allowlist_count_zero(self, monkeypatch):
        srv = TelegramMCPServer(token="TOK", allowed_chat_ids=[])
        body = json.loads(srv._do_list_allowed()[0].text)
        assert body["count"] == 0
        assert body["allowed_chat_ids"] == []


# ──────────────────────────────────────────────────────────────
# Real MCP protocol smoke (manual, not in pytest)
# ──────────────────────────────────────────────────────────────
#
# The integration test in ``_smoke_telegram_mcp.py`` spawns the
# server in a real subprocess and drives it over the MCP JSON-RPC
# protocol via ``mcp.client.stdio.stdio_client``. It needs an
# async test runner (pytest-asyncio) which is not in our
# ``pyproject.toml [test]`` extras to keep the dep list minimal.
# Run it manually when validating a release:
#
#     python _smoke_telegram_mcp.py
#
# It asserts:
#   * The 3 tools are advertised in ``list_tools``.
#   * ``list_allowed_chats`` returns the env-configured allowlist.
#   * ``send_telegram_message(chat_id=non-allowed)`` is rejected
#     with a clear error string, BEFORE any HTTP request is made.

    def test_rejects_when_empty_allowlist(self, monkeypatch):
        srv = TelegramMCPServer(token="TOK", allowed_chat_ids=[])
        with patch(
            "src.mcp_server.telegram_server._send_text",
        ) as send:
            res = srv._do_send(
                {"chat_id": 100, "text": "x"}, parse_mode=None,
            )
        assert not send.called
        assert "ALLOWED_CHAT_IDS" in res[0].text



