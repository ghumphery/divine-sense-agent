"""v0.9.17.0 — Agent cross-provider failover tests.

The describe_image-style backup mechanism (v0.9.16.4) applied to the
chat LLM itself: when the PRIMARY provider fails (2 attempts — API
errors or HTTP 200 with choices=None), ``_create_validated`` walks
every OTHER configured provider in ``_PROVIDER_ORDER`` priority
(ollama → openrouter → siliconflow → openai), 1 attempt each. The
OpenRouter-only ``extra_body`` routing object is stripped for
non-openrouter fallbacks.

Covers: candidate ordering, end-to-end failover via ``chat_turn``,
``extra_body`` stripping / preservation, last-exception re-raise,
empty-choices degradation, ``last_provider`` bookkeeping,
``_summarize_history`` failover, single-provider no-op.
"""
from __future__ import annotations

from collections import deque
from unittest.mock import MagicMock

import pytest

from src.agent.agent import EMPTY_RESPONSE_TEXT


def _resolved_agent(tmp_path, monkeypatch, keys: dict | None = None):
    """Agent whose provider config is pinned hermetically: ollama
    primary + optional openrouter/siliconflow/openai fallback keys,
    neutral LLM_PROVIDER / LLM_MODEL, and no routing env leakage
    (the user's real .env would otherwise force providers / models).

    v0.9.19.0: every pin lands on the CURRENT ``src.config.config``
    object (resolved via ``sys.modules`` — the same pattern as
    test_upload_ocr_fixes). Earlier test files reload src.config,
    which made the old module-top ``config`` binding STALE: the
    setattr landed on an orphan while ``Agent._get_config()`` read
    the fresh object, so the pins silently no-op'd and the helper
    blew up with "No LLM provider configured" in combined runs."""
    monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path))
    keys = keys or {}
    bases = {
        "ollama": "https://ollama.com/v1",
        "openrouter": "https://openrouter.ai/api/v1",
        "siliconflow": "https://api.siliconflow.cn/v1",
        "openai": "https://api.openai.com/v1",
    }
    chat_models = {
        "ollama": "gpt-oss:120b-cloud",
        "openrouter": "anthropic/claude-3.5-sonnet",
        "siliconflow": "Qwen/Qwen2.5-72B-Instruct",
    }
    import src.config as _cfg_mod  # resolves via sys.modules

    cfg = _cfg_mod.config
    for name in ("ollama", "openrouter", "siliconflow", "openai"):
        monkeypatch.setattr(
            cfg, f"{name.upper()}_API_KEY", keys.get(name, ""),
            raising=False,
        )
        monkeypatch.setattr(
            cfg, f"{name.upper()}_BASE_URL", bases[name], raising=False,
        )
    monkeypatch.setattr(
        cfg, "OLLAMA_CHAT_MODEL", chat_models["ollama"], raising=False,
    )
    monkeypatch.setattr(
        cfg, "OPENROUTER_CHAT_MODEL",
        chat_models["openrouter"], raising=False,
    )
    monkeypatch.setattr(
        cfg, "SILICONFLOW_CHAT_MODEL",
        chat_models["siliconflow"], raising=False,
    )
    # neutral global override + no forced provider
    monkeypatch.setattr(cfg, "LLM_PROVIDER", "", raising=False)
    monkeypatch.setattr(cfg, "LLM_MODEL", "gpt-4o-mini", raising=False)
    # no routing env leakage (suffix / reasoning / provider routing)
    for var in (
        "OPENROUTER_MODEL_SUFFIX", "OPENROUTER_REASONING_EFFORT",
        "OPENROUTER_PROVIDER_ORDER", "OPENROUTER_ALLOW_FALLBACKS",
        "OPENROUTER_REQUIRE_PARAMETERS", "OPENROUTER_DATA_COLLECTION",
        "OPENROUTER_IGNORE", "OPENROUTER_QUANTIZATIONS",
        "OPENROUTER_SORT",
        # v0.9.45.0 (drive-by, same live-.env leak defence as
        # test_llm_providers / test_openrouter_routing): the chain
        # order is read from os.environ PER CALL by
        # _resolved_provider_order, so the user's live
        # LLM_PROVIDER_CHAIN must be wiped here too — otherwise a
        # STANDALONE run of this file reorders the candidates and
        # every order assertion fails.
        "LLM_PROVIDER_CHAIN", "VISION_PROVIDER_CHAIN",
    ):
        monkeypatch.delenv(var, raising=False)
    # v0.9.19.0: pin the OpenAI-compatible escape-hatch knobs — the
    # user's live .env carries OPENAI_CHAT_MODEL, and the openai
    # candidate's model must stay LLM_MODEL (gpt-4o-mini) here.
    for _attr in (
        "OPENAI_CHAT_MODEL",
        "OPENAI_COMPAT_API_KEY", "OPENAI_COMPAT_BASE_URL",
        "OPENAI_COMPAT_CHAT_MODEL",
    ):
        monkeypatch.setattr(cfg, _attr, "", raising=False)
    from src.agent.agent import Agent

    a = Agent()
    _ = a.client  # resolve primary
    return a


def _stub_primary(a, outcomes: list):
    """Replace the resolved primary client with a fake whose create()
    pops ``outcomes`` (Exception | response) per call."""
    fake = MagicMock()
    fake.chat.completions.create.side_effect = list(outcomes)
    a._client = fake
    return fake


def _good_resp(content="done"):
    resp = MagicMock()
    resp.choices = [
        MagicMock(message=MagicMock(content=content), finish_reason="stop")
    ]
    return resp


def _bad_resp():
    resp = MagicMock()
    resp.choices = None
    return resp


class _FakeOpenAICls:
    """Stand-in for ``openai.OpenAI`` used by failover candidate
    construction: records constructor kwargs + instances and serves
    an outcome queue (str content | Exception | response) per call."""

    constructed: list = []
    instances: list = []
    outcomes: deque = deque()

    def __init__(self, api_key=None, base_url=None, **kw):
        type(self).constructed.append(
            {"api_key": api_key, "base_url": base_url}
        )
        self.api_key = api_key
        self.base_url = base_url
        self.chat = MagicMock()
        type(self).instances.append(self)
        outcome = (
            type(self).outcomes.popleft() if type(self).outcomes else None
        )
        if isinstance(outcome, Exception):
            self.chat.completions.create.side_effect = outcome
        elif outcome is None:
            # no scripted outcome → HTTP 200 with choices=None
            # (the invalid-response shape _valid_completion rejects)
            bad = MagicMock()
            bad.choices = None
            self.chat.completions.create.return_value = bad
        elif isinstance(outcome, str):
            # content string → wrap into a valid, stop-finished
            # response object (so the tool loop returns it as-is)
            resp = MagicMock()
            resp.choices = [MagicMock(
                message=MagicMock(content=outcome), finish_reason="stop",
            )]
            self.chat.completions.create.return_value = resp
        else:
            self.chat.completions.create.return_value = outcome


def _patch_agent_openai(outcomes: list):
    """Patch ``src.agent.agent.OpenAI``; returns (constructed, last_instance)."""
    _FakeOpenAICls.constructed = []
    _FakeOpenAICls.instances = []
    _FakeOpenAICls.outcomes = deque(outcomes)
    patcher = patch("src.agent.agent.OpenAI", _FakeOpenAICls)
    patcher.start()
    return _FakeOpenAICls.constructed


from unittest.mock import patch  # noqa: E402  (after helper defs)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr("src.agent.agent.time.sleep", lambda s: None)


# ─── _provider_candidates ────────────────────────────────────


class TestProviderCandidates:
    def test_primary_first_then_priority_order(
        self, tmp_path, monkeypatch,
    ):
        a = _resolved_agent(tmp_path, monkeypatch, keys={
            "ollama": "sk-ol", "openrouter": "sk-or",
            "siliconflow": "sk-sf", "openai": "sk-oai",
        })
        with patch("src.agent.agent.OpenAI", _FakeOpenAICls):
            cands = a._provider_candidates()
        assert [c[0] for c in cands] == [
            "ollama", "openrouter", "siliconflow", "openai",
        ]
        assert cands[0][2] is a._client  # primary keeps its client

    def test_keyless_providers_skipped(self, tmp_path, monkeypatch):
        a = _resolved_agent(tmp_path, monkeypatch, keys={
            "ollama": "sk-ol", "openai": "sk-oai",
        })
        with patch("src.agent.agent.OpenAI", _FakeOpenAICls):
            cands = a._provider_candidates()
        assert [c[0] for c in cands] == ["ollama", "openai"]
        assert cands[1][1] == "gpt-4o-mini"  # openai model_fn = LLM_MODEL

    def test_single_provider_no_failover(self, tmp_path, monkeypatch):
        a = _resolved_agent(tmp_path, monkeypatch, keys={"ollama": "sk-ol"})
        assert a._failover_candidates() == []

    def test_openrouter_candidate_applies_routing(
        self, tmp_path, monkeypatch,
    ):
        monkeypatch.setenv("OPENROUTER_PROVIDER_ORDER", "together")
        a = _resolved_agent(tmp_path, monkeypatch, keys={
            "ollama": "sk-ol", "openrouter": "sk-or",
        })
        with patch("src.agent.agent.OpenAI", _FakeOpenAICls):
            cands = a._provider_candidates()
        fb = {c[0]: c for c in cands[1:]}
        assert "openrouter" in fb
        # routing applied to the candidate model; the primary's
        # self._extra_body is NOT clobbered by candidate building
        assert a._extra_body in (None, {})


# ─── _create_validated failover ──────────────────────────────


class TestCreateValidatedFailover:
    def test_primary_fails_fallback_answers(self, tmp_path, monkeypatch):
        a = _resolved_agent(tmp_path, monkeypatch, keys={
            "ollama": "sk-ol", "openrouter": "sk-or",
        })
        primary = _stub_primary(a, outcomes=[
            RuntimeError("boom"), RuntimeError("boom"),
        ])
        with patch("src.agent.agent.OpenAI", _FakeOpenAICls):
            _patch_agent_openai(["fallback ok"])
            resp, ok = a._create_validated(model="m", messages=[])
        assert ok is True
        assert resp.choices[0].message.content == "fallback ok"
        assert a.last_provider == "openrouter"
        assert primary.chat.completions.create.call_count == 2
        # fallback client was constructed with the OPENROUTER key
        assert _FakeOpenAICls.constructed[0]["api_key"] == "sk-or"
        assert "openrouter.ai" in _FakeOpenAICls.constructed[0]["base_url"]

    def test_extra_body_stripped_for_non_openrouter_fallback(
        self, tmp_path, monkeypatch,
    ):
        a = _resolved_agent(tmp_path, monkeypatch, keys={
            "ollama": "sk-ol", "siliconflow": "sk-sf",
        })
        _stub_primary(a, outcomes=[RuntimeError("x"), RuntimeError("x")])
        with patch("src.agent.agent.OpenAI", _FakeOpenAICls):
            _patch_agent_openai(["sf ok"])
            resp, ok = a._create_validated(
                model="m", messages=[],
                extra_body={"provider": {"order": ["together"]}},
            )
        assert ok is True
        assert a.last_provider == "siliconflow"
        fb_client = _FakeOpenAICls.instances[-1]
        create_kwargs = fb_client.chat.completions.create.call_args.kwargs
        assert "extra_body" not in create_kwargs
        assert create_kwargs["model"] == "Qwen/Qwen2.5-72B-Instruct"

    def test_openrouter_fallback_keeps_extra_body(
        self, tmp_path, monkeypatch,
    ):
        a = _resolved_agent(tmp_path, monkeypatch, keys={
            "ollama": "sk-ol", "openrouter": "sk-or",
        })
        _stub_primary(a, outcomes=[RuntimeError("x"), RuntimeError("x")])
        with patch("src.agent.agent.OpenAI", _FakeOpenAICls):
            _patch_agent_openai(["or ok"])
            resp, ok = a._create_validated(
                model="m", messages=[],
                extra_body={"reasoning": {"effort": "low"}},
            )
        assert ok is True
        assert a.last_provider == "openrouter"
        fb_client = _FakeOpenAICls.instances[-1]
        create_kwargs = fb_client.chat.completions.create.call_args.kwargs
        # openrouter fallback KEEPS the routing extra_body
        assert create_kwargs.get("extra_body") == {
            "reasoning": {"effort": "low"}
        }

    def test_all_fail_raises_last_exception(self, tmp_path, monkeypatch):
        a = _resolved_agent(tmp_path, monkeypatch, keys={
            "ollama": "sk-ol", "openrouter": "sk-or",
        })
        _stub_primary(a, outcomes=[RuntimeError("e1"), RuntimeError("e2")])
        with patch("src.agent.agent.OpenAI", _FakeOpenAICls):
            _patch_agent_openai([RuntimeError("fallback boom")])
            with pytest.raises(RuntimeError, match="fallback boom"):
                a._create_validated(model="m", messages=[])

    def test_all_invalid_degrades_to_none_false(
        self, tmp_path, monkeypatch,
    ):
        a = _resolved_agent(tmp_path, monkeypatch, keys={
            "ollama": "sk-ol", "openrouter": "sk-or",
        })
        _stub_primary(a, outcomes=[_bad_resp(), _bad_resp()])
        with patch("src.agent.agent.OpenAI", _FakeOpenAICls):
            _patch_agent_openai([_bad_resp()])
            resp, ok = a._create_validated(model="m", messages=[])
        assert resp is None and ok is False

    def test_primary_success_no_failover(self, tmp_path, monkeypatch):
        a = _resolved_agent(tmp_path, monkeypatch, keys={
            "ollama": "sk-ol", "openrouter": "sk-or",
        })
        primary = _stub_primary(a, outcomes=[_good_resp("primary ok")])
        with patch("src.agent.agent.OpenAI", _FakeOpenAICls):
            _patch_agent_openai([])
            resp, ok = a._create_validated(model="m", messages=[])
        assert ok is True
        assert resp.choices[0].message.content == "primary ok"
        assert a.last_provider == "ollama"
        assert len(_FakeOpenAICls.constructed) == 0
        assert primary.chat.completions.create.call_count == 1


# ─── end-to-end chat_turn / summarize ────────────────────────


class TestFailoverEndToEnd:
    def test_chat_turn_survives_primary_outage(
        self, tmp_path, monkeypatch,
    ):
        a = _resolved_agent(tmp_path, monkeypatch, keys={
            "ollama": "sk-ol", "openrouter": "sk-or",
        })
        _stub_primary(a, outcomes=[
            RuntimeError("Error code: 404 - No endpoints found"),
            RuntimeError("Error code: 404 - No endpoints found"),
        ])
        mgr = MagicMock()
        mgr.has_tools.return_value = True
        a._mcp_manager = mgr
        with patch("src.agent.agent.OpenAI", _FakeOpenAICls):
            _patch_agent_openai(["failover answer"])
            answer, _ctx, _src = a.chat_turn(
                "hello", history=[], use_tools=True,
            )
        assert answer == "failover answer"
        assert a.last_provider == "openrouter"

    def test_summarize_history_failover(self, tmp_path, monkeypatch):
        a = _resolved_agent(tmp_path, monkeypatch, keys={
            "ollama": "sk-ol", "openrouter": "sk-or",
        })
        _stub_primary(a, outcomes=[
            RuntimeError("down"), RuntimeError("down"),
        ])
        with patch("src.agent.agent.OpenAI", _FakeOpenAICls):
            _patch_agent_openai(["the summary"])
            out = a._summarize_history("transcript...", max_chars=500)
        assert out == "the summary"

    def test_summarize_history_all_fail_returns_none(
        self, tmp_path, monkeypatch,
    ):
        a = _resolved_agent(tmp_path, monkeypatch, keys={
            "ollama": "sk-ol", "openrouter": "sk-or",
        })
        _stub_primary(a, outcomes=[
            RuntimeError("down"), RuntimeError("down"),
        ])
        with patch("src.agent.agent.OpenAI", _FakeOpenAICls):
            _patch_agent_openai([RuntimeError("still down")])
            out = a._summarize_history("transcript...", max_chars=500)
        assert out is None

    def test_empty_choices_all_providers_graceful_text(
        self, tmp_path, monkeypatch,
    ):
        a = _resolved_agent(tmp_path, monkeypatch, keys={
            "ollama": "sk-ol", "openrouter": "sk-or",
        })
        _stub_primary(a, outcomes=[_bad_resp(), _bad_resp()])
        mgr = MagicMock()
        mgr.has_tools.return_value = True
        a._mcp_manager = mgr
        with patch("src.agent.agent.OpenAI", _FakeOpenAICls):
            _patch_agent_openai([_bad_resp()])
            answer, _ctx, _src = a.chat_turn(
                "hello", history=[], use_tools=True,
            )
        assert answer == EMPTY_RESPONSE_TEXT