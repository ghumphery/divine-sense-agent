"""v0.9.16.1 — upload → OCR pipeline hardening (user log 2026-09-05).

The log showed three failures chained together:

1. ``describe_image(file_path, prompt)`` WITHOUT the enum'd ``mode``
   param was bounced → **mode is now inferred from file_path /
   data_base64**.
2. Even with the mode fixed, an uploaded file under the filesystem
   WRITE sandbox was "outside the import sandbox root" → the
   image tools now accept import + filesystem read/write roots
   (``_allowed_file_roots``).
3. The provider (OpenRouter) answered HTTP 200 with
   ``choices=None`` → ``resp.choices[0]`` raised TypeError and
   killed the whole chat turn → one retry + a graceful
   ``EMPTY_RESPONSE_TEXT`` instead of a crash.
4. The phone browser gave up before the answer landed → the web
   server's response write hit ConnectionResetError → now logged
   at DEBUG.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.agent.agent import EMPTY_RESPONSE_TEXT, _valid_completion
from src.config import config
from src.mcp_client.host import QdrantLocalHost


class _FakeFallbackOpenAI:
    """Failover-candidate stand-in: every create() answers HTTP 200
    with choices=None (invalid) — no network."""

    def __init__(self, api_key=None, base_url=None, **kw):
        self.chat = MagicMock()
        bad = MagicMock()
        bad.choices = None
        self.chat.completions.create.return_value = bad

# 1x1 transparent PNG (canonical test fixture, same as
# tests/test_describe_image.py).
PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000"
    "001f15c4890000000d49444154789c63f8cf00000003000100"
    "b63e0c920000000049454e44ae426082"
)

_PATCH_IMG_DESC = "src.ingest.image_describer.ImageDescriber"


def _fake_describer(description="a tiny png"):
    fake = MagicMock()
    fake.describe.return_value = description
    fake.PROMPT = "default bilingual prompt"
    fake._cache = {}
    return fake


def _make_host():
    host = QdrantLocalHost()
    host._store = MagicMock()
    host._retriever = MagicMock()
    return host


@pytest.fixture
def sandboxes(tmp_path, monkeypatch):
    """Separate import / write / read roots, as a user might scope
    them, and no WATCH_DIR interference."""
    imp = tmp_path / "import"
    wr = tmp_path / "write"
    rd = tmp_path / "read"
    for d in (imp, wr, rd):
        d.mkdir()
    monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(imp))
    monkeypatch.setenv("QDRANT_FILESYSTEM_WRITE_ROOT", str(wr))
    monkeypatch.setenv("QDRANT_FILESYSTEM_READ_ROOT", str(rd))
    monkeypatch.delenv("WATCH_DIR", raising=False)
    return {"import": imp, "write": wr, "read": rd}


# ─── _valid_completion ───────────────────────────────────────


class TestValidCompletion:
    def test_none_choices_invalid(self):
        resp = MagicMock()
        resp.choices = None
        assert _valid_completion(resp) is False

    def test_empty_choices_invalid(self):
        resp = MagicMock()
        resp.choices = []
        assert _valid_completion(resp) is False

    def test_missing_choices_attr_invalid(self):
        class _R:  # bare object without .choices
            pass

        assert _valid_completion(_R()) is False

    def test_valid_response(self):
        resp = MagicMock()
        resp.choices = [MagicMock(message=MagicMock(content="hi"))]
        assert _valid_completion(resp) is True


# ─── tool loop + legacy paths degrade gracefully ─────────────


def _pin_provider_config(monkeypatch, keys: dict | None = None):
    """Hermetically pin the provider config so the FAILOVER walk in
    ``_create_validated`` builds FAKE fallback clients instead of
    hitting the real OpenRouter / SiliconFlow endpoints from the
    user's .env.

    v0.9.18.0: patch the CURRENT ``src.config.config`` object (the
    one ``Agent._get_config()`` hands out), not a possibly-stale
    module-top binding — earlier test files reload src.config, which
    made the setattr land on an orphan and leaked the live .env keys
    into the failover walk (a real HTTP 401 was observed in combined
    runs).
    """
    import src.config as _mod  # resolves via sys.modules

    cfg = _mod.config
    keys = keys or {}
    for name in ("ollama", "openrouter", "siliconflow", "openai"):
        monkeypatch.setattr(
            cfg, f"{name.upper()}_API_KEY", keys.get(name, ""),
            raising=False,
        )
    monkeypatch.setattr(cfg, "LLM_PROVIDER", "", raising=False)
    monkeypatch.setattr(cfg, "LLM_MODEL", "gpt-4o-mini", raising=False)
    # v0.9.19.0: pin the OpenAI-compatible escape-hatch knobs too —
    # a leaked live OPENAI_COMPAT_API_KEY would add a compat
    # candidate to the failover walk and consume outcome queues.
    for _attr in (
        "OPENAI_CHAT_MODEL",
        "OPENAI_COMPAT_API_KEY", "OPENAI_COMPAT_BASE_URL",
        "OPENAI_COMPAT_CHAT_MODEL",
    ):
        monkeypatch.setattr(cfg, _attr, "", raising=False)
    monkeypatch.setattr(
        cfg, "OLLAMA_CHAT_MODEL", "gemma4:31b-cloud", raising=False,
    )
    monkeypatch.setattr(
        cfg, "OPENROUTER_CHAT_MODEL",
        "anthropic/claude-3.5-sonnet", raising=False,
    )


def _agent_with_responses(tmp_path, monkeypatch, responses):
    """Agent stub whose client create() pops ``responses`` in order."""
    monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path))
    _pin_provider_config(monkeypatch, keys={
        "ollama": "sk-ol", "openrouter": "sk-or",
    })
    from src.agent.agent import Agent

    a = Agent()
    a._provider = "ollama"
    a._model = "stub-model"
    fake_openai = MagicMock()
    fake_openai.chat.completions.create.side_effect = responses
    a._client = fake_openai
    return a


def _bad_resp():
    resp = MagicMock()
    resp.choices = None
    return resp


def _good_resp(content="done"):
    resp = MagicMock()
    resp.choices = [
        MagicMock(message=MagicMock(content=content), finish_reason="stop")
    ]
    return resp


class TestEmptyChoicesGraceful:
    def test_tool_loop_returns_graceful_text(
        self, tmp_path, monkeypatch,
    ):
        monkeypatch.setattr("src.agent.agent.time.sleep", lambda s: None)
        a = _agent_with_responses(
            tmp_path, monkeypatch, [_bad_resp(), _bad_resp()],
        )
        mgr = MagicMock()
        mgr.has_tools.return_value = True
        a._mcp_manager = mgr
        # failover candidates must be FAKE clients (no real network)
        with patch("src.agent.agent.OpenAI", _FakeFallbackOpenAI):
            answer, _ctx, _src = a.chat_turn("q", history=[], use_tools=True)
        assert answer == EMPTY_RESPONSE_TEXT

    def test_tool_loop_retries_once_then_succeeds(
        self, tmp_path, monkeypatch,
    ):
        monkeypatch.setattr("src.agent.agent.time.sleep", lambda s: None)
        a = _agent_with_responses(
            tmp_path, monkeypatch, [_bad_resp(), _good_resp("recovered")],
        )
        mgr = MagicMock()
        mgr.has_tools.return_value = True
        a._mcp_manager = mgr
        with patch("src.agent.agent.OpenAI", _FakeFallbackOpenAI):
            answer, _ctx, _src = a.chat_turn("q", history=[], use_tools=True)
        assert answer == "recovered"
        assert a._client.chat.completions.create.call_count == 2

    def test_ask_legacy_path_graceful_text(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.agent.agent.time.sleep", lambda s: None)
        a = _agent_with_responses(
            tmp_path, monkeypatch, [_bad_resp(), _bad_resp()],
        )
        mgr = MagicMock()
        mgr.has_tools.return_value = False
        a._mcp_manager = mgr
        a._build_context = lambda *args, **kwargs: ""
        with patch("src.agent.agent.OpenAI", _FakeFallbackOpenAI):
            out = a.ask("q", use_tools=True)
        assert out == EMPTY_RESPONSE_TEXT

    def test_chat_turn_legacy_path_graceful_text(
        self, tmp_path, monkeypatch,
    ):
        monkeypatch.setattr("src.agent.agent.time.sleep", lambda s: None)
        a = _agent_with_responses(
            tmp_path, monkeypatch, [_bad_resp(), _bad_resp()],
        )
        mgr = MagicMock()
        mgr.has_tools.return_value = False
        a._mcp_manager = mgr
        a._build_context = lambda *args, **kwargs: ""
        with patch("src.agent.agent.OpenAI", _FakeFallbackOpenAI):
            answer, _ctx, _src = a.chat_turn("q", history=[], use_tools=True)
        assert answer == EMPTY_RESPONSE_TEXT


# ─── describe_image mode inference + upload sandbox ──────────


class TestDescribeImageModeInference:
    def test_mode_inferred_from_file_path(
        self, sandboxes, tmp_path,
    ):
        img = sandboxes["import"] / "pic.png"
        img.write_bytes(PNG_BYTES)
        host = _make_host()
        with patch(_PATCH_IMG_DESC, return_value=_fake_describer()):
            result = host._describe_image({
                # NOTE: no 'mode' — the exact user-log call shape.
                "file_path": str(img),
                "prompt": "ocr",
            })
        assert result.get("ok") is True, result
        assert result["mode"] == "path"
        assert result["description"] == "a tiny png"

    def test_mode_inferred_from_data_base64(self, sandboxes):
        import base64

        host = _make_host()
        with patch(_PATCH_IMG_DESC, return_value=_fake_describer()):
            result = host._describe_image({
                # NOTE: no 'mode' — base64 payload present instead.
                "data_base64": (
                    "data:image/png;base64,"
                    + base64.b64encode(PNG_BYTES).decode()
                ),
            })
        assert result.get("ok") is True, result
        assert result["mode"] == "base64"

    def test_no_inferable_mode_still_bounced(self, sandboxes):
        host = _make_host()
        result = host._describe_image({"prompt": "ocr"})
        assert "error" in result
        assert "'mode' must be 'path' or 'base64'" in result["error"]

    def test_file_outside_all_roots_lists_roots(
        self, sandboxes, tmp_path,
    ):
        outside = tmp_path / "outside"
        outside.mkdir()
        img = outside / "pic.png"
        img.write_bytes(PNG_BYTES)
        host = _make_host()
        result = host._describe_image({
            "file_path": str(img),  # mode inferred
        })
        assert "error" in result
        assert "allowed sandbox roots" in result["error"]


class TestUploadsSandboxAccepted:
    def test_describe_image_on_upload(self, sandboxes):
        uploads = sandboxes["write"] / "uploads"
        uploads.mkdir()
        img = uploads / "20260905-231833-IMG_5749.jpeg"
        img.write_bytes(PNG_BYTES)
        host = _make_host()
        with patch(_PATCH_IMG_DESC, return_value=_fake_describer()):
            result = host._describe_image({
                "file_path": str(img),  # mode inferred
            })
        assert result.get("ok") is True, result
        assert result["description"] == "a tiny png"

    def test_resize_image_on_upload_lands_beside_source(
        self, sandboxes,
    ):
        pytest.importorskip("PIL")
        import io

        from PIL import Image

        uploads = sandboxes["write"] / "uploads"
        uploads.mkdir()
        buf = io.BytesIO()
        Image.new("RGB", (16, 16), (200, 30, 30)).save(buf, format="PNG")
        img = uploads / "big.png"
        img.write_bytes(buf.getvalue())
        host = _make_host()
        result = host._resize_image({
            "file_path": str(img), "max_width": 8,
        })
        assert "error" not in result, result
        assert Path(result["output_path"]).parent == uploads
        assert Path(result["output_path"]).exists()


# ─── web server: client-gone guard (source level) ────────────


class TestWebServerClientGoneGuard:
    def test_do_POST_wraps_send_in_connection_error_guard(self):
        src = (
            Path(__file__).resolve().parents[1]
            / "gui" / "web_server.py"
        ).read_text(encoding="utf-8")
        assert "except ConnectionError as e:" in src
        assert "client gone before response" in src


# ─── v0.9.16.3 — image OCR prompt rule ───────────────────────


class TestImageOcrPromptRule:
    """The GUI-turn rabbit hole (describe_image OK → model rebuilt
    OCR itself via windows-mcp PowerShell, 19 calls, 568k tokens,
    hit the 20-iteration cap) is now taught away: answer from the
    describe_image description; never re-implement OCR."""

    def _prompt(self, tools_enabled=True) -> str:
        import os

        os.environ.setdefault("QDRANT_RAG_HOME", "")
        from src.agent.agent import Agent

        a = Agent()
        return a._system_prompt(
            web_enabled=False, tools_enabled=tools_enabled,
        )

    def test_tools_prompt_contains_rule(self):
        prompt = self._prompt()
        assert "describe_image" in prompt
        assert "Do NOT re-implement OCR" in prompt
        assert "PowerShell" in prompt

    def test_legacy_prompt_excluded(self):
        prompt = self._prompt(tools_enabled=False)
        assert "Do NOT re-implement OCR" not in prompt


# ─── v0.9.16.2 — tool-result base64 stripping ────────────────


class TestStripBase64FromToolText:
    def test_dict_result_raw_image_base64_replaced(self):
        from src.agent.agent import _strip_base64_from_tool_text

        blob = "QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVphYmNkZWZnaGlr" * 120
        text = (
            '{"ok": true, "description": "a tiny png", '
            '"raw_image_base64": "' + blob + '"}'
        )
        out = _strip_base64_from_tool_text(text)
        assert "raw_image_base64" not in out or "omitted" in out
        assert "<base64 omitted:" in out
        assert '"description": "a tiny png"' in out
        assert blob not in out

    def test_non_json_long_base64_run_replaced(self):
        from src.agent.agent import _strip_base64_from_tool_text

        blob = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef0123456789+/" * 120
        text = "result: " + blob + " done"
        out = _strip_base64_from_tool_text(text)
        assert "<base64 omitted:" in out
        assert blob not in out

    def test_short_text_untouched(self):
        from src.agent.agent import _strip_base64_from_tool_text

        assert _strip_base64_from_tool_text("short ok result") == (
            "short ok result"
        )

    def test_prose_not_mangled(self):
        from src.agent.agent import _strip_base64_from_tool_text

        prose = "字 " * 3000  # long text, no base64 runs
        assert _strip_base64_from_tool_text(prose) == prose

    def test_kill_switch_off(self, monkeypatch):
        from src.agent.agent import _strip_base64_from_tool_text

        monkeypatch.setenv("AGENT_STRIP_TOOL_BASE64", "off")
        blob = "QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVphYmNkZWZnaGlr" * 40
        text = '{"raw_image_base64": "' + blob + '"}'
        assert _strip_base64_from_tool_text(text) == text


class TestRunToolCallStripsBase64:
    def test_tool_message_content_is_slimmed(self, tmp_path, monkeypatch):
        monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path))
        from src.agent.agent import Agent
        from src.mcp_client.base import McpCallResult

        a = Agent()
        a._provider = "stub"
        a._model = "stub-model"
        blob = "QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVphYmNkZWZnaGlr" * 150
        huge = (
            '{"ok": true, "description": "OCR text here", '
            '"raw_image_base64": "' + blob + '"}'
        )
        mgr = MagicMock()
        mgr.has_tools.return_value = True
        mgr.call.return_value = McpCallResult(text=huge, is_error=False)
        a._mcp_manager = mgr
        call = MagicMock()
        call.function.name = "describe_image"
        call.function.arguments = '{"file_path": "x.jpeg"}'
        call.id = "call_1"
        msg = a._run_tool_call(call)
        assert msg["role"] == "tool"
        assert "OCR text here" in msg["content"]
        assert "<base64 omitted:" in msg["content"]
        assert blob not in msg["content"]


class TestCreateValidatedExceptionRetry:
    def _agent(self, tmp_path, monkeypatch, responses):
        monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path))
        # no fallback keys → empty failover candidate list → the
        # last exception re-raises without touching real endpoints
        _pin_provider_config(monkeypatch, keys={})
        from src.agent.agent import Agent

        a = Agent()
        a._provider = "stub"
        a._model = "stub-model"
        fake = MagicMock()
        fake.chat.completions.create.side_effect = responses
        a._client = fake
        return a

    def test_exception_then_success(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.agent.agent.time.sleep", lambda s: None)
        a = self._agent(
            tmp_path, monkeypatch,
            [RuntimeError("transient"), _good_resp("ok")],
        )
        resp, ok = a._create_validated(model="m", messages=[])
        assert ok is True
        assert resp.choices[0].message.content == "ok"

    def test_both_exceptions_reraise_last(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.agent.agent.time.sleep", lambda s: None)
        a = self._agent(
            tmp_path, monkeypatch,
            [RuntimeError("e1"), RuntimeError("e2")],
        )
        with pytest.raises(RuntimeError, match="e2"):
            a._create_validated(model="m", messages=[])

    def test_tool_loop_api_error_degrades_gracefully(
        self, tmp_path, monkeypatch,
    ):
        monkeypatch.setattr("src.agent.agent.time.sleep", lambda s: None)
        a = _agent_with_responses(
            tmp_path, monkeypatch,
            [RuntimeError("Error code: 404 - No endpoints found"),
             RuntimeError("Error code: 404 - No endpoints found")],
        )
        mgr = MagicMock()
        mgr.has_tools.return_value = True
        a._mcp_manager = mgr

        class _RaisingOpenAI:
            """Failover candidate that also 404s (offline)."""

            def __init__(self, api_key=None, base_url=None, **kw):
                self.chat = MagicMock()
                self.chat.completions.create.side_effect = RuntimeError(
                    "Error code: 404 - No endpoints found",
                )

        with patch("src.agent.agent.OpenAI", _RaisingOpenAI):
            answer, _ctx, _src = a.chat_turn(
                "q", history=[], use_tools=True,
            )
        assert "LLM call failed after one retry" in answer
        assert "404" in answer