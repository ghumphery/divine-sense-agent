"""v0.9.127.11 — hands-free voice loop + auto-TTS + browser TTS
fallback.

The 🎉 toggle speaks every assistant reply as it lands; the 🎧 toggle
runs the full hands-free loop (listen → VAD silence → auto-stop →
transcript auto-SENDS → spoken reply → mic reopens); when the backend
TTS (OpenRouter kokoro) fails the BROWSER's own speechSynthesis speaks
instead. Both toggles persist in localStorage and paint green.
"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "gui" / "assets"


def _src(name: str) -> str:
    return (ASSETS / name).read_text(encoding="utf-8")


class TestHandsFreeVoiceV012711:
    def test_index_has_the_two_toggle_buttons(self):
        html = _src("index.html")
        assert 'id="chat-autotts-btn"' in html
        assert 'id="chat-loop-btn"' in html
        assert 'data-i18n-title="autoTtsOff"' in html
        assert 'data-i18n-title="voiceLoopOff"' in html

    def test_app_js_toggle_functions_and_persistence(self):
        js = _src("app.js")
        assert "function toggleAutoTTS()" in js
        assert "function toggleVoiceLoop()" in js
        assert "localStorage.setItem('chat-auto-tts'" in js
        assert "localStorage.setItem('chat-voice-loop'" in js
        assert "function bindVoiceToggles()" in js
        assert "bindVoiceToggles._done" in js
        assert "btn.classList.toggle('btn-loop-on'" in js

    def test_app_js_hands_free_loop_mechanics(self):
        js = _src("app.js")
        # the loop: mic restart after playback + VAD watchdog
        assert "function _voiceLoopStartMic()" in js
        assert "function _voiceLoopRestartMic()" in js
        assert "function _voiceLoopStopAll()" in js
        assert "function _vadStart()" in js
        assert "_VAD_SILENCE_MS = 2000" in js
        assert "_VAD_MIN_SPEECH_MS = 1200" in js
        assert "speechSeen" in js
        assert "silentFor >= _VAD_SILENCE_MS" in js
        # the transcript lands → auto-SEND via the hook
        assert "_voiceTranscriptHook" in js
        assert "try { sendChat(); } catch (_) {}" in js
        # failures STOP the loop (no endless toast storms)
        assert js.count("_voiceLoopStopAll();") >= 3

    def test_app_js_auto_tts_lands_in_send_core(self):
        js = _src("app.js")
        i = js.index("async function _sendChatCore()")
        body = js[i:js.index("async function autoSaveChat()", i)]
        assert "_speakReplyForLoop(res.answer || '')" in body
        assert "if (_autoTTS || _voiceLoop)" in body

    def test_browser_tts_fallback(self):
        js = _src("app.js")
        assert "function _speakBrowserTTS(text, onEnd)" in js
        assert "window.speechSynthesis" in js
        assert "SpeechSynthesisUtterance" in js
        # speakChatText degrades on backend-TTS failure
        assert "async function speakChatText(btn, text, onEnd)" in js
        assert "if (!_speakBrowserTTS(text, onEnd)) {" in js
        # playback end hooks the loop restart
        assert "typeof onEnd === 'function'" in js

    def test_i18n_keys_four_locales(self):
        js = _src("app.js")
        for key in ("autoTtsOn:", "autoTtsOff:", "voiceLoopOn:",
                    "voiceLoopOff:", "voiceFallback:"):
            assert js.count(key) == 4, key

    def test_active_button_css(self):
        css = _src("style.css")
        assert "#chat-autotts-btn.btn-loop-on" in css
        assert "#chat-loop-btn.btn-loop-on" in css

    def test_bind_events_wires_toggles_early(self):
        js = _src("app.js")
        i = js.index("function bindEvents()")
        head = js[i:i + 1400]
        assert "bindVoiceToggles();" in head