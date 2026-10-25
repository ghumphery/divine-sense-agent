"""v0.9.58.0 — Telegram bidirectional voice (OpenRouter STT/TTS).

Live-verified on 2026-09-15 with the real OpenRouter key:
  TTS  hexgrad/kokoro-82m voice=zf_xiaoxiao (zh) → 35 KB mp3
  → ffmpeg transcode → 20 KB OggS (Opus, sendVoice-ready)
  → STT openai/gpt-4o-mini-transcribe (ogg→wav bridge) →
  「你好,這是語音雙向溝通的測試,今天天氣不錯。」
Also locked: OpenRouter voices are MANDATORY, response_format ∈
{mp3, pcm}, and the multipart filename must match the payload
container (wav bytes under a .ogg filename made the provider 400).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.agent.speech import (  # noqa: E402
    TTS_DEFAULT_MODEL,
    TTS_DEFAULT_VOICE,
    STT_DEFAULT_MODEL,
    SpeechClient,
    audio_format_of,
    strip_for_tts,
)

MP3_HEAD = b"\xff\xf3\x84\xc4"
OGG_HEAD = b"OggS"


@pytest.fixture()
def _env(monkeypatch):
    """Clean speech env: no keys by default; tests opt in."""
    for name in (
        "TELEGRAM_STT_API_KEY", "TELEGRAM_STT_BASE_URL",
        "TELEGRAM_STT_MODEL", "TELEGRAM_STT_LANGUAGE",
        "TELEGRAM_TTS_API_KEY", "TELEGRAM_TTS_BASE_URL",
        "TELEGRAM_TTS_MODEL", "TELEGRAM_TTS_VOICE",
        "OPENROUTER_API_KEY", "OPENAI_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    yield


class TestEnvResolution:
    def test_openrouter_key_reused_with_openrouter_base(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-abc")
        sc = SpeechClient.from_env()
        assert sc.stt_configured() and sc.tts_configured()
        assert sc.stt_base_url == "https://openrouter.ai/api/v1"
        assert sc.stt_model == STT_DEFAULT_MODEL
        assert sc.tts_model == TTS_DEFAULT_MODEL
        assert sc.tts_voice == TTS_DEFAULT_VOICE

    def test_dedicated_key_overrides(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-abc")
        monkeypatch.setenv("TELEGRAM_STT_API_KEY", "sk-xyz")
        monkeypatch.setenv("TELEGRAM_STT_BASE_URL",
                           "https://api.openai.com/v1")
        sc = SpeechClient.from_env()
        assert sc.stt_api_key == "sk-xyz"
        assert sc.stt_base_url == "https://api.openai.com/v1"

    def test_openai_key_falls_back_to_openai_base(self, _env, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
        sc = SpeechClient.from_env()
        assert sc.stt_api_key == "sk-openai"
        assert sc.stt_base_url == "https://api.openai.com/v1"

    def test_unconfigured_without_keys(self, _env, monkeypatch):
        sc = SpeechClient.from_env()
        assert not sc.stt_configured() and not sc.tts_configured()
        with pytest.raises(RuntimeError, match="STT"):
            sc.transcribe(b"xx")
        with pytest.raises(RuntimeError, match="TTS"):
            sc.synthesize("hi")


class TestHelpers:
    def test_strip_for_tts(self):
        assert strip_for_tts("**## 標題 | a | b**") == "標題 a b"
        assert "``" not in strip_for_tts("`code`")
        assert strip_for_tts("[text](http://x)") == "text"

    def test_audio_format_of(self):
        assert audio_format_of(b"OggS...") == "ogg"
        assert audio_format_of(b"ID3...") == "mp3"
        assert audio_format_of(b"RIFF....WAVE") == "wav"
        assert audio_format_of(b"\x01\x02") == "raw"

    def test_synthesize_mp3_and_markdown_strip(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-abc")
        sc = SpeechClient.from_env()
        captured = {}

        def fake_create(**kw):
            captured.update(kw)
            return MagicMock(content=OGG_HEAD + b"payload")

        fake = MagicMock()
        fake.audio.speech.create.side_effect = fake_create
        with patch("openai.OpenAI", lambda **kw: fake):
            audio, fmt = sc.synthesize("# **你好** | a | b")
        assert captured["input"] == "你好 a b"
        assert captured["model"] == TTS_DEFAULT_MODEL
        assert captured["voice"] == TTS_DEFAULT_VOICE
        assert captured["response_format"] == "mp3"
        assert fmt == "mp3" and audio.startswith(OGG_HEAD)

    def test_synthesize_pcm_fallback(self, monkeypatch):
        """mp3 rejected (400) → pcm retried (Gemini TTS path)."""
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-abc")
        sc = SpeechClient.from_env()
        fmts = []

        def fake_create(**kw):
            fmts.append(kw["response_format"])
            if kw["response_format"] == "mp3":
                from openai import BadRequestError
                raise BadRequestError(
                    message="Gemini TTS only supports pcm",
                    response=MagicMock(status_code=400), body=None,
                )
            return MagicMock(content=b"\x01\x02pcm-bytes")

        fake = MagicMock()
        fake.audio.speech.create.side_effect = fake_create
        with patch("openai.OpenAI", lambda **kw: fake):
            audio, fmt = sc.synthesize("hello")
        assert fmts == ["mp3", "pcm"]
        assert fmt == "pcm" and audio == b"\x01\x02pcm-bytes"

    def test_to_voice_ogg_passthrough_and_transcode(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-abc")
        sc = SpeechClient.from_env()
        # ogg passthrough — no transcode call
        assert sc.to_voice_ogg(OGG_HEAD + b"x") == OGG_HEAD + b"x"
        monkeypatch.setattr(
            SpeechClient, "_transcode",
            staticmethod(lambda data, fmt, codec, ext: OGG_HEAD + b"t"),
        )
        assert sc.to_voice_ogg(MP3_HEAD + b"x") == OGG_HEAD + b"t"
        assert sc.to_voice_ogg(b"\x01\x02pcm") == OGG_HEAD + b"t"

    def test_transcode_none_without_ffmpeg(self, monkeypatch):
        import src.agent.speech as sp
        monkeypatch.setattr(sp, "_find_ffmpeg", lambda: None)
        sc = SpeechClient.from_env()
        assert sc._transcode(b"data", "mp3", ["-c:a", "x"], "ogg") \
            is None
        # stt falls back to the ORIGINAL bytes (ogg) when no ffmpeg
        assert sc._to_wav_for_stt(b"OggSxx", "ogg") == b"OggSxx"


# ── bot client + daemon voice flow ───────────────────────────────

class _FakeClient:
    def __init__(self):
        self.voices = []
        self.texts = []
        self.downloads = 0

    def get_file(self, file_id):
        return "voice/file_1.ogg"

    def download_file(self, file_path):
        self.downloads += 1
        return b"OggS-voice-bytes"

    def send_message(self, chat_id, text):
        self.texts.append((chat_id, text))

    def send_voice(self, chat_id, audio, filename="reply.ogg"):
        self.voices.append((chat_id, audio, filename))


class _FakeSpeech:
    def __init__(self, transcript="明天下午三點開會", tts=True):
        self._t = transcript
        self._tts = tts
        self.spoken: list[str] = []

    def stt_configured(self):
        return True

    def tts_configured(self):
        return self._tts

    def transcribe(self, audio, filename="voice.ogg"):
        return self._t

    def synthesize(self, text):
        self.spoken.append(text)
        return OGG_HEAD + b"tts-audio", "ogg"

    def to_voice_ogg(self, audio):
        return audio


def _voice_update(chat_id=111, file_id="V1", duration=4.0):
    return {"update_id": 1, "message": {
        "chat": {"id": chat_id},
        "voice": {"file_id": file_id, "duration": duration},
    }}


@pytest.fixture()
def tg_home(tmp_path, monkeypatch):
    monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path / "home"))
    return tmp_path


@pytest.fixture()
def _tg_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "T")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "111")
    monkeypatch.delenv("QDRANT_TELEGRAM_BOT_POLLING", raising=False)
    yield


class TestDaemonVoiceFlow:
    def _daemon(self, client, speech, answers=("好的，已安排。",)):
        from src.agent.telegram_bot import TelegramBotDaemon

        calls = []

        def on_message(chat_id, text):
            calls.append((chat_id, text))
            return answers[0]

        return TelegramBotDaemon(
            client=client, on_message=on_message, speech=speech,
        ), calls

    def test_voice_in_voice_out(self, tg_home, _tg_env):
        client = _FakeClient()
        daemon, calls = self._daemon(client, _FakeSpeech())
        daemon._handle_update(_voice_update())
        # transcript framed to the LLM
        assert calls == [(111, "[語音訊息（已轉文字）]\n明天下午三點開會")]
        # v0.9.59.0 — BOTH channels: short voice note + full text
        assert len(client.voices) == 1
        assert client.voices[0][0] == 111
        assert client.voices[0][2] == "reply.ogg"
        assert len(client.texts) == 1
        assert "已安排" in client.texts[0][1]
        # the TTS input is the (short) spoken version
        speech = daemon.speech
        assert speech.spoken and speech.spoken[0]

    def test_voice_reply_env_off_text_only(self, tg_home, _tg_env,
                                           monkeypatch):
        monkeypatch.setenv("QDRANT_TELEGRAM_VOICE_REPLY", "off")
        client = _FakeClient()
        daemon, _ = self._daemon(client, _FakeSpeech())
        daemon._handle_update(_voice_update())
        assert client.voices == []
        assert len(client.texts) == 1

    def test_per_turn_text_only_request(self, tg_home, _tg_env):
        client = _FakeClient()
        daemon, _ = self._daemon(
            client, _FakeSpeech(transcript="說明一下，改用文字回覆"),
        )
        daemon._handle_update(_voice_update())
        assert client.voices == []
        assert len(client.texts) == 1

    def test_voice_uses_condensed_spoken_text(self, tg_home, _tg_env):
        client = _FakeClient()
        daemon, _ = self._daemon(
            client, _FakeSpeech(),
            answers=("這是口語摘要。\n\n## 詳細說明\n- a\n- b",),
        )
        daemon._handle_update(_voice_update())
        speech = daemon.speech
        # v0.9.60.1 — the spoken version is a ≤50-char deterministic
        # summary of the FULL answer (markdown stripped, sentences
        # packed) — never the title-only lead.
        assert speech.spoken == ["這是口語摘要。 詳細說明 a b"]
        assert len(speech.spoken[0]) <= 50
        # the FULL answer still arrives as text
        assert "詳細說明" in client.texts[0][1] or "## 詳細說明" \
            in client.texts[0][1]
        assert "a" in client.texts[0][1]

    def test_voice_spoken_long_answer_packed_not_title(
        self, tg_home, _tg_env,
    ):
        # the v0.9.59.0 failure mode: a long reply whose first line
        # reads like a title — the voice note must still carry the
        # CONTENT from the body, packed ≤50 chars.
        client = _FakeClient()
        daemon, _ = self._daemon(
            client, _FakeSpeech(),
            answers=("已為您整理完成。\n\n台積電今天上漲百分之二。"
                     "外資連續買超五天。分析師看好後市。",),
        )
        daemon._handle_update(_voice_update())
        spoken = daemon.speech.spoken[0]
        assert len(spoken) <= 50
        assert "台積電" in spoken or "外資" in spoken  # real content

    def test_voice_out_falls_back_to_text_without_tts(
        self, tg_home, _tg_env,
    ):
        client = _FakeClient()
        daemon, calls = self._daemon(
            client, _FakeSpeech(tts=False),
        )
        daemon._handle_update(_voice_update())
        assert len(calls) == 1
        assert client.voices == []
        assert len(client.texts) == 1
        assert "已安排" in client.texts[0][1]

    def test_voice_out_falls_back_on_tts_failure(self, tg_home, _tg_env):
        client = _FakeClient()

        class _BrokenTTS(_FakeSpeech):
            def synthesize(self, text):
                raise RuntimeError("tts down")

        daemon, _ = self._daemon(client, _BrokenTTS())
        daemon._handle_update(_voice_update())
        assert client.voices == []
        assert len(client.texts) == 1

    def test_transcribe_failure_replies_error(self, tg_home, _tg_env):
        client = _FakeClient()

        class _BrokenSTT(_FakeSpeech):
            def transcribe(self, audio, filename="voice.ogg"):
                raise RuntimeError("STT 未設定")

        daemon, calls = self._daemon(client, _BrokenSTT())
        daemon._handle_update(_voice_update())
        assert calls == []  # no LLM turn
        assert len(client.texts) == 1
        assert "無法轉寫" in client.texts[0][1]

    def test_voice_outside_allowlist_ignored(self, tg_home, _tg_env):
        client = _FakeClient()
        daemon, calls = self._daemon(client, _FakeSpeech())
        daemon._handle_update(_voice_update(chat_id=999))
        assert calls == [] and client.downloads == 0

    def test_text_message_still_text_reply(self, tg_home, _tg_env):
        client = _FakeClient()
        daemon, calls = self._daemon(client, _FakeSpeech(tts=False))
        upd = {"update_id": 2, "message": {
            "chat": {"id": 111}, "text": "hello",
        }}
        daemon._handle_update(upd)
        assert calls == [(111, "hello")]
        assert len(client.texts) == 1
        assert client.voices == []


# ── MCP tool send_telegram_voice ─────────────────────────────────

def _text(contents):
    return contents[0].text


class TestMcpVoiceTool:
    def _srv(self, monkeypatch, token="TOK", allowed=(100,)):
        from src.mcp_server.telegram_server import TelegramMCPServer

        return TelegramMCPServer(token=token, allowed_chat_ids=allowed)

    def test_token_required(self, monkeypatch):
        s = self._srv(monkeypatch, token="")
        out = _text(s._do_send_voice({"chat_id": 100, "text": "hi"}))
        assert "TELEGRAM_BOT_TOKEN is not configured" in out

    def test_allowlist_rejection(self, monkeypatch):
        s = self._srv(monkeypatch, allowed=(200,))
        out = _text(s._do_send_voice({"chat_id": 100, "text": "hi"}))
        assert "not in the allowlist" in out

    def test_bad_args(self, monkeypatch):
        s = self._srv(monkeypatch)
        assert "must be an integer" in _text(
            s._do_send_voice({"chat_id": "x", "text": "hi"}))
        assert "non-empty" in _text(
            s._do_send_voice({"chat_id": 100, "text": "  "}))
        assert "must be a string" in _text(
            s._do_send_voice({
                "chat_id": 100, "text": "hi", "caption": 5,
            }))

    def test_tts_unconfigured_error(self, monkeypatch):
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("TELEGRAM_TTS_API_KEY", raising=False)
        s = self._srv(monkeypatch)
        s.__dict__.pop("_speech_client", None)
        out = _text(s._do_send_voice({"chat_id": 100, "text": "hi"}))
        assert "TTS" in out

    def test_send_voice_dispatch(self, monkeypatch, _env):
        s = self._srv(monkeypatch)
        s.__dict__["_speech_client"] = _FakeSpeech()
        captured = {}

        def fake_post(token, method, fields, file_field, filename,
                      content_type, data, timeout=15.0):
            captured.update({
                "token": token, "method": method, "fields": fields,
                "file_field": file_field, "filename": filename,
                "mime": content_type, "data": data,
            })
            return {"message": {"message_id": 77}}

        import src.mcp_server.telegram_server as ts

        monkeypatch.setattr(ts, "_post_bot_multipart", fake_post)
        out = _text(s._do_send_voice({
            "chat_id": 100, "text": "**你好** 呀", "caption": "cc",
        }))
        body = json.loads(out)
        assert captured["method"] == "sendVoice"
        assert captured["file_field"] == "voice"
        assert captured["fields"]["chat_id"] == "100"
        assert captured["fields"]["caption"] == "cc"
        assert captured["data"].startswith(OGG_HEAD)
        assert body["ok"] is True and body["message_id"] == 77
        # spoken_chars reports the RAW input length (pre-strip)
        assert body["spoken_chars"] == len("**你好** 呀")

    def test_tool_registered_in_list(self):
        """Source guard: the 5th tool is registered in the tool list."""
        src = Path("src/mcp_server/telegram_server.py").read_text(
            encoding="utf-8",
        )
        assert 'name="send_telegram_voice"' in src
        assert 'if name == "send_telegram_voice":' in src
        assert '"sendVoice"' in src


class TestPromptVoiceBlock:
    def _prompt(self, tools_enabled=True):
        from src.agent.agent import Agent

        return Agent.__new__(Agent)._system_prompt(
            tools_enabled=tools_enabled,
        )

    def test_voice_rules_present(self):
        p = self._prompt()
        assert "語音訊息（已轉文字）" in p
        assert "send_telegram_voice" in p
        assert "用語音回覆" in p

    def test_voice_rules_absent_in_legacy(self):
        p = self._prompt(tools_enabled=False)
        assert "send_telegram_voice" not in p
        assert "語音訊息（已轉文字）" not in p

    def test_source_wiring(self):
        src = Path("src/agent/telegram_bot.py").read_text(
            encoding="utf-8",
        )
        assert "[語音訊息（已轉文字）]" in src
        assert "_handle_voice" in src
        assert "_deliver_reply" in src
        srv = Path("src/mcp_server/telegram_server.py").read_text(
            encoding="utf-8",
        )
        assert "send_telegram_voice" in srv
        assert "sendVoice" in srv

    def test_prompt_no_title_only_lead(self):
        """v0.9.60.1 — the LEAD-paragraph rule is GONE (it made models
        answer voice turns with a bare title); the spoken version is
        now described as auto-generated."""
        p = self._prompt()
        assert "so LEAD your answer" not in p
        assert "DETERMINISTIC ≤50-char" in p
        assert "NEVER answer with a bare" in p


# ── v0.9.60.1 — deterministic ≤50-char broadcast voice summary ────

class TestCondenseForVoice:
    def test_short_passes_through(self):
        from src.agent.telegram_bot import _condense_for_voice

        assert _condense_for_voice("好的。") == "好的。"

    def test_empty(self):
        from src.agent.telegram_bot import _condense_for_voice

        assert _condense_for_voice("") == ""
        assert _condense_for_voice(None) == ""

    def test_long_answer_packed_under_default_budget(self, monkeypatch):
        from src.agent.telegram_bot import _condense_for_voice

        monkeypatch.delenv("QDRANT_TELEGRAM_VOICE_MAX_CHARS",
                           raising=False)
        text = ("台積電今天收盤上漲百分之二。外資連續買超五天。"
                "分析師認為後市仍然樂觀，逢回可分批布局。"
                "以上是今日的重點整理與觀察。")
        out = _condense_for_voice(text)
        assert 0 < len(out) <= 50
        assert out != text
        assert "外資" in out  # more than just the FIRST sentence packed

    def test_first_sentence_over_budget_clause_cut(self, monkeypatch):
        from src.agent.telegram_bot import _condense_for_voice

        monkeypatch.delenv("QDRANT_TELEGRAM_VOICE_MAX_CHARS",
                           raising=False)
        long = "這是一段沒有句號只有逗號的長說明，" * 6
        out = _condense_for_voice(long)
        assert len(out) <= 50
        assert out.endswith("…")

    def test_markdown_stripped(self):
        from src.agent.telegram_bot import _condense_for_voice

        out = _condense_for_voice(
            "## 標題\n- 重點一\n- 重點二\n```\ncode\n```")
        assert "#" not in out and "```" not in out
        assert "重點一" in out and "重點二" in out

    def test_filler_lead_stripped(self):
        from src.agent.telegram_bot import _condense_for_voice

        out = _condense_for_voice("好的，以下是重點：第一，X。")
        assert not out.startswith("好的")
        assert "重點" in out

    def test_env_budget_override(self, monkeypatch):
        from src.agent.telegram_bot import _condense_for_voice

        monkeypatch.setenv("QDRANT_TELEGRAM_VOICE_MAX_CHARS", "20")
        text = "第一句話很長很長很長很長很長。第二句也很長。"
        out = _condense_for_voice(text)
        assert len(out) <= 20

    def test_env_budget_clamped_floor(self, monkeypatch):
        from src.agent.telegram_bot import _condense_for_voice

        monkeypatch.setenv("QDRANT_TELEGRAM_VOICE_MAX_CHARS", "1")
        text = "第一句話很長很長很長很長很長。"
        out = _condense_for_voice(text)
        assert len(out) <= 21  # clamp 20 (+ ellipsis)

    def test_tables_and_fences_skipped(self):
        from src.agent.telegram_bot import _condense_for_voice, \
            _strip_md_for_voice

        md = ("| a | b |\n|---|---|\n| 1 | 2 |\n\n正常句子。")
        plain = _strip_md_for_voice(md)
        assert "|" not in plain
        assert "正常句子" in plain