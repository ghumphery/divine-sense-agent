"""v0.9.67.0 — Markdown → Telegram MarkdownV2 converter tests.

The ``send_telegram_markdown`` MCP tool sends with
``parse_mode="MarkdownV2"`` but the LLM writes NORMAL markdown —
unescaped specials (``. - ( )`` …) 400 the whole sendMessage with
"can't parse entities", and ``**bold**`` renders literally (MDV2
bold is ``*x*``). Covers:

* ``escape_mdv2`` / ``escape_mdv2_url`` / ``escape_mdv2_code`` —
  the three Telegram escaping scopes;
* the converter: bold/italic/strike/underline (incl. ``***x***``),
  headings → bold, bullets → ``•``, task lists → ✅/☐, blockquotes,
  horizontal rules, inline code, fenced code (lang sanitised,
  specials inside untouched except `` ` ``/``\\``), inline links
  (text escaped, URL per-scope), tables → pre, ``snake_case``
  preserved;
* wiring: ``send_telegram_markdown`` converts before sending and
  degrades to a PLAIN send of the original text on Telegram 400;
  the daemon reply path converts via ``send_markdown`` and falls
  back to plain text when the client lacks the method.
"""
from __future__ import annotations

import io
from unittest.mock import patch

import pytest

from src.telegram_md import (
    escape_mdv2,
    escape_mdv2_code,
    escape_mdv2_url,
    markdown_to_telegram_v2,
)


# ─── escaping scopes ─────────────────────────────────────────


class TestEscape:
    def test_escape_mdv2(self):
        assert escape_mdv2("a.b-c_d!") == "a\\.b\\-c\\_d\\!"
        assert escape_mdv2("*[]()~`>#+-=|{}") == (
            "\\*\\[\\]\\(\\)\\~\\`\\>\\#\\+\\-\\=\\|\\{\\}"
        )
        assert escape_mdv2("普通文字") == "普通文字"
        assert escape_mdv2("") == ""

    def test_escape_mdv2_url(self):
        assert escape_mdv2_url("https://x.com/p)ath") == (
            "https://x.com/p\\)ath"
        )
        assert escape_mdv2_url("a\\b") == "a\\\\b"
        # URL scope: only \\ and ) — other specials untouched
        assert escape_mdv2_url("https://x.com/_a_.md") == (
            "https://x.com/_a_.md"
        )

    def test_escape_mdv2_code(self):
        assert escape_mdv2_code("a`b\\c") == "a\\`b\\\\c"
        assert escape_mdv2_code("*_.-!") == "*_.-!"  # untouched


# ─── the converter ───────────────────────────────────────────


class TestConverter:
    def test_bold_italic_strike(self):
        assert markdown_to_telegram_v2("**粗體**") == "*粗體*"
        assert markdown_to_telegram_v2("_斜體_") == "_斜體_"
        assert markdown_to_telegram_v2("*斜體*") == "_斜體_"
        assert markdown_to_telegram_v2("~~刪除~~") == "~刪除~"
        assert markdown_to_telegram_v2("__底線__") == "__底線__"
        assert markdown_to_telegram_v2("***粗斜***") == "*_粗斜_*"

    def test_snake_case_survives(self):
        assert markdown_to_telegram_v2("snake_case 與 file_name.md") == (
            "snake\\_case 與 file\\_name\\.md"
        )

    def test_headings(self):
        assert markdown_to_telegram_v2("# 標題一") == "*標題一*"
        assert markdown_to_telegram_v2("### 三級 標題") == "*三級 標題*"
        assert markdown_to_telegram_v2("###### h6 名.md") == (
            "*h6 名\\.md*"
        )

    def test_bullets_and_task_lists(self):
        assert markdown_to_telegram_v2("- 項目A\n- 項目B") == (
            "• 項目A\n• 項目B"
        )
        assert markdown_to_telegram_v2("* 星號清單") == "• 星號清單"
        assert markdown_to_telegram_v2("- [x] 已完成\n- [ ] 待辦") == (
            "• ✅ 已完成\n• ☐ 待辦"
        )

    def test_blockquote_and_hr(self):
        assert markdown_to_telegram_v2("> 引言內容") == "> 引言內容"
        assert markdown_to_telegram_v2("---") == "—"
        assert markdown_to_telegram_v2("***") == "—"

    def test_bare_specials_escaped(self):
        assert markdown_to_telegram_v2("3.5 分 - v1.2 測試") == (
            "3\\.5 分 \\- v1\\.2 測試"
        )
        assert markdown_to_telegram_v2("(括號) [中括] {花} = 等 | 管") == (
            "\\(括號\\) \\[中括\\] \\{花\\} \\= 等 \\| 管"
        )

    def test_inline_code(self):
        assert markdown_to_telegram_v2("`code_span`") == "`code_span`"
        # specials inside a code span: untouched
        assert markdown_to_telegram_v2("`a*b_c.d`") == "`a*b_c.d`"

    def test_fenced_code(self):
        assert markdown_to_telegram_v2(
            "```python\nprint('a.b')\n```",
        ) == "```python\nprint('a.b')\n```"
        # backticks/backslashes inside pre are escaped
        assert markdown_to_telegram_v2("```\na`b\\c\n```") == (
            "```\na\\`b\\\\c\n```"
        )
        # language token sanitised
        assert markdown_to_telegram_v2("```c++\nx\n```") == (
            "```c++\nx\n```"
        )

    def test_unclosed_fence(self):
        assert markdown_to_telegram_v2("```\n尾端無閉合") == (
            "```\n尾端無閉合\n```"
        )

    def test_inline_links(self):
        assert markdown_to_telegram_v2(
            "[文字](https://x.com/a_b_c)",
        ) == "[文字](https://x.com/a_b_c)"
        assert markdown_to_telegram_v2(
            "[文件 v1.2](https://x.com/doc.md?q=1)",
        ) == "[文件 v1\\.2](https://x.com/doc.md?q=1)"
        assert markdown_to_telegram_v2(
            "[連結內_文](https://x.com/p_a_1)",
        ) == "[連結內\\_文](https://x.com/p_a_1)"

    def test_tables_become_pre(self):
        assert markdown_to_telegram_v2(
            "| a | b |\n|---|---|\n| 1 | 2 |",
        ) == "```\n| a | b |\n|---|---|\n| 1 | 2 |\n```"

    def test_bold_containing_link(self):
        assert markdown_to_telegram_v2("**[開單](https://x.com/ok)**") == (
            "*[開單](https://x.com/ok)*"
        )

    def test_empty_and_newlines(self):
        assert markdown_to_telegram_v2("") == ""
        assert "\n" in markdown_to_telegram_v2("a\r\nb")

# __PART_WIRING__


# ─── send_telegram_markdown wiring ───────────────────────────


class TestServerMarkdownSend:
    """Drive ``TelegramMCPServer._do_send`` directly (fast, no MCP
    async plumbing) — the MarkdownV2 mode CONVERTS the text before
    sending and degrades to a plain send on Telegram 400."""

    def _srv(self):
        from src.mcp_server.telegram_server import TelegramMCPServer

        return TelegramMCPServer(token="TOK", allowed_chat_ids=[100])

    def test_markdown_text_is_converted(self):
        srv = self._srv()
        captured: dict = {}

        def fake_send(token, chat_id, text, parse_mode=None):
            captured["text"] = text
            captured["parse_mode"] = parse_mode
            return {"message": {"message_id": 1}}

        with patch(
            "src.mcp_server.telegram_server._send_text",
            side_effect=fake_send,
        ):
            srv._do_send(
                {"chat_id": 100, "text": "**粗體** 與 3.5 分"},
                parse_mode="MarkdownV2",
            )
        assert captured["parse_mode"] == "MarkdownV2"
        assert captured["text"] == "*粗體* 與 3\\.5 分"

    def test_plain_mode_byte_identical(self):
        srv = self._srv()
        captured: dict = {}

        def fake_send(token, chat_id, text, parse_mode=None):
            captured["text"] = text
            captured["parse_mode"] = parse_mode
            return {"message": {"message_id": 2}}

        with patch(
            "src.mcp_server.telegram_server._send_text",
            side_effect=fake_send,
        ):
            srv._do_send(
                {"chat_id": 100, "text": "**粗體** 3.5"},
                parse_mode=None,
            )
        assert captured["parse_mode"] is None
        assert captured["text"] == "**粗體** 3.5"

    def test_mdv2_parse_failure_falls_back_to_plain(self):
        srv = self._srv()
        calls: list[tuple] = []

        def fake_send(token, chat_id, text, parse_mode=None):
            calls.append((text, parse_mode))
            if parse_mode == "MarkdownV2":
                raise RuntimeError(
                    "HTTP 400: Bad Request: can't parse entities")
            return {"message": {"message_id": 3}}

        with patch(
            "src.mcp_server.telegram_server._send_text",
            side_effect=fake_send,
        ):
            res = srv._do_send(
                {"chat_id": 100, "text": "**粗體** 3.5"},
                parse_mode="MarkdownV2",
            )
        body = res[0].text
        assert '"mdv2_fallback": true' in body
        assert '"parse_mode": null' in body
        # two calls: converted MDV2 first, then the ORIGINAL plain text
        assert calls[0][1] == "MarkdownV2"
        assert calls[1] == ("**粗體** 3.5", None)

    def test_both_failing_returns_error(self):
        srv = self._srv()

        def fake_send(token, chat_id, text, parse_mode=None):
            raise RuntimeError("HTTP 400: Bad Request")

        with patch(
            "src.mcp_server.telegram_server._send_text",
            side_effect=fake_send,
        ):
            res = srv._do_send(
                {"chat_id": 100, "text": "x"}, parse_mode="MarkdownV2",
            )
        assert res[0].text.startswith("Error:")

    def test_tool_description_mentions_converter(self):
        # Tool specs live inside the async ``_register_tools`` closure
        # — source-guard the converter wording instead.
        py = io.open(
            "src/mcp_server/telegram_server.py", encoding="utf-8",
            errors="replace").read()
        assert "NORMAL markdown" in py
        assert 'parse_mode="MarkdownV2"' in py

# __PART_DAEMON__


# ─── daemon reply wiring ─────────────────────────────────────


class TestDaemonMarkdownReply:
    def test_reply_goes_out_as_mdv2(self):
        from src.agent.telegram_bot import TelegramBotDaemon

        class _MDClient:
            def __init__(self):
                self.md: list[tuple[int, str]] = []

            def send_markdown(self, chat_id, text):
                self.md.append((chat_id, text))

            def send_message(self, chat_id, text):
                raise AssertionError("plain path must not fire")

        client = _MDClient()
        d = TelegramBotDaemon(client=client, on_message=lambda c, t: "",
                              poll_interval=0.1)
        delivered = d._send_reply_md(111, "**粗體** 3.5")
        assert delivered is True
        assert client.md == [(111, "*粗體* 3\\.5")]

    def test_falls_back_when_client_lacks_send_markdown(self):
        from src.agent.telegram_bot import TelegramBotDaemon

        class _PlainClient:
            def __init__(self):
                self.sent: list[tuple[int, str]] = []

            def send_message(self, chat_id, text):
                self.sent.append((chat_id, text))

        client = _PlainClient()
        d = TelegramBotDaemon(client=client, on_message=lambda c, t: "",
                              poll_interval=0.1)
        delivered = d._send_reply_md(111, "**粗體** 原文")
        assert delivered is False  # caller sends plain

    def test_md_failure_falls_back_to_plain(self):
        from src.agent.telegram_bot import TelegramBotDaemon

        class _BrokenMDClient:
            def send_markdown(self, chat_id, text):
                raise RuntimeError("boom")

            def send_message(self, chat_id, text):
                raise AssertionError("not reached in _send_reply_md")

        client = _BrokenMDClient()
        d = TelegramBotDaemon(client=client, on_message=lambda c, t: "",
                              poll_interval=0.1)
        delivered = d._send_reply_md(111, "**粗體** 原文")
        assert delivered is False  # caller falls back to _send_text

    def test_deliver_reply_uses_md_path(self):
        from src.agent.telegram_bot import TelegramBotDaemon

        class _MDClient:
            def __init__(self):
                self.md: list[tuple[int, str]] = []
                self.plain: list[tuple[int, str]] = []

            def send_markdown(self, chat_id, text):
                self.md.append((chat_id, text))

            def send_message(self, chat_id, text):
                self.plain.append((chat_id, text))

        client = _MDClient()
        d = TelegramBotDaemon(client=client, on_message=lambda c, t: "",
                              poll_interval=0.1, speech=object())
        d._deliver_reply(222, "**粗體** 回覆")
        assert client.md == [(222, "*粗體* 回覆")]
        assert client.plain == []


# ─── TelegramBotClient.send_markdown chunk fallback ──────────


class TestClientSendMarkdown:
    def _client(self):
        from src.agent.telegram_bot import TelegramBotClient

        return TelegramBotClient(token="TOK")

    def test_chunks_carry_parse_mode(self, monkeypatch):
        c = self._client()
        calls: list[tuple[str, dict]] = []

        def fake_call(method, params, timeout=None):
            calls.append((method, params))
            return {"message_id": 1}

        monkeypatch.setattr(c, "_call", fake_call)
        c.send_markdown(7, "x" * 5000)
        assert len(calls) == 2
        assert calls[0][1]["parse_mode"] == "MarkdownV2"
        assert calls[1][1]["parse_mode"] == "MarkdownV2"

    def test_parse_failure_degrades_that_chunk(self, monkeypatch):
        c = self._client()
        calls: list[tuple[str, dict]] = []

        def fake_call(method, params, timeout=None):
            calls.append((method, params))
            if params.get("parse_mode") == "MarkdownV2":
                raise RuntimeError(
                    "HTTP 400: Bad Request: can't parse entities")
            return {"message_id": 1}

        monkeypatch.setattr(c, "_call", fake_call)
        c.send_markdown(7, "短訊息")
        assert len(calls) == 2  # mdv2 attempt + plain resend
        assert calls[1][1]["text"] == "短訊息"
        assert "parse_mode" not in calls[1][1]

    def test_non_400_errors_propagate(self, monkeypatch):
        c = self._client()

        def fake_call(method, params, timeout=None):
            raise RuntimeError("HTTP 403: forbidden")

        monkeypatch.setattr(c, "_call", fake_call)
        with pytest.raises(RuntimeError) as exc:
            c.send_markdown(7, "x")
        assert "403" in str(exc.value)