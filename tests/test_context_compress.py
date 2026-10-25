"""Tests for v0.9.10.0+ chat context compression.

Covers the three techniques + the opt-in config surface:

* Config parsing — mode validation (bad → off), int fallbacks,
  aggressive halving.
* Summarization — trigger/keep split, LLM summarizer called once and
  cached, extractive fallback, summary cap, off → identical copy.
* Offloading — threshold, file written with full content, pointer
  text, write-failure inline fallback, off → unchanged.
* Selective compression — code fences + JSON verbatim, prose
  squeezed with an explicit marker, short messages untouched.
* Agent integration — _messages_with_user compression chain,
  _run_tool_call offload (errors never offloaded), system prompt
  offloading note, _summarize_history LLM failure fallback.
"""
from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_ENV_KEYS = (
    "CHAT_CONTEXT_COMPRESSION",
    "CHAT_COMPRESS_TRIGGER_MESSAGES",
    "CHAT_COMPRESS_KEEP_MESSAGES",
    "CHAT_COMPRESS_SUMMARY_MODE",
    "CHAT_COMPRESS_SUMMARY_MAX_CHARS",
    "CHAT_COMPRESS_MSG_MAX_CHARS",
    "CHAT_OFFLOAD_THRESHOLD_CHARS",
    "QDRANT_OFFLOAD_DIR",
    "QDRANT_FILESYSTEM_READ_ROOT",
    "QDRANT_FILESYSTEM_WRITE_ROOT",
    "WATCH_DIR",
    "QDRANT_RAG_HOME",
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
    "OLLAMA_API_KEY",
    "SILICONFLOW_API_KEY",
    "LLM_PROVIDER",
)


@pytest.fixture()
def fresh_config(monkeypatch, tmp_path):
    """Wipe the compression + provider env vars, pin the sandboxes at
    a per-test tmp dir, then reload src.config so the late-bound
    ContextCompressor._cfg() picks the new values up."""
    for k in _ENV_KEYS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path))
    monkeypatch.setenv(
        "QDRANT_FILESYSTEM_READ_ROOT", str(tmp_path / "read")
    )
    monkeypatch.setenv(
        "QDRANT_FILESYSTEM_WRITE_ROOT", str(tmp_path / "write")
    )
    import dotenv

    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **kw: None)
    if "src.config" in sys.modules:
        del sys.modules["src.config"]
    import src.config

    monkeypatch.setattr(src.config, "load_dotenv", lambda *a, **kw: None)
    importlib.reload(src.config)
    yield tmp_path


def _msgs(n: int, fill: str = "msg") -> list[dict]:
    """n alternating user/assistant messages with two sentences each."""
    out = []
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        out.append(
            {
                "role": role,
                "content": f"{fill} {i} — 第一句話。Second sentence {i}.",
            }
        )
    return out


def _big_prose(chars: int, seed: str = "lorem") -> str:
    return " ".join(f"{seed}{i}" for i in range(chars // 7))


# ─── config parsing ──────────────────────────────────────────


class TestConfig:
    def test_default_off(self, fresh_config):
        from src.agent.context_compress import ContextCompressor

        cfg = ContextCompressor._cfg()
        assert cfg.mode == "off"
        assert cfg.trigger == 24
        assert cfg.keep == 12
        assert cfg.offload_threshold == 20000
        assert cfg.msg_max == 4000

    def test_bad_mode_falls_back_off(self, fresh_config, monkeypatch):
        monkeypatch.setenv("CHAT_CONTEXT_COMPRESSION", "bogus")
        from src.agent.context_compress import ContextCompressor

        assert ContextCompressor._cfg().mode == "off"

    def test_bad_int_falls_back(self, fresh_config, monkeypatch):
        monkeypatch.setenv("CHAT_CONTEXT_COMPRESSION", "auto")
        monkeypatch.setenv("CHAT_COMPRESS_TRIGGER_MESSAGES", "abc")
        monkeypatch.setenv("CHAT_COMPRESS_KEEP_MESSAGES", "")
        from src.agent.context_compress import ContextCompressor

        cfg = ContextCompressor._cfg()
        assert cfg.trigger == 24  # bad int → default
        assert cfg.keep == 12

    def test_aggressive_halves_budgets(self, fresh_config, monkeypatch):
        monkeypatch.setenv("CHAT_CONTEXT_COMPRESSION", "aggressive")
        from src.agent.context_compress import ContextCompressor

        cfg = ContextCompressor._cfg()
        assert cfg.mode == "aggressive"
        assert cfg.trigger == 12  # 24 // 2
        assert cfg.msg_max == 2000  # 4000 // 2

    def test_bad_summary_mode_falls_back_llm(
        self, fresh_config, monkeypatch
    ):
        monkeypatch.setenv("CHAT_COMPRESS_SUMMARY_MODE", "psychic")
        from src.agent.context_compress import ContextCompressor

        assert ContextCompressor._cfg().summary_mode == "llm"

# ─── 1. summarization ────────────────────────────────────────


class TestSummarization:
    def test_off_returns_identical_copy(self, fresh_config):
        from src.agent.context_compress import ContextCompressor

        c = ContextCompressor()
        msgs = _msgs(40)
        out = c.compress_history(msgs)
        assert out == msgs
        assert out is not msgs  # a copy — callers may mutate it
        assert c.stats.summarized_messages == 0

    def test_under_trigger_untouched(self, fresh_config, monkeypatch):
        monkeypatch.setenv("CHAT_CONTEXT_COMPRESSION", "auto")
        from src.agent.context_compress import ContextCompressor

        c = ContextCompressor()
        msgs = _msgs(20)  # trigger 24
        assert c.compress_history(msgs) == msgs

    def test_over_trigger_folds_old_into_summary(
        self, fresh_config, monkeypatch
    ):
        monkeypatch.setenv("CHAT_CONTEXT_COMPRESSION", "auto")
        monkeypatch.setenv("CHAT_COMPRESS_KEEP_MESSAGES", "12")
        from src.agent.context_compress import ContextCompressor

        c = ContextCompressor()
        msgs = _msgs(30)
        out = c.compress_history(msgs, summarizer=lambda t, m: "SUM")
        assert len(out) == 13  # 1 summary + 12 recent
        first = out[0]
        assert first["role"] == "system"
        assert "Auto-compacted" in first["content"]
        assert "18" in first["content"]  # 30 - 12 folded
        assert "SUM" in first["content"]
        # the recent tail is verbatim and in order
        assert [m["content"] for m in out[1:]] == [
            m["content"] for m in msgs[18:]
        ]
        assert c.stats.summarized_messages == 18

    def test_llm_summarizer_called_once_then_cached(
        self, fresh_config, monkeypatch
    ):
        monkeypatch.setenv("CHAT_CONTEXT_COMPRESSION", "auto")
        from src.agent.context_compress import ContextCompressor

        c = ContextCompressor()
        calls = []

        def summarizer(transcript, max_chars):
            calls.append(transcript)
            return "CACHED-SUMMARY"

        msgs = _msgs(30)
        c.compress_history(msgs, summarizer=summarizer)
        c.compress_history(msgs, summarizer=summarizer)
        assert len(calls) == 1  # same old prefix → cache hit
        assert c.stats.summary_cache_hits == 1
        assert c.stats.summary_llm == 1

    def test_growing_old_prefix_resummarizes(
        self, fresh_config, monkeypatch
    ):
        monkeypatch.setenv("CHAT_CONTEXT_COMPRESSION", "auto")
        from src.agent.context_compress import ContextCompressor

        c = ContextCompressor()
        calls = []

        def summarizer(transcript, max_chars):
            calls.append(transcript)
            return "S"

        c.compress_history(_msgs(30), summarizer=summarizer)
        c.compress_history(_msgs(31), summarizer=summarizer)
        assert len(calls) == 2  # old grew 18 → 19, new cache key

    def test_extractive_fallback_when_summarizer_none(
        self, fresh_config, monkeypatch
    ):
        monkeypatch.setenv("CHAT_CONTEXT_COMPRESSION", "auto")
        from src.agent.context_compress import ContextCompressor

        c = ContextCompressor()
        out = c.compress_history(_msgs(30), summarizer=lambda t, m: None)
        content = out[0]["content"]
        assert "user:" in content and "assistant:" in content
        assert c.stats.summary_extractive == 1
        assert c.stats.summary_llm == 0

    def test_summarizer_exception_falls_back(
        self, fresh_config, monkeypatch
    ):
        monkeypatch.setenv("CHAT_CONTEXT_COMPRESSION", "auto")
        from src.agent.context_compress import ContextCompressor

        c = ContextCompressor()

        def boom(transcript, max_chars):
            raise RuntimeError("llm down")

        out = c.compress_history(_msgs(30), summarizer=boom)
        assert "user:" in out[0]["content"]
        assert c.stats.summary_extractive == 1

    def test_extractive_mode_never_calls_llm(
        self, fresh_config, monkeypatch
    ):
        monkeypatch.setenv("CHAT_CONTEXT_COMPRESSION", "auto")
        monkeypatch.setenv("CHAT_COMPRESS_SUMMARY_MODE", "extractive")
        from src.agent.context_compress import ContextCompressor

        c = ContextCompressor()
        called = []
        out = c.compress_history(
            _msgs(30),
            summarizer=lambda t, m: called.append(t) or "SHOULD-NOT",
        )
        assert called == []
        assert "SHOULD-NOT" not in out[0]["content"]
        assert c.stats.summary_extractive == 1

    def test_summary_capped_at_max_chars(self, fresh_config, monkeypatch):
        monkeypatch.setenv("CHAT_CONTEXT_COMPRESSION", "auto")
        monkeypatch.setenv("CHAT_COMPRESS_SUMMARY_MAX_CHARS", "300")
        from src.agent.context_compress import ContextCompressor

        c = ContextCompressor()
        out = c.compress_history(
            _msgs(30), summarizer=lambda t, m: "x" * 5000
        )
        content = out[0]["content"]
        assert len(content) < 300 + 200  # cap + wrapper lines
        assert "x" * 299 in content  # summary carried up to the cap
        assert "…" in content  # truncation marker inside the cap

# ─── 2. offloading ───────────────────────────────────────────


class TestOffload:
    def test_small_result_unchanged(self, fresh_config, monkeypatch):
        monkeypatch.setenv("CHAT_CONTEXT_COMPRESSION", "auto")
        from src.agent.context_compress import ContextCompressor

        c = ContextCompressor()
        text = "short result"
        assert c.offload_tool_result("search_documents", text) == (
            text,
            None,
        )
        assert c.stats.offloaded_calls == 0

    def test_off_mode_never_offloads(self, fresh_config):
        from src.agent.context_compress import ContextCompressor

        c = ContextCompressor()
        big = "x" * 50000
        assert c.offload_tool_result("t", big) == (big, None)

    def test_big_result_offloaded_to_read_sandbox(
        self, fresh_config, monkeypatch
    ):
        monkeypatch.setenv("CHAT_CONTEXT_COMPRESSION", "auto")
        from src.agent.context_compress import ContextCompressor

        c = ContextCompressor()
        big = _big_prose(30000)
        pointer, path = c.offload_tool_result("search_documents", big)
        assert path is not None
        p = Path(path)
        assert p.exists()
        assert p.read_text(encoding="utf-8") == big  # full content saved
        # file lives inside the read sandbox → LLM can read_file it
        read_root = Path(os.environ["QDRANT_FILESYSTEM_READ_ROOT"])
        assert p.parent == read_root / "chat_offloads"
        assert "[offloaded]" in pointer
        assert "search_documents" in pointer
        assert str(p) in pointer
        assert "read_file" in pointer
        assert big[:800] in pointer  # preview
        assert big[801:900] not in pointer  # but not the whole body
        assert c.stats.offloaded_calls == 1

    def test_custom_threshold(self, fresh_config, monkeypatch):
        monkeypatch.setenv("CHAT_CONTEXT_COMPRESSION", "auto")
        # 500 is the floor — any lower value clamps back to 500.
        monkeypatch.setenv("CHAT_OFFLOAD_THRESHOLD_CHARS", "500")
        from src.agent.context_compress import ContextCompressor

        c = ContextCompressor()
        small, big = "y" * 400, "y" * 2000
        assert c.offload_tool_result("t", small) == (small, None)
        pointer, path = c.offload_tool_result("t", big)
        assert path is not None
        assert "[offloaded]" in pointer

    def test_custom_offload_dir(self, fresh_config, monkeypatch, tmp_path):
        monkeypatch.setenv("CHAT_CONTEXT_COMPRESSION", "auto")
        monkeypatch.setenv("QDRANT_OFFLOAD_DIR", str(tmp_path / "od"))
        from src.agent.context_compress import ContextCompressor

        c = ContextCompressor()
        _, path = c.offload_tool_result("t", "z" * 30000)
        assert path is not None
        assert Path(path).parent == tmp_path / "od"

    def test_write_failure_falls_back_inline(
        self, fresh_config, monkeypatch
    ):
        monkeypatch.setenv("CHAT_CONTEXT_COMPRESSION", "auto")
        from src.agent.context_compress import ContextCompressor

        c = ContextCompressor()
        big = "x" * 30000

        def raiser(self, *a, **kw):
            raise OSError("disk full")

        monkeypatch.setattr(Path, "write_text", raiser)
        pointer, path = c.offload_tool_result("t", big)
        assert path is None
        assert pointer == big  # inline kept — tool call never breaks

# ─── 3. selective (semantic) compression ─────────────────────


class TestSelective:
    def test_short_message_untouched(self, fresh_config, monkeypatch):
        monkeypatch.setenv("CHAT_CONTEXT_COMPRESSION", "auto")
        from src.agent.context_compress import ContextCompressor

        c = ContextCompressor()
        msgs = [{"role": "user", "content": "tiny"}]
        out = c.compress_history(msgs)
        assert out[0]["content"] == "tiny"
        assert c.stats.squeezed_messages == 0

    def test_long_prose_squeezed_with_marker(
        self, fresh_config, monkeypatch
    ):
        monkeypatch.setenv("CHAT_CONTEXT_COMPRESSION", "auto")
        from src.agent.context_compress import ContextCompressor

        c = ContextCompressor()
        prose = " ".join(
            f"Sentence number {i} explains something useful."
            for i in range(400)
        )
        msgs = [{"role": "assistant", "content": prose}]
        out = c.compress_history(msgs)
        squeezed = out[0]["content"]
        assert len(squeezed) < len(prose)
        assert "prose compressed" in squeezed
        assert "chars omitted" in squeezed
        assert c.stats.squeezed_messages == 1

    def test_code_fence_verbatim(self, fresh_config, monkeypatch):
        monkeypatch.setenv("CHAT_CONTEXT_COMPRESSION", "auto")
        monkeypatch.setenv("CHAT_COMPRESS_MSG_MAX_CHARS", "800")
        from src.agent.context_compress import ContextCompressor

        c = ContextCompressor()
        code = "\n".join(
            f"x_{i} = {i}  # do not touch" for i in range(120)
        )
        fence = f"```python\n{code}\n```"
        content = (
            "Intro prose. " * 60 + fence + "\n" + "Outro prose. " * 60
        )
        out = c.compress_history(
            [{"role": "assistant", "content": content}]
        )
        squeezed = out[0]["content"]
        assert fence in squeezed  # structured block survived verbatim
        assert "prose compressed" in squeezed

    def test_json_block_verbatim(self, fresh_config, monkeypatch):
        monkeypatch.setenv("CHAT_CONTEXT_COMPRESSION", "auto")
        monkeypatch.setenv("CHAT_COMPRESS_MSG_MAX_CHARS", "600")
        from src.agent.context_compress import ContextCompressor

        c = ContextCompressor()
        data = {
            "items": [{"id": i, "name": f"item-{i}"} for i in range(80)]
        }
        blob = json.dumps(data, indent=2)
        prose = "Explains the payload. " * 40  # ~860 chars → over budget
        content = prose + "\n\n" + blob
        out = c.compress_history(
            [{"role": "assistant", "content": content}]
        )
        squeezed = out[0]["content"]
        assert blob in squeezed  # JSON untouched
        assert "prose compressed" in squeezed  # prose around it squeezed
        assert "Explains the payload." in squeezed  # head sentence kept

    def test_all_structured_untouched(self, fresh_config, monkeypatch):
        monkeypatch.setenv("CHAT_CONTEXT_COMPRESSION", "auto")
        from src.agent.context_compress import ContextCompressor

        c = ContextCompressor()
        blob = json.dumps({"k": "v" * 8000}, indent=2)
        msgs = [{"role": "assistant", "content": blob}]
        out = c.compress_history(msgs)
        assert out[0]["content"] == blob  # no prose → nothing squeezed
        assert c.stats.squeezed_messages == 0

    def test_cjk_sentence_split(self, fresh_config, monkeypatch):
        monkeypatch.setenv("CHAT_CONTEXT_COMPRESSION", "auto")
        monkeypatch.setenv("CHAT_COMPRESS_MSG_MAX_CHARS", "600")
        from src.agent.context_compress import ContextCompressor

        c = ContextCompressor()
        prose = "".join(
            f"這是第{i}句說明，包含一些細節。" for i in range(200)
        )
        out = c.compress_history([{"role": "user", "content": prose}])
        squeezed = out[0]["content"]
        assert len(squeezed) < len(prose)
        assert squeezed.startswith("這是第0句")
        assert "prose compressed" in squeezed

# ─── agent integration ───────────────────────────────────────


def _offline_agent():
    """Agent with mocked OpenAI client (no provider resolution) and
    the McpManager backing field stubbed — no network anywhere."""
    from src.agent.agent import Agent

    a = Agent()
    a._client = MagicMock()
    a._model = "mock-model"
    a._provider = "mock"
    return a


def _tool_call(name: str, args_json: str = "{}"):
    call = MagicMock()
    call.id = "call_1"
    call.function.name = name
    call.function.arguments = args_json
    return call


class TestSelectiveURLKeepV013317:
    """v0.9.133.17 — multi-turn fetch continuity: URLs squeezed out of
    the middle of long messages are re-attached verbatim (target-drift
    lesson: TG multi-article fetches drifted until the user reminded
    the target; the summary/squeeze keeping ONLY prose lost the exact
    link the next turn needed)."""

    def test_squeeze_preserves_urls_verbatim(self, fresh_config):
        from src.agent.context_compress import ContextCompressor

        comp = ContextCompressor()
        text = (
            "第一句開頭說明。\n"
            + "這裡是很長的閒聊文字，內容完全不重要，就是為了把中段句"
              "子擠出預算範圍。" * 20
            + "\n重要文章連結在這句 https://x.com/someone/post/12345 。\n"
            + "再一大段無關填充文字，同樣是為了讓擠壓真的發生。" * 20
        )
        out = comp._squeeze_prose(text, 400)
        assert "https://x.com/someone/post/12345" in out
        assert "URLs present in the omitted part" in out

    def test_no_duplicate_urls_when_already_kept(self, fresh_config):
        from src.agent.context_compress import _append_omitted_urls

        orig = "keep https://a.example/1 and https://b.example/2 here"
        out = _append_omitted_urls(orig, "https://a.example/1 (kept)")
        assert out.count("https://a.example/1") == 1
        assert "https://b.example/2" in out

    def test_summarize_prompt_verbatim_preserves_links(self):
        src_txt = open("src/agent/agent.py", encoding="utf-8").read()
        assert "VERBATIM-preserve every URL" in src_txt


class TestAgentIntegration:
    def test_messages_with_user_off_identical(self, fresh_config):
        from src.agent.agent import Agent

        a = Agent()
        msgs = _msgs(30)
        out = a._messages_with_user(
            "Q?", history=msgs, web_enabled=False, tools_enabled=False,
        )
        assert out[0]["role"] == "system"  # the main system prompt
        assert "[Auto-compacted" not in out[0]["content"]
        assert [m["content"] for m in out[1:-1]] == [
            m["content"] for m in msgs
        ]
        assert out[-1] == {"role": "user", "content": "Q?"}

    def test_messages_with_user_auto_folds(self, fresh_config, monkeypatch):
        monkeypatch.setenv("CHAT_CONTEXT_COMPRESSION", "auto")
        from src.agent.agent import Agent

        a = Agent()
        # the agent wires its own _summarize_history (mocked client →
        # a junk-but-nonempty summary, which is fine for the shape)
        out = a._messages_with_user(
            "Q?", history=_msgs(30),
            web_enabled=False, tools_enabled=False,
        )
        # out[0] = main system prompt; out[1] = summary; last = question
        assert out[1]["role"] == "system"
        assert "[Auto-compacted" in out[1]["content"]
        assert out[-1]["content"] == "Q?"
        assert len(out) == 2 + 1 + 12  # prompt + summary + 12 + question

    @staticmethod
    def _noop_summarizer(transcript, max_chars):
        return "AGENT-SUMMARY"

    def test_compressor_failure_degrades_to_plain_history(
        self, fresh_config, monkeypatch
    ):
        monkeypatch.setenv("CHAT_CONTEXT_COMPRESSION", "auto")
        from src.agent.agent import Agent

        a = Agent()
        # break the summarizer path — compress must still succeed
        a.compressor = None  # type: ignore[assignment]
        msgs = _msgs(30)
        out = a._messages_with_user(
            "Q?", history=msgs, web_enabled=False, tools_enabled=False,
        )
        assert out[-1]["content"] == "Q?"
        assert len(out) == 1 + 30 + 1  # uncompressed fallback

    def test_run_tool_call_offloads_big_result(
        self, fresh_config, monkeypatch
    ):
        monkeypatch.setenv("CHAT_CONTEXT_COMPRESSION", "auto")
        from src.agent.agent import Agent
        from src.mcp_client.base import McpCallResult

        a = _offline_agent()
        mgr = MagicMock()
        mgr.call.return_value = McpCallResult(
            text="r" * 30000, is_error=False
        )
        a._mcp_manager = mgr
        msg = a._run_tool_call(_tool_call("search_documents"))
        assert msg["role"] == "tool"
        assert msg["content"].startswith("[offloaded]")
        assert "search_documents" in msg["content"]
        # the offloaded file landed in the read sandbox
        ro = Path(os.environ["QDRANT_FILESYSTEM_READ_ROOT"]) / "chat_offloads"
        files = list(ro.glob("*.txt"))
        assert files and files[0].read_text(encoding="utf-8") == "r" * 30000

    def test_run_tool_call_error_never_offloaded(
        self, fresh_config, monkeypatch
    ):
        monkeypatch.setenv("CHAT_CONTEXT_COMPRESSION", "auto")
        from src.agent.agent import Agent
        from src.mcp_client.base import McpCallResult

        a = _offline_agent()
        mgr = MagicMock()
        mgr.call.return_value = McpCallResult(text="e" * 30000, is_error=True)
        a._mcp_manager = mgr
        msg = a._run_tool_call(_tool_call("t"))
        assert msg["role"] == "tool"
        # v0.9.133.17: the rulebook prediction-error hint MAY be
        # appended to the first failed tool message (fire-and-forget,
        # by design); what must NOT happen is the OFFLOAD path on
        # errors.
        assert msg["content"].startswith("[tool error] " + "e" * 1000)
        assert "offloaded" not in str(msg["content"]).lower()
        ro = Path(os.environ["QDRANT_FILESYSTEM_READ_ROOT"]) / "chat_offloads"
        assert not ro.exists()  # nothing written

    def test_run_tool_call_small_inline(
        self, fresh_config, monkeypatch
    ):
        monkeypatch.setenv("CHAT_CONTEXT_COMPRESSION", "auto")
        from src.agent.agent import Agent
        from src.mcp_client.base import McpCallResult

        a = _offline_agent()
        mgr = MagicMock()
        mgr.call.return_value = McpCallResult(text="ok", is_error=False)
        a._mcp_manager = mgr
        msg = a._run_tool_call(_tool_call("t"))
        assert msg["content"] == "ok"

    def test_summarize_history_llm_ok(self, fresh_config):
        a = _offline_agent()
        resp = MagicMock()
        resp.choices = [
            MagicMock(message=MagicMock(content="  CLEAN SUMMARY  "))
        ]
        a._client.chat.completions.create.return_value = resp
        assert a._summarize_history("transcript", 500) == "CLEAN SUMMARY"
        kwargs = a._client.chat.completions.create.call_args.kwargs
        assert kwargs["model"] == "mock-model"
        assert kwargs["max_tokens"] == 600

    def test_summarize_history_failure_returns_none(self, fresh_config):
        a = _offline_agent()
        a._client.chat.completions.create.side_effect = RuntimeError("5xx")
        assert a._summarize_history("transcript", 500) is None

    def test_system_prompt_offloading_note(self, fresh_config):
        a = _offline_agent()
        tools = a._system_prompt(tools_enabled=True)
        assert "[offloaded]" in tools
        assert "read_file" in tools
        legacy = a._system_prompt(tools_enabled=False)
        assert "[offloaded]" not in legacy
