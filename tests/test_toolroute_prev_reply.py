"""v0.9.104.1 — toolroute CONTEXT: the previous LLM reply joins the
route decision.

Problem (user report): the Jev MCP tool gate looked at ONLY the current
prompt — bare/short follow-ups (「ok」「執行」「繼續」「run」「核准」)
carry no class keywords, so the turn surface lost the fleet the
previous turn's plan announced (announced send_telegram / stock_* /
… could never mount → tool calls failed or were faked).

Fix:
* ``_prev_assistant_reply(history)`` — the LAST assistant reply
  (exactly what a continuation continues).
* ``_toolroute_prev_reply_servers(prev)`` — mines the announced tool
  names / CJK nouns for server-union candidates (union-only).
* ``_toolroute_servers_for(cls, question, prev_reply=…)`` — unions
  them.
* ``_jev_toolroute(question, prev_reply=…)`` — a SHORT question (≤64
  chars) + the labelled previous-reply block (capped 500 chars)
  compose the classification context.
* The chat_turn surface call site threads it from history (the
  single-turn ask path has no history and stays 2-arg).
All Jev calls are mock-injected — no network.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

import src.agent.agent as A  # noqa: E402
import src.agent.rerank as R  # noqa: E402

_PLAN = (
    "依你的指示我準備：1) 呼叫 stock_get_kline 抓 2330 日 K 線；"
    "2) 計算 MA5/MA20；3) 判斷交叉；4) 你確認後用 send_telegram_message "
    "回報結果。請回覆確認。")


class TestPrevAssistantReply(unittest.TestCase):
    def test_last_assistant_wins(self):
        hist = [
            {"role": "user", "content": "先查 2330"},
            {"role": "assistant", "content": _PLAN},
        ]
        self.assertEqual(A._prev_assistant_reply(hist)[:20],
                         _PLAN[:20])

    def test_skips_user_error_empty_non_dict(self):
        hist = [
            {"role": "assistant", "content": "older plan"},
            {"role": "user", "content": "ok"},
            {"role": "assistant", "content": ""},           # empty
            {"role": "assistant", "content": "x", "error": True},
            {"role": "assistant", "content": "THE PLAN"},
        ]
        self.assertEqual(A._prev_assistant_reply(hist), "THE PLAN")
        self.assertEqual(A._prev_assistant_reply(
            [{"role": "user", "content": "只有 user"}, 123]), "")

    def test_caps_and_never_raises(self):
        self.assertEqual(
            len(A._prev_assistant_reply(
                [{"role": "assistant", "content": "x" * 5000}], 700)),
            700)
        self.assertEqual(A._prev_assistant_reply(None), "")
        self.assertEqual(A._prev_assistant_reply([]), "")
        self.assertEqual(A._prev_assistant_reply([{1, 2}]), "")


class TestPrevReplyServerMining(unittest.TestCase):
    def test_tool_names_map_to_servers(self):
        got = A._toolroute_prev_reply_servers(
            "我會用 stock_place_order(symbol=2330) 下單")
        self.assertIn("stock-local", got)
        self.assertIn("telegram",
                      A._toolroute_prev_reply_servers(
                          "接著用 send_telegram_message 傳回報"))
        self.assertIn("imagegen-local",
                      A._toolroute_prev_reply_servers(
                          "我會 image_generate 產一張海報"))
        self.assertIn("keep-local",
                      A._toolroute_prev_reply_servers(
                          "查完後 keep_update_note 記進筆記"))
        self.assertIn("exec-local",
                      A._toolroute_prev_reply_servers(
                          "用 run_python 算 MA"))
        self.assertIn("google-calendar",
                      A._toolroute_prev_reply_servers(
                          "再讀一次行事曆 schedule 內容"))
        self.assertIn("stock-local",
                      A._toolroute_prev_reply_servers(
                          "2330 下單前先看報價與 K 線"))

    def test_garbage_and_empty(self):
        self.assertEqual(A._toolroute_prev_reply_servers(None), [])
        self.assertEqual(A._toolroute_prev_reply_servers(""), [])
        self.assertEqual(A._toolroute_prev_reply_servers("ok"), [])
        self.assertEqual(A._toolroute_prev_reply_servers(12345), [])


class TestRouteWithContext(unittest.TestCase):
    """The user's exact scenario — bare continuations."""

    def test_ok_continuation_mounts_plan_servers(self):
        srvs, cls = A._toolroute_servers_for(
            "rag",
            question="ok",
            prev_reply=_PLAN,
        )
        self.assertIn("stock-local", srvs)   # the plan's tools NOW
        self.assertIn("telegram", srvs)
        self.assertEqual(cls, ["rag"])

    def test_no_prev_reply_unchanged(self):
        srvs, _ = A._toolroute_servers_for("stock", question="ok")
        self.assertIn("stock-local", srvs)
        self.assertNotIn("telegram", srvs)
        srvs2, _ = A._toolroute_servers_for("stock")
        self.assertNotIn("telegram", srvs2)

    def test_jev_context_composition(self):
        captured = {}

        def fake_jev(instructions, text, options, timeout_s=6.0):
            captured["text"] = text
            return "stock", 0.95, {"elapsed_s": 0.1}

        orig = R.jev_choice
        R.jev_choice = fake_jev
        try:
            cls, p, _ = A._jev_toolroute("ok", prev_reply=_PLAN)
            self.assertEqual(cls, "stock")
            self.assertIn("ok", captured["text"])
            self.assertIn("上一輪 LLM 回覆", captured["text"])
            self.assertIn("stock_get_kline", captured["text"])
        finally:
            R.jev_choice = orig

    def test_jev_long_question_skips_prev_block(self):
        captured = {}

        def fake_jev(instructions, text, options, timeout_s=6.0):
            captured["text"] = text
            return "rag", 0.95, {"elapsed_s": 0.1}

        orig = R.jev_choice
        R.jev_choice = fake_jev
        long_q = "請分析這則很長很長很長很長很長很長很長很長很長很長" * 4
        try:
            A._jev_toolroute(long_q, prev_reply=_PLAN)
            self.assertNotIn("上一輪 LLM 回覆", captured["text"])
            self.assertIn(long_q[:10], captured["text"])
        finally:
            R.jev_choice = orig


class TestCallSites(unittest.TestCase):
    def test_chat_impl_threads_prev_reply(self):
        src = open("src/agent/agent.py", encoding="utf-8").read()
        self.assertEqual(
            src.count("_prev_assistant_reply(history)"), 1)
        self.assertEqual(
            src.count("_toolroute_servers_for(\n"
                      "                    _tcls, question, "
                      "prev_reply=_prev_reply)"), 1)
        # the ask path (no history) keeps the 2-arg call
        self.assertEqual(
            src.count("_toolroute_servers_for(_tcls, question)"), 1)

    def test_legacy_toolroute_test_updated(self):
        """tests/test_toolroute.py's guard follows the new call shape."""
        tsrc = open("tests/test_toolroute.py", encoding="utf-8").read()
        self.assertIn("prev_reply=_prev_reply", tsrc)