"""v0.9.104.2 — firefox_craw routing tests (gate + ToolRoute union).

The user wired the firefox_craw stdio MCP in config/mcp.json but the
agent could not see/use it:
1. the MCP_ACTIVE_SERVERS gate hid every tool (name not listed);
2. ToolRoute (on) has NO class/hint that unions the server — even
   gated-on, normal turns never mounted it.

Covers the route layer (the gate itself is env-config). All source
guards read the live files.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

import src.agent.agent as A  # noqa: E402


class TestBrowseHint(unittest.TestCase):
    def test_explicit_browser_intent(self):
        self.assertTrue(A._toolroute_browse_hint(
            "請用瀏覽器(firefox_craw)讀 https://example.com/ 的內容"))
        self.assertTrue(A._toolroute_browse_hint(
            "看一下 x.com/shao__meng/status/2101 這則 po 文"))
        self.assertTrue(A._toolroute_browse_hint(
            "請 crawl 這個網頁並轉 markdown"))
        self.assertTrue(A._toolroute_browse_hint(
            "用 get_page_html 讀整頁"))

    def test_non_browser_intents_do_not_union(self):
        for q in ("現在幾點", "幫我搜尋台積電新聞", "ok", "執行",
                  "幫我畫一張柴犬插圖", "查 2330 現在的價格"):
            self.assertFalse(A._toolroute_browse_hint(q), q)


class TestWebExternalClass(unittest.TestCase):
    def test_web_external_mounts_both(self):
        srvs, cls = A._toolroute_servers_for("web-external")
        self.assertIn("my_web", srvs)
        self.assertIn("firefox_craw", srvs)
        self.assertEqual(cls, ["web-external"])

    def test_browse_question_unions_firefox(self):
        srvs, cls = A._toolroute_servers_for(
            "rag", question="讀 x.com/xxx 這則 po 的內容")
        self.assertIn("firefox_craw", srvs)
        self.assertNotIn("my_web", srvs)     # class didn't win
        self.assertEqual(cls, ["rag"])

    def test_prev_reply_mining_firefox_row(self):
        self.assertIn("firefox_craw",
                      A._toolroute_prev_reply_servers(
                          "已用 get_page_html 讀完頁面…"))


class TestSourceGuards(unittest.TestCase):
    def test_agent_wiring(self):
        src = open("src/agent/agent.py", encoding="utf-8").read()
        assert '"web-external": ("my_web", "firefox_craw")' in src
        assert src.count("_toolroute_browse_hint(question)") == 1
        assert 'servers.add("firefox_craw")' in src
        # the prompt block teaches the tool choice
        assert "get_page_html" in src

    def test_env_gate_has_firefox(self):
        env = open(".env", encoding="utf-8", errors="replace").read()
        line = next(ln for ln in env.splitlines()
                    if ln.strip().startswith("MCP_ACTIVE_SERVERS="))
        assert line.count("firefox_craw") == 1, line

    def test_mcp_json_entry_intact(self):
        cfg = open("config/mcp.json", encoding="utf-8").read()
        assert '"firefox_craw"' in cfg
        assert "mcp_getweb" in cfg


if __name__ == "__main__":
    unittest.main()