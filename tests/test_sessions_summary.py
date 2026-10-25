"""v0.9.133.7 — instant per-session summary for the Sessions table.

``SessionManager.list()`` computes, from the already-loaded session
JSON (no LLM, no extra IO): the first real user message (~topic),
the last non-error assistant reply, and the total character volume.
The GUI ``list_sessions`` bridge and the LLM's
``list_chat_sessions`` both consume the same fields; the Sessions
table shows 💬 + 🤖 two-line summary per row so rename/delete
decisions need no load-first.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from src.agent.session import SessionManager


def _mk(mgr: SessionManager, sid: str, messages):
    d = {"id": sid, "title": f"t-{sid}",
         "created_at": "2026-10-21T08:00:00",
         "updated_at": "2026-10-21T09:00:00",
         "messages": messages}
    (mgr.root / f"{sid}.json").write_text(
        json.dumps(d, ensure_ascii=False), encoding="utf-8")


class TestListSummary:
    def test_summary_fields_present(self, tmp_path):
        mgr = SessionManager(root=tmp_path)
        _mk(mgr, "s1", [
            {"role": "user", "content": "幫我看九九乘法表怎麼印"},
            {"role": "assistant", "content": "用雙層 for 迴圈…"},
            {"role": "user", "content": "那注音呢？"},
            {"role": "assistant", "content": "注音可以用拼音表…"},
        ])
        rows = {r["id"]: r for r in mgr.list()}
        s1 = rows["s1"]
        assert s1["summary_first_user"].startswith("幫我看九九乘法")
        assert s1["summary_last_assistant"].startswith("注音可以")
        assert s1["summary_chars"] == sum(
            len(str(m["content"])) for m in json.loads(
                (tmp_path / "s1.json").read_text(
                    encoding="utf-8"))["messages"])

    def test_summary_120_char_clamp(self, tmp_path):
        mgr = SessionManager(root=tmp_path)
        _mk(mgr, "s2", [
            {"role": "user", "content": "長" * 500},
            {"role": "assistant", "content": "答" * 400},
        ])
        r = {x["id"]: x for x in mgr.list()}["s2"]
        assert r["summary_first_user"].endswith("…")
        assert len(r["summary_first_user"]) == 121      # 120 + ellipsis
        assert len(r["summary_last_assistant"]) == 121

    def test_error_bubbles_and_marker_lines_skipped(self, tmp_path):
        mgr = SessionManager(root=tmp_path)
        _mk(mgr, "s3", [
            {"role": "user", "content": "[已上傳] C:/x.png"},   # marker line
            {"role": "assistant", "content": "[error] no resp"},
            {"role": "assistant", "content": "真正的最新回答"},
        ])
        r = {x["id"]: x for x in mgr.list()}["s3"]
        # the marker line (starts with '[') is not a topic
        assert r["summary_first_user"] == ""
        assert r["summary_last_assistant"] == "真正的最新回答"

    def test_empty_and_corrupt_files(self, tmp_path):
        mgr = SessionManager(root=tmp_path)
        _mk(mgr, "s4", [])
        (mgr.root / "bad.json").write_text("{broken", encoding="utf-8")
        rows = {r["id"]: r for r in mgr.list()}
        s4 = rows["s4"]
        assert s4["summary_first_user"] == ""
        assert s4["summary_last_assistant"] == ""
        assert s4["summary_chars"] == 0
        assert s4["n_messages"] == 0
        assert "bad" not in rows        # corrupt file skipped


class TestGuiBridgePassThrough:
    def test_api_list_sessions_carries_summary(self, tmp_path, monkeypatch):
        mgr = SessionManager(root=tmp_path)
        _mk(mgr, "s1", [
            {"role": "user", "content": "問題"},
            {"role": "assistant", "content": "回答"},
        ])
        from unittest.mock import patch

        from gui.api import Api

        api = object.__new__(Api)
        with patch.object(Api, "_get_session_mgr", return_value=mgr):
            out = api.list_sessions()
        s = out["sessions"][0]
        assert s["summary_first_user"] == "問題"
        assert s["summary_last_assistant"] == "回答"


class TestFrontendWiring:
    def _read(self, name: str) -> str:
        import io

        root = Path(__file__).resolve().parents[1]
        return io.open(root / "gui" / "assets" / name,
                       encoding="utf-8", errors="replace").read()

    def test_table_has_summary_column(self):
        html = self._read("index.html")
        assert 'data-i18n="thSummary"' in html

    def test_row_renders_two_line_summary(self):
        js = self._read("app.js")
        assert "summary_first_user" in js
        assert "summary_last_assistant" in js
        assert "sess-summary" in js
        assert 'colspan="7"' in js

    def test_i18n_four_locales(self):
        js = self._read("app.js")
        import re

        assert len(re.findall(r"^\s*thSummary:\s", js, re.M)) == 4

    def test_css_rules_exist(self):
        css = self._read("style.css")
        for frag in (".sess-summary-cell", ".sess-summary",
                     ".sess-summary-2"):
            assert frag in css, frag