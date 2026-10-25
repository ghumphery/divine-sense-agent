"""v0.9.57.0 — Telegram intermittent-delivery fixes.

Diagnosis for 「送畫面到 telegram 有時可以收到有時收不到」:
  1. fixed 15 s timeout on the multipart photo upload (multi-MB
     uploads on a fluctuating uplink regularly exceed it);
  2. zero retries for Telegram 429 flood / 5xx blips / network hiccups;
  3. >10 MB photos refused (sendDocument allows 50 MB).
Locks down: size-scaled upload timeout, retry-after-aware bounded
retry on transient errors only, and the sendDocument downgrade.
"""
from __future__ import annotations

import json

import pytest

from src.mcp_server.telegram_server import (
    REQUEST_TIMEOUT_S,
    _bot_post_retry,
    _is_transient_error,
    _retry_after_s,
    _upload_timeout,
)


class TestUploadTimeout:
    def test_small_payload_floors_at_30s(self):
        assert _upload_timeout(1000) == 30.0

    def test_scales_with_bytes(self):
        assert _upload_timeout(1024 * 1024) == 30.0
        assert _upload_timeout(10 * 1024 * 1024) == pytest.approx(
            15 + 10 * 1024 * 1024 / 262144, abs=0.5,
        )
        assert _upload_timeout(10 * 1024 * 1024) > REQUEST_TIMEOUT_S


class TestTransientClassification:
    def test_429_and_5xx_transient(self):
        for code in (429, 500, 502, 503, 504, 524, 529):
            assert _is_transient_error(f"Telegram API HTTP {code}: boom")

    def test_permanent_not_transient(self):
        for code in (400, 401, 403, 404):
            assert not _is_transient_error(
                f"Telegram API HTTP {code}: bad"
            )

    def test_network_error_transient(self):
        assert _is_transient_error(
            "Telegram API network error: timed out"
        )
        assert _is_transient_error(
            "Telegram API network error: [Errno 11001] getaddrinfo"
        )

    def test_retry_after_parsed(self):
        body = ('Telegram API HTTP 429: {"ok":false,"error_code":429,'
                '"description":"Too Many Requests: retry after 5",'
                '"parameters":{"retry_after":5}}')
        assert _retry_after_s(body) == 5.0
        assert _retry_after_s("Telegram API HTTP 500: boom") is None


class _CM:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _resp_body(message_id=5):
    return json.dumps({
        "ok": True,
        "result": {"message_id": message_id, "photo": [{"width": 100}]},
    }).encode()


class TestRetry:
    def test_429_retry_after_then_success(self, monkeypatch):
        import urllib.error

        sleeps: list[float] = []
        monkeypatch.setattr(
            "src.mcp_server.telegram_server.time.sleep",
            lambda s: sleeps.append(s),
        )
        err_body = json.dumps({
            "ok": False, "error_code": 429,
            "description": "Too Many Requests: retry after 3",
            "parameters": {"retry_after": 3},
        }).encode()

        class _BodyErr(urllib.error.HTTPError):
            """429 WITH a body — _bot_post reads it via e.read()."""

            def __init__(self):
                super().__init__(
                    "https://api.telegram.org/botTOK/sendPhoto",
                    429, "Too Many Requests", hdrs=None, fp=None,
                )

            def read(self):
                return err_body

        state = {"n": 0}

        def fake_open(req, timeout=None):
            state["n"] += 1
            if state["n"] == 1:
                raise _BodyErr()
            return _CM(_resp_body(message_id=7))

        monkeypatch.setattr(
            "src.mcp_server.telegram_server.urllib.request.urlopen",
            fake_open,
        )
        req = object.__new__(type("R", (), {}))
        result = _bot_post_retry(req, timeout=10)
        assert result["message_id"] == 7
        assert sleeps == [3.0]  # honoured Telegram's retry_after
        assert state["n"] == 2

    def test_500_retries_with_backoff(self, monkeypatch):
        import urllib.error

        sleeps: list[float] = []
        monkeypatch.setattr(
            "src.mcp_server.telegram_server.time.sleep",
            lambda s: sleeps.append(s),
        )
        state = {"n": 0}

        def fake_open(req, timeout=None):
            state["n"] += 1
            if state["n"] < 3:
                raise urllib.error.HTTPError(
                    "https://api.telegram.org/botTOK/sendPhoto",
                    502, "Bad Gateway", hdrs=None, fp=None,
                )
            return _CM(_resp_body())

        monkeypatch.setattr(
            "src.mcp_server.telegram_server.urllib.request.urlopen",
            fake_open,
        )
        req = object.__new__(type("R", (), {}))
        result = _bot_post_retry(req, timeout=10)
        assert result["message_id"] == 5
        assert sleeps == [2.0, 4.0]  # exponential backoff
        assert state["n"] == 3

    def test_permanent_error_never_retried(self, monkeypatch):
        import urllib.error

        calls = {"n": 0}

        def fake_open(req, timeout=None):
            calls["n"] += 1
            raise urllib.error.HTTPError(
                "https://api.telegram.org/botTOK/sendPhoto",
                400, "Bad Request", hdrs=None, fp=None,
            )

        monkeypatch.setattr(
            "src.mcp_server.telegram_server.urllib.request.urlopen",
            fake_open,
        )
        req = object.__new__(type("R", (), {}))
        with pytest.raises(RuntimeError, match="HTTP 400"):
            _bot_post_retry(req, timeout=10)
        assert calls["n"] == 1

    def test_attempts_exhausted_raises(self, monkeypatch):
        import urllib.error

        sleeps: list[float] = []
        monkeypatch.setattr(
            "src.mcp_server.telegram_server.time.sleep",
            lambda s: sleeps.append(s),
        )

        def fake_open(req, timeout=None):
            raise urllib.error.URLError("timed out")

        monkeypatch.setattr(
            "src.mcp_server.telegram_server.urllib.request.urlopen",
            fake_open,
        )
        req = object.__new__(type("R", (), {}))
        with pytest.raises(RuntimeError, match="network error"):
            _bot_post_retry(req, timeout=10, attempts=3)
        assert sleeps == [2.0, 4.0]  # 3 attempts → 2 waits