"""v0.9.68.0 — web/GUI chat bidirectional voice bridge tests.

Locks the v0.9.68.0 voice surface: gui/api.py ``voice_stt`` (browser
MediaRecorder blob → STT text) + ``tts_speak`` (reply text → playable
data-URL audio), the SpeechClient container additions that make
browser audio work (EBML webm / ftyp mp4 sniffing + transcoding +
truthful multipart names), the real web-server ``POST /api/voice_stt``
/ ``/api/tts_speak`` round-trips, and the frontend wiring (mic button,
per-bubble 🔊, i18n ×4, styles).
"""
from __future__ import annotations

import http.client
import json
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gui.api import Api, _ui_voice_max_b64  # noqa: E402
import gui.api as gui_api  # noqa: E402
import src.agent.speech as speech_mod  # noqa: E402
from src.agent.speech import (  # noqa: E402
    SpeechClient,
    audio_format_of,
)

WEBM = b"\x1aE\xdf\xa3" + b"\x00" * 16
MP4 = b"\x00\x00\x00\x18ftypm4a\x00" + b"\x00" * 8
OGG = b"OggS" + b"\x00" * 8
WAV = b"RIFF\x00\x00\x00\x00WAVE" + b"\x00" * 8
MP3 = b"\xff\xf3\x84\xc4" + b"\x00" * 8


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for name in (
        "QDRANT_UI_VOICE_MAX_MB",
        "TELEGRAM_STT_API_KEY", "TELEGRAM_STT_BASE_URL",
        "TELEGRAM_STT_MODEL", "TELEGRAM_STT_LANGUAGE",
        "TELEGRAM_TTS_API_KEY", "TELEGRAM_TTS_BASE_URL",
        "TELEGRAM_TTS_MODEL", "TELEGRAM_TTS_VOICE",
        "OPENROUTER_API_KEY", "OPENAI_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    gui_api._TTS_UI_CACHE.clear()
    yield
    gui_api._TTS_UI_CACHE.clear()


def _api() -> Api:
    return Api.__new__(Api)


class _StubSC:
    """Shared SpeechClient stub — class-level dispatch records."""

    calls: list = []

    def __init__(self, stt=True, tts=True):
        self._stt = stt
        self._tts = tts

    def stt_configured(self):
        return self._stt

    def tts_configured(self):
        return self._tts

    def transcribe(self, data, filename="voice.ogg"):
        _StubSC.calls.append(("stt", bytes(data), filename))
        return "辨識文字"

    def synthesize(self, text):
        _StubSC.calls.append(("tts", text))
        return (b"\xff\xf3MP3BYTES", "mp3")

    def to_browser_audio(self, data, container):
        if container == "mp3":
            return bytes(data), "audio/mpeg"
        if container == "ogg":
            return bytes(data), "audio/ogg"
        return None


# __PART2__


# ─── speech.py container sniff + bridges ─────────────────────────


class TestContainerSniff:
    def test_webm_sniffed(self):
        assert audio_format_of(WEBM) == "webm"

    def test_mp4_sniffed(self):
        assert audio_format_of(MP4) == "mp4"

    def test_classic_containers_unchanged(self):
        assert audio_format_of(OGG) == "ogg"
        assert audio_format_of(WAV) == "wav"
        assert audio_format_of(MP3) == "mp3"
        assert audio_format_of(b"????") == "raw"

    def test_webm_transcoded_to_wav(self, monkeypatch):
        sc = SpeechClient()
        seen = {}

        def fake_transcode(data, fmt, codec_args, out_ext):
            seen["fmt"] = fmt
            return WAV

        # monkeypatch auto-restores the class attribute
        monkeypatch.setattr(
            SpeechClient, "_transcode", staticmethod(fake_transcode))
        out = sc._to_wav_for_stt(WEBM, audio_format_of(WEBM))
        assert seen["fmt"] == "webm"
        assert audio_format_of(out) == "wav"

    def test_stt_names_cover_webm(self):
        assert speech_mod._STT_NAMES["webm"] == (
            "voice.webm", "audio/webm")
        assert speech_mod._STT_NAMES["mp4"] == ("audio.m4a", "audio/mp4")

    def test_to_browser_audio_passthrough(self):
        sc = SpeechClient()
        audio, mime = sc.to_browser_audio(b"X", "mp3")
        assert (audio, mime) == (b"X", "audio/mpeg")
        audio, mime = sc.to_browser_audio(b"X", "ogg")
        assert (audio, mime) == (b"X", "audio/ogg")

    def test_to_browser_audio_pcm_no_ffmpeg(self, monkeypatch):
        sc = SpeechClient()

        def fail_transcode(*a, **kw):
            return None

        monkeypatch.setattr(
            SpeechClient, "_transcode", staticmethod(fail_transcode))
        assert sc.to_browser_audio(b"PCM", "pcm") is None


# __PART3__


# ─── gui/api.py voice_stt ────────────────────────────────────────


class TestApiVoiceStt:
    def test_ok_decodes_and_transcribes(self, monkeypatch):
        import base64 as b64

        stub = _StubSC()
        monkeypatch.setattr(speech_mod, "SpeechClient", lambda: stub)
        r = _api().voice_stt(
            b64.b64encode(WEBM).decode(), "voice.webm")
        assert r["ok"] is True and r["text"] == "辨識文字"
        kind, data, fname = _StubSC.calls[-1]
        assert kind == "stt" and data == WEBM and fname == "voice.webm"

    def test_data_url_prefix_tolerated(self, monkeypatch):
        import base64 as b64

        stub = _StubSC()
        monkeypatch.setattr(speech_mod, "SpeechClient", lambda: stub)
        r = _api().voice_stt(
            "data:audio/webm;base64,"
            + b64.b64encode(WEBM).decode(), "voice.webm")
        assert r["ok"] is True
        assert _StubSC.calls[-1][1] == WEBM

    def test_empty_and_invalid(self, monkeypatch):
        monkeypatch.setattr(speech_mod, "SpeechClient", _StubSC)
        _StubSC.calls.clear()
        assert _api().voice_stt("")["ok"] is False
        # a data URL with an EMPTY payload → stripped to ""
        assert _api().voice_stt("data:audio/webm;base64,")["ok"] is False
        assert _api().voice_stt("@@@not-base64@@@")["ok"] is False
        _StubSC.calls.clear()

    def test_oversize_capped(self, monkeypatch):
        monkeypatch.setenv("QDRANT_UI_VOICE_MAX_MB", "1")
        monkeypatch.setattr(speech_mod, "SpeechClient", _StubSC)
        _StubSC.calls.clear()
        big = "A" * (1024 * 1024 + 16)
        r = _api().voice_stt(big, "voice.webm")
        assert r["ok"] is False and "too large" in r["error"]
        assert not _StubSC.calls

    def test_unconfigured_clear_error(self, monkeypatch):
        class _Off(_StubSC):
            def __init__(self):
                pass

            def stt_configured(self):
                return False

        monkeypatch.setattr(speech_mod, "SpeechClient", _Off)
        r = _api().voice_stt("QUJD", "voice.webm")
        assert r["ok"] is False and "STT" in r["error"]

    def test_bad_filename_falls_back(self, monkeypatch):
        stub = _StubSC()
        monkeypatch.setattr(speech_mod, "SpeechClient", lambda: stub)
        import base64 as b64

        r = _api().voice_stt(
            b64.b64encode(WEBM).decode(), "..\\evil.exe")
        assert r["ok"] is True
        assert _StubSC.calls[-1][2] == "voice.webm"

    def test_transcribe_crash_becomes_error(self, monkeypatch):
        class _Boom(_StubSC):
            def transcribe(self, data, filename="voice.ogg"):
                raise RuntimeError("provider down")

        monkeypatch.setattr(speech_mod, "SpeechClient", lambda: _Boom())
        r = _api().voice_stt("QUJD", "voice.webm")
        # v0.9.127.12 — the raw「provider down」repr is HUMANISED away
        # (the retry + fallback layers ran before this lands).
        assert r["ok"] is False
        assert "provider down" not in r["error"]
        assert "暫時不可用" in r["error"]


# __PART4__


# ─── gui/api.py tts_speak ────────────────────────────────────────


class TestApiTtsSpeak:
    def test_ok_mp3_data_url(self, monkeypatch):
        monkeypatch.setattr(speech_mod, "SpeechClient", _StubSC)
        r = _api().tts_speak("你好，世界")
        assert r["ok"] is True
        assert r["mime"] == "audio/mpeg"
        import base64 as b64

        assert b64.b64decode(r["data_base64"]).startswith(b"\xff\xf3")
        _StubSC.calls.clear()

    def test_empty_text(self, monkeypatch):
        monkeypatch.setattr(speech_mod, "SpeechClient", _StubSC)
        assert _api().tts_speak("")["ok"] is False
        assert _api().tts_speak("   ")["ok"] is False

    def test_unconfigured(self, monkeypatch):
        class _Off(_StubSC):
            def tts_configured(self):
                return False

        monkeypatch.setattr(speech_mod, "SpeechClient", _Off)
        r = _api().tts_speak("hi")
        assert r["ok"] is False and "TTS" in r["error"]

    def test_cache_hit_skips_synthesize(self, monkeypatch):
        monkeypatch.setattr(speech_mod, "SpeechClient", _StubSC)
        first = _api().tts_speak("同一句話")
        second = _api().tts_speak("同一句話")
        assert first["ok"] and second["ok"]
        assert second.get("cached") is True
        assert "cached" not in first
        assert len([c for c in _StubSC.calls if c[0] == "tts"]) == 1
        _StubSC.calls.clear()

    def test_cache_lru_eviction(self, monkeypatch):
        monkeypatch.setattr(speech_mod, "SpeechClient", _StubSC)
        monkeypatch.setattr(gui_api, "_TTS_UI_CACHE_MAX", 2)
        for t in ("a", "b", "c", "d"):
            assert _api().tts_speak(t)["ok"] is True
        assert len(gui_api._TTS_UI_CACHE) <= 2
        _StubSC.calls.clear()

    def test_pcm_without_ffmpeg_degrades(self, monkeypatch):
        class _Pcm(_StubSC):
            def synthesize(self, text):
                return (b"\x00\x01PCM", "pcm")

            def to_browser_audio(self, data, container):
                return None

        monkeypatch.setattr(speech_mod, "SpeechClient", _Pcm)
        r = _api().tts_speak("pcm case")
        assert r["ok"] is False and "PCM" in r["error"]


class TestResolver:
    def test_default_8mb(self, monkeypatch):
        monkeypatch.delenv("QDRANT_UI_VOICE_MAX_MB", raising=False)
        assert _ui_voice_max_b64() == 8 * 1024 * 1024

    def test_env_override_and_floor(self, monkeypatch):
        monkeypatch.setenv("QDRANT_UI_VOICE_MAX_MB", "2")
        assert _ui_voice_max_b64() == 2 * 1024 * 1024
        monkeypatch.setenv("QDRANT_UI_VOICE_MAX_MB", "0")
        assert _ui_voice_max_b64() >= 1024 * 1024
        monkeypatch.setenv("QDRANT_UI_VOICE_MAX_MB", "junk")
        assert _ui_voice_max_b64() == 8 * 1024 * 1024


# __PART5__


# ─── real HTTP round-trips (web mode) ────────────────────────────


class _HttpSrv:
    def __init__(self, api, assets_dir: Path):
        from gui.web_server import make_handler

        handler = make_handler(api, assets_dir)
        from http.server import ThreadingHTTPServer

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True,
        )

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()

    def post(self, path: str, body: str) -> dict:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request(
                "POST", path, body=body,
                headers={"Content-Type": "application/json"},
            )
            r = conn.getresponse()
            return json.loads(r.read().decode("utf-8"))
        finally:
            conn.close()


@pytest.fixture
def real_assets() -> Path:
    return Path(__file__).resolve().parents[1] / "gui" / "assets"


class TestWebVoiceRoundTrips:
    def test_voice_stt_round_trip(self, real_assets, monkeypatch):
        import base64 as b64

        stub = _StubSC()
        monkeypatch.setattr(speech_mod, "SpeechClient", lambda: stub)
        with _HttpSrv(_api(), real_assets) as srv:
            out = srv.post(
                "/api/voice_stt",
                json.dumps([b64.b64encode(WEBM).decode(), "voice.webm"]),
            )
        assert out["ok"] is True
        assert out["result"]["ok"] is True
        assert out["result"]["text"] == "辨識文字"
        assert _StubSC.calls[-1][2] == "voice.webm"
        _StubSC.calls.clear()

    def test_tts_speak_round_trip(self, real_assets, monkeypatch):
        import base64 as b64

        monkeypatch.setattr(speech_mod, "SpeechClient", _StubSC)
        with _HttpSrv(_api(), real_assets) as srv:
            out = srv.post("/api/tts_speak", json.dumps(["朗讀測試"]))
        assert out["ok"] is True
        res = out["result"]
        assert res["ok"] is True and res["mime"] == "audio/mpeg"
        assert b64.b64decode(res["data_base64"]).startswith(b"\xff\xf3")
        _StubSC.calls.clear()


# ─── frontend wiring (source guards) ─────────────────────────────


def _src(p: str) -> str:
    return Path(p).read_text(encoding="utf-8", errors="replace")


class TestFrontendWiring:
    def test_index_has_mic_button(self):
        html = _src("gui/assets/index.html")
        assert 'id="chat-mic-btn"' in html
        assert 'data-i18n="voiceMic"' in html

    def test_app_js_recorder_flow(self):
        js = _src("gui/assets/app.js")
        for fn in ("toggleMicRecording", "_transcribeBlob", "bindMic",
                   "_blobToBase64", "speakChatText"):
            assert "function " + fn in js, fn
        assert "api.call('voice_stt'" in js
        assert "api.call('tts_speak'" in js
        assert "MediaRecorder(stream)" in js
        assert "_VOICE_REC_MAX_MS = 60000" in js

    def test_render_chat_speak_button(self):
        js = _src("gui/assets/app.js")
        assert "btn-msg-voice" in js
        assert "speakChatText(vbtn, btext)" in js

    def test_bind_events_wires_mic(self):
        js = _src("gui/assets/app.js")
        assert "bindMic();" in js

    def test_i18n_four_locales(self):
        js = _src("gui/assets/app.js")
        for key in ("voiceMic", "voiceRecording", "voiceTranscribing",
                    "voiceSpeak", "voiceFail", "voiceMicErr",
                    "voiceNoSupport"):
            assert js.count(key + ":") >= 4, key

    def test_css_styles(self):
        css = _src("gui/assets/style.css")
        assert ".btn-mic-rec" in css
        assert ".btn-msg-voice" in css
        assert "voice-rec-pulse" in css