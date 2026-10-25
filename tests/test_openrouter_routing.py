"""Tests for v0.9.9.2+ OpenRouter provider routing.

Coverage:
    * Model-variant fast lanes — ``OPENROUTER_MODEL_SUFFIX``
      appends ``:nitro`` / ``:floor`` / ``:free`` to the model
      slug (idempotent; no-colon input auto-prefixed; unset
      suffix leaves the model untouched).
    * Provider routing object — ``order`` / ``allow_fallbacks`` /
      ``require_parameters`` / ``data_collection`` / ``ignore`` /
      ``quantizations`` / ``sort`` env vars parse into the exact
      JSON shapes OpenRouter's ``provider`` object expects.
    * Invalid values are SKIPPED with a warning, never raise —
      a typo in one routing knob must not take down chat.
    * All-empty configuration injects NOTHING ({} extra_body —
      byte-identical request to the v0.8.9 behaviour).
    * Injection reaches ``chat.completions.create`` via
      ``extra_body`` on the openrouter provider only.
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


ROUTING_ENV_KEYS = (
    "OPENROUTER_PROVIDER_ORDER",
    "OPENROUTER_ALLOW_FALLBACKS",
    "OPENROUTER_REQUIRE_PARAMETERS",
    "OPENROUTER_DATA_COLLECTION",
    "OPENROUTER_IGNORE",
    "OPENROUTER_QUANTIZATIONS",
    "OPENROUTER_SORT",
    "OPENROUTER_MODEL_SUFFIX",
    "OPENROUTER_REASONING_EFFORT",
)


@pytest.fixture()
def fresh_config(monkeypatch):
    """Clean env slate (same shape as tests/test_llm_providers.py
    fresh_config, extended with the v0.9.9.2 routing vars)."""
    env_keys = (
        "OLLAMA_API_KEY", "OLLAMA_BASE_URL", "OLLAMA_CHAT_MODEL",
        "OPENAI_API_KEY", "OPENAI_BASE_URL", "LLM_MODEL",
        "OPENROUTER_API_KEY", "OPENROUTER_BASE_URL",
        "OPENROUTER_CHAT_MODEL",
        "SILICONFLOW_API_KEY", "SILICONFLOW_BASE_URL",
        "SILICONFLOW_CHAT_MODEL",
        "LLM_PROVIDER", "QDRANT_RAG_HOME",
        # v0.9.18.0: chain-order vars (live .env leak defence)
        "LLM_PROVIDER_CHAIN", "VISION_PROVIDER_CHAIN",
        # v0.9.19.0: OpenAI-compatible escape-hatch knobs
        "OPENAI_CHAT_MODEL",
        "OPENAI_COMPAT_API_KEY", "OPENAI_COMPAT_BASE_URL",
        "OPENAI_COMPAT_CHAT_MODEL",
    ) + ROUTING_ENV_KEYS
    for k in env_keys:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv(
        "QDRANT_RAG_HOME",
        str(Path(__file__).resolve().parent / "_test_home_routing"),
    )
    import dotenv
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **kw: None)
    if "src.config" in sys.modules:
        del sys.modules["src.config"]
    import src.config
    monkeypatch.setattr(src.config, "load_dotenv", lambda *a, **kw: None)
    importlib.reload(src.config)
    yield
    # Teardown: tests set routing vars via plain os.environ[...]
    # (same pattern as test_llm_providers.py), so undo them here —
    # otherwise they leak into test_llm_providers.py whose own
    # fresh_config fixture doesn't know these v0.9.9.2 keys.
    for k in ROUTING_ENV_KEYS:
        os.environ.pop(k, None)


def _reload_config():
    if "src.config" in sys.modules:
        del sys.modules["src.config"]
    import src.config
    importlib.reload(src.config)


def _agent_with_openrouter():
    """Set an OpenRouter key, reload config, build a fresh Agent
    with the provider already resolved."""
    os.environ["OPENROUTER_API_KEY"] = "sk-test-or"
    _reload_config()
    from src.agent.agent import Agent
    a = Agent()
    _ = a.client  # force _pick_provider -> _apply_openrouter_routing
    return a


class _FakeResp:
    """Minimal chat.completions response stub."""

    def __init__(self, content: str = "ok"):
        msg = MagicMock()
        msg.message.content = content
        msg.message.tool_calls = None
        msg.finish_reason = "stop"
        self.choices = [msg]


def _make_agent():
    """Reload config FIRST so env vars set inside the test body
    (after the fresh_config fixture already reloaded) are picked
    up by the Config class attributes — same reason
    tests/test_llm_providers.py routes every construction through
    ``fresh_agent()``."""
    _reload_config()
    from src.agent.agent import Agent
    return Agent()


# --- model-variant fast lanes (:nitro / :floor) ---


def test_suffix_nitro_appended(fresh_config):
    os.environ["OPENROUTER_MODEL_SUFFIX"] = ":nitro"
    a = _make_agent()
    model, extra = a._apply_openrouter_routing("anthropic/claude-3.5-sonnet")
    assert model == "anthropic/claude-3.5-sonnet:nitro"
    assert extra == {}


def test_suffix_floor_appended(fresh_config):
    os.environ["OPENROUTER_MODEL_SUFFIX"] = ":floor"
    a = _make_agent()
    model, _ = a._apply_openrouter_routing("meta-llama/llama-3.1-8b-instruct")
    assert model == "meta-llama/llama-3.1-8b-instruct:floor"


def test_suffix_not_duplicated(fresh_config):
    """Model already carrying the variant must stay untouched."""
    os.environ["OPENROUTER_MODEL_SUFFIX"] = ":nitro"
    a = _make_agent()
    model, _ = a._apply_openrouter_routing("openai/gpt-4o:nitro")
    assert model == "openai/gpt-4o:nitro"


def test_suffix_without_colon_gets_prefixed(fresh_config):
    os.environ["OPENROUTER_MODEL_SUFFIX"] = "nitro"
    a = _make_agent()
    model, _ = a._apply_openrouter_routing("openai/gpt-4o")
    assert model == "openai/gpt-4o:nitro"


def test_no_suffix_model_untouched(fresh_config):
    a = _make_agent()
    model, extra = a._apply_openrouter_routing("openai/gpt-4o")
    assert model == "openai/gpt-4o"
    assert extra == {}


# --- provider routing object ---


def test_all_empty_injects_nothing(fresh_config):
    """No routing env vars set -> {} (identical to v0.8.9 request)."""
    a = _make_agent()
    model, extra = a._apply_openrouter_routing("openai/gpt-4o")
    assert model == "openai/gpt-4o"
    assert extra == {}


def test_order_parsed_as_list(fresh_config):
    os.environ["OPENROUTER_PROVIDER_ORDER"] = "DeepInfra, Together"
    a = _make_agent()
    _, extra = a._apply_openrouter_routing("openai/gpt-4o")
    assert extra == {"provider": {"order": ["DeepInfra", "Together"]}}


def test_ignore_parsed_as_list(fresh_config):
    os.environ["OPENROUTER_IGNORE"] = "ProviderA,ProviderB,,"
    a = _make_agent()
    _, extra = a._apply_openrouter_routing("openai/gpt-4o")
    assert extra["provider"]["ignore"] == ["ProviderA", "ProviderB"]


def test_allow_fallbacks_false(fresh_config):
    os.environ["OPENROUTER_ALLOW_FALLBACKS"] = "false"
    a = _make_agent()
    _, extra = a._apply_openrouter_routing("openai/gpt-4o")
    assert extra["provider"]["allow_fallbacks"] is False


def test_allow_fallbacks_true(fresh_config):
    os.environ["OPENROUTER_ALLOW_FALLBACKS"] = "true"
    a = _make_agent()
    _, extra = a._apply_openrouter_routing("openai/gpt-4o")
    assert extra["provider"]["allow_fallbacks"] is True


def test_allow_fallbacks_invalid_skipped(fresh_config):
    os.environ["OPENROUTER_ALLOW_FALLBACKS"] = "maybe"
    a = _make_agent()
    _, extra = a._apply_openrouter_routing("openai/gpt-4o")
    assert "allow_fallbacks" not in extra.get("provider", {})


def test_require_parameters_true(fresh_config):
    os.environ["OPENROUTER_REQUIRE_PARAMETERS"] = "true"
    a = _make_agent()
    _, extra = a._apply_openrouter_routing("openai/gpt-4o")
    assert extra["provider"]["require_parameters"] is True


def test_require_parameters_invalid_skipped(fresh_config):
    os.environ["OPENROUTER_REQUIRE_PARAMETERS"] = "sometimes"
    a = _make_agent()
    _, extra = a._apply_openrouter_routing("openai/gpt-4o")
    assert extra == {}


def test_data_collection_deny(fresh_config):
    os.environ["OPENROUTER_DATA_COLLECTION"] = "deny"
    a = _make_agent()
    _, extra = a._apply_openrouter_routing("openai/gpt-4o")
    assert extra["provider"]["data_collection"] == "deny"


def test_data_collection_allow(fresh_config):
    os.environ["OPENROUTER_DATA_COLLECTION"] = "allow"
    a = _make_agent()
    _, extra = a._apply_openrouter_routing("openai/gpt-4o")
    assert extra["provider"]["data_collection"] == "allow"


def test_data_collection_invalid_skipped(fresh_config):
    os.environ["OPENROUTER_DATA_COLLECTION"] = "opt-in"
    a = _make_agent()
    _, extra = a._apply_openrouter_routing("openai/gpt-4o")
    assert extra == {}


def test_quantizations_filtered(fresh_config):
    os.environ["OPENROUTER_QUANTIZATIONS"] = "int4, int8"
    a = _make_agent()
    _, extra = a._apply_openrouter_routing("openai/gpt-4o")
    assert extra["provider"]["quantizations"] == ["int4", "int8"]


def test_quantizations_invalid_tokens_dropped(fresh_config):
    """Invalid tokens are dropped with a warning; valid ones survive."""
    os.environ["OPENROUTER_QUANTIZATIONS"] = "int4,bogus-q,fp8"
    a = _make_agent()
    _, extra = a._apply_openrouter_routing("openai/gpt-4o")
    assert extra["provider"]["quantizations"] == ["int4", "fp8"]


def test_quantizations_all_invalid_means_absent(fresh_config):
    os.environ["OPENROUTER_QUANTIZATIONS"] = "bogus-q"
    a = _make_agent()
    _, extra = a._apply_openrouter_routing("openai/gpt-4o")
    assert extra == {}


def test_sort_price(fresh_config):
    os.environ["OPENROUTER_SORT"] = "price"
    a = _make_agent()
    _, extra = a._apply_openrouter_routing("openai/gpt-4o")
    assert extra["provider"]["sort"] == "price"


def test_sort_throughput(fresh_config):
    os.environ["OPENROUTER_SORT"] = "throughput"
    a = _make_agent()
    _, extra = a._apply_openrouter_routing("openai/gpt-4o")
    assert extra["provider"]["sort"] == "throughput"


def test_sort_invalid_skipped(fresh_config):
    os.environ["OPENROUTER_SORT"] = "latency"
    a = _make_agent()
    _, extra = a._apply_openrouter_routing("openai/gpt-4o")
    assert extra == {}


# --- full combination + injection into chat.completions.create ---


def test_all_fields_combined(fresh_config):
    """The full user scenario — every knob set at once."""
    os.environ["OPENROUTER_PROVIDER_ORDER"] = "DeepInfra,Together"
    os.environ["OPENROUTER_ALLOW_FALLBACKS"] = "false"
    os.environ["OPENROUTER_REQUIRE_PARAMETERS"] = "true"
    os.environ["OPENROUTER_DATA_COLLECTION"] = "deny"
    os.environ["OPENROUTER_IGNORE"] = "Aetherium,Alpaca Network"
    os.environ["OPENROUTER_QUANTIZATIONS"] = "int4,int8"
    os.environ["OPENROUTER_SORT"] = "price"
    os.environ["OPENROUTER_MODEL_SUFFIX"] = ":nitro"
    a = _make_agent()
    model, extra = a._apply_openrouter_routing(
        "meta-llama/llama-3.1-8b-instruct"
    )
    assert model == "meta-llama/llama-3.1-8b-instruct:nitro"
    assert extra == {
        "provider": {
            "order": ["DeepInfra", "Together"],
            "allow_fallbacks": False,
            "require_parameters": True,
            "data_collection": "deny",
            "ignore": ["Aetherium", "Alpaca Network"],
            "quantizations": ["int4", "int8"],
            "sort": "price",
        },
    }


def _stub_offline(a):
    """Detach the freshly-built agent from Qdrant / MCP / web:
    stub _build_context (legacy ask path) and the McpManager
    property backing field, same pattern as
    tests/test_response_language.py::_agent_with_mock_client."""
    a._build_context = (
        lambda q, top_k, use_web_search, web_max_results: "CTX"
    )
    mgr = MagicMock()
    mgr.has_tools.return_value = False
    a._mcp_manager = mgr
    return a


def test_extra_body_reaches_create(fresh_config):
    os.environ["OPENROUTER_PROVIDER_ORDER"] = "DeepInfra"
    os.environ["OPENROUTER_SORT"] = "throughput"
    a = _stub_offline(_agent_with_openrouter())
    a._client = MagicMock()
    a._client.chat.completions.create.return_value = _FakeResp()
    a.ask("hi", use_tools=False)
    kwargs = a._client.chat.completions.create.call_args.kwargs
    assert kwargs["extra_body"] == {
        "provider": {
            "order": ["DeepInfra"],
            "sort": "throughput",
        },
    }
    # The model string itself is unchanged (no suffix configured).
    assert kwargs["model"] == "anthropic/claude-3.5-sonnet"


def test_suffix_and_provider_reach_create(fresh_config):
    os.environ["OPENROUTER_MODEL_SUFFIX"] = ":floor"
    os.environ["OPENROUTER_ALLOW_FALLBACKS"] = "false"
    a = _stub_offline(_agent_with_openrouter())
    a._client = MagicMock()
    a._client.chat.completions.create.return_value = _FakeResp()
    a.ask("hi", use_tools=False)
    kwargs = a._client.chat.completions.create.call_args.kwargs
    assert kwargs["model"] == "anthropic/claude-3.5-sonnet:floor"
    assert kwargs["extra_body"] == {
        "provider": {"allow_fallbacks": False},
    }


def test_no_routing_no_extra_body_param(fresh_config):
    """All-empty config -> NO extra_body kwarg at all (legacy
    byte-identical request)."""
    a = _stub_offline(_agent_with_openrouter())
    a._client = MagicMock()
    a._client.chat.completions.create.return_value = _FakeResp()
    a.ask("hi", use_tools=False)
    kwargs = a._client.chat.completions.create.call_args.kwargs
    assert "extra_body" not in kwargs


def test_non_openrouter_provider_never_injects(fresh_config):
    """A non-openrouter provider must get {} extra_body — other
    OpenAI-compatible APIs would reject the unknown field."""
    os.environ["OPENROUTER_PROVIDER_ORDER"] = "DeepInfra"
    os.environ["OPENAI_API_KEY"] = "sk-test-oa"
    os.environ["LLM_PROVIDER"] = "openai"
    a = _stub_offline(_agent_with_openrouter())
    assert a._provider == "openai"
    assert a._extra_body == {}
    a._client = MagicMock()
    a._client.chat.completions.create.return_value = _FakeResp()
    a.ask("hi", use_tools=False)
    kwargs = a._client.chat.completions.create.call_args.kwargs
    assert "extra_body" not in kwargs
    assert kwargs["model"] == "gpt-4o-mini"


# ─── v0.9.11.1+: OPENROUTER_REASONING_EFFORT ─────────────────────


def test_reasoning_effort_injected_top_level(fresh_config):
    """`reasoning` is a TOP-LEVEL extra_body field (NOT inside
    `provider`) — OpenRouter's documented injection point."""
    os.environ["OPENROUTER_REASONING_EFFORT"] = "low"
    a = _make_agent()
    model, extra = a._apply_openrouter_routing("z-ai/glm-5.3-flash")
    assert extra.get("reasoning") == {"effort": "low"}


def test_reasoning_effort_composes_with_provider(fresh_config):
    os.environ["OPENROUTER_PROVIDER_ORDER"] = "Together"
    os.environ["OPENROUTER_REASONING_EFFORT"] = "low"
    a = _make_agent()
    _, extra = a._apply_openrouter_routing("z-ai/glm-5.3-flash")
    assert extra["provider"] == {"order": ["Together"]}
    assert extra["reasoning"] == {"effort": "low"}


def test_reasoning_effort_invalid_skipped(fresh_config):
    os.environ["OPENROUTER_REASONING_EFFORT"] = "turbo"
    a = _make_agent()
    _, extra = a._apply_openrouter_routing("z-ai/glm-5.3-flash")
    assert "reasoning" not in extra  # skipped, never raised


def test_reasoning_effort_unset_sends_nothing(fresh_config):
    a = _make_agent()
    _, extra = a._apply_openrouter_routing("z-ai/glm-5.3-flash")
    assert "reasoning" not in extra  # byte-identical legacy request


def test_reasoning_effort_reaches_create(fresh_config):
    os.environ["OPENROUTER_REASONING_EFFORT"] = "low"
    a = _stub_offline(_agent_with_openrouter())
    a._client = MagicMock()
    a._client.chat.completions.create.return_value = _FakeResp()
    a.ask("hi", use_tools=False)
    kwargs = a._client.chat.completions.create.call_args.kwargs
    assert kwargs["extra_body"]["reasoning"] == {"effort": "low"}


