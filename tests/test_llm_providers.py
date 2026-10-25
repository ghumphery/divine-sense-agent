"""Tests for v0.8.9+ LLM provider dispatch — 4 providers (ollama /
openrouter / siliconflow / openai) + the 5th `openai-compat` and the
dedicated `OPENAI_CHAT_MODEL` knob added in v0.9.19.0.

Coverage:
    * Auto-detect priority order (ollama -> openrouter ->
      siliconflow -> openai) when LLM_PROVIDER is empty
      and multiple keys are set.
    * Explicit LLM_PROVIDER override wins even when the
      chosen provider's key is empty (must raise with a
      helpful error naming the missing env var).
    * LLM_MODEL override works for every provider.
    * Each provider's default model + base_url are picked
      from the right Config attribute (no cross-pollution).
    * Config.__repr__ hides all 4 API keys (not just the
      original 3).
    * No provider configured at all -> error message
      mentions all 4 env var names so the user can pick one.
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture()
def fresh_config(monkeypatch):
    """Reset the env vars + dotenv + config module so each test
    starts from a clean slate.

    The fixture only WIPES env and patches load_dotenv. Tests
    set their own *_API_KEY values, then call
    ``reload_config()`` to re-import ``src.config`` so the new
    class-level attributes pick up the freshly-set env vars.

    This split (wipe + per-test reload) avoids the subtle bug
    where the fixture reloads too early - the Config class
    binds its attributes from ``os.environ`` at class-definition
    time, so any env var set after the reload is invisible.
    """
    # All provider env vars + base_url overrides (the legacy
    # escape hatch uses OPENAI_BASE_URL to point at LM Studio
    # / vLLM / etc., so we have to wipe those too).
    env_keys = (
        "OLLAMA_API_KEY", "OLLAMA_BASE_URL", "OLLAMA_CHAT_MODEL",
        "OPENAI_API_KEY", "OPENAI_BASE_URL", "LLM_MODEL",
        "OPENROUTER_API_KEY", "OPENROUTER_BASE_URL",
        "OPENROUTER_CHAT_MODEL",
        "SILICONFLOW_API_KEY", "SILICONFLOW_BASE_URL",
        "SILICONFLOW_CHAT_MODEL",
        "LLM_PROVIDER", "QDRANT_RAG_HOME",
        # v0.9.18.0: chain-order vars — the user's live .env must
        # not leak into provider-resolution tests.
        "LLM_PROVIDER_CHAIN", "VISION_PROVIDER_CHAIN",
        # v0.9.19.0: OpenAI-compatible escape-hatch knobs (the user's
        # live .env will carry OPENAI_CHAT_MODEL once configured).
        "OPENAI_CHAT_MODEL",
        "OPENAI_COMPAT_API_KEY", "OPENAI_COMPAT_BASE_URL",
        "OPENAI_COMPAT_CHAT_MODEL",
    )
    for k in env_keys:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv(
        "QDRANT_RAG_HOME",
        str(Path(__file__).resolve().parent / "_test_home_prov"),
    )
    # Block .env re-reads across reloads.
    import dotenv
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **kw: None)
    if "src.config" in sys.modules:
        del sys.modules["src.config"]
    import src.config
    monkeypatch.setattr(src.config, "load_dotenv", lambda *a, **kw: None)
    importlib.reload(src.config)
    yield


def _new_agent():
    from src.agent.agent import Agent
    return Agent()


def reload_config():
    """Re-import ``src.config`` so the Config class
    re-reads ``os.environ`` for its class-level attributes.
    Tests call this after setting *_API_KEY env vars so the
    new values land in the Config singleton."""
    if "src.config" in sys.modules:
        del sys.modules["src.config"]
    import src.config
    importlib.reload(src.config)


def fresh_agent():
    """reload_config() then construct a fresh Agent. Tests
    call this in place of `_new_agent()` so the just-set
    env vars actually land in the singleton."""
    reload_config()
    from src.agent.agent import Agent
    return Agent()


# --- no provider configured ---


def test_no_provider_raises_with_all_four_env_names(fresh_config):
    """When no API key is set anywhere, the error message
    must name all 4 env vars so the user can pick one. The
    LLM_PROVIDER hint is also surfaced so the user knows
    about the explicit override."""
    a = _new_agent()
    with pytest.raises(ValueError) as exc_info:
        a._pick_provider()
    msg = str(exc_info.value)
    for k in (
        "OLLAMA_API_KEY",
        "OPENROUTER_API_KEY",
        "SILICONFLOW_API_KEY",
        "OPENAI_API_KEY",
        # v0.9.19.0: the 5th provider's key is named in the message too
        "OPENAI_COMPAT_API_KEY",
    ):
        assert k in msg, f"missing {k} in error message"
    assert "LLM_PROVIDER" in msg
    for k in ("ollama", "openrouter", "siliconflow", "openai"):
        assert k in msg


# --- auto-detect priority order ---


def test_ollama_wins_when_all_four_keys_set(fresh_config):
    """Auto-detect priority: ollama > openrouter > siliconflow > openai.
    Setting all four keys must select ollama."""
    os.environ["OLLAMA_API_KEY"] = "sk-test-ol"
    os.environ["OPENROUTER_API_KEY"] = "sk-test-or"
    os.environ["SILICONFLOW_API_KEY"] = "sk-test-sf"
    os.environ["OPENAI_API_KEY"] = "sk-test-oi"
    name, model, client = fresh_agent()._pick_provider()
    assert name == "ollama"
    assert client.api_key == "sk-test-ol"


def test_openrouter_wins_when_ollama_missing(fresh_config):
    os.environ["OPENROUTER_API_KEY"] = "sk-test-or"
    os.environ["SILICONFLOW_API_KEY"] = "sk-test-sf"
    os.environ["OPENAI_API_KEY"] = "sk-test-oi"
    name, _, client = fresh_agent()._pick_provider()
    assert name == "openrouter"
    assert "openrouter.ai" in str(client.base_url)


def test_siliconflow_wins_when_only_siliconflow_and_openai(fresh_config):
    os.environ["SILICONFLOW_API_KEY"] = "sk-test-sf"
    os.environ["OPENAI_API_KEY"] = "sk-test-oi"
    name, _, client = fresh_agent()._pick_provider()
    assert name == "siliconflow"
    assert "siliconflow.cn" in str(client.base_url)


def test_openai_is_last_resort(fresh_config):
    os.environ["OPENAI_API_KEY"] = "sk-test-oi"
    name, _, client = fresh_agent()._pick_provider()
    assert name == "openai"
    assert "api.openai.com" in str(client.base_url)



# --- LLM_PROVIDER override ---


def test_llm_provider_override_wins(fresh_config):
    """Setting LLM_PROVIDER=openrouter picks openrouter even
    when ollama's key is also set (would otherwise win on
    auto-detect)."""
    os.environ["OLLAMA_API_KEY"] = "sk-test-ol"
    os.environ["OPENROUTER_API_KEY"] = "sk-test-or"
    os.environ["LLM_PROVIDER"] = "openrouter"
    name, _, client = fresh_agent()._pick_provider()
    assert name == "openrouter"
    assert client.api_key == "sk-test-or"


def test_llm_provider_override_unknown_raises_helpfully(fresh_config):
    os.environ["LLM_PROVIDER"] = "bogus-provider"
    a = fresh_agent()
    with pytest.raises(ValueError) as exc_info:
        a._pick_provider()
    msg = str(exc_info.value)
    assert "bogus-provider" in msg
    for k in ("ollama", "openrouter", "siliconflow", "openai"):
        assert k in msg


def test_llm_provider_override_with_empty_key_raises(fresh_config):
    """LLM_PROVIDER=openrouter + OPENROUTER_API_KEY='' ->
    error must explicitly say which env var is empty so
    the user fixes the right thing."""
    os.environ["LLM_PROVIDER"] = "openrouter"
    a = fresh_agent()
    with pytest.raises(ValueError) as exc_info:
        a._pick_provider()
    msg = str(exc_info.value)
    assert "OPENROUTER_API_KEY" in msg
    assert "empty" in msg


def test_llm_provider_override_case_insensitive(fresh_config):
    os.environ["OPENROUTER_API_KEY"] = "sk-test-or"
    os.environ["LLM_PROVIDER"] = "OpenRouter"  # mixed case
    name, _, _ = fresh_agent()._pick_provider()
    assert name == "openrouter"


def test_llm_provider_siliconflow_override(fresh_config):
    """The two new providers in v0.8.9+ can also be pinned."""
    os.environ["OLLAMA_API_KEY"] = "sk-test-ol"
    os.environ["SILICONFLOW_API_KEY"] = "sk-test-sf"
    os.environ["LLM_PROVIDER"] = "siliconflow"
    name, _, client = fresh_agent()._pick_provider()
    assert name == "siliconflow"
    assert client.api_key == "sk-test-sf"


# --- LLM_MODEL override per provider ---


def test_llm_model_override_works_for_ollama(fresh_config):
    os.environ["OLLAMA_API_KEY"] = "sk-test-ol"
    os.environ["LLM_MODEL"] = "gpt-oss:20b-cloud"
    name, model, _ = fresh_agent()._pick_provider()
    assert name == "ollama"
    assert model == "gpt-oss:20b-cloud"


def test_llm_model_override_works_for_openai(fresh_config):
    os.environ["OPENAI_API_KEY"] = "sk-test-oi"
    os.environ["LLM_MODEL"] = "claude-3-opus-via-proxy"
    name, model, _ = fresh_agent()._pick_provider()
    assert name == "openai"
    assert model == "claude-3-opus-via-proxy"


def test_default_models_per_provider(fresh_config):
    """When LLM_MODEL is the default placeholder, each
    provider falls back to its own *_CHAT_MODEL setting."""
    os.environ["OLLAMA_API_KEY"] = "sk-test-ol"
    name, model, _ = fresh_agent()._pick_provider()
    assert name == "ollama"
    assert model == "gpt-oss:120b-cloud"

    os.environ.pop("OLLAMA_API_KEY")
    os.environ["OPENROUTER_API_KEY"] = "sk-test-or"
    importlib.reload(importlib.import_module("src.config"))
    name, model, _ = fresh_agent()._pick_provider()
    assert name == "openrouter"
    assert model == "anthropic/claude-3.5-sonnet"

    os.environ.pop("OPENROUTER_API_KEY")
    os.environ["SILICONFLOW_API_KEY"] = "sk-test-sf"
    importlib.reload(importlib.import_module("src.config"))
    name, model, _ = fresh_agent()._pick_provider()
    assert name == "siliconflow"
    assert model == "Qwen/Qwen2.5-72B-Instruct"



# --- base_url per provider ---


def test_base_urls_point_at_correct_providers(fresh_config):
    """Verify each provider's base_url is the expected
    well-known endpoint."""
    os.environ["OPENROUTER_API_KEY"] = "sk-test-or"
    name, _, client = fresh_agent()._pick_provider()
    assert "openrouter.ai" in str(client.base_url)

    os.environ.pop("OPENROUTER_API_KEY")
    os.environ["SILICONFLOW_API_KEY"] = "sk-test-sf"
    importlib.reload(importlib.import_module("src.config"))
    name, _, client = fresh_agent()._pick_provider()
    assert "siliconflow.cn" in str(client.base_url)

    os.environ.pop("SILICONFLOW_API_KEY")
    os.environ["OLLAMA_API_KEY"] = "sk-test-ol"
    importlib.reload(importlib.import_module("src.config"))
    name, _, client = fresh_agent()._pick_provider()
    assert "ollama.com" in str(client.base_url)


def test_custom_openai_base_url_via_proxy(fresh_config):
    """Legacy escape hatch: any OpenAI-compatible endpoint
    via OPENAI_BASE_URL (LM Studio, vLLM, etc.)."""
    os.environ["OPENAI_API_KEY"] = "sk-test"
    os.environ["OPENAI_BASE_URL"] = "https://my-proxy.example.com/v1"
    os.environ["LLM_PROVIDER"] = "openai"
    os.environ["LLM_MODEL"] = "claude-3-opus"
    name, model, client = fresh_agent()._pick_provider()
    assert name == "openai"
    assert "my-proxy.example.com" in str(client.base_url)
    assert model == "claude-3-opus"


# --- Config.__repr__ hides all 4 API keys ---


def test_config_repr_hides_all_four_api_keys():
    """__repr__ must not leak any of the 4 API keys in plain
    text - security regression guard."""
    import src.config
    fake = src.config.Config.__new__(src.config.Config)
    fake.__dict__ = {
        "OPENAI_API_KEY": "sk-leak-oi",
        "QDRANT_API_KEY": "sk-leak-qd",
        "OLLAMA_API_KEY": "sk-leak-ol",
        "OPENROUTER_API_KEY": "sk-leak-or",
        "SILICONFLOW_API_KEY": "sk-leak-sf",
    }
    r = repr(fake)
    for k in (
        "sk-leak-oi", "sk-leak-qd", "sk-leak-ol",
        "sk-leak-or", "sk-leak-sf",
    ):
        assert k not in r, f"{k} leaked in repr: {r}"


# --- Config attribute defaults ---


def test_config_has_four_provider_attribute_groups():
    """Config must expose the env-var attrs for all providers
    even when the corresponding *_API_KEY is empty."""
    from src.config import config
    assert hasattr(config, "OLLAMA_API_KEY")
    assert hasattr(config, "OPENROUTER_API_KEY")
    assert hasattr(config, "SILICONFLOW_API_KEY")
    assert hasattr(config, "OPENAI_API_KEY")
    # v0.9.19.0: 5th provider + dedicated openai chat-model knob
    assert hasattr(config, "OPENAI_COMPAT_API_KEY")
    assert hasattr(config, "OPENAI_COMPAT_BASE_URL")
    assert hasattr(config, "OPENAI_COMPAT_CHAT_MODEL")
    assert hasattr(config, "OPENAI_CHAT_MODEL")
    assert hasattr(config, "OLLAMA_BASE_URL")
    assert hasattr(config, "OPENROUTER_BASE_URL")
    assert hasattr(config, "SILICONFLOW_BASE_URL")
    assert hasattr(config, "OPENAI_BASE_URL")
    assert hasattr(config, "OLLAMA_CHAT_MODEL")
    assert hasattr(config, "OPENROUTER_CHAT_MODEL")
    assert hasattr(config, "SILICONFLOW_CHAT_MODEL")
    assert hasattr(config, "LLM_MODEL")
    assert hasattr(config, "LLM_PROVIDER")


def test_default_base_urls_well_known_endpoints(fresh_config):
    """Each provider's default base_url must point at the
    well-known production endpoint, not localhost - a user
    who sets just *_API_KEY should reach the real service
    without further config."""
    from src.config import config
    assert config.OLLAMA_BASE_URL == "https://ollama.com/v1"
    assert config.OPENROUTER_BASE_URL == "https://openrouter.ai/api/v1"
    assert config.SILICONFLOW_BASE_URL == "https://api.siliconflow.cn/v1"
    assert config.OPENAI_BASE_URL == "https://api.openai.com/v1"


def test_default_chat_models_are_strong_popular_choices(fresh_config):
    """Each provider's default CHAT_MODEL should be a known-
    good model name (so a user who sets just *_API_KEY gets
    a sensible answer, not a 404 on first turn)."""
    from src.config import config
    for v in (
        config.OLLAMA_CHAT_MODEL,
        config.OPENROUTER_CHAT_MODEL,
        config.SILICONFLOW_CHAT_MODEL,
    ):
        assert v and v.strip() == v


# --- OPENAI_CHAT_MODEL: dedicated model for the openai slot (v0.9.19.0) ---


def test_openai_chat_model_used_when_llm_model_default(fresh_config):
    """OPENAI_CHAT_MODEL is the dedicated model for the openai
    provider — a compat-endpoint config (DeepSeek / LM Studio /
    vLLM / ...) works WITHOUT touching LLM_MODEL (whose global
    override would poison the other providers' failover models)."""
    os.environ["OPENAI_API_KEY"] = "sk-test-oai"
    os.environ["OPENAI_CHAT_MODEL"] = "deepseek-chat"
    name, model, _ = fresh_agent()._pick_provider()
    assert name == "openai"
    assert model == "deepseek-chat"


def test_openai_chat_model_unset_falls_back_to_llm_model(fresh_config):
    """Zero behaviour change when the new knob is unset: the openai
    provider keeps using LLM_MODEL (v0.8.9-v0.9.18 behaviour)."""
    os.environ["OPENAI_API_KEY"] = "sk-test-oai"
    name, model, _ = fresh_agent()._pick_provider()
    assert name == "openai"
    assert model == "gpt-4o-mini"  # LLM_MODEL default


def test_llm_model_still_overrides_openai_chat_model(fresh_config):
    """LLM_MODEL (non-default) keeps its global-override semantics:
    it beats OPENAI_CHAT_MODEL, exactly like the other 3 providers."""
    os.environ["OPENAI_API_KEY"] = "sk-test-oai"
    os.environ["OPENAI_CHAT_MODEL"] = "deepseek-chat"
    os.environ["LLM_MODEL"] = "claude-3-opus"
    _, model, _ = fresh_agent()._pick_provider()
    assert model == "claude-3-opus"


def test_openai_chat_model_does_not_poison_other_providers(fresh_config):
    """The whole point of OPENAI_CHAT_MODEL: a compat-endpoint
    model on the openai slot must NOT leak into ollama's model —
    the cross-provider failover stays intact."""
    os.environ["OPENAI_API_KEY"] = "sk-test-oai"
    os.environ["OPENAI_CHAT_MODEL"] = "deepseek-chat"
    os.environ["OLLAMA_API_KEY"] = "sk-test-ol"
    name, model, _ = fresh_agent()._pick_provider()
    assert name == "ollama"  # auto-detect order unchanged
    assert model == "gpt-oss:120b-cloud"  # OLLAMA_CHAT_MODEL, NOT deepseek


# --- 5th provider: openai-compat (v0.9.19.0) ---


def test_openai_compat_auto_detect_last(fresh_config):
    """openai-compat is appended LAST in _PROVIDER_ORDER: any other
    key wins auto-detect (default order unchanged for existing
    users even with the compat key configured)."""
    os.environ["OPENAI_COMPAT_API_KEY"] = "sk-compat"
    os.environ["OPENAI_COMPAT_BASE_URL"] = "https://api.deepseek.com/v1"
    os.environ["OPENAI_COMPAT_CHAT_MODEL"] = "deepseek-chat"
    os.environ["OLLAMA_API_KEY"] = "sk-test-ol"
    name, model, _ = fresh_agent()._pick_provider()
    assert name == "ollama"
    assert model == "gpt-oss:120b-cloud"


def test_openai_compat_primary_when_only_key(fresh_config):
    """With only the compat key configured, the 5th provider
    becomes the primary with its own dedicated model + base_url."""
    os.environ["OPENAI_COMPAT_API_KEY"] = "sk-compat"
    os.environ["OPENAI_COMPAT_BASE_URL"] = "https://api.deepseek.com/v1"
    os.environ["OPENAI_COMPAT_CHAT_MODEL"] = "deepseek-chat"
    name, model, client = fresh_agent()._pick_provider()
    assert name == "openai-compat"
    assert model == "deepseek-chat"
    assert "api.deepseek.com" in str(client.base_url)


def test_openai_compat_base_url_required(fresh_config):
    """Key without base_url must raise a clear error — a default
    would silently send the compat key to api.openai.com."""
    os.environ["OPENAI_COMPAT_API_KEY"] = "sk-compat"
    a = _new_agent()
    with pytest.raises(ValueError) as exc_info:
        a._pick_provider()
    msg = str(exc_info.value)
    assert "OPENAI_COMPAT_BASE_URL" in msg


def test_llm_provider_openai_compat_override(fresh_config):
    """LLM_PROVIDER=openai-compat forces the 5th provider even
    when other keys are set."""
    os.environ["OPENAI_COMPAT_API_KEY"] = "sk-compat"
    os.environ["OPENAI_COMPAT_BASE_URL"] = "https://api.deepseek.com/v1"
    os.environ["OPENAI_COMPAT_CHAT_MODEL"] = "deepseek-chat"
    os.environ["OLLAMA_API_KEY"] = "sk-test-ol"
    os.environ["LLM_PROVIDER"] = "openai-compat"
    name, model, client = fresh_agent()._pick_provider()
    assert name == "openai-compat"
    assert model == "deepseek-chat"
    assert "api.deepseek.com" in str(client.base_url)


def test_llm_provider_openai_compat_empty_key_raises(fresh_config):
    """The override-with-empty-key error must name the correct
    env var (OPENAI_COMPAT_API_KEY — underscore, not the naive
    upper() of the provider name)."""
    os.environ["LLM_PROVIDER"] = "openai-compat"
    a = _new_agent()
    with pytest.raises(ValueError) as exc_info:
        a._pick_provider()
    msg = str(exc_info.value)
    assert "OPENAI_COMPAT_API_KEY" in msg
    assert "OPENAI-COMPAT_API_KEY" not in msg
