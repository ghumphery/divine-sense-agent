"""v0.9.127.14 — the iPhone web-app error: iOS WebKit only opens
the mic inside a USER GESTURE, so the hands-free loop's timer-driven
mic reopen threw NotAllowedError (the live「not allowed…denied
permission」report from the iPhone PWA). On iOS the loop now shows
ONE big resume button (the tap IS the gesture) and permission
teaching messages are iOS-specific (Settings → Privacy → Microphone).
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "gui" / "assets"


def _src(name: str) -> str:
    return (ASSETS / name).read_text(encoding="utf-8")


class TestIOSVoiceGestureV012714:
    def test_ios_detection_and_resume_overlay(self):
        js = _src("app.js")
        assert "function _isIOS()" in js
        assert "navigator.standalone === true" in js
        assert "maxTouchPoints > 1" in js
        assert "function _voiceLoopResumeOverlay(text, onResume)" in js
        assert 'id = \'voice-loop-resume\'' in js
        assert "t('voiceResumeListen')" in js

    def test_restart_mic_gestures_on_ios(self):
        js = _src("app.js")
        i = js.index("function _voiceLoopRestartMic()")
        body = js[i:js.index("function _isIOS()", i)]
        # iOS path = the overlay (gesture), desktop path = the timer
        assert "_isIOS()" in body
        assert "_voiceLoopResumeOverlay();" in body
        assert "setTimeout(function () { _voiceLoopStartMic(); }, 800);" \
            in body

    def test_ios_permission_teaching_message(self):
        js = _src("app.js")
        i = js.index("function _micPermissionMessage(e)")
        body = js[i:js.index("async function _micPreflight()", i)]
        assert "_isIOS()" in body
        assert "t('voicePermissionIOS')" in body

    def test_i18n_keys_four_locales(self):
        js = _src("app.js")
        for key in ("voicePermissionIOS:", "voiceResume:",
                    "voicePermissionIOSDenied:"):
            assert js.count(key) == 4, key

    def test_denied_state_probe_and_walkthrough(self):
        """iOS remembers a denial system-wide and never re-prompts:
        the Permissions API probe feeds the FULL recovery walkthrough
        instead of a generic retry suggestion."""
        js = _src("app.js")
        assert "async function _micPermissionState()" in js
        assert "navigator.permissions.query(\n"
        "        { name: 'microphone' })" in js
        assert "voicePermissionIOSDenied" in js
        i = js.index("async function _micPreflight()")
        body = js[i:js.index("function _voiceLoopResumeOverlay", i)]
        assert "=== 'denied'" in body


class TestIOSAutoplayV012716:
    """The iPhone screenshot proved the「not allowed」toast fired on
    REPLY-LAND (auto-TTS audio.play), not on the mic — iOS blocks
    gesture-less autoplay. On iOS the reply is now played FROM the
    resume overlay's tap; a bare autoTTS gets a one-time hint."""

    def test_speak_reply_loops_ios_to_overlay(self):
        js = _src("app.js")
        i = js.index("function _speakReplyForLoop(text)")
        body = js[i:js.index("function _voiceLoopStartMic()", i)]
        assert "_isIOS()" in body
        assert "_voiceLoopResumeOverlay(t2, null);" in body
        assert "_iosAutoplayHintShown" in body

    def test_overlay_two_buttons_with_pending_reply(self):
        js = _src("app.js")
        i = js.index("function _voiceLoopResumeOverlay(text, onResume)")
        body = js[i:js.index("function bindVoiceToggles()", i)]
        assert "voiceResumePlay" in body
        assert "voiceResumeListen" in body
        # the play tap feeds the reply INTO speakChatText (gesture-legal)
        assert "speakChatText(ghost, text, function () {" in body

    def test_i18n_and_css(self):
        js = _src("app.js")
        for key in ("voiceResumePlay:", "voiceResumeListen:",
                    "voiceIOSAutoplayHint:"):
            assert js.count(key) == 4, key
        css = _src("style.css")
        assert "#voice-loop-resume .vlr-box {" in css


class TestStopFabAndUnlockV012717:
    """① a stop-speaking FAB that appears while TTS plays and hands
    the turn back (iOS → overlay, desktop → mic reopens); ② ONE
    persistent audio element unlocked inside the 🎉/🎧 tap gesture —
    iOS then allows every reply to be spoken IMMEDIATELY as it
    lands (「資料查完立即語音回覆」on the iPhone)."""

    def test_stop_speech_stack(self):
        js = _src("app.js")
        assert "function _stopSpeech()" in js
        assert "function _showVoiceStopFab()" in js
        assert "function _hideVoiceStopFab()" in js
        assert "window.speechSynthesis.cancel()" in js
        # manual stop continues the loop (iOS → overlay / desktop → mic)
        assert "if (_isIOS()) _voiceLoopResumeOverlay(null, null);" in js
        assert "else _voiceLoopRestartMic();" in js

    def test_persistent_unlocked_element(self):
        js = _src("app.js")
        assert "function _ensureVoiceEl()" in js
        assert "function _unlockVoiceEl()" in js
        assert "el.muted = true;" in js
        assert "setAttribute('playsinline', '')" in js
        # speakChatText reuses the element (never a fresh Audio per reply)
        assert "const audio = _ensureVoiceEl();" in js
        assert "audio.src = url;" in js
        assert "new Audio(url);" not in js
        # the unlock fires inside the 🎉/🎧 tap gestures
        assert "if (_autoTTS) _unlockVoiceEl();" in js
        assert "_unlockVoiceEl();" in js[js.index(
            "function toggleVoiceLoop()"):js.index(
            "function _autoTTSPaint()")]

    def test_fab_shows_and_hides_with_playback(self):
        js = _src("app.js")
        assert "_showVoiceStopFab();" in js[
            js.index("async function speakChatText"):js.index(
                "function bindVoiceToggles()")]
        assert js.count("_hideVoiceStopFab();") >= 4

    def test_i18n_and_css(self):
        js = _src("app.js")
        assert js.count("voiceStop:") == 4
        css = _src("style.css")
        assert "#voice-stop-fab {" in css
        assert "#voice-stop-fab .vsf-btn {" in css


class TestGlobalUnlockV012718:
    """127.17's unlock only fired inside the 🎉/🎧 toggle taps — but
    those toggles were already ON (restored from localStorage after a
    reload), so the unlock never ran and the iPhone kept seeing the
    「播放回覆」overlay. Now the FIRST touch anywhere unlocks the
    element, and an unlocked element is spoken immediately."""

    def test_global_first_gesture_unlock(self):
        js = _src("app.js")
        assert "function _bindGlobalUnlock()" in js
        assert "'pointerdown', 'touchstart', 'touchend', 'click'" in js
        assert "document.removeEventListener(evs[i], once);" in js
        assert "bindVoiceToggles._done = true;" in js
        # wired at boot, before the toggle state restore
        i = js.index("function bindVoiceToggles()")
        head = js[i:i + 600]
        assert "_bindGlobalUnlock();" in head

    def test_unlock_flag_and_immediate_speak(self):
        js = _src("app.js")
        assert "let _voiceElUnlocked = false;" in js
        assert "_voiceElUnlocked = true;" in js
        # unlocked iOS replies are spoken IMMEDIATELY (no overlay)
        i = js.index("function _speakReplyForLoop(text)")
        body = js[i:js.index("function _voiceLoopStartMic()", i)]
        assert "if (_voiceElUnlocked) {" in body
        assert "_speakReplyNow(t2);" in body
        assert "function _speakReplyNow(t2)" in js

    def test_overlay_css(self):
        css = _src("style.css")
        assert "#voice-loop-resume {" in css
        assert "#voice-loop-resume .vlr-btn {" in css


class TestMicPermissionV012713:
    def test_teaching_message_and_preflight_exist(self):
        js = _src("app.js")
        assert "function _micPermissionMessage(e)" in js
        assert "NotAllowedError" in js
        assert "PermissionDeniedError" in js
        assert "async function _micPreflight()" in js
        # one-shot stream: tracks are released immediately
        assert "getTracks().forEach(function (tr) { tr.stop(); })" in js

    def test_toggle_uses_preflight_and_rolls_back(self):
        js = _src("app.js")
        i = js.index("function toggleVoiceLoop()")
        body = js[i:js.index("function _autoTTSPaint()", i)]
        assert "Promise.resolve(_micPreflight())" in body
        assert "_voiceLoopStopAll();" in body

    def test_toggle_mic_uses_teaching_message(self):
        js = _src("app.js")
        i = js.index("async function toggleMicRecording()")
        body = js[i:js.index("function chunksNow", i)]
        assert "_micPermissionMessage(e)" in body
        assert "toast(t('voiceMicErr'," not in body

    def test_i18n_voice_permission_four_locales(self):
        js = _src("app.js")
        assert js.count("voicePermission:") == 4