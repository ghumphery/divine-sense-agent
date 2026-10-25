"""v0.9.16.4 — ImageDescriber OpenRouter vision fallback tests.

``describe()`` keeps its ``str`` contract but now runs a provider
CHAIN: Ollama Cloud (primary, unchanged) → OpenRouter
(``OPENROUTER_API_KEY`` + ``OPENROUTER_VISION_MODEL``) → generic
``OPENAI_API_KEY`` + ``OPENAI_BASE_URL``. Covers:

* ``_fallback_vision_config`` resolution (openrouter / openai / none);
* ``enabled`` True with only the fallback key;
* ollama failure → openrouter answers (2 clients, right base_urls);
* ollama unconfigured → straight to openrouter (1 client);
* only OPENAI_API_KEY → openai-compat backend;
* empty ollama response → treated as failure → fallback;
* both fail → combined ``vision error`` marker (uncached);
* no providers → ``no vision provider configured`` marker;
* ollama success → NO fallback call;
* fallback success is cached (second call hits the SHA-1 cache);
* host ``_describe_image`` result carries ``vision_backend``.

All network calls are mocked at ``openai.OpenAI`` (the constructor
resolves it lazily inside ``_call_openai_compat``).
"""
from __future__ import annotations

from collections import deque
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.ingest.image_describer import ImageDescriber

PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000"
    "001f15c4890000000d49444154789c63f8cf00000003000100"
    "b63e0c920000000049454e44ae426082"
)


@pytest.fixture(autouse=True)
def _clean_shared_describer_cache():
    """v0.9.104.5 — the SHA-1 cache is now CLASS-level (shared across
    ImageDescriber instances). Tests must start from an empty shared
    cache or the first describe() poisons every later one."""
    from src.ingest.image_describer import _shared_cache_clear

    _shared_cache_clear()
    yield
    _shared_cache_clear()


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Isolate every vision-provider env var per test."""
    for var in (
        "OLLAMA_API_KEY", "OLLAMA_BASE_URL", "OLLAMA_VISION_MODEL",
        "OPENAI_API_KEY", "OPENAI_BASE_URL",
        "OPENROUTER_API_KEY", "OPENROUTER_BASE_URL",
        "OPENROUTER_VISION_MODEL",
        # v0.9.17.0 siliconflow candidate + v0.9.18.0 chain var were
        # missing from this wipe list (live .env leak defence).
        "SILICONFLOW_API_KEY", "SILICONFLOW_BASE_URL",
        "SILICONFLOW_VISION_MODEL", "VISION_PROVIDER_CHAIN",
    ):
        monkeypatch.delenv(var, raising=False)
    yield


class _FakeOpenAICls:
    """Stand-in for ``openai.OpenAI``: records constructor kwargs and
    serves a queue of outcomes (str content | Exception) per call."""

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
    """Patch ``openai.OpenAI``; returns the constructed-kwargs list.
    The patch is stopped by the autouse ``_stop_openai_patches``
    fixture — without it the fake class leaks into every test module
    that runs after this one (test_llm_providers /
    test_openrouter_routing construct REAL providers)."""
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


def _img(tmp_path: Path) -> Path:
    p = tmp_path / "shot.png"
    p.write_bytes(PNG_BYTES)
    return p


# ─── _fallback_vision_candidates ─────────────────────────────


class TestFallbackConfig:
    def test_no_keys(self, monkeypatch):
        d = ImageDescriber()
        assert d._fallback_vision_candidates() == []
        assert d.enabled is False

    def test_openrouter_candidate_first(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-oai")  # lower priority
        d = ImageDescriber()
        cands = d._fallback_vision_candidates()
        assert [c[3] for c in cands] == ["openrouter", "openai-compat"]
        assert cands[0][0] == "sk-or"
        assert cands[0][1] == "https://openrouter.ai/api/v1"
        assert cands[0][2] == "google/gemini-2.5-flash"
        assert d.enabled is True

    def test_siliconflow_candidate(self, monkeypatch):
        monkeypatch.setenv("SILICONFLOW_API_KEY", "sk-sf")
        d = ImageDescriber()
        cands = d._fallback_vision_candidates()
        assert len(cands) == 1
        key, url, model, backend = cands[0]
        assert key == "sk-sf"
        assert url == "https://api.siliconflow.cn/v1"
        assert model == "Qwen/Qwen2.5-VL-72B-Instruct"
        assert backend == "siliconflow"
        assert d.enabled is True

    def test_full_chain_order(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or")
        monkeypatch.setenv("SILICONFLOW_API_KEY", "sk-sf")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-oai")
        d = ImageDescriber()
        cands = d._fallback_vision_candidates()
        assert [c[3] for c in cands] == [
            "openrouter", "siliconflow", "openai-compat",
        ]

    def test_custom_vision_models(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or")
        monkeypatch.setenv("SILICONFLOW_API_KEY", "sk-sf")
        monkeypatch.setenv("OPENROUTER_VISION_MODEL", "qwen/qwen2.5-vl")
        monkeypatch.setenv("SILICONFLOW_VISION_MODEL", "Pro/Qwen/Qwen2.5-VL")
        d = ImageDescriber()
        cands = d._fallback_vision_candidates()
        assert cands[0][2] == "qwen/qwen2.5-vl"
        assert cands[1][2] == "Pro/Qwen/Qwen2.5-VL"


# ─── describe() fallback chain ───────────────────────────────


class TestDescribeFallbackChain:
    def test_ollama_fails_openrouter_answers(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OLLAMA_API_KEY", "sk-ol")
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or")
        constructed = _patch_openai([
            RuntimeError("Error code: 429"),   # ollama → 429
            "fallback description",            # openrouter → ok
        ])
        d = ImageDescriber()
        out = d.describe(_img(tmp_path))
        assert out == "fallback description"
        assert d.last_backend == "openrouter"
        assert len(constructed) == 2
        assert "ollama.com" in constructed[0]["base_url"]
        assert "openrouter.ai" in constructed[1]["base_url"]
        assert constructed[1]["api_key"] == "sk-or"

    def test_ollama_unconfigured_goes_straight_to_openrouter(
        self, tmp_path, monkeypatch,
    ):
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or")
        constructed = _patch_openai(["direct fallback"])
        d = ImageDescriber()
        out = d.describe(_img(tmp_path))
        assert out == "direct fallback"
        assert d.last_backend == "openrouter"
        assert len(constructed) == 1
        assert "openrouter.ai" in constructed[0]["base_url"]

    def test_openai_only_backend(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-oai")
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        constructed = _patch_openai(["oai desc"])
        d = ImageDescriber()
        out = d.describe(_img(tmp_path))
        assert out == "oai desc"
        assert d.last_backend == "openai-compat"
        assert constructed[0]["base_url"] == "https://api.openai.com/v1"

    def test_empty_ollama_response_is_failure(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OLLAMA_API_KEY", "sk-ol")
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or")
        constructed = _patch_openai(["   ", "fb"])  # empty → fallback
        d = ImageDescriber()
        out = d.describe(_img(tmp_path))
        assert out == "fb"
        assert d.last_backend == "openrouter"
        assert len(constructed) == 2

    def test_both_fail_combined_marker(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OLLAMA_API_KEY", "sk-ol")
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or")
        _patch_openai([
            RuntimeError("429 rate limited"),
            RuntimeError("404 no endpoints"),
        ])
        d = ImageDescriber()
        out = d.describe(_img(tmp_path))
        assert out.startswith("[Image: shot.png | vision error: ")
        assert "ollama(gemma4:31b-cloud): 429" in out
        assert "404 no endpoints" in out
        assert d.last_backend is None
        assert d._cache == {}  # markers are never cached

    def test_no_providers_marker(self, tmp_path):
        _patch_openai([])
        d = ImageDescriber()
        out = d.describe(_img(tmp_path))
        assert "no vision provider configured" in out
        assert "OPENROUTER_API_KEY" in out

    def test_ollama_success_no_fallback(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OLLAMA_API_KEY", "sk-ol")
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or")
        constructed = _patch_openai(["primary desc"])
        d = ImageDescriber()
        out = d.describe(_img(tmp_path))
        assert out == "primary desc"
        assert d.last_backend == "ollama-cloud"
        assert len(constructed) == 1  # fallback NOT called

    def test_fallback_result_is_cached(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or")
        constructed = _patch_openai(["cached fb desc"])
        d = ImageDescriber()
        img = _img(tmp_path)
        assert d.describe(img) == "cached fb desc"
        assert d.describe(img) == "cached fb desc"
        assert len(constructed) == 1  # second call = SHA-1 cache hit


# ─── host describe_image surfaces vision_backend ─────────────


class TestHostVisionBackend:
    def test_result_carries_vision_backend(self, tmp_path, monkeypatch):
        monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(tmp_path))
        monkeypatch.setenv("QDRANT_FILESYSTEM_WRITE_ROOT", str(tmp_path))
        monkeypatch.setenv("QDRANT_FILESYSTEM_READ_ROOT", str(tmp_path))
        from src.mcp_client.host import QdrantLocalHost

        host = QdrantLocalHost()
        host._store = MagicMock()
        host._retriever = MagicMock()
        img = tmp_path / "shot.png"
        img.write_bytes(PNG_BYTES)

        fake = MagicMock()
        fake.describe.return_value = "a tiny png"
        fake.PROMPT = "p"
        fake._cache = {}
        fake.last_backend = "openrouter"
        with patch(
            "src.ingest.image_describer.ImageDescriber",
            return_value=fake,
        ):
            result = host._describe_image({
                "mode": "path", "file_path": str(img),
            })
        assert result.get("ok") is True
        assert result["vision_backend"] == "openrouter"