"""v0.9.18.0 — provider chain ORDER via .env tests.

chat 主鏈(Agent primary + failover)與 vision OCR 鏈(ImageDescriber)
是兩個獨立的應用需求,各自可在 .env 調整鏈則順序:

* ``LLM_PROVIDER_CHAIN``   → ``Agent._resolved_provider_order`` /
  ``_pick_provider`` / ``_provider_candidates``;
* ``VISION_PROVIDER_CHAIN`` → ``ImageDescriber._chain_names`` /
  ``_vision_candidates`` / ``describe()`` (aliases ``ollama-cloud``
  / ``openai-compat`` accepted).

Shared parser ``src.provider_chain.parse_provider_chain`` (also
re-exported from ``src.config``) rules locked here:
empty → default (zero behaviour change); unknown tokens SKIPPED with
a warning; duplicates collapse to the first occurrence; partial
lists keep failover completeness (unlisted providers appended in
default relative order). ``LLM_PROVIDER`` forced override still wins
for chat PRIMARY selection.

All network construction is inert (OpenAI clients are built without
calls); describe() outcomes are mocked at ``openai.OpenAI`` (same
pattern as tests/test_image_describer_fallback.py).
"""
from __future__ import annotations

import logging
import sys
from collections import deque
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config import parse_provider_chain  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_shared_describer_cache():
    """v0.9.104.5 — ImageDescriber's SHA-1 cache is CLASS-level now;
    every test must start from an empty shared cache."""
    from src.ingest.image_describer import _shared_cache_clear

    _shared_cache_clear()
    yield
    _shared_cache_clear()


def _cfg():
    """The CURRENT ``src.config.config`` object (reload-safe).

    Earlier test files (test_llm_providers) del + re-import
    src.config, so a module-top ``from src.config import config``
    binding goes STALE. The Agent's ``_get_config()`` resolves via
    ``sys.modules`` every call — resolve the same way so
    ``monkeypatch.setattr`` lands on the object the agent reads.
    """
    import src.config as _mod  # resolves via sys.modules

    return _mod.config

VALID = ("ollama", "openrouter", "siliconflow", "openai")

_BASES = {
    "ollama": "https://ollama.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "siliconflow": "https://api.siliconflow.cn/v1",
    "openai": "https://api.openai.com/v1",
}
_CHAT_MODELS = {
    "ollama": "gpt-oss:120b-cloud",
    "openrouter": "anthropic/claude-3.5-sonnet",
    "siliconflow": "Qwen/Qwen2.5-72B-Instruct",
}
_CHAIN_ENV = ("LLM_PROVIDER_CHAIN", "VISION_PROVIDER_CHAIN")
_ROUTING_ENV = (
    "OPENROUTER_MODEL_SUFFIX", "OPENROUTER_REASONING_EFFORT",
    "OPENROUTER_PROVIDER_ORDER", "OPENROUTER_ALLOW_FALLBACKS",
    "OPENROUTER_REQUIRE_PARAMETERS", "OPENROUTER_DATA_COLLECTION",
    "OPENROUTER_IGNORE", "OPENROUTER_QUANTIZATIONS", "OPENROUTER_SORT",
)


# ─── shared parser ────────────────────────────────────────────


class TestParseProviderChain:
    def test_empty_returns_default(self):
        assert parse_provider_chain("", VALID) == VALID
        assert parse_provider_chain("   ", VALID) == VALID

    def test_custom_order(self):
        out = parse_provider_chain(
            "openai,siliconflow,openrouter,ollama", VALID,
        )
        assert out == ("openai", "siliconflow", "openrouter", "ollama")

    def test_case_and_whitespace_tolerant(self):
        out = parse_provider_chain(
            "  OpenRouter ,  OLLAMA ,SiliconFlow , OpenAI ", VALID,
        )
        assert out == ("openrouter", "ollama", "siliconflow", "openai")

    def test_semicolon_separator(self):
        out = parse_provider_chain("openai;ollama", VALID)
        assert out == ("openai", "ollama", "openrouter", "siliconflow")

    def test_unknown_tokens_skipped_with_warning(self, caplog):
        with caplog.at_level(logging.WARNING, logger="src.provider_chain"):
            out = parse_provider_chain("bogus,ollama,xyz", VALID)
        assert out == ("ollama", "openrouter", "siliconflow", "openai")
        assert any("bogus" in r.message for r in caplog.records)

    def test_duplicates_collapse(self):
        out = parse_provider_chain("openai,openai,ollama", VALID)
        assert out == ("openai", "ollama", "openrouter", "siliconflow")

    def test_partial_list_appends_rest_in_default_order(self):
        out = parse_provider_chain("openai", VALID)
        assert out == ("openai", "ollama", "openrouter", "siliconflow")
        out = parse_provider_chain("siliconflow,openrouter", VALID)
        assert out == ("siliconflow", "openrouter", "ollama", "openai")

    def test_all_invalid_returns_default_with_warning(self, caplog):
        with caplog.at_level(logging.WARNING, logger="src.provider_chain"):
            out = parse_provider_chain("foo,bar", VALID)
        assert out == VALID
        assert any(
            "no valid provider token" in r.message for r in caplog.records
        )

    def test_aliases(self):
        out = parse_provider_chain(
            "ollama-cloud,openai-compat", VALID,
            aliases={"ollama-cloud": "ollama", "openai-compat": "openai"},
        )
        assert out == ("ollama", "openai", "openrouter", "siliconflow")


# ─── chat chain (Agent) ───────────────────────────────────────


@pytest.fixture()
def chat_env(monkeypatch, tmp_path):
    """Hermetic Agent provider config: no keys, neutral
    LLM_PROVIDER / LLM_MODEL, no chain / routing env leakage (the
    user's real .env is loaded by src.config at import time)."""
    monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path))
    for var in _CHAIN_ENV + _ROUTING_ENV + (
        # v0.9.19.0: OpenAI-compatible escape-hatch knobs (live .env
        # leak defence — the openai model_fn falls back to them).
        "OPENAI_CHAT_MODEL",
        "OPENAI_COMPAT_API_KEY", "OPENAI_COMPAT_BASE_URL",
        "OPENAI_COMPAT_CHAT_MODEL",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(_cfg(), "LLM_PROVIDER", "", raising=False)
    monkeypatch.setattr(_cfg(), "LLM_MODEL", "gpt-4o-mini", raising=False)
    # v0.9.19.0: pin the OpenAI-compatible escape-hatch knobs (the
    # config attrs are frozen from the live .env at import time).
    for _attr in (
        "OPENAI_CHAT_MODEL",
        "OPENAI_COMPAT_API_KEY", "OPENAI_COMPAT_BASE_URL",
        "OPENAI_COMPAT_CHAT_MODEL",
    ):
        monkeypatch.setattr(_cfg(), _attr, "", raising=False)
    for name in VALID:
        monkeypatch.setattr(
            _cfg(), f"{name.upper()}_API_KEY", "", raising=False,
        )
        monkeypatch.setattr(
            _cfg(), f"{name.upper()}_BASE_URL", _BASES[name], raising=False,
        )
    for name, model in _CHAT_MODELS.items():
        monkeypatch.setattr(
            _cfg(), f"{name.upper()}_CHAT_MODEL", model, raising=False,
        )
    from src.agent.agent import Agent

    return Agent


class TestChatChainOrder:
    def test_default_order_when_unset(self, chat_env):
        a = chat_env()
        names = tuple(e[0] for e in a._resolved_provider_order())
        assert names == (
            "ollama", "openrouter", "siliconflow", "openai",
            "openai-compat",
        )
        # and identical to the class tuple, entry for entry
        assert a._resolved_provider_order() == a._PROVIDER_ORDER

    def test_chain_reorders_resolved_order(self, chat_env, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER_CHAIN", "openai,ollama")
        a = chat_env()
        names = tuple(e[0] for e in a._resolved_provider_order())
        assert names == (
            "openai", "ollama", "openrouter", "siliconflow",
            "openai-compat",
        )

    def test_pick_provider_follows_chain(self, chat_env, monkeypatch):
        monkeypatch.setattr(_cfg(), "OLLAMA_API_KEY", "sk-ol", raising=False)
        monkeypatch.setattr(
            _cfg(), "OPENROUTER_API_KEY", "sk-or", raising=False,
        )
        monkeypatch.setenv("LLM_PROVIDER_CHAIN", "openrouter, ollama")
        a = chat_env()
        name, model, client = a._pick_provider()
        assert name == "openrouter"
        assert model == "anthropic/claude-3.5-sonnet"
        assert "openrouter.ai" in str(client.base_url)

    def test_chain_skips_keyless_leading_providers(
        self, chat_env, monkeypatch,
    ):
        monkeypatch.setattr(_cfg(), "OPENAI_API_KEY", "sk-oa", raising=False)
        monkeypatch.setenv("LLM_PROVIDER_CHAIN", "openrouter,openai")
        a = chat_env()
        name, _, _ = a._pick_provider()
        assert name == "openai"  # openrouter has no key → skipped

    def test_candidates_follow_chain_after_primary(
        self, chat_env, monkeypatch,
    ):
        for n, k in (
            ("ollama", "sk-ol"), ("siliconflow", "sk-sf"),
            ("openai", "sk-oa"),
        ):
            monkeypatch.setattr(
                _cfg(), f"{n.upper()}_API_KEY", k, raising=False,
            )
        monkeypatch.setenv(
            "LLM_PROVIDER_CHAIN", "siliconflow,openai,ollama",
        )
        a = chat_env()
        _ = a.client  # resolve primary
        cands = a._provider_candidates()
        assert [c[0] for c in cands] == [
            "siliconflow", "openai", "ollama",
        ]

    def test_llm_provider_override_still_wins(self, chat_env, monkeypatch):
        monkeypatch.setattr(_cfg(), "OLLAMA_API_KEY", "sk-ol", raising=False)
        monkeypatch.setattr(
            _cfg(), "OPENROUTER_API_KEY", "sk-or", raising=False,
        )
        monkeypatch.setenv("LLM_PROVIDER_CHAIN", "openrouter,ollama")
        monkeypatch.setattr(_cfg(), "LLM_PROVIDER", "ollama", raising=False)
        a = chat_env()
        name, _, _ = a._pick_provider()
        assert name == "ollama"  # chain reordered, forced wins anyway

    def test_invalid_chain_token_warns_but_chat_works(
        self, chat_env, monkeypatch, caplog,
    ):
        monkeypatch.setattr(_cfg(), "OLLAMA_API_KEY", "sk-ol", raising=False)
        monkeypatch.setenv("LLM_PROVIDER_CHAIN", "bogus,ollama")
        a = chat_env()
        with caplog.at_level(logging.WARNING, logger="src.provider_chain"):
            name, _, _ = a._pick_provider()
        assert name == "ollama"
        assert any("bogus" in r.message for r in caplog.records)

    def test_failover_walk_uses_chain_order(
        self, chat_env, monkeypatch,
    ):
        """Chain-first provider without a key is skipped (openrouter
        has no key → siliconflow becomes the primary); the failover
        walk follows the CHAIN order (openai before ollama) rather
        than the default class order."""
        monkeypatch.setattr(_cfg(), "OLLAMA_API_KEY", "sk-ol", raising=False)
        monkeypatch.setattr(
            _cfg(), "SILICONFLOW_API_KEY", "sk-sf", raising=False,
        )
        monkeypatch.setattr(_cfg(), "OPENAI_API_KEY", "sk-oa", raising=False)
        monkeypatch.setenv(
            "LLM_PROVIDER_CHAIN", "openrouter,siliconflow,openai,ollama",
        )
        a = chat_env()
        _ = a.client
        assert a._provider == "siliconflow"
        names = [c[0] for c in a._failover_candidates()]
        assert names == ["openai", "ollama"]

    def test_chain_includes_openai_compat(self, chat_env, monkeypatch):
        """v0.9.19.0: the 5th provider joins the chain. With only
        the openai-compat key set and the chain putting it first,
        it becomes the primary and the (all-keyless) failover walk
        is empty."""
        monkeypatch.setattr(
            _cfg(), "OPENAI_COMPAT_API_KEY", "sk-compat", raising=False,
        )
        monkeypatch.setattr(
            _cfg(), "OPENAI_COMPAT_BASE_URL",
            "https://api.deepseek.com/v1", raising=False,
        )
        monkeypatch.setattr(
            _cfg(), "OPENAI_COMPAT_CHAT_MODEL", "deepseek-chat",
            raising=False,
        )
        monkeypatch.setenv(
            "LLM_PROVIDER_CHAIN", "openai-compat,openrouter,ollama",
        )
        a = chat_env()
        name, model, client = a._pick_provider()
        assert name == "openai-compat"
        assert model == "deepseek-chat"
        assert "api.deepseek.com" in str(client.base_url)
        # every other provider is keyless → empty failover walk
        assert a._failover_candidates() == []


# ─── vision chain (ImageDescriber) ────────────────────────────

PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000"
    "001f15c4890000000d49444154789c63f8cf00000003000100"
    "b63e0c920000000049454e44ae426082"
)


class _FakeOpenAICls:
    constructed: list = []
    outcomes: deque = deque()

    def __init__(self, api_key=None, base_url=None, **kw):
        type(self).constructed.append(
            {"api_key": api_key, "base_url": base_url}
        )
        self.chat = MagicMock()
        outcome = type(self).outcomes.popleft()
        if isinstance(outcome, Exception):
            self.chat.completions.create.side_effect = outcome
        elif isinstance(outcome, str):
            resp = MagicMock()
            resp.choices = [MagicMock(message=MagicMock(content=outcome))]
            self.chat.completions.create.return_value = resp
        else:
            self.chat.completions.create.return_value = outcome


_ACTIVE_PATCHERS: list = []


def _patch_openai(outcomes: list) -> list:
    _FakeOpenAICls.constructed = []
    _FakeOpenAICls.outcomes = deque(outcomes)
    patcher = patch("openai.OpenAI", _FakeOpenAICls)
    patcher.start()
    _ACTIVE_PATCHERS.append(patcher)
    return _FakeOpenAICls.constructed


@pytest.fixture(autouse=True)
def _stop_openai_patches():
    yield
    while _ACTIVE_PATCHERS:
        _ACTIVE_PATCHERS.pop().stop()


@pytest.fixture(autouse=True)
def _clean_vision_env(monkeypatch):
    """Isolate every vision-provider env var + the chain var."""
    for var in (
        "OLLAMA_API_KEY", "OLLAMA_BASE_URL", "OLLAMA_VISION_MODEL",
        "OPENAI_API_KEY", "OPENAI_BASE_URL",
        "OPENROUTER_API_KEY", "OPENROUTER_BASE_URL",
        "OPENROUTER_VISION_MODEL",
        "SILICONFLOW_API_KEY", "SILICONFLOW_BASE_URL",
        "SILICONFLOW_VISION_MODEL",
        "VISION_PROVIDER_CHAIN",
    ):
        monkeypatch.delenv(var, raising=False)
    yield


def _img(tmp_path: Path) -> Path:
    p = tmp_path / "shot.png"
    p.write_bytes(PNG_BYTES)
    return p


def _set_all_keys(monkeypatch):
    for var in (
        "OLLAMA_API_KEY", "OPENROUTER_API_KEY",
        "SILICONFLOW_API_KEY", "OPENAI_API_KEY",
    ):
        monkeypatch.setenv(var, "sk-x")


class TestVisionChainOrder:
    def test_default_chain_full_keys(self, monkeypatch):
        _set_all_keys(monkeypatch)
        from src.ingest.image_describer import ImageDescriber

        d = ImageDescriber()
        assert d._chain_names() == (
            "ollama", "openrouter", "siliconflow", "openai",
        )
        assert [c[3] for c in d._vision_candidates()] == [
            "ollama-cloud", "openrouter", "siliconflow", "openai-compat",
        ]

    def test_chain_reorders_candidates(self, monkeypatch):
        _set_all_keys(monkeypatch)
        monkeypatch.setenv(
            "VISION_PROVIDER_CHAIN", "openai,openrouter,siliconflow,ollama",
        )
        from src.ingest.image_describer import ImageDescriber

        d = ImageDescriber()
        assert [c[3] for c in d._vision_candidates()] == [
            "openai-compat", "openrouter", "siliconflow", "ollama-cloud",
        ]

    def test_alias_tokens_accepted(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_API_KEY", "sk-ol")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-oa")
        monkeypatch.setenv(
            "VISION_PROVIDER_CHAIN", "openai-compat, ollama-cloud",
        )
        from src.ingest.image_describer import ImageDescriber

        d = ImageDescriber()
        assert [c[3] for c in d._vision_candidates()] == [
            "openai-compat", "ollama-cloud",
        ]

    def test_partial_chain_appends_rest(self, monkeypatch):
        _set_all_keys(monkeypatch)
        monkeypatch.setenv("VISION_PROVIDER_CHAIN", "openrouter")
        from src.ingest.image_describer import ImageDescriber

        d = ImageDescriber()
        assert [c[3] for c in d._vision_candidates()] == [
            "openrouter", "ollama-cloud", "siliconflow", "openai-compat",
        ]

    def test_fallback_view_excludes_ollama(self, monkeypatch):
        """Backward compat: _fallback_vision_candidates keeps its
        pre-0.9.18.0 shape when the chain env is unset."""
        _set_all_keys(monkeypatch)
        from src.ingest.image_describer import ImageDescriber

        d = ImageDescriber()
        assert [c[3] for c in d._fallback_vision_candidates()] == [
            "openrouter", "siliconflow", "openai-compat",
        ]

    def test_describe_walks_chain_openai_first(
        self, tmp_path, monkeypatch,
    ):
        monkeypatch.setenv("OLLAMA_API_KEY", "sk-ol")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-oa")
        monkeypatch.setenv("VISION_PROVIDER_CHAIN", "openai,ollama")
        constructed = _patch_openai(["oai desc"])
        from src.ingest.image_describer import ImageDescriber

        d = ImageDescriber()
        out = d.describe(_img(tmp_path))
        assert out == "oai desc"
        assert d.last_backend == "openai-compat"
        assert constructed[0]["base_url"] == "https://api.openai.com/v1"
        assert constructed[0]["api_key"] == "sk-oa"
        assert len(constructed) == 1  # ollama never tried

    def test_describe_ollama_off_first_slot_still_tried(
        self, tmp_path, monkeypatch,
    ):
        monkeypatch.setenv("OLLAMA_API_KEY", "sk-ol")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-oa")
        monkeypatch.setenv("VISION_PROVIDER_CHAIN", "openai,ollama")
        constructed = _patch_openai([
            RuntimeError("oai down"),
            "ol desc",
        ])
        from src.ingest.image_describer import ImageDescriber

        d = ImageDescriber()
        out = d.describe(_img(tmp_path))
        assert out == "ol desc"
        assert d.last_backend == "ollama-cloud"
        assert "ollama.com" in constructed[1]["base_url"]
        assert len(constructed) == 2

    def test_invalid_chain_token_warns_but_ocr_works(
        self, tmp_path, monkeypatch, caplog,
    ):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-oa")
        monkeypatch.setenv("VISION_PROVIDER_CHAIN", "bogus,openai")
        _patch_openai(["ok desc"])
        from src.ingest.image_describer import ImageDescriber

        d = ImageDescriber()
        with caplog.at_level(logging.WARNING, logger="src.provider_chain"):
            out = d.describe(_img(tmp_path))
        assert out == "ok desc"
        assert d.last_backend == "openai-compat"
        assert any("bogus" in r.message for r in caplog.records)

    def test_ollama_error_label_shape_preserved(
        self, tmp_path, monkeypatch,
    ):
        """Pre-0.9.18.0 marker shape locked by the old suite — the
        ollama entry reports as ollama(<model>) even though the
        backend name is ollama-cloud."""
        monkeypatch.setenv("OLLAMA_API_KEY", "sk-ol")
        _patch_openai([RuntimeError("429 rate limited")])
        from src.ingest.image_describer import ImageDescriber

        d = ImageDescriber()
        out = d.describe(_img(tmp_path))
        assert out.startswith("[Image: shot.png | vision error: ")
        assert "ollama(gemma4:31b-cloud): 429" in out
        assert d.last_backend is None

    def test_enabled_semantics_unchanged(self, monkeypatch):
        from src.ingest.image_describer import ImageDescriber

        assert ImageDescriber().enabled is False  # no keys at all
        monkeypatch.setenv("OPENAI_API_KEY", "sk-oa")
        assert ImageDescriber().enabled is True  # fallback-only enables
        monkeypatch.delenv("OPENAI_API_KEY")
        monkeypatch.setenv("OLLAMA_API_KEY", "sk-ol")
        assert ImageDescriber().enabled is True

    def test_no_providers_marker_unchanged(self, tmp_path):
        _patch_openai([])
        from src.ingest.image_describer import ImageDescriber

        d = ImageDescriber()
        out = d.describe(_img(tmp_path))
        assert "no vision provider configured" in out
        assert "OPENROUTER_API_KEY" in out
