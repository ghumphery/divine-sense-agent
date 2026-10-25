# -*- coding: utf-8 -*-
"""v0.9.127.1 — sendChat fail-closed hardening (no more silent sends).

User report: 「上傳圖檔 UI前端沒有任何回應」— the upload chain itself was
verified healthy end-to-end (live probes: serve fresh assets, POST
/upload contract match, a REAL-image turn answered the contents
correctly). This lock-down addresses the SILENT-FAILURE class instead:
any client-side throw inside sendChat (bridge gone / fetch refused /
renderer bug) used to propagate out of the event handler = zero
feedback + the send button staying disabled forever. Now a wrapper
catches everything: toast + error bubble + restored button + the user's
draft restored to the input.
"""
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(rel: str) -> str:
    with open(os.path.join(ROOT, "gui", "assets", rel),
              encoding="utf-8") as f:
        return f.read()


class TestSendNeverSilentV0127_1(unittest.TestCase):
    def test_wrapper_and_core_split(self):
        js = _read("app.js")
        self.assertIn("async function sendChat()", js)
        self.assertIn("async function _sendChatCore()", js)
        self.assertIn("await _sendChatCore();", js)

    def test_catch_surfaces_and_restores(self):
        js = _read("app.js")
        i = js.index("async function sendChat()")
        j = js.index("async function _sendChatCore()")
        body = js[i:j]
        # toast + error bubble + button restore + draft restore
        self.assertIn("toast('⚠️", body)
        self.assertIn("appendChatMsg('error'", body)
        self.assertIn("btn.disabled = false", body)
        self.assertIn("inp.value = draft", body)
        self.assertIn("autoSaveChat();", body)
        # nothing inside the catch may itself crash the handler
        self.assertGreaterEqual(body.count("try {"), 3)

    def test_pending_upload_buffer_v0127_2(self):
        # v0.9.127.2 — the upload/send RACE: the ✅ toast used to arrive
        # without the [已上傳] marker ever reaching the SENT message
        # (user typed/sent while the upload fetch was in flight). The
        # fix: successful uploads push paths into a pending buffer and
        # the send-side MERGES them into the outgoing message.
        js = _read("app.js")
        self.assertIn("window.__PENDING_UPLOAD_PATHS__", js)
        # sender side: merge-if-missing, then clear the buffer
        self.assertIn("window.__PENDING_UPLOAD_PATHS__ = []", js)
        i = js.index("const pend = window.__PENDING_UPLOAD_PATHS__ || [];")
        self.assertIn("marks[0]", js[i:i + 900])
        self.assertIn("UPLOAD_MARK + ' ' + p", js[i:i + 500])


if __name__ == "__main__":
    unittest.main()