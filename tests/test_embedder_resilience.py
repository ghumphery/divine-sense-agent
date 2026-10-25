"""v0.9.48.0 — Embedder failure cooldown + actual-backend cache keying.

Live log 2026-09-14 (schedule fire 積極型股票追蹤): llama.cpp was in
the Vulkan ``ErrorDeviceLost`` state — process alive, /v1/models 200,
every /v1/embeddings POST 500 — AND the ollama fallback paid a doomed
https://ollama.com/v1/embeddings 404 per call. Two failed HTTP
round-trips + two log lines on EVERY stock snapshot save. Locks down:

* failure cooldown (circuit breaker) — a hard backend failure skips
  that backend for EMBEDDER_BACKEND_COOLDOWN seconds (0 = legacy
  always-retry); success clears it immediately.
* the ollama leg retries the NATIVE /api/embed endpoint on 404.
* degraded (hash) vectors are disk-cached under backend='hash' — never
  under the healthy backend's key (cache poisoning fix).
"""
from __future__ import annotations

import sqlite3
import time

import pytest


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    # Pre-import src.config BEFORE the delenv loop — its import runs
    # load_dotenv (override=False) which would re-inject the live
    # .env's provider keys AFTER the wipe (the documented trap from
    # test_mcp_active_servers).
    import src.config  # noqa: F401
    monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path))
    monkeypatch.delenv("EMBEDDER_BACKEND_COOLDOWN", raising=False)
    monkeypatch.delenv("EMBEDDING_BACKEND", raising=False)
    # Provider keys — the live .env leaks into the process env at
    # src.config import; the new openrouter leg (v0.9.49.0) activates
    # whenever OPENROUTER_API_KEY is present, so wipe it (tests that
    # want the leg set it explicitly).
    for _k in ("OPENROUTER_API_KEY", "OPENROUTER_EMBED_MODEL",
               "OPENROUTER_EMBED_PROVIDER_ORDER",
               "OPENROUTER_EMBED_ALLOW_FALLBACKS",
               "OLLAMA_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(_k, raising=False)
    yield


class _FakeResp:
    def __init__(self, status, payload=None):
        self.status_code = status
        self._payload = payload or {}

    def raise_for_status(self):
        import requests

        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(
                f"{self.status_code} error"
            )

    def json(self):
        return self._payload


def _llamacpp_vec():
    return {"data": [{"embedding": [0.1] * 4096}]}


def _ollama_vec():
    return {"embeddings": [[0.2] * 768]}


class TestCooldown:
    def test_failed_backend_skipped_on_next_call(self, monkeypatch):
        posts: list[str] = []
        gets: list[str] = []

        def fake_post(url, **kw):
            posts.append(url)
            return _FakeResp(500)  # every backend hard-fails

        def fake_get(url, **kw):
            gets.append(url)
            return _FakeResp(200)  # probes green (the live trap)

        monkeypatch.setattr(
            "src.ingest.embedder.requests.post", fake_post,
        )
        monkeypatch.setattr(
            "src.ingest.embedder.requests.get", fake_get,
        )
        from src.ingest.embedder import Embedder

        e = Embedder()
        # v0.9.58.2: auto = llama.cpp FIRST (probe green here) → the
        # POST 500 puts local on cooldown → chain falls through to
        # hash (no provider keys). The SECOND embed pays ZERO extra
        # HTTP (cooldown + probe cache respected).
        e.embed("第一次")
        n_posts_after_first = len(posts)
        assert n_posts_after_first >= 1  # llama.cpp POST attempted
        e.embed("第二次")
        assert len(posts) == n_posts_after_first
        assert len(gets) == 1  # one llama.cpp probe, cached after

    def test_cooldown_marks_probe_unavailable(self, monkeypatch):
        monkeypatch.setattr(
            "src.ingest.embedder.requests.post",
            lambda url, **kw: _FakeResp(500),
        )
        monkeypatch.setattr(
            "src.ingest.embedder.requests.get",
            lambda url, **kw: _FakeResp(200),
        )
        from src.ingest.embedder import Embedder

        e = Embedder()
        e.backend = "local"  # v0.9.51.0: local is explicit-only now
        e.embed("x")
        assert e._in_cooldown("local")
        # ollama is out of the auto chain — never attempted, so it is
        # NOT put on cooldown by an auto embed.
        assert not e._in_cooldown("ollama")
        assert e._llamacpp_available() is False

    def test_cooldown_expiry_retries(self, monkeypatch):
        posts: list[str] = []

        def fake_post(url, **kw):
            posts.append(url)
            return _FakeResp(500)

        monkeypatch.setattr(
            "src.ingest.embedder.requests.post", fake_post,
        )
        monkeypatch.setattr(
            "src.ingest.embedder.requests.get",
            lambda url, **kw: _FakeResp(200),
        )
        monkeypatch.setenv("EMBEDDER_BACKEND_COOLDOWN", "0.2")
        from src.ingest.embedder import Embedder

        e = Embedder()
        e.backend = "local"  # v0.9.51.0: explicit selection
        e.embed("x")
        assert len(posts) == 1  # local only
        time.sleep(0.25)
        e._llamacpp_ok = None  # probe cache expiry too
        e.embed("y")
        assert len(posts) == 2  # local retried after expiry

    def test_mark_ok_clears_cooldown(self):
        from src.ingest.embedder import Embedder

        e = Embedder()
        e._backend_cooldown = 300
        e._mark_failed("local", RuntimeError("boom"))
        assert e._in_cooldown("local")
        e._mark_ok("local")
        assert not e._in_cooldown("local")

    def test_resolver_values(self, monkeypatch):
        from src.ingest.embedder import _resolve_backend_cooldown

        monkeypatch.delenv("EMBEDDER_BACKEND_COOLDOWN", raising=False)
        assert _resolve_backend_cooldown() == 300.0
        monkeypatch.setenv("EMBEDDER_BACKEND_COOLDOWN", "60")
        assert _resolve_backend_cooldown() == 60.0
        monkeypatch.setenv("EMBEDDER_BACKEND_COOLDOWN", "abc")
        assert _resolve_backend_cooldown() == 300.0
        monkeypatch.setenv("EMBEDDER_BACKEND_COOLDOWN", "-5")
        assert _resolve_backend_cooldown() == 300.0
        monkeypatch.setenv("EMBEDDER_BACKEND_COOLDOWN", "0")
        assert _resolve_backend_cooldown() == 0.0  # 0 = legacy retry


class TestLocalClipGuard:
    """v0.9.66.1 — the local llama.cpp ctx guard. Live log
    2026-09-16: a scheduler's 1240-char CJK answer exceeded the
    llama.cpp embedding slot ctx (n_ctx=1024) → HTTP 400
    exceed_context_size_error → backend cooldown → 300 s of
    everything falling to OpenRouter. The clip guard keeps over-long
    inputs under the cap so local NEVER 400s."""

    def test_resolver_values(self, monkeypatch):
        from src.ingest.embedder import (
            _DEFAULT_LOCAL_MAX_CHARS,
            _resolve_local_max_chars,
        )

        monkeypatch.delenv(
            "QDRANT_EMBED_LOCAL_MAX_CHARS", raising=False)
        assert _resolve_local_max_chars() == _DEFAULT_LOCAL_MAX_CHARS
        assert _DEFAULT_LOCAL_MAX_CHARS == 950
        monkeypatch.setenv("QDRANT_EMBED_LOCAL_MAX_CHARS", "500")
        assert _resolve_local_max_chars() == 500
        monkeypatch.setenv("QDRANT_EMBED_LOCAL_MAX_CHARS", "abc")
        assert _resolve_local_max_chars() == 950
        monkeypatch.setenv("QDRANT_EMBED_LOCAL_MAX_CHARS", "-3")
        assert _resolve_local_max_chars() == 950
        monkeypatch.setenv("QDRANT_EMBED_LOCAL_MAX_CHARS", "0")
        assert _resolve_local_max_chars() == 0  # 0 = clipping off

    def test_clip_local_passthrough_and_clip(self, monkeypatch):
        from src.ingest.embedder import Embedder

        monkeypatch.delenv(
            "QDRANT_EMBED_LOCAL_MAX_CHARS", raising=False)
        e = Embedder()
        short = "短文本"
        assert e._clip_local(short) == short
        long_text = "長" * 2000
        clipped = e._clip_local(long_text)
        assert clipped == "長" * 950
        assert len(clipped) == 950
        # 0 disables clipping
        monkeypatch.setenv("QDRANT_EMBED_LOCAL_MAX_CHARS", "0")
        e2 = Embedder()
        assert e2._clip_local(long_text) == long_text

    def test_local_post_payload_is_clipped(self, monkeypatch):
        """The bytes actually POSTed to llama.cpp carry the clipped
        text; a long input can no longer 400 the local backend."""
        payloads: list[dict] = []

        def fake_post(url, json=None, timeout=None, **kw):
            payloads.append(json)
            return _FakeResp(200, {
                "data": [{"embedding": [0.5] * 4096}]})

        monkeypatch.setattr(
            "src.ingest.embedder.requests.post", fake_post,
        )
        from src.ingest.embedder import Embedder

        monkeypatch.setenv("EMBEDDING_BACKEND", "local")
        e2 = Embedder()
        long_text = "x" * 5000
        vec = e2.embed(long_text)
        assert len(vec) == e2.dimension
        assert len(payloads) == 1
        sent = payloads[0]["input"]
        assert len(sent) == 950  # clipped, not 5000
        # cache keys still use the FULL text
        key = e2._cache_key(long_text)
        assert key in e2._cache

    def test_local_batch_payload_clipped(self, monkeypatch):
        payloads: list[dict] = []

        def fake_post(url, json=None, timeout=None, **kw):
            payloads.append(json)
            n = len(json["input"])
            return _FakeResp(200, {
                "data": [{"embedding": [0.5] * 4096} for _ in range(n)]})

        monkeypatch.setattr(
            "src.ingest.embedder.requests.post", fake_post,
        )
        from src.ingest.embedder import Embedder

        monkeypatch.setenv("EMBEDDING_BACKEND", "local")
        e = Embedder()
        texts = ["短" * 10, "長" * 3000, "ok"]
        vecs = e.embed_batch(texts)
        assert len(vecs) == 3
        sent = payloads[0]["input"]
        assert len(sent) == 3
        assert sent[0] == "短" * 10       # untouched
        assert len(sent[1]) == 950        # clipped
        assert sent[2] == "ok"            # untouched


class TestAutoChainSkipsOllama:
    def test_ollama_never_probed_or_posted_in_auto(self, monkeypatch):
        """v0.9.58.2 — llama.cpp is BACK as the FIRST auto backend;
        ollama stays OUT of the chain. With no provider keys an auto
        embed probes llama.cpp, its failing POST (500) puts it on
        cooldown, and the chain lands on hash with ZERO ollama
        traffic (the v0.9.50.0 semantics unchanged)."""
        posts: list[str] = []
        gets: list[str] = []

        def fake_post(url, **kw):
            posts.append(url)
            return _FakeResp(500)

        def fake_get(url, **kw):
            gets.append(url)
            return _FakeResp(200)

        monkeypatch.setattr(
            "src.ingest.embedder.requests.post", fake_post,
        )
        monkeypatch.setattr(
            "src.ingest.embedder.requests.get", fake_get,
        )
        from src.ingest.embedder import Embedder

        e = Embedder()
        vec = e.embed("鏈則測試")
        assert not any("ollama" in u for u in posts)
        assert not any("ollama" in u for u in gets)
        # llama.cpp is tried FIRST now — one probe + one POST, then
        # the cooldown locks it out.
        llama_posts = [u for u in posts if "llama" in u or "8080" in u]
        assert len(llama_posts) == 1
        assert len(gets) == 1
        assert e._last_backend == "hash"  # llama failed, no keys
        assert len(vec) == e.dimension

    def test_auto_chain_prefers_llamacpp_first(self, monkeypatch):
        """v0.9.58.2 — llama.cpp is the FIRST auto backend. Even with
        a green OpenRouter probe, the auto chain must never touch
        openrouter while llama.cpp is up."""
        posts: list[str] = []
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")

        def fake_post(url, **kw):
            posts.append(url)
            if "llama" in url or "8080" in url:
                return _FakeResp(200, _llamacpp_vec())
            return _FakeResp(200, {"data": [
                {"embedding": [0.4] * 4096},
            ]})

        monkeypatch.setattr(
            "src.ingest.embedder.requests.post", fake_post,
        )
        monkeypatch.setattr(
            "src.ingest.embedder.requests.get",
            lambda url, **kw: _FakeResp(200),
        )
        from src.ingest.embedder import Embedder

        e = Embedder()
        vec = e.embed("第一順位測試")
        assert e._select_backend() == "local"
        assert e._last_backend == "local"
        assert len(vec) == e.dimension
        # The OpenRouter embeddings endpoint is NEVER POSTed.
        assert not any("openrouter" in u for u in posts)

    def test_local_down_falls_through_to_openrouter(
        self, monkeypatch,
    ):
        """A dead llama.cpp probe (v0.9.48 live trap) skips local and
        serves openrouter — the automatic swap the cooldown was
        built for."""
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")

        def fake_post(url, **kw):
            assert "openrouter" in url
            return _FakeResp(200, {"data": [
                {"embedding": [0.4] * 4096},
            ]})

        def fake_get(url, **kw):
            # llama.cpp probe fails (connection refused) — the
            # openrouter probe must still be reachable.
            if "8080" in url:
                raise ConnectionError("refused")
            return _FakeResp(200)

        monkeypatch.setattr(
            "src.ingest.embedder.requests.post", fake_post,
        )
        monkeypatch.setattr(
            "src.ingest.embedder.requests.get", fake_get,
        )
        from src.ingest.embedder import Embedder

        e = Embedder()
        vec = e.embed("退而求其次")
        assert e._select_backend() == "openrouter"
        assert e._last_backend == "openrouter"
        assert len(vec) == e.dimension

    def test_explicit_ollama_still_reachable(self, monkeypatch):
        """EMBEDDING_BACKEND=ollama keeps working (explicit-only)."""
        monkeypatch.setenv("EMBEDDING_BACKEND", "ollama")

        def fake_post(url, **kw):
            if url.endswith("/v1/embeddings"):
                return _FakeResp(404)
            return _FakeResp(200, _ollama_vec())

        monkeypatch.setattr(
            "src.ingest.embedder.requests.post", fake_post,
        )
        from src.ingest.embedder import Embedder

        e = Embedder()
        vec = e.embed("explicit ollama")
        assert e._last_backend == "ollama"
        assert len(vec) == e.dimension


class TestOllamaNativeEndpoint:
    def test_404_retries_native_api_embed(self, monkeypatch):
        posts: list[str] = []

        def fake_post(url, **kw):
            posts.append(url)
            if url.endswith("/v1/embeddings"):
                return _FakeResp(404)
            assert url.endswith("/api/embed")
            return _FakeResp(200, _ollama_vec())

        monkeypatch.setattr(
            "src.ingest.embedder.requests.post", fake_post,
        )
        monkeypatch.setattr(
            "src.ingest.embedder.requests.get",
            lambda url, **kw: _FakeResp(200),
        )
        from src.ingest.embedder import Embedder

        e = Embedder()
        # v0.9.50.0: ollama is explicit-only — select it directly.
        e.backend = "ollama"
        e._llamacpp_ok = False
        vec = e.embed("native test")
        assert posts == [
            "https://ollama.com/v1/embeddings",
            "https://ollama.com/api/embed",
        ]
        assert e._last_backend == "ollama"
        assert len(vec) == e.dimension

    def test_non_404_error_not_retried(self, monkeypatch):
        posts: list[str] = []

        def fake_post(url, **kw):
            posts.append(url)
            return _FakeResp(500)

        monkeypatch.setattr(
            "src.ingest.embedder.requests.post", fake_post,
        )
        monkeypatch.setattr(
            "src.ingest.embedder.requests.get",
            lambda url, **kw: _FakeResp(200),
        )
        from src.ingest.embedder import Embedder

        e = Embedder()
        e.backend = "ollama"  # v0.9.50.0: explicit selection
        e._llamacpp_ok = False
        try:
            e.embed("boom")
        except Exception:
            pass  # the per-item path swallows into hash
        # 500 on the OpenAI shape must NOT hit the native endpoint.
        assert posts == ["https://ollama.com/v1/embeddings"]


class TestActualBackendCacheKeying:
    def test_hash_vector_keyed_as_hash_not_local(self, tmp_path,
                                                 monkeypatch):
        from src.ingest.disk_cache import DiskEmbeddingCache
        from src.ingest.embedder import Embedder

        def fake_post(url, **kw):
            return _FakeResp(500)  # every remote backend fails

        monkeypatch.setattr(
            "src.ingest.embedder.requests.post", fake_post,
        )
        monkeypatch.setattr(
            "src.ingest.embedder.requests.get",
            lambda url, **kw: _FakeResp(200),
        )
        e = Embedder()
        used_model = e._model_for("hash")
        text = "污染測試文字"
        e.embed(text)
        # The disk row must be under the BACKEND the vector actually came
        # from ('hash') — NOT under (local, llama.cpp) like the pre-0.9.48
        # poisoning. v0.9.113: the model id carries the dim suffix.
        dkey = DiskEmbeddingCache.make_key(text, "hash", used_model)
        conn = sqlite3.connect(str(tmp_path / "embed_cache.db"))
        try:
            row = conn.execute(
                "SELECT backend, model FROM embeddings WHERE key = ?",
                (dkey,),
            ).fetchone()
        finally:
            conn.close()
        assert row is not None
        assert row[0] == "hash" and row[1].startswith("hash:")

    def test_success_keyed_by_planned_backend(self, tmp_path,
                                              monkeypatch):
        from src.ingest.disk_cache import DiskEmbeddingCache
        from src.ingest.embedder import Embedder

        def fake_post(url, **kw):
            assert url.endswith("/v1/embeddings")
            return _FakeResp(200, _llamacpp_vec())

        monkeypatch.setattr(
            "src.ingest.embedder.requests.post", fake_post,
        )
        from src.ingest.embedder import Embedder

        e = Embedder()
        e.backend = "local"  # v0.9.51.0: local is explicit-only now
        e._llamacpp_ok = True  # probe green
        used_model = e._model_for("local")  # v0.9.113: "llama.cpp:<dim>"
        text = "正常路徑文字"
        e.embed(text)
        dkey = DiskEmbeddingCache.make_key(text, "local", used_model)
        conn = sqlite3.connect(str(tmp_path / "embed_cache.db"))
        try:
            row = conn.execute(
                "SELECT backend FROM embeddings WHERE key = ?",
                (dkey,),
            ).fetchone()
        finally:
            conn.close()
        assert row is not None and row[0] == "local"
        assert used_model.startswith("llama.cpp:")


# ─── OpenRouter embeddings backend (v0.9.49.0) ───────────────


class TestOpenRouterBackend:
    def test_no_key_never_probes_or_posts(self, monkeypatch):
        gets: list = []
        monkeypatch.setattr(
            "src.ingest.embedder.requests.get",
            lambda url, **kw: gets.append(url) or _FakeResp(200),
        )
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        from src.ingest.embedder import Embedder

        e = Embedder()
        assert e._openrouter_available() is False
        assert gets == []  # no pointless probe without a key

    def test_explicit_backend_openrouter_success(
        self, tmp_path, monkeypatch,
    ):
        posts: list[str] = []
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
        monkeypatch.setenv("EMBEDDING_BACKEND", "openrouter")

        def fake_post(url, **kw):
            posts.append(url)
            assert url == "https://openrouter.ai/api/v1/embeddings"
            return _FakeResp(200, {"data": [
                {"embedding": [0.3] * 4096},
            ]})

        def fake_get(url, **kw):
            # ollama probe must come up UNAVAILABLE so the explicit
            # openrouter backend skips the ollama leg.
            if url.endswith("/api/tags"):
                return _FakeResp(404)
            return _FakeResp(200)

        monkeypatch.setattr(
            "src.ingest.embedder.requests.post", fake_post,
        )
        monkeypatch.setattr(
            "src.ingest.embedder.requests.get", fake_get,
        )
        from src.ingest.disk_cache import DiskEmbeddingCache
        from src.ingest.embedder import Embedder

        e = Embedder()
        text = "openrouter 測試"
        vec = e.embed(text)
        assert e._last_backend == "openrouter"
        assert len(vec) == e.dimension
        assert posts == ["https://openrouter.ai/api/v1/embeddings"]
        # Disk row keyed by the openrouter backend + its model.
        dkey = DiskEmbeddingCache.make_key(
            text, "openrouter", e.openrouter_model,
        )
        conn = sqlite3.connect(str(tmp_path / "embed_cache.db"))
        try:
            row = conn.execute(
                "SELECT backend, model FROM embeddings WHERE key = ?",
                (dkey,),
            ).fetchone()
        finally:
            conn.close()
        assert row is not None
        assert row[0] == "openrouter"
        assert row[1] == e.openrouter_model

    def test_auto_chain_falls_through_to_openrouter(self, monkeypatch):
        """v0.9.58.2 auto order — llama.cpp is FIRST; a failing local
        POST (500, the v0.9.48 live trap) falls through to openrouter
        within the SAME embed call."""
        posts: list[str] = []
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")

        def fake_post(url, **kw):
            posts.append(url)
            if "llama" in url or "8080" in url:
                return _FakeResp(500)  # the degraded-GPU live trap
            assert "openrouter" in url
            return _FakeResp(200, {"data": [
                {"embedding": [0.4] * 4096},
            ]})

        monkeypatch.setattr(
            "src.ingest.embedder.requests.post", fake_post,
        )
        monkeypatch.setattr(
            "src.ingest.embedder.requests.get",
            lambda url, **kw: _FakeResp(200),
        )
        from src.ingest.embedder import Embedder

        e = Embedder()
        vec = e.embed("鏈則測試")
        assert posts[:1] == ["http://127.0.0.1:8080/v1/embeddings"]
        assert "https://openrouter.ai/api/v1/embeddings" in posts
        assert e._last_backend == "openrouter"
        assert len(vec) == e.dimension

    def test_openrouter_cooldown_skips_after_failure(
        self, monkeypatch,
    ):
        """Explicit EMBEDDING_BACKEND=openrouter (v0.9.50.0: ollama is
        out of the chain, so openrouter is the FIRST remote leg) — one
        POST, then the cooldown silences it."""
        posts: list[str] = []
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
        monkeypatch.setenv("EMBEDDING_BACKEND", "openrouter")

        def fake_post(url, **kw):
            posts.append(url)
            return _FakeResp(500)

        monkeypatch.setattr(
            "src.ingest.embedder.requests.post", fake_post,
        )
        monkeypatch.setattr(
            "src.ingest.embedder.requests.get",
            lambda url, **kw: _FakeResp(200),
        )
        from src.ingest.embedder import Embedder

        e = Embedder()
        e.embed("失敗一")
        assert posts == ["https://openrouter.ai/api/v1/embeddings"]
        e.embed("失敗二")
        assert posts == ["https://openrouter.ai/api/v1/embeddings"]

    def test_batch_dispatch_uses_openrouter(self, monkeypatch):
        posts: list[str] = []
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
        monkeypatch.setenv("EMBEDDING_BACKEND", "openrouter")

        def fake_post(url, **kw):
            posts.append(url)
            n = len(kw["json"]["input"])
            return _FakeResp(200, {"data": [
                {"embedding": [0.5] * 4096} for _ in range(n)
            ]})

        monkeypatch.setattr(
            "src.ingest.embedder.requests.post", fake_post,
        )
        from src.ingest.embedder import Embedder

        e = Embedder()
        vecs = e.embed_batch(["a", "b", "c"])
        assert len(vecs) == 3
        assert all(len(v) == e.dimension for v in vecs)
        assert posts == ["https://openrouter.ai/api/v1/embeddings"]

    def test_model_for_openrouter(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_EMBED_MODEL", "qwen/qwen3-embedding-8b")
        from src.ingest.embedder import Embedder

        e = Embedder()
        assert e._model_for("openrouter") == "qwen/qwen3-embedding-8b"

    def test_model_for_openrouter_4b_override(self, monkeypatch):
        # v0.9.115.2 — 4B-primary boxes pin the DIMENSION-EXACT
        # fallback (native 2560d): llama.cpp down → openrouter 4b =
        # same model, same dim, no padding.
        monkeypatch.setenv("OPENROUTER_EMBED_MODEL",
                           "qwen/qwen3-embedding-4b")
        from src.ingest.embedder import Embedder

        e = Embedder()
        assert e._model_for("openrouter") == "qwen/qwen3-embedding-4b"
        assert e.openrouter_model == "qwen/qwen3-embedding-4b"

    def test_model_for_openrouter_default_is_8b(self, monkeypatch):
        # the code default stays qwen/qwen3-embedding-8b (the historic
        # 8B-main-backend pairing); the live 4B era pins 4b in .env.
        monkeypatch.delenv("OPENROUTER_EMBED_MODEL", raising=False)
        from src.ingest.embedder import Embedder

        e = Embedder()
        assert e._model_for("openrouter") == "qwen/qwen3-embedding-8b"

    def test_failed_probe_not_cached(self, monkeypatch):
        """v0.9.51.0 — a single transient probe failure must NOT lock
        the first-choice backend out permanently (live: one flaky
        GET /models dropped an auto embed to hash). The failed probe
        is NOT cached: the openrouter leg's own availability check
        re-probes IN THE SAME CALL (transient absorbed without even
        degrading to hash); a truly down endpoint is caught by the
        POST-failure cooldown."""
        gets: list[str] = []
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")

        def flaky_get(url, **kw):
            gets.append(url)
            if len(gets) == 1:
                raise RuntimeError("transient network glitch")
            return _FakeResp(200)

        monkeypatch.setattr(
            "src.ingest.embedder.requests.get", flaky_get,
        )
        monkeypatch.setattr(
            "src.ingest.embedder.requests.post",
            lambda url, **kw: _FakeResp(200, {"data": [
                {"embedding": [0.6] * 4096},
            ]}),
        )
        from src.ingest.embedder import Embedder

        e = Embedder()
        e.embed("第一次")
        assert e._last_backend == "openrouter"
        assert e._openrouter_ok is True
        assert len(gets) == 2  # failed probe + in-call re-probe
        # The success caches — the second embed probes nothing.
        e.embed("第二次")
        assert e._last_backend == "openrouter"
        assert len(gets) == 2


# ─── OpenRouter provider routing for embeddings (v0.9.52.0) ──


class TestOpenRouterProviderRouting:
    def _posting_host(self, monkeypatch, capture, response=None):
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
        monkeypatch.setenv("EMBEDDING_BACKEND", "openrouter")

        def fake_post(url, **kw):
            capture["url"] = url
            capture["json"] = kw.get("json") or {}
            return _FakeResp(200, response or {"data": [
                {"embedding": [0.7] * 4096},
            ]})

        monkeypatch.setattr(
            "src.ingest.embedder.requests.post", fake_post,
        )
        monkeypatch.setattr(
            "src.ingest.embedder.requests.get",
            lambda url, **kw: _FakeResp(200),
        )
        from src.ingest.embedder import Embedder

        return Embedder()

    def test_no_order_no_provider_key(self, monkeypatch):
        capture: dict = {}
        e = self._posting_host(monkeypatch, capture)
        e.embed("x")
        assert "provider" not in capture["json"]

    def test_order_reaches_request_body(self, monkeypatch):
        capture: dict = {}
        monkeypatch.setenv(
            "OPENROUTER_EMBED_PROVIDER_ORDER", "nebius,deepinfra",
        )
        e = self._posting_host(monkeypatch, capture)
        e.embed("x")
        assert capture["json"]["provider"] == {
            "order": ["nebius", "deepinfra"],
            "allow_fallbacks": True,
        }

    def test_allow_fallbacks_false_parsed(self, monkeypatch):
        capture: dict = {}
        monkeypatch.setenv(
            "OPENROUTER_EMBED_PROVIDER_ORDER", "nebius",
        )
        monkeypatch.setenv("OPENROUTER_EMBED_ALLOW_FALLBACKS", "false")
        e = self._posting_host(monkeypatch, capture)
        e.embed("x")
        assert capture["json"]["provider"]["allow_fallbacks"] is False
        assert capture["json"]["provider"]["order"] == ["nebius"]

    def test_batch_carries_provider_order(self, monkeypatch):
        capture: dict = {}
        monkeypatch.setenv(
            "OPENROUTER_EMBED_PROVIDER_ORDER", "deepinfra,nebius",
        )
        e = self._posting_host(monkeypatch, capture)
        vecs = e.embed_batch(["a", "b"])
        assert len(vecs) == 2
        assert capture["json"]["provider"]["order"] == [
            "deepinfra", "nebius",
        ]

    def test_served_by_logged(self, monkeypatch, caplog):
        capture: dict = {}
        e = self._posting_host(monkeypatch, capture, response={
            "data": [{"embedding": [0.8] * 4096}],
            "provider": "DeepInfra",
        })
        import logging

        with caplog.at_level(logging.INFO, logger="src.ingest.embedder"):
            e.embed("x")
        assert "DeepInfra" in caplog.text

    def test_slug_parsing_edge_cases(self, monkeypatch):
        from src.ingest.embedder import _parse_provider_slugs

        assert _parse_provider_slugs("", "X") == []
        assert _parse_provider_slugs(" , , ", "X") == []
        assert _parse_provider_slugs(" nebius , deepinfra ", "X") == [
            "nebius", "deepinfra",
        ]