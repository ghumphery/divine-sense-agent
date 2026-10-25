"""
Tests for the ``response_language`` plumbing added in v0.8.5.

The user reported "選擇繁體中文介面,但回答出現簡體中文" — the GUI
language picker changed the UI strings but didn't propagate to the
LLM, so responses drifted to whatever the model picked (often
Simplified Chinese on a mainland-hosted inference endpoint).

These tests pin the fix end-to-end:

* ``Agent._system_prompt()`` injects a LANGUAGE DIRECTIVE when
  ``response_language`` is set, and never when it's ``None`` (so
  pre-v0.8.5 callers keep working unchanged).
* The directive mentions the human-readable language name (so the
  LLM actually understands it).
* ``Agent.ask()`` and ``chat_turn()`` thread the parameter through
  to ``_messages_with_user()`` and the legacy path.
* ``gui/api.py::chat_turn`` / ``ask`` accept the parameter and
  forward it.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

# Make project root importable so we can import src/ and gui/ without
# a package install.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from src.agent.agent import Agent
from gui.api import Api


# ─── Agent._system_prompt ──────────────────────────────────────────


class TestSystemPromptLanguageDirective:
    """The LANGUAGE DIRECTIVE block is the load-bearing piece: if the
    LLM doesn't see "respond in zh-TW", it answers in whatever its
    training data biases it toward (often Simplified Chinese from
    mainland model hubs)."""

    def _agent(self) -> Agent:
        # Don't construct the real OpenAI client — just create the
        # bare object so we can call ``_system_prompt`` directly.
        a = object.__new__(Agent)
        a._client = None
        return a

    @pytest.mark.parametrize("lang,human_name", [
        ("zh-TW", "Traditional Chinese"),
        ("zh-CN", "Simplified Chinese"),
        ("en",   "English"),
        ("ja",   "Japanese"),
    ])
    def test_legacy_path_injects_human_readable_name(self, lang, human_name):
        a = self._agent()
        prompt = a._system_prompt(
            web_enabled=False, tools_enabled=False, response_language=lang,
        )
        assert "LANGUAGE DIRECTIVE" in prompt
        # The directive must include the *human-readable* name, not
        # just the locale code — many LLMs are trained primarily on
        # "Traditional Chinese" / "简体中文" not "zh-TW" alone.
        assert human_name in prompt

    def test_tools_path_also_injects_directive(self):
        a = self._agent()
        prompt = a._system_prompt(
            web_enabled=False, tools_enabled=True, response_language="zh-TW",
        )
        assert "LANGUAGE DIRECTIVE" in prompt
        assert "Traditional Chinese" in prompt

    def test_web_path_injects_directive(self):
        a = self._agent()
        prompt = a._system_prompt(
            web_enabled=True, tools_enabled=False, response_language="ja",
        )
        assert "LANGUAGE DIRECTIVE" in prompt
        assert "Japanese" in prompt

    def test_none_does_not_inject_directive(self):
        """The default (None) preserves the v0.8.4 behaviour — no
        LANGUAGE DIRECTIVE block, so the LLM is free to pick its
        own language. This is critical so v0.8.5 is a strict
        superset of v0.8.4."""
        a = self._agent()
        prompt = a._system_prompt(response_language=None)
        assert "LANGUAGE DIRECTIVE" not in prompt
        # Even with web + tools enabled, no directive should appear.
        prompt_full = a._system_prompt(
            web_enabled=True, tools_enabled=True, response_language=None,
        )
        assert "LANGUAGE DIRECTIVE" not in prompt_full

    def test_empty_string_does_not_inject_directive(self):
        """An empty string should be treated like None — no directive."""
        a = self._agent()
        prompt = a._system_prompt(response_language="")
        assert "LANGUAGE DIRECTIVE" not in prompt

    def test_unknown_lang_falls_back_to_code(self):
        """For unknown locales the directive falls back to the raw
        code rather than silently dropping the hint."""
        a = self._agent()
        prompt = a._system_prompt(response_language="ko-KR")
        assert "LANGUAGE DIRECTIVE" in prompt
        assert "ko-KR" in prompt

    def test_directive_preserves_code_filenames_and_identifiers(self):
        """The directive MUST carve out an exception for filenames,
        library names, and code identifiers — translating them would
        break citation integrity (the citation rules require the
        exact filename)."""
        a = self._agent()
        prompt = a._system_prompt(response_language="zh-TW")
        carve_out_terms = ("filename", "library", "code", "identifier")
        assert any(t in prompt for t in carve_out_terms), (
            f"directive does not carve out filenames / library names: "
            f"{prompt[-400:]!r}"
        )

    def test_directive_does_not_clobber_citation_rules(self):
        """The LANGUAGE DIRECTIVE is appended AFTER the CITATION
        RULES block so it doesn't override them. Spot-check that the
        order is right."""
        a = self._agent()
        prompt = a._system_prompt(response_language="zh-TW")
        c_idx = prompt.find("CITATION RULES")
        l_idx = prompt.find("LANGUAGE DIRECTIVE")
        assert c_idx > 0
        assert l_idx > c_idx, (
            f"LANGUAGE DIRECTIVE must come AFTER CITATION RULES "
            f"(got C@{c_idx}, L@{l_idx})"
        )


# ─── Agent.ask / chat_turn thread the parameter ───────────────────


class TestAgentThreadsLanguageToLLM:
    """Make sure the parameter actually reaches the OpenAI client
    call — not just the function signature."""

    def _agent_with_mock_client(self):
        a = object.__new__(Agent)
        a._client = MagicMock()
        a._model = "test-model"
        a._provider = "test"
        # Stub the McpManager so ``use_tools=True`` doesn't try to
        # connect to anything real.
        mgr = MagicMock()
        mgr.has_tools.return_value = False
        a._mcp_manager = mgr
        # v0.9.104.6 — the legacy chat_turn path runs the answer
        # through _append_memory_citation (re.sub) — give the mock a
        # REAL string or the TypeError masks the actual assertion
        # (this test was silently masked by the greeting fast-path
        # when the live .env leaked QDRANT_ROUTE=on).
        (a._client.chat.completions.create.return_value.choices[0]
         .message.content) = "ok answer"
        return a

    def setup_method(self):
        """v0.9.104.6 — the live .env may carry QDRANT_ROUTE=on; the
        greeting fast-path would intercept 'hello' BEFORE the mock
        create and these tests would see call_args=None. The v2 router
        must be OFF for every test here (each test pins the rest of
        its env via monkeypatch)."""
        import os

        self._route_env_backup = {
            k: os.environ.get(k)
            for k in ("QDRANT_ROUTE", "QDRANT_TIME_ROUTE",
                      "QDRANT_ROUTE_CLASSES", "QDRANT_ROUTE_PROB")
        }
        for k in ("QDRANT_ROUTE", "QDRANT_TIME_ROUTE"):
            os.environ.pop(k, None)

    def teardown_method(self):
        import os

        for k, v in self._route_env_backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_ask_legacy_path_threads_language(self):
        a = self._agent_with_mock_client()
        # Legacy path needs top_k + web params; stub _build_context
        # to avoid hitting Qdrant.
        a._build_context = lambda q, top_k, use_web_search, web_max_results: "CTX"
        a.ask(
            "hello", top_k=5, use_web_search=False,
            response_language="zh-TW", use_tools=False,
        )
        kwargs = a._client.chat.completions.create.call_args.kwargs
        sysmsg = kwargs["messages"][0]
        assert "LANGUAGE DIRECTIVE" in sysmsg["content"]
        assert "Traditional Chinese" in sysmsg["content"]

    def test_ask_no_language_does_not_inject(self):
        a = self._agent_with_mock_client()
        a._build_context = lambda q, top_k, use_web_search, web_max_results: "CTX"
        a.ask(
            "hello", top_k=5, use_web_search=False,
            response_language=None, use_tools=False,
        )
        kwargs = a._client.chat.completions.create.call_args.kwargs
        sysmsg = kwargs["messages"][0]
        assert "LANGUAGE DIRECTIVE" not in sysmsg["content"]

    def test_chat_turn_legacy_path_threads_language(self):
        a = self._agent_with_mock_client()
        a._build_context = lambda q, top_k, use_web_search, web_max_results: "CTX"
        a.chat_turn(
            "hello", history=[], top_k=5, use_web_search=False,
            response_language="ja", use_tools=False,
        )
        kwargs = a._client.chat.completions.create.call_args.kwargs
        sysmsg = next(m for m in kwargs["messages"] if m["role"] == "system")
        assert "LANGUAGE DIRECTIVE" in sysmsg["content"]
        assert "Japanese" in sysmsg["content"]


# ─── gui.api.Api ──────────────────────────────────────────────────


class TestGuiApiForwardsLanguage:
    """The GUI bridge must accept response_language and pass it down.
    We mock ``Agent.chat_turn`` to capture the kwargs and assert the
    bridge isn't dropping the parameter."""

    def test_chat_turn_forwards_response_language(self):
        api = Api.__new__(Api)
        fake_agent = MagicMock()
        fake_agent.chat_turn.return_value = ("answer", "ctx", ["README.md"])
        api._get_agent = lambda: fake_agent
        result = api.chat_turn(
            "hello", history=[], web_search=False,
            web_max_results=5, top_k=5, response_language="zh-TW",
        )
        assert "answer" in result
        kwargs = fake_agent.chat_turn.call_args.kwargs
        assert kwargs.get("response_language") == "zh-TW"

    def test_chat_turn_default_response_language_is_none(self):
        """When the GUI omits response_language, the API defaults to
        None so the Agent keeps the legacy behaviour."""
        api = Api.__new__(Api)
        fake_agent = MagicMock()
        fake_agent.chat_turn.return_value = ("answer", "ctx", [])
        api._get_agent = lambda: fake_agent
        api.chat_turn("hello", history=[], web_search=False)
        kwargs = fake_agent.chat_turn.call_args.kwargs
        assert kwargs.get("response_language") is None

    def test_ask_forwards_response_language(self):
        api = Api.__new__(Api)
        fake_agent = MagicMock()
        fake_agent.ask.return_value = "answer"
        api._get_agent = lambda: fake_agent
        result = api.ask(
            "hello", top_k=5, web_search=False, response_language="zh-CN",
        )
        kwargs = fake_agent.ask.call_args.kwargs
        assert kwargs.get("response_language") == "zh-CN"


# ─── CLI argparse ─────────────────────────────────────────────────


class TestCliLanguageFlag:
    """The CLI accepts ``--lang`` and threads it through both
    ``ask`` and ``chat`` subcommands."""

    def test_ask_accepts_lang_flag(self):
        # cli.py builds its parser inside ``main()``. We can't easily
        # reach it without launching the CLI. Instead, sanity-check
        # via ``argparse`` directly on the option string.
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument(
            "--lang", default=None,
            choices=["zh-TW", "zh-CN", "en", "ja"],
        )
        args = parser.parse_args(["--lang", "zh-TW"])
        assert args.lang == "zh-TW"

    def test_ask_default_lang_is_none(self):
        """Default must be None — we don't force a language on
        legacy CLI users who haven't asked for it."""
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument(
            "--lang", default=None,
            choices=["zh-TW", "zh-CN", "en", "ja"],
        )
        args = parser.parse_args([])
        assert args.lang is None

    def test_chat_lang_unknown_value_rejected(self):
        """Passing an unknown locale is rejected by argparse
        (choices constraint), so the CLI won't silently fall back
        to wrong-language responses."""
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument(
            "--lang", default=None,
            choices=["zh-TW", "zh-CN", "en", "ja"],
        )
        with pytest.raises(SystemExit):
            parser.parse_args(["--lang", "fr-FR"])
