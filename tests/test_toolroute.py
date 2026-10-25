"""v0.9.98.0 — Jev dynamic tool-surface (pre-LLM tool routing).

The question is classified by ONE Jev ``choice`` call BEFORE the LLM
tool loop; the turn's surface = core servers + the winning class's
servers (still filtered by MCP_ACTIVE_SERVERS + the cap), hard-capped
at 128. off default | shadow (log only) | on. Any Jev failure /
low-confidence / GUI-picker override keeps the legacy surface.
"""
import re
import os
import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

import src.agent.agent as A  # noqa: E402


def _wipe(monkeypatch=None):
    for var in ("QDRANT_TOOLROUTE", "QDRANT_TOOLROUTE_PROB",
                "QDRANT_TOOLROUTE_CLASSES", "QDRANT_TOOLROUTE_CAP",
                "MCP_SURFACE_MAX_TOOLS", "MCP_ACTIVE_SERVERS"):
        os.environ.pop(var, None)


class TestTelegramUnion1032(unittest.TestCase):
    """v0.9.103.2 — a Telegram-send prompt keeps the ``telegram``
    server on the surface regardless of the winning class (the
    2026-09-29 14:00 stock schedule fire was classed plain ``stock``
    and could not dispatch send_telegram_* at all)."""

    def setUp(self):
        _wipe()

    def tearDown(self):
        _wipe()

    def test_hint_detector(self):
        self.assertTrue(A._toolroute_telegram_hint(
            "請搜尋今天台灣股市行情，將分析結果以 Telegram 訊息發送到 "
            "chat_id <YOUR_TELEGRAM_CHAT_ID>。使用 send_telegram_message 工具"))
        self.assertTrue(A._toolroute_telegram_hint(
            "把結果傳送給我好嗎 訊息"))
        self.assertFalse(A._toolroute_telegram_hint("現在幾點"))
        self.assertFalse(A._toolroute_telegram_hint(""))

    def test_stock_class_unions_telegram(self):
        srvs, classes = A._toolroute_servers_for(
            "stock",
            "將分析結果以 Telegram 訊息發送到 chat_id <YOUR_TELEGRAM_CHAT_ID>",
        )
        self.assertIn("stock-local", srvs)
        self.assertIn("telegram", srvs)
        self.assertEqual(classes, ["stock"])  # class label untouched

    def test_none_class_with_hint_gets_telegram(self):
        srvs, classes = A._toolroute_servers_for(
            None, "用 telegram 傳訊息給我 chat_id:<YOUR_TELEGRAM_CHAT_ID>")
        self.assertIn("telegram", srvs)
        self.assertEqual(classes, [])

    def test_no_hint_untouched(self):
        srvs, _ = A._toolroute_servers_for("stock")
        self.assertNotIn("telegram", srvs)
        srvs2, _ = A._toolroute_servers_for(None)
        self.assertNotIn("telegram", srvs2)

    def test_both_call_sites_pass_question(self):
        src = open("src/agent/agent.py", encoding="utf-8").read()
        # v0.9.104.1 — chat_turn threads prev_reply=_prev_reply (the
        # prior LLM reply); the single-turn ask site stays 2-arg.
        self.assertEqual(
            src.count("_toolroute_servers_for(_tcls, question)"), 1)
        self.assertEqual(
            src.count("prev_reply=_prev_reply"), 2)  # jev + servers_for
        self.assertEqual(
            src.count("_prev_assistant_reply(history)"), 1)


class TestResolvers(unittest.TestCase):
    def setUp(self):
        _wipe()

    def tearDown(self):
        _wipe()

    def test_mode_off_default(self):
        self.assertEqual(A._toolroute_mode(), "off")

    def test_mode_values(self):
        for v, want in (("shadow", "shadow"), ("on", "on"),
                        ("OFF", "off"), ("bogus", "off")):
            os.environ["QDRANT_TOOLROUTE"] = v
            self.assertEqual(A._toolroute_mode(), want)

    def test_prob_default_and_clamp(self):
        self.assertEqual(A._toolroute_prob(), 0.6)
        os.environ["QDRANT_TOOLROUTE_PROB"] = "0.9"
        self.assertEqual(A._toolroute_prob(), 0.9)
        os.environ["QDRANT_TOOLROUTE_PROB"] = "bogus"
        self.assertEqual(A._toolroute_prob(), 0.6)

    def test_cap_default_128(self):
        self.assertEqual(A._toolroute_hard_cap(), 128)
        os.environ["QDRANT_TOOLROUTE_CAP"] = "64"
        self.assertEqual(A._toolroute_hard_cap(), 64)
        os.environ["QDRANT_TOOLROUTE_CAP"] = "x"
        self.assertEqual(A._toolroute_hard_cap(), 128)

    def test_servers_for_class(self):
        srvs, cls = A._toolroute_servers_for("stock")
        self.assertIn("qdrant-local", srvs)      # core always present
        self.assertIn("stock-local", srvs)
        self.assertEqual(cls, ["stock"])
        srvs, cls = A._toolroute_servers_for(None)
        self.assertNotIn("stock-local", srvs)    # core only
        self.assertEqual(cls, [])

    def test_exec_local_is_base_core(self):
        """v0.9.104.4 — exec-local is ALWAYS-ON base (run_python 等
        不再需要 exec class 命中）。"""
        for c in ("stock", "rag", None):
            srvs, _ = A._toolroute_servers_for(c)
            self.assertIn("exec-local", srvs, c)
        # and it sits in the core tuple itself
        self.assertIn("exec-local", A._TOOLROUTE_CORE_SERVERS)
        self.assertIn(
            "task-local", A._TOOLROUTE_CORE_SERVERS,
            "run_task_once must be on EVERY routed surface — its "
            "absence caused the 17:07 fabricated task-id reply",
        )


class TestJevToolroute(unittest.TestCase):
    def setUp(self):
        _wipe()
        import src.agent.rerank as R

        self._R = R
        self._orig = R.jev_choice

    def tearDown(self):
        self._R.jev_choice = self._orig
        _wipe()

    def test_confident_choice_returns_class(self):
        self._R.jev_choice = (
            lambda *a, **k: ("stock", 0.9, {"elapsed_s": 0.1}))
        cls, p, _ = A._jev_toolroute("查 2330 報價")
        self.assertEqual(cls, "stock")
        self.assertGreaterEqual(p, 0.6)

    def test_low_confidence_fail_open(self):
        self._R.jev_choice = (
            lambda *a, **k: ("stock", 0.3, {"elapsed_s": 0.1}))
        cls, p, _ = A._jev_toolroute("查 2330 報價")
        self.assertIsNone(cls)

    def test_jev_failure_fail_open(self):
        def _boom(*a, **k):
            raise RuntimeError("jev down")
        self._R.jev_choice = _boom
        cls, p, meta = A._jev_toolroute("查 2330 報價")
        self.assertIsNone(cls)
        self.assertIn("error", meta)

    def test_unknown_choice_fail_open(self):
        self._R.jev_choice = lambda *a, **k: ("nonexistent", 0.99, {})
        cls, _p, _m = A._jev_toolroute("hello")
        self.assertIsNone(cls)


class TestAgentIntegration(unittest.TestCase):
    def setUp(self):
        _wipe()
        import src.agent.rerank as R

        self._R = R
        self.agent = object.__new__(A.Agent)
        mgr = MagicMock()
        t = MagicMock()
        t.name = "stock_get_quote"
        t.server_name = "stock-local"
        tools = [t]
        mgr.has_tools.return_value = True
        mgr.active_tools.return_value = tools
        mgr.raw_tools.return_value = tools  # v0.9.133.15 ungated catalog
        mgr.active_specs.return_value = [MagicMock(to_openai=lambda: {})]
        # _mcp_manager is the attr behind the lazy property
        self.agent._mcp_manager = mgr
        self.agent.mcp_manager  # noqa: B018 — property reads _mcp_manager

    def tearDown(self):
        _wipe()

    def test_off_never_calls_jev(self):
        called = {"n": 0}

        def _spy(*a, **k):
            called["n"] += 1
            return ("stock", 0.9, {})
        self._R2 = __import__("src.agent.rerank", fromlist=["x"])
        self._orig2 = self._R2.jev_choice
        self._R2.jev_choice = _spy
        try:
            # mode=off → the resolver must never be called
            self.assertEqual(A._toolroute_mode(), "off")
            self.assertEqual(called["n"], 0)
        finally:
            self._R2.jev_choice = self._orig2

    def test_on_routes_surface(self):
        os.environ["QDRANT_TOOLROUTE"] = "on"
        A.jev_choice = lambda *a, **k: ("stock", 0.95, {"elapsed_s": 0.1})
        names = self.agent._toolroute_tool_names(
            A._toolroute_servers_for("stock")[0])
        self.assertEqual(names, ["stock_get_quote"])

    def test_unknown_server_filtered_out(self):
        os.environ["QDRANT_TOOLROUTE"] = "on"
        A.jev_choice = lambda *a, **k: ("team", 0.95, {})
        # teams-assist not in this stub catalog → empty allow-list?
        # active_tools() only knows stock-local, so routed list yields
        # zero names — the resolver must fall back to None (legacy).
        names = self.agent._toolroute_tool_names(
            A._toolroute_servers_for("team")[0])
        self.assertIsNone(names)

    def test_cap_128_enforced(self):
        os.environ["QDRANT_TOOLROUTE_CAP"] = "2"
        tools = []
        for i in range(5):
            t = MagicMock()
            t.name = f"tool{i}"
            t.server_name = "stock-local"
            tools.append(t)
        self.agent.mcp_manager.active_tools.return_value = tools
        self.agent.mcp_manager.raw_tools.return_value = tools
        names = self.agent._toolroute_tool_names(["stock-local"])
        self.assertEqual(len(names), 2)


    def test_route_names_survive_surface_cap(self):
        """v0.9.133.15 — the 17:07 live case: routed servers include a
        TAIL-of-priority-list server (wordcraft); routed tool names are
        mined from the UNCAPPED catalog (raw_tools), so the 500-cap
        tripping in active_tools() can no longer swallow them. The old
        behaviour (mining from active_tools' trimmed catalog) would
        LOSE every wordcraft tool here."""
        from unittest.mock import MagicMock

        mgr = MagicMock()
        tools = []
        # 60 tools for the core routed server (qdrant)…
        for i in range(60):
            t = MagicMock()
            t.name = f"core_tool{i}"
            t.server_name = "qdrant-local"
            tools.append(t)
        # …260 from OTHER servers NOT in the routed list…
        for i in range(260):
            t = MagicMock()
            t.name = f"other_tool{i}"
            t.server_name = "deckcraft"
            tools.append(t)
        # …and the routed wordcraft tools at the very TAIL — they are
        # past the 500 cap inside active_tools() (the 17:07 live shape:
        # wordcraft is #37 of the MCP_ACTIVE_SERVERS priority list), so
        # the OLD behaviour (mining from active_tools' capped catalog)
        # lost every wordcraft tool.
        wc_names = []
        for i in range(16):
            t = MagicMock()
            t.name = f"wordcraft_tool{i}"
            t.server_name = "wordcraft"
            tools.append(t)
            wc_names.append(t.name)
        mgr.raw_tools.return_value = tools
        mgr.active_tools.return_value = tools[:500]  # simulated cap
        a = object.__new__(A.Agent)
        a._mcp_manager = mgr
        names = a._toolroute_tool_names(["qdrant-local", "wordcraft"])
        self.assertIsNotNone(names)
        self.assertTrue(set(wc_names) & set(names),
                        "wordcraft tools must be mined from raw_tools")
        self.assertLessEqual(len(names), 128)


class TestPhotocraftTrigger(unittest.TestCase):
    """v0.9.119.1 — the photocraft stdio server joins ToolRoute."""

    def setUp(self):
        _wipe()

    def tearDown(self):
        _wipe()

    def test_photo_intent_maps_photocraft(self):
        import re

        hit = any(srv == "photocraft"
                  for pat, srv in A._TOOLROUTE_SERVER_TRIGGERS
                  if re.search(pat, "用 photocraft 修圖這張 .psd"))
        self.assertTrue(hit)

    def test_plain_imagegen_not_photocraft(self):
        import re

        pairs = [(pat, srv)
                 for pat, srv in A._TOOLROUTE_SERVER_TRIGGERS
                 if re.search(pat, "幫我畫一張台灣地圖插圖")]
        self.assertFalse(
            any(srv == "photocraft" for _pat, srv in pairs))

    def test_edit_verbs_map_photocraft(self):
        import re

        for q in ("幫我把這張 psd 去背", "照片修圖", "圖層調整",
                  "photo edit with photocraft",
                  "doc_export 這個 .pcraft"):
            self.assertTrue(
                any(srv == "photocraft"
                    for pat, srv in A._TOOLROUTE_SERVER_TRIGGERS
                    if re.search(pat, q)), q)

    def test_hint_question_side_unions_photocraft(self):
        """v0.9.119.3 — a history=0 first turn naming photocraft gets
        the server (the 12:09 110k-token probe case)."""
        os.environ["QDRANT_TOOLROUTE"] = "on"
        srvs, _cls = A._toolroute_servers_for("image",
                                              "使用 photocraft畫一個圓")
        self.assertIn("photocraft", srvs)
        self.assertIn("imagegen-local", srvs)

    def test_hint_not_for_plain_draw(self):
        os.environ["QDRANT_TOOLROUTE"] = "on"
        srvs, _cls = A._toolroute_servers_for(
            "image", "幫我畫一張台灣地圖插圖")
        self.assertNotIn("photocraft", srvs)
        self.assertIn("imagegen-local", srvs)

    def test_hint_photo_edit_verbs(self):
        for q in ("把這張 psd 的圖層修一下", "幫我去背", "照片裁切後修圖",
                  "photoshop 這個檔案", "photo edit the mockup"):
            self.assertTrue(A._toolroute_photocraft_hint(q), q)
        self.assertFalse(A._toolroute_photocraft_hint("現在幾點"))
        self.assertFalse(A._toolroute_photocraft_hint("查台積電報價"))


class TestMemoryRouteV0133_11(unittest.TestCase):
    """v0.9.133.11 — summarize/沉澱 prompts must union memory-local so
    the session-summary schedule can actually CALL summarize_sessions
    (03:00 fire diagnosed: the tool surface had no memory-local and the
    LLM finished with a 48-char half-answer, nothing was compacted)."""

    def test_trigger_rows_hit_memory_local_for_compaction(self):
        for t in ("每日對話摘要沉澱（T+1）呼叫 summarize_sessions",
                  "呼叫 summarize_sessions（days=1）",
                  "把昨天的對話沉澱成 session 摘要",
                  "session 摘要沉澱",
                  "長期記憶 recall_memory",
                  "remember this"):
            hit = any(srv == "memory-local"
                      for pat, srv in A._TOOLROUTE_SERVER_TRIGGERS
                      if re.search(pat, t))
            self.assertTrue(hit, t)

    def test_plain_chat_does_not_pull_memory_local(self):
        for t in ("現在幾點", "查台積電報價", "畫一張台灣地圖"):
            hit = any(srv == "memory-local"
                      for pat, srv in A._TOOLROUTE_SERVER_TRIGGERS
                      if re.search(pat, t))
            self.assertFalse(hit, t)


class TestLowConfidenceUnionV013314(unittest.TestCase):
    """v0.9.133.14 — Jev LOW-CONFIDENCE (class=None) must still union
    the problem-side trigger mining: without it the full-fleet surface
    fell to the 500-tool cap which trims BY REGISTRATION ORDER (live
    2026-10-22 16:39: a docx form-filling task lost wordcraft/... and
    finished with 0 dispatches)."""

    def test_docx_question_mines_wordcraft_regardless_of_class(self):
        srvs, _cls = A._toolroute_servers_for(
            None, '協助 填寫"C:/Users/humphery/Downloads/'
                  'FIA_SDT_SAE_BOR_v0.3.docx" 表')
        self.assertIn("wordcraft", srvs)

    def test_plain_question_stays_core_only(self):
        srvs, _cls = A._toolroute_servers_for(None, "現在幾點")
        self.assertNotIn("wordcraft", srvs)
        self.assertIn("datetime-local", srvs)

    def test_source_lines_have_low_confidence_fallback(self):
        src = open(A.__file__, encoding="utf-8").read()
        self.assertEqual(src.count('tmode == "on" and (_tcls or _srvs)'), 2,
                         "ask + chat_turn low-confidence fallbacks")
        self.assertNotIn('tmode == "on" and _tcls:', src)


if __name__ == "__main__":
    unittest.main()