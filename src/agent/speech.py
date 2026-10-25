"""Speech client — STT + TTS over OpenRouter (v0.9.58.0, Telegram
bidirectional voice). User request: 「使用openrouter的tts/stt」.

OpenRouter exposes two OpenAI-compatible audio endpoints (2025-12+):
* **Transcriptions** ``POST /api/v1/audio/transcriptions`` —
  whisper / gpt-4o-transcribe / Chirp (STT).
* **Speech** ``POST /api/v1/audio/speech`` — GPT-4o Mini TTS /
  Gemini TTS / Voxtral (TTS; outputs mp3 or pcm).

Zero new dependencies — the ``openai`` SDK (already a dependency)
talks to both endpoints (OpenRouter documents the exact python-SDK
shape). ffmpeg (already required by the ONVIF tools) bridges the
container gap: Telegram voice notes arrive as OGG/Opus which is
transcoded to WAV before STT, and TTS output is transcoded to
OGG/Opus for ``sendVoice`` (Telegram requires opus; TTS providers
emit mp3/pcm — ``response_format="opus"`` is attempted first and
falls back to mp3 + transcode).

Env vars (all optional — the feature degrades gracefully):

* ``TELEGRAM_STT_API_KEY``   → ``OPENROUTER_API_KEY`` →
  ``OPENAI_API_KEY`` (the base URL default follows the key's vendor)
* ``TELEGRAM_STT_BASE_URL``  (default ``https://openrouter.ai/api/v1``)
* ``TELEGRAM_STT_MODEL``     (default ``openai/gpt-4o-mini-transcribe``)
* ``TELEGRAM_STT_LANGUAGE``  (optional ISO hint, e.g. ``zh``; unset =
  auto-detect)
* ``TELEGRAM_TTS_API_KEY``   → ``OPENROUTER_API_KEY`` →
  ``OPENAI_API_KEY``
* ``TELEGRAM_TTS_BASE_URL``  (default ``https://openrouter.ai/api/v1``)
* ``TELEGRAM_TTS_MODEL``     (default ``openai/gpt-4o-mini-tts-2025-12-15``)
* ``TELEGRAM_TTS_VOICE``     (default ``zf_xiaoxiao`` — the zh
                              female voice of kokoro-82m)
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
from typing import Any

logger = logging.getLogger(__name__)

OPENROUTER_AUDIO_BASE = "https://openrouter.ai/api/v1"
OPENAI_AUDIO_BASE = "https://api.openai.com/v1"
STT_DEFAULT_MODEL = "openai/gpt-4o-mini-transcribe"
# TTS default (live-verified 2026-09-15 against the real OpenRouter
# catalog): kokoro-82m with the zh female voice ``zf_xiaoxiao`` — the
# ONLY {model, voice} combo that accepted a zh-TW test string on the
# first try. Gemini TTS (preview) only emits raw PCM and Deepgram's
# voices are EN/EU-centric; both stay available via env overrides.
TTS_DEFAULT_MODEL = "hexgrad/kokoro-82m"
TTS_DEFAULT_VOICE = "zf_xiaoxiao"
_TTS_RESPONSE_FORMATS = ("mp3", "pcm")  # OpenRouter-accepted formats
# v0.9.68.0 — multipart (filename, mime) per sniffed STT container.
_STT_NAMES: dict[str, tuple[str, str]] = {
    "wav": ("audio.wav", "audio/wav"),
    "ogg": ("voice.ogg", "audio/ogg"),
    "webm": ("voice.webm", "audio/webm"),
    "mp3": ("audio.mp3", "audio/mpeg"),
    "mp4": ("audio.m4a", "audio/mp4"),
}
_STT_TIMEOUT_S = 120.0
_TTS_TIMEOUT_S = 60.0
_FFMPEG_TIMEOUT_S = 30.0
# TTS input cap (API limit 4096 chars; keep voices short).
TTS_MAX_INPUT_CHARS = 2000

_MD_STRIP_RE = re.compile(
    r"[#*`_>~]"                       # markdown markers
    r"|\|+",                          # table pipes
)
_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")

# ffmpeg discovery (same box layout as the ONVIF tools' winget
# install): PATH first, then the winget Links shim + common install
# dirs — some shells launch without the Links dir on PATH.
_FFMPEG_CACHE: str | None = None
_FFMPEG_PROBED = False


def _find_ffmpeg() -> str | None:
    global _FFMPEG_CACHE, _FFMPEG_PROBED
    if _FFMPEG_PROBED:
        return _FFMPEG_CACHE or None
    _FFMPEG_PROBED = True
    import glob as _glob

    exe = shutil.which("ffmpeg")
    if exe:
        _FFMPEG_CACHE = exe
        return exe
    appdata = os.environ.get("LOCALAPPDATA", "")
    patterns = [
        os.path.join(appdata, "Microsoft", "WinGet", "Links",
                     "ffmpeg.exe"),
        r"C:\ffmpeg*\bin\ffmpeg.exe",
        r"C:\Program Files\ffmpeg*\bin\ffmpeg.exe",
        r"C:\Program Files (x86)\ffmpeg*\bin\ffmpeg.exe",
    ]
    for pat in patterns:
        if not pat:
            continue
        hits = sorted(_glob.glob(pat))
        if hits:
            _FFMPEG_CACHE = hits[0]
            return _FFMPEG_CACHE
    return None


def strip_for_tts(text: str) -> str:
    """Cheap markdown→speech normalisation: ``[text](url)`` → ``text``,
    drop markdown markers and table pipes, collapse whitespace."""
    text = _LINK_RE.sub(r"\1", text or "")
    text = _MD_STRIP_RE.sub(" ", text)
    return re.sub(r"\s{2,}", " ", text).strip()


def audio_format_of(data: bytes) -> str:
    """Sniff the container: ``ogg`` / ``mp3`` / ``wav`` / ``webm`` /
    ``mp4`` (m4a) / ``raw``.

    v0.9.68.0 — the web/GUI chat voice input arrives as a
    ``MediaRecorder`` blob (``audio/webm;codecs=opus`` by default in
    Chromium/WebView2) whose EBML magic (``1A 45 DF A3``) used to fall
    through as ``raw`` and get POSTed under a mismatched ``.ogg``
    filename — fixed by sniffing + transcoding below."""
    if data[:4] == b"OggS":
        return "ogg"
    if data[:4] == b"\x1aE\xdf\xa3":  # EBML — webm / mkv
        return "webm"
    if len(data) > 8 and data[4:8] == b"ftyp":  # mp4 / m4a family
        return "mp4"
    if data[:3] == b"ID3" or (data[:2] not in (b"",) and
                              data[0] == 0xFF and data[1] & 0xE0 == 0xE0):
        return "mp3"
    if data[:4] == b"RIFF":
        return "wav"
    return "raw"


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name, default) or "").strip()


class SpeechClient:
    """STT + TTS via OpenRouter's audio endpoints (OpenAI-compatible)."""

    def __init__(
        self,
        stt_base_url: str = "",
        stt_model: str = "",
        stt_api_key: str = "",
        stt_language: str = "",
        tts_base_url: str = "",
        tts_model: str = "",
        tts_voice: str = "",
        tts_api_key: str = "",
    ):
        self.stt_api_key = (
            stt_api_key or _env("TELEGRAM_STT_API_KEY")
            or _env("OPENROUTER_API_KEY") or _env("OPENAI_API_KEY")
        )
        self.stt_base_url = (
            stt_base_url or _env("TELEGRAM_STT_BASE_URL")
            or self._default_base(self.stt_api_key)
        )
        self.stt_model = (
            stt_model or _env("TELEGRAM_STT_MODEL", STT_DEFAULT_MODEL)
        )
        self.stt_language = (
            stt_language if stt_language is not None
            else _env("TELEGRAM_STT_LANGUAGE")
        )
        self.tts_api_key = (
            tts_api_key or _env("TELEGRAM_TTS_API_KEY")
            or _env("OPENROUTER_API_KEY") or _env("OPENAI_API_KEY")
        )
        self.tts_base_url = (
            tts_base_url or _env("TELEGRAM_TTS_BASE_URL")
            or self._default_base(self.tts_api_key)
        )
        self.tts_model = (
            tts_model or _env("TELEGRAM_TTS_MODEL", TTS_DEFAULT_MODEL)
        )
        self.tts_voice = (
            tts_voice or _env("TELEGRAM_TTS_VOICE", TTS_DEFAULT_VOICE)
        )

    @staticmethod
    def _default_base(api_key: str) -> str:
        """The base URL follows the vendor of the resolved key: an
        ``sk-or-…`` OpenRouter key → OpenRouter; otherwise OpenAI."""
        return OPENROUTER_AUDIO_BASE if (
            api_key.startswith("sk-or-")
        ) else OPENAI_AUDIO_BASE

    @classmethod
    def from_env(cls) -> "SpeechClient":
        return cls()

    def stt_configured(self) -> bool:
        return bool(self.stt_api_key)

    def tts_configured(self) -> bool:
        return bool(self.tts_api_key)

    # -- ffmpeg bridging ------------------------------------------------
    @staticmethod
    def _transcode(
        data: bytes, fmt: str, codec_args: list[str],
        out_ext: str,
    ) -> bytes | None:
        """Re-container/re-encode via ffmpeg (``_find_ffmpeg``).
        Returns the new bytes, or ``None`` when ffmpeg is missing or
        failed."""
        exe = _find_ffmpeg()
        if not exe:
            return None
        try:
            with tempfile.TemporaryDirectory(prefix="tg-voice-") as td:
                src = os.path.join(td, f"in.{fmt}")
                dst = os.path.join(td, f"out.{out_ext}")
                with open(src, "wb") as f:
                    f.write(data)
                proc = subprocess.run(
                    [exe, "-hide_banner", "-loglevel", "error",
                     "-y", "-i", src, *codec_args, dst],
                    timeout=_FFMPEG_TIMEOUT_S,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                )
                if proc.returncode not in (0,):
                    return None
                with open(dst, "rb") as f:
                    return f.read() or None
        except Exception as e:
            logger.warning("[speech] ffmpeg transcode failed: %r", e)
            return None

    def _to_wav_for_stt(self, data: bytes, fmt: str) -> bytes:
        """Telegram voice notes are OGG/Opus which several transcription
        providers reject — transcode to 16 kHz mono WAV (universally
        accepted) when ffmpeg is available.

        v0.9.68.0 — also covers the web/GUI MediaRecorder containers
        (``webm`` / ``mp4``) which are ALWAYS transcoded (never passed
        through as raw). True unknown bytes (``raw``) and native wav
        pass through untouched; when ffmpeg is missing the original
        bytes are sent as-is with the container-correct filename
        (see ``transcribe``)."""
        if fmt == "wav" or fmt == "raw":
            return data
        wav = self._transcode(
            data, fmt,
            ["-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le"], "wav",
        )
        return wav if wav else data

    def to_voice_ogg(self, data: bytes) -> bytes:
        """Convert TTS output (mp3 / raw pcm) to OGG/Opus for
        ``sendVoice``. Already-ogg bytes pass through untouched."""
        fmt = audio_format_of(data)
        if fmt == "ogg":
            return data
        if fmt == "mp3":
            ogg = self._transcode(
                data, "mp3",
                ["-c:a", "libopus", "-b:a", "32k"], "ogg",
            )
            return ogg if ogg else data
        # Raw PCM (Gemini TTS): Gemini speech emits 24 kHz s16le mono.
        ogg = self._transcode(
            data, "pcm",
            ["-f", "s16le", "-ar", "24000", "-ac", "1",
             "-c:a", "libopus", "-b:a", "32k"], "ogg",
        )
        return ogg if ogg else data

    def to_browser_audio(self, data: bytes, container: str) -> tuple[bytes, str]:
        """v0.9.68.0 — adapt TTS output for browser/WebView2 playback.

        Browsers play mp3 and ogg/opus natively; raw PCM does NOT.
        Returns ``(bytes, mime)``: mp3/ogg pass through with their
        mime; pcm (Gemini TTS) is transcoded to 24 kHz WAV via ffmpeg —
        when ffmpeg is missing the conversion is impossible, so
        ``None`` is returned (the caller degrades to a text-only
        reply instead of a broken audio element)."""
        if container == "mp3":
            return data, "audio/mpeg"
        if container == "ogg":
            return data, "audio/ogg"
        if container == "wav":
            return data, "audio/wav"
        # pcm (or anything else): transcode to wav.
        wav = self._transcode(
            data, "pcm",
            ["-f", "s16le", "-ar", "24000", "-ac", "1",
             "-c:a", "pcm_s16le"], "wav",
        )
        if wav:
            return wav, "audio/wav"
        return None

    # -- STT ------------------------------------------------------------
    def transcribe(
        self, audio_bytes: bytes, filename: str = "voice.ogg",
    ) -> str:
        """Transcribe a Telegram voice note (OGG/Opus) to text."""
        if not self.stt_configured():
            raise RuntimeError(
                "STT 未設定 — 需要 TELEGRAM_STT_API_KEY（或 "
                "OPENROUTER_API_KEY）",
            )
        if not audio_bytes:
            raise RuntimeError("語音檔為空")
        data = self._to_wav_for_stt(
            audio_bytes, audio_format_of(audio_bytes),
        )
        fmt = audio_format_of(data)
        # The multipart FILENAME must match the payload (live-verified:
        # wav bytes under a .ogg filename made the provider-side
        # decoder 400). The extension wins over the caller's name.
        # v0.9.68.0 — the map covers every sniffed container so the
        # untranscoded webm/mp4 fallback (ffmpeg missing) still posts
        # under a truthful name.
        fname, mime = _STT_NAMES.get(fmt, ("voice.ogg", "audio/ogg"))

        def _once(base_url: str, api_key: str, model: str) -> str:
            from openai import OpenAI

            client = OpenAI(
                base_url=base_url, api_key=api_key,
                timeout=_STT_TIMEOUT_S,
            )
            kwargs: dict[str, Any] = {
                "model": model,
                "file": (fname, data, mime),
            }
            if self.stt_language:
                kwargs["language"] = self.stt_language
            resp = client.audio.transcriptions.create(**kwargs)
            text = str(getattr(resp, "text", "") or "").strip()
            if not text:
                raise RuntimeError("STT 回傳空轉寫")
            return text

        # v0.9.127.12 — TRANSIENT-FAILURE RESILIENCE: the provider's
        # audio endpoint can blink (live 2026-10-16 19:58: two
        # consecutive「provider down」502s from the OpenRouter STT
        # endpoint; the SAME chain succeeded at 20:05). Retry twice
        # with a short backoff before giving up.
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                return _once(
                    self.stt_base_url, self.stt_api_key, self.stt_model)
            except Exception as e:  # noqa: BLE001 — retry loop
                last_exc = e
                logger.warning(
                    "[speech] STT attempt %d failed: %r", attempt + 1, e)
                if attempt < 2:
                    time.sleep(0.5 * (attempt + 1))
        # v0.9.127.12 — STT FALLBACK provider: a second base/key/model
        # triple (defaults wire up the SiliconFlow SenseVoice endpoint
        # — the key the project already carries), used ONLY after the
        # primary retries are exhausted.
        fb_key = (_env("TELEGRAM_STT_FALLBACK_API_KEY")
                  or _env("SILICONFLOW_API_KEY"))
        fb_base = (_env("TELEGRAM_STT_FALLBACK_BASE_URL")
                   or ("https://api.siliconflow.cn/v1" if fb_key else ""))
        fb_model = (_env("TELEGRAM_STT_FALLBACK_MODEL")
                    or ("FunAudioLLM/SenseVoiceSmall" if fb_key else ""))
        if fb_key and fb_base and fb_model:
            try:
                return _once(fb_base, fb_key, fb_model)
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "[speech] STT fallback (%s) also failed: %r",
                    fb_model, e)
        raise RuntimeError(
            "語音辨識服務暫時不可用（已自動重試與切換備援）— 請稍後再說一次"
        ) if not last_exc else RuntimeError(
            f"語音辨識失敗（已自動重試與切換備援）: {last_exc}")

    # -- TTS ------------------------------------------------------------
    def synthesize(self, text: str) -> tuple[bytes, str]:
        """Synthesize speech. Returns ``(audio_bytes, container)`` where
        container is ``ogg`` (opus passthrough worked) or ``mp3`` (call
        ``to_voice_ogg`` before ``sendVoice``)."""
        if not self.tts_configured():
            raise RuntimeError(
                "TTS 未設定 — 需要 TELEGRAM_TTS_API_KEY（或 "
                "OPENROUTER_API_KEY）",
            )
        clean = strip_for_tts(text)[:TTS_MAX_INPUT_CHARS].strip()
        if not clean:
            raise RuntimeError("TTS 輸入為空（去除 markdown 後無內容）")
        from openai import OpenAI

        client = OpenAI(
            base_url=self.tts_base_url, api_key=self.tts_api_key,
            timeout=_TTS_TIMEOUT_S,
        )
        last_exc: Exception | None = None
        for r_fmt in _TTS_RESPONSE_FORMATS:
            try:
                resp = client.audio.speech.create(
                    model=self.tts_model,
                    voice=self.tts_voice,
                    input=clean,
                    response_format=r_fmt,
                )
                audio = getattr(resp, "content", None)
                if not audio:
                    raise RuntimeError("TTS 回傳空音訊")
                return bytes(audio), ("ogg" if r_fmt == "opus" else r_fmt)
            except Exception as e:  # noqa: BLE001 — fallback loop
                last_exc = e
                logger.debug("[speech] tts %s failed: %r", r_fmt, e)
        raise RuntimeError(f"TTS 失敗: {last_exc!r}")