# -*- coding: utf-8 -*-
"""v0.9.127.3 — 語音輸入不能用 root cause: name drift.

``toggleMicRecording`` called ``_isVoiceSupported()`` — a name that
does not exist (the definition is ``_voiceSupported()``) — so EVERY 🎤
click threw an invisible ReferenceError: no toast, no recording, no
request, nothing in the server log. Fixed by renaming the call to the
real function + the bindMic never-silent catch (any future drift/
failure surfaces as the voiceFail toast).
"""
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _js() -> str:
    with open(os.path.join(ROOT, "gui", "assets", "app.js"),
              encoding="utf-8") as f:
        return f.read()


class TestVoiceMicWiringV0127_3(unittest.TestCase):
    def test_support_check_name_is_real(self):
        import re as _re
        js = _js()
        # the drifted name must never be CALLED (def was _voiceSupported;
        # the call drift silently killed every mic click with a
        # ReferenceError). The name may still appear in comments.
        self.assertIsNone(_re.search(r"_isVoiceSupported\(", js))
        self.assertIn("function _voiceSupported()", js)

    def test_toggle_uses_the_real_name(self):
        js = _js()
        i = js.index("function toggleMicRecording()")
        j = js.index("if (!_voiceSupported())", i)
        self.assertGreater(j, i)
        self.assertLess(j - i, 1200)

    def test_bind_mic_never_silent(self):
        js = _js()
        self.assertIn(
            "Promise.resolve(toggleMicRecording()).catch", js)
        self.assertIn("voiceFail", js)


if __name__ == "__main__":
    unittest.main()