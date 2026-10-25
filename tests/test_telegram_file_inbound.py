"""Tests for the Telegram INBOUND file pipeline (v0.9.63.0+).

User report: 「mcp telegram對於從 tg上傳的檔案沒有回應，在log沒有
看到收接的回應」 — ``_handle_update`` only read ``msg["text"]``, so a
document / photo / video / audio message fell through the no-text
return and was SILENTLY ignored. Now: getFile → download →
filesystem WRITE sandbox (``uploads/telegram/<chat_id>/``) → framed
``chat_turn`` → reply. Mock-driven — no real network traffic.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import src.agent.telegram_bot as tb
from src.agent.telegram_bot import (
    TelegramBotDaemon,
    _extract_file_payload,
    _needs_photo_hallucination_retry,
    _sanitize_filename,
)


class _FakeClient:
    """Records getFile / download / sendMessage; returns canned bytes."""

    def __init__(self, data: bytes = b"%PDF-1.4 fake"):
        self.data = data
        self.updates: list[dict] = []
        self.get_file_calls: list[str] = []
        self.downloads: list[str] = []
        self.sent: list[tuple[int, str]] = []

    def get_updates(self, offset: int, wait: float = 10.0):
        return list(self.updates)

    def get_file(self, file_id: str) -> str:
        self.get_file_calls.append(file_id)
        return "documents/file_1"

    def download_file(self, file_path: str) -> bytes:
        self.downloads.append(file_path)
        return self.data

    def send_message(self, chat_id: int, text: str) -> None:
        self.sent.append((chat_id, text))


@pytest.fixture()
def tg_env(tmp_path, monkeypatch):
    """Hermetic env: WRITE root inside tmp_path + allowlist chat 100."""
    for var in (
        "QDRANT_FILESYSTEM_WRITE_ROOT",
        "WATCH_DIR",
        "TELEGRAM_ALLOWED_CHAT_IDS",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("QDRANT_FILESYSTEM_WRITE_ROOT", str(tmp_path))
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "100")
    return tmp_path


def _daemon(client, on_message=None) -> TelegramBotDaemon:
    return TelegramBotDaemon(
        client=client, on_message=on_message, speech=object(),
    )


def _text_sent(client: _FakeClient) -> str:
    assert client.sent, "no sendMessage call"
    return client.sent[-1][1]


class TestExtractFilePayload:
    def test_document_full(self):
        payload = _extract_file_payload({
            "document": {
                "file_id": "F1", "file_name": "report.pdf",
                "mime_type": "application/pdf", "file_size": 12345,
            },
            "caption": "幫我看",
        })
        assert payload["kind"] == "document"
        assert payload["file_id"] == "F1"
        assert payload["file_name"] == "report.pdf"
        assert payload["mime_type"] == "application/pdf"
        assert payload["file_size"] == 12345
        assert payload["caption"] == "幫我看"

    def test_document_missing_name_falls_back(self):
        payload = _extract_file_payload({"document": {"file_id": "F2"}})
        assert payload["kind"] == "document"
        assert payload["file_name"].startswith("document-")
        assert payload["file_name"].endswith(".bin")

    def test_photo_picks_largest_size(self):
        payload = _extract_file_payload({
            "photo": [
                {"file_id": "S", "width": 90, "height": 90},
                {"file_id": "B", "width": 800, "height": 600,
                 "file_size": 54321},
                {"file_id": "M", "width": 320, "height": 240},
            ],
        })
        assert payload["kind"] == "photo"
        assert payload["file_id"] == "B"
        assert payload["file_name"].startswith("photo-")
        assert payload["file_name"].endswith(".jpg")
        assert payload["mime_type"] == "image/jpeg"
        assert payload["file_size"] == 54321

    def test_video_audio_animation(self):
        v = _extract_file_payload({
            "video": {"file_id": "V", "mime_type": "video/mp4",
                      "file_size": 9},
        })
        assert v["kind"] == "video" and v["file_id"] == "V"
        a = _extract_file_payload({"audio": {"file_id": "A"}})
        assert a["kind"] == "audio"
        assert a["file_name"].endswith(".mp3")
        assert a["mime_type"] == "audio/mpeg"
        n = _extract_file_payload({"video_note": {"file_id": "N"}})
        assert n["kind"] == "video_note"
        an = _extract_file_payload({
            "animation": {"file_id": "AN", "file_name": "cat.gif"},
        })
        assert an["kind"] == "animation"
        assert an["file_name"] == "cat.gif"

    def test_plain_text_and_sticker_are_not_files(self):
        assert _extract_file_payload({"text": "hello"}) is None
        assert _extract_file_payload({"sticker": {"file_id": "S"}}) is None
        assert _extract_file_payload({}) is None

    def test_traversal_name_sanitized(self):
        payload = _extract_file_payload({
            "document": {"file_id": "F3", "file_name": "../../evil.sh"},
        })
        assert payload["file_name"] == "evil.sh"


class TestSanitizeFilename:
    def test_strips_separators_and_traversal(self):
        assert _sanitize_filename("..\\evil\\x.pdf") == "x.pdf"
        assert _sanitize_filename("/a/b/notes.md") == "notes.md"
        assert _sanitize_filename("...") == ""
        assert _sanitize_filename("") == ""
class TestSaveInbound:
    def test_happy_path_saves_under_write_root(self, tg_env):
        client = _FakeClient()
        d = _daemon(client)
        payload = _extract_file_payload({
            "document": {"file_id": "F1", "file_name": "report.pdf",
                         "mime_type": "application/pdf",
                         "file_size": 14},
        })
        saved, error = d._save_inbound_file(100, payload)
        assert error == ""
        target = Path(saved)
        assert target.name == "report.pdf"
        assert target.parent.name == "100"
        assert "uploads" in target.parent.parts
        assert "telegram" in target.parent.parts
        assert target.parent.parent.parent.name == "uploads"
        assert target.parent.parent.parent.parent == tg_env.resolve()
        assert target.read_bytes() == b"%PDF-1.4 fake"
        assert client.get_file_calls == ["F1"]

    def test_duplicate_name_gets_counter_suffix(self, tg_env):
        d = _daemon(_FakeClient())
        payload = _extract_file_payload({
            "document": {"file_id": "F1", "file_name": "report.pdf"},
        })
        saved1, _ = d._save_inbound_file(100, payload)
        saved2, _ = d._save_inbound_file(100, payload)
        assert Path(saved1).name == "report.pdf"
        assert Path(saved2).name == "report-1.pdf"

    def test_dangerous_ext_refused_no_download(self, tg_env):
        client = _FakeClient()
        d = _daemon(client)
        payload = {"kind": "document", "file_id": "F1",
                   "file_name": "evil.exe", "file_size": 10}
        saved, error = d._save_inbound_file(100, payload)
        assert saved == ""
        assert "拒收" in error and ".exe" in error
        assert not client.get_file_calls
        assert not client.downloads

    def test_oversize_preflight_refused_no_download(self, tg_env):
        client = _FakeClient()
        d = _daemon(client)
        payload = {"kind": "document", "file_id": "F1",
                   "file_name": "big.zip", "file_size": 21 * 1024 * 1024}
        saved, error = d._save_inbound_file(100, payload)
        assert saved == ""
        assert "20 MB" in error
        assert not client.get_file_calls

    def test_download_failure_returns_error(self, tg_env):
        class _Boom(_FakeClient):
            def get_file(self, file_id):
                raise RuntimeError("file is too big")

        d = _daemon(_Boom())
        payload = {"kind": "document", "file_id": "F1",
                   "file_name": "big.zip", "file_size": 0}
        saved, error = d._save_inbound_file(100, payload)
        assert saved == ""
        assert "下載失敗" in error


class TestHandleFileMessage:
    def test_document_flow_logs_saves_frames_and_replies(
        self, tg_env, caplog,
    ):
        client = _FakeClient()
        got = {}

        def on_message(chat_id, text):
            got["chat_id"] = chat_id
            got["text"] = text
            return "已收到並分析檔案。"

        d = _daemon(client, on_message)
        payload = _extract_file_payload({
            "document": {"file_id": "F1", "file_name": "report.pdf",
                         "mime_type": "application/pdf",
                         "file_size": 14},
            "caption": "分析這份報告",
        })
        with caplog.at_level("INFO", logger="src.agent.telegram_bot"):
            d._handle_file_message(100, payload)
        # the receipt log the user reported missing
        assert any("file from chat 100" in r.message for r in caplog.records)
        assert any("file saved" in r.message for r in caplog.records)
        # framed prompt carries the path + caption + hint
        assert "檔名: report.pdf" in got["text"]
        assert "已存到: " in got["text"]
        assert "使用者附註: 分析這份報告" in got["text"]
        assert "read_file" in got["text"]
        # reply delivered as text (files are never voice_in)
        assert client.sent[-1] == (100, "已收到並分析檔案。")
        assert got["chat_id"] == 100

    def test_photo_flow_uses_describe_hint(self, tg_env):
        client = _FakeClient()
        texts = []
        d = _daemon(client, lambda cid, t: texts.append(t) or "ok")
        payload = _extract_file_payload({
            "photo": [{"file_id": "P", "width": 100, "height": 100}],
        })
        d._handle_file_message(100, payload)
        assert "describe_image" in texts[0]
        saved = Path(
            [ln for ln in texts[0].splitlines()
             if ln.startswith("已存到:")][0].split("已存到: ")[1],
        )
        assert saved.suffix == ".jpg"
        assert saved.exists()

    def test_failure_replies_warning_without_llm_turn(self, tg_env):
        client = _FakeClient()
        calls: list[tuple] = []
        d = _daemon(client, lambda cid, t: calls.append((cid, t)))
        payload = {"kind": "document", "file_id": "F1",
                   "file_name": "evil.bat", "file_size": 10}
        d._handle_file_message(100, payload)
        assert not calls
        out = _text_sent(client)
        assert "⚠️ 檔案接收失敗" in out
        assert ".bat" in out

    def test_on_message_none_replies_wiring_note(self, tg_env):
        client = _FakeClient()
        d = _daemon(client)
        payload = _extract_file_payload({
            "document": {"file_id": "F1", "file_name": "a.txt"},
        })
        d._handle_file_message(100, payload)
        assert "未接線" in _text_sent(client)
# __HANDLE__
class TestHandleUpdateWiring:
    def test_document_update_routed_to_file_handler(self, tg_env):
        client = _FakeClient()
        texts: list[tuple] = []
        d = _daemon(client, lambda cid, t: texts.append((cid, t)) or "done")
        upd = {
            "update_id": 5,
            "message": {
                "message_id": 1, "chat": {"id": 100},
                "document": {"file_id": "F1",
                             "file_name": "report.pdf",
                             "mime_type": "application/pdf",
                             "file_size": 14},
                "caption": "看這個",
            },
        }
        d._handle_update(upd)
        assert len(texts) == 1
        assert "[Telegram 檔案上傳]" in texts[0][1]
        assert texts[0][0] == 100
        saved_line = [
            ln for ln in texts[0][1].splitlines()
            if ln.startswith("已存到:")
        ][0]
        assert Path(saved_line.split("已存到: ")[1]).exists()
        # offset advanced by poll_once normally; handle_update itself
        # does not touch it — the reply went out as text
        assert client.sent[-1] == (100, "done")

    def test_text_update_still_takes_normal_path(self, tg_env):
        client = _FakeClient()
        texts: list[tuple] = []
        d = _daemon(client, lambda cid, t: texts.append((cid, t)) or "ok")
        d._handle_update({
            "message": {"chat": {"id": 100}, "text": "哈囉"},
        })
        assert len(texts) == 1 and texts[0][1] == "哈囉"
        assert not client.get_file_calls

    def test_photo_update_routed_to_file_handler(self, tg_env):
        client = _FakeClient()
        texts: list[tuple] = []
        d = _daemon(client, lambda cid, t: texts.append((cid, t)) or "ok")
        d._handle_update({
            "message": {
                "chat": {"id": 100},
                "photo": [{"file_id": "P1", "width": 50, "height": 50}],
            },
        })
        assert len(texts) == 1
        assert "已存到: " in texts[0][1]
        assert Path(
            [ln for ln in texts[0][1].splitlines()
             if ln.startswith("已存到:")][0].split("已存到: ")[1],
        ).exists()

    def test_empty_message_ignored(self, tg_env):
        client = _FakeClient()
        texts: list[tuple] = []
        d = _daemon(client, lambda cid, t: texts.append((cid, t)) or "ok")
        d._handle_update({"message": {"chat": {"id": 100}}})
        d._handle_update({})
        assert not texts
        assert not client.sent
        assert not client.get_file_calls

    def test_poll_once_end_to_end_saves_and_replies(self, tg_env):
        client = _FakeClient()
        client.updates = [{
            "update_id": 7,
            "message": {
                "chat": {"id": 100},
                "document": {"file_id": "F9", "file_name": "data.csv",
                             "mime_type": "text/csv", "file_size": 6},
            },
        }]
        texts: list[tuple] = []
        d = _daemon(client, lambda cid, t: texts.append((cid, t)) or "ok")
        handled = d.poll_once()
        assert handled == 1
        assert d._offset == 8
        assert len(texts) == 1
        saved = Path(
            [ln for ln in texts[0][1].splitlines()
             if ln.startswith("已存到:")][0].split("已存到: ")[1],
        )
        assert saved.name == "data.csv"
        assert saved.read_bytes() == b"%PDF-1.4 fake"


class TestGuardHardening:
    def test_send_telegram_file_counts_as_legit_delivery(self):
        assert not _needs_photo_hallucination_retry(
            "抓大門攝影機畫面",
            "✅ 已發送到 Telegram（sent_as=document）",
            ["onvif_get_snapshot", "send_telegram_file"],
        )

    def test_no_delivery_tool_still_fires(self):
        assert _needs_photo_hallucination_retry(
            "抓大門攝影機畫面",
            "✅ 已發送到 Telegram（訊息 #219）",
            ["onvif_get_snapshot"],
        )


class TestSourceWiring:
    def test_module_exposes_new_surface(self):
        src = Path("src/agent/telegram_bot.py").read_text(
            encoding="utf-8",
        )
        assert "_extract_file_payload" in src
        assert "_handle_file_message" in src
        assert "_save_inbound_file" in src
        assert "uploads/telegram" in src
        assert "TELEGRAM_FILE_MAX_BYTES" in src
        assert "file from chat" in src
        assert tb._PHOTO_GUARD_TOOL_RE.search("send_telegram_file")
        assert tb._PHOTO_GUARD_TOOL_RE.search("send_telegram_photo")