"""v0.9.127.12 — STT resilience: transient provider failures (the
live 2026-10-16 19:58「provider down」502 pair from the OpenRouter
STT endpoint) now RETRY twice with a short backoff, then fall back
to the SiliconFlow SenseVoice endpoint (the key the project already
carries); the user-facing error is humanised instead of a raw repr.
"""
from pathlib import Path
import sys
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import src.agent.speech as speech_mod  # noqa: E402


class _FakeResp:
    def __init__(self, text):
        self.text = text


class _FakeTranscriptions:
    calls = []

    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        _FakeTranscriptions.calls.append(kwargs.get("model"))
        step = _FakeTranscriptions.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


class _FakeAudio:
    def __init__(self):
        self.transcriptions = _FakeTranscriptions()


class _FakeClient:
    """Fake openai.OpenAI: records base_url, drives a shared script."""

    script = []
    base_urls = []

    def __init__(self, base_url="", api_key="", timeout=None):
        self.base_url = base_url
        _FakeClient.base_urls.append(base_url)
        self.audio = _FakeAudio()


def _clean_env(monkeypatch):
    for var in ("TELEGRAM_STT_API_KEY", "OPENROUTER_API_KEY",
                "OPENAI_API_KEY", "SILICONFLOW_API_KEY",
                "TELEGRAM_STT_FALLBACK_API_KEY",
                "TELEGRAM_STT_FALLBACK_BASE_URL",
                "TELEGRAM_STT_FALLBACK_MODEL",
                "TELEGRAM_STT_LANGUAGE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(speech_mod.time, "sleep", lambda s: None)


def _reset_fakes():
    _FakeTranscriptions.script = []
    _FakeTranscriptions.calls = []
    _FakeClient.base_urls = []


def test_retry_recovers_from_transient_failures(monkeypatch):
    """Two「provider down」blinks then success — the SAME chain that
    failed live at 19:58 and succeeded at 20:05 now self-heals."""
    _clean_env(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    _reset_fakes()
    _FakeTranscriptions.script = [
        RuntimeError("provider down"),
        RuntimeError("provider down"),
        _FakeResp("你好"),
    ]
    with mock.patch("openai.OpenAI", _FakeClient):
        sc = speech_mod.SpeechClient()
        out = sc.transcribe(b"\x1aE\xdf\xa3webm", "voice.webm")
    assert out == "你好"
    # three attempts, all against the PRIMARY provider
    assert len(_FakeTranscriptions.calls) == 3
    assert all(m == speech_mod.STT_DEFAULT_MODEL
               for m in _FakeTranscriptions.calls)


def test_fallback_provider_wired_after_retries_exhausted(monkeypatch):
    """Primary dies three times → the SECOND triple (SiliconFlow
    SenseVoice by default) gets the call."""
    _clean_env(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setenv("SILICONFLOW_API_KEY", "sk-sf-test")
    _reset_fakes()
    _FakeTranscriptions.script = [
        RuntimeError("provider down"),
        RuntimeError("provider down"),
        RuntimeError("provider down"),
        _FakeResp("備援成功"),
    ]
    with mock.patch("openai.OpenAI", _FakeClient):
        sc = speech_mod.SpeechClient()
        out = sc.transcribe(b"\x1aE\xdf\xa3webm", "voice.webm")
    assert out == "備援成功"
    # the 4th client instance was constructed against SiliconFlow
    assert "siliconflow" in _FakeClient.base_urls[-1]
    assert _FakeTranscriptions.calls[-1] == "FunAudioLLM/SenseVoiceSmall"


def test_total_failure_humanised_message(monkeypatch):
    """Everything down → the raised error is USER-READABLE, not a raw
    「provider down」repr."""
    _clean_env(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setenv("SILICONFLOW_API_KEY", "sk-sf-test")
    _reset_fakes()
    _FakeTranscriptions.script = [RuntimeError("provider down")] * 4
    with mock.patch("openai.OpenAI", _FakeClient):
        sc = speech_mod.SpeechClient()
        with pytest.raises(RuntimeError) as ei:
            sc.transcribe(b"\x1aE\xdf\xa3webm", "voice.webm")
    assert "暫時不可用" in str(ei.value) or "已自動重試" in str(ei.value)


def test_api_voice_stt_error_humanised(monkeypatch):
    """gui.api voice_stt no longer surfaces the raw provider repr."""
    from gui.api import Api

    api = Api.__new__(Api)

    class _Boom:
        def stt_configured(self):
            return True

        def transcribe(self, data, fname):
            raise RuntimeError("provider down")

    monkeypatch.setattr(speech_mod, "SpeechClient", lambda: _Boom())
    r = api.voice_stt("QUJD", "voice.webm")
    assert r["ok"] is False
    assert "provider down" not in r["error"]
    assert "暫時不可用" in r["error"]