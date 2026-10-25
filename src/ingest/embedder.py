"""Vector embedding with explicit backend selection.

Backend selection (v0.2.6+) is **driven by the ``EMBEDDING_BACKEND`` env
var** so different collections can use different embeddings without
process restart. Supported backends:

  - ``local``  — local llama.cpp server at ``LLAMACPP_EMBED_URL``
                  (default ``http://localhost:8080/v1/embeddings``).
                  Serves Qwen3-Embedding-8B via Vulkan/CPU. Lowest
                  latency, no network, full privacy. Dim: 4096.
                  FIRST in the auto chain since v0.9.58.2 (the 5700
                  XT was made GPU-healthy in v0.9.58.1 — 138.7 ms
                  single p50 / 44.7 s batch-64); the v0.9.48.0
                  failure cooldown skips it automatically when the
                  Vulkan pipeline degrades again, falling through to
                  openrouter. Was explicit-only in
                  v0.9.51.0-v0.9.58.1.
  - ``ollama`` — Ollama Cloud (or any OpenAI-compatible Ollama server)
                  at ``OLLAMA_BASE_URL`` with ``OLLAMA_EMBED_MODEL``.
                  Default: ``nomic-embed-text`` (768-dim). Requires
                  ``OLLAMA_API_KEY`` for ollama.com cloud, otherwise
                  the local Ollama server may be used without a key.
                  EXPLICIT-ONLY since v0.9.50.0 — not in the auto
                  chain (the cloud endpoint 404/401'd on the live
                  box); set ``EMBEDDING_BACKEND=ollama`` to use it.
  - ``openrouter`` — OpenRouter's embeddings endpoint (v0.9.49.0+)
                  at ``OPENROUTER_BASE_URL`` using
                  ``OPENROUTER_EMBED_MODEL`` (default
                  ``qwen/qwen3-embedding-8b`` — native dim 4096, the
                  SAME model/dim as the local llama.cpp server, so
                  vectors are drop-in compatible). Reuses
                  ``OPENROUTER_API_KEY``. v0.9.115.2: 4B-primary
                  boxes set ``OPENROUTER_EMBED_MODEL=qwen/qwen3
                  -embedding-4b`` (live-verified native 2560d,
                  DeepInfra) for a DIMENSION-EXACT fallback instead
                  of the padded-8B mismatch.
  - ``openai`` — OpenAI-compatible API at ``OPENAI_BASE_URL`` if
                  ``OPENAI_API_KEY`` is set. Default model:
                  ``text-embedding-3-small`` (1536-dim, padded).
  - ``hash``   — Deterministic hash-based fallback. Last resort,
                  NOT semantic. Dim: matches ``EMBEDDING_DIM`` env.

Switching backends is a **per-collection** operation:

  1. ``set EMBEDDING_BACKEND=ollama`` + ``set QDRANT_COLLECTION=docs_ollama``
     in the shell, then ``divine-sense-agent.bat recreate-collection`` + ingest.
  2. ``set EMBEDDING_BACKEND=local`` + ``set QDRANT_COLLECTION=docs_local``
     in another shell, ``recreate-collection`` + ingest.

Each collection is keyed by ``QDRANT_COLLECTION``; its vector dim is
fixed at ``recreate-collection`` time. ``EMBEDDING_DIM`` env just
controls the configured dim (the active backend must match it).

LLM provider auto-switch already supports Ollama for ``Agent`` / image
description; the embedding path now uses llama.cpp directly (which
exposes an OpenAI-compatible ``/v1/embeddings`` endpoint).
"""
from __future__ import annotations

import hashlib
import logging
import os
import time

import numpy as np
import requests

from src.config import _normalize_loopback_url, config
from src.ingest.disk_cache import DiskEmbeddingCache, build_cache

logger = logging.getLogger(__name__)


# ── v0.9.48.0 — backend failure cooldown (circuit breaker) ──────
#
# Live log 2026-09-14: a llama.cpp Vulkan ``ErrorDeviceLost`` leaves
# the server ALIVE (/v1/models still 200 → the availability probe
# passes) while every /v1/embeddings POST fails with 500. The auto
# chain then also paid a doomed https://ollama.com/v1/embeddings 404
# per call — TWO failed HTTP round-trips + two log lines on EVERY
# stock snapshot save. The cooldown skips a backend that just failed
# hard for N seconds instead of re-trying it on every embed.
_DEFAULT_BACKEND_COOLDOWN = 300.0  # seconds


def _parse_provider_slugs(raw: str, env_name: str) -> list[str]:
    """Parse a comma-separated OpenRouter provider-slug list (e.g.
    ``nebius,deepinfra``). Empty/whitespace tokens are dropped; an
    empty result means "no routing" (zero behaviour change). Bad
    tokens are dropped with a warning — a typo must never take the
    embeddings backend down."""
    tokens = [t.strip() for t in (raw or "").split(",")]
    slugs = [t for t in tokens if t]
    if raw and tokens and not slugs:
        logger.warning(
            "[Embedder] %s=%r produced no usable provider slugs — "
            "ignoring (no routing)", env_name, raw,
        )
    return slugs


def _resolve_bool_env(env_name: str, default: bool) -> bool:
    """Read a boolean env var (true/1/yes vs false/0/no; case-
    insensitive). Unset/empty → ``default``; bad values → ``default``
    with a warning."""
    raw = (os.environ.get(env_name, "") or "").strip().lower()
    if not raw:
        return default
    if raw in ("true", "1", "yes", "on"):
        return True
    if raw in ("false", "0", "no", "off"):
        return False
    logger.warning(
        "[Embedder] %s=%r is not a boolean — falling back to %s",
        env_name, raw, default,
    )
    return default


def _resolve_backend_cooldown() -> float:
    """``EMBEDDER_BACKEND_COOLDOWN`` — seconds a failed backend is
    skipped (default 300; 0 = retry every call = legacy behaviour).
    Bad values fall back to the default (warning only, never raises).
    """
    raw = (os.environ.get("EMBEDDER_BACKEND_COOLDOWN", "") or "").strip()
    if not raw:
        return _DEFAULT_BACKEND_COOLDOWN
    try:
        v = float(raw)
    except ValueError:
        logger.warning(
            "[Embedder] EMBEDDER_BACKEND_COOLDOWN=%r is not a number — "
            "falling back to %gs", raw, _DEFAULT_BACKEND_COOLDOWN,
        )
        return _DEFAULT_BACKEND_COOLDOWN
    if v < 0:
        logger.warning(
            "[Embedder] EMBEDDER_BACKEND_COOLDOWN=%r is negative — "
            "falling back to %gs", raw, _DEFAULT_BACKEND_COOLDOWN,
        )
        return _DEFAULT_BACKEND_COOLDOWN
    return v


# v0.9.66.1 — default char cap for the LOCAL llama.cpp embedder.
# The live server runs with a 1024-token embedding slot ctx; the
# measured ratio on this box is ~0.78-1.0 tokens/char (mixed CJK/EN),
# so 950 chars stays safely under 1024 tokens.
_DEFAULT_LOCAL_MAX_CHARS = 950


def _resolve_local_max_chars() -> int:
    """``QDRANT_EMBED_LOCAL_MAX_CHARS`` — clip over-long inputs before
    the local llama.cpp POST so a too-long text can never 400
    (exceed_context_size_error) and trip the backend cooldown.
    0 disables clipping. Bad values fall back to the default."""
    raw = (os.environ.get("QDRANT_EMBED_LOCAL_MAX_CHARS", "") or "").strip()
    if not raw:
        return _DEFAULT_LOCAL_MAX_CHARS
    try:
        v = int(raw)
    except ValueError:
        logger.warning(
            "[Embedder] QDRANT_EMBED_LOCAL_MAX_CHARS=%r is not a number "
            "— falling back to %d", raw, _DEFAULT_LOCAL_MAX_CHARS,
        )
        return _DEFAULT_LOCAL_MAX_CHARS
    if v < 0:
        return _DEFAULT_LOCAL_MAX_CHARS
    return v


# Known per-backend native dims. Used when caller hasn't pinned
# ``EMBEDDING_DIM`` (e.g. switching to a backend with a different dim).
_BACKEND_NATIVE_DIMS: dict[str, int] = {
    "local": 4096,      # Qwen3-Embedding-8B
    "ollama": 768,      # nomic-embed-text
    "openrouter": 4096,  # qwen/qwen3-embedding-8b (v0.9.49.0)
    "openai": 1536,     # text-embedding-3-small
    "hash": 384,        # legacy
}


class Embedder:
    """Wrapper around an embedding model.

    Backend selection is **explicit** via ``EMBEDDING_BACKEND`` env var
    (no auto-fallback chain — that would silently produce semantically
    wrong vectors when a primary backend is down). All paths return a
    list of floats; lengths are normalised to ``EMBEDDING_DIM`` so
    Qdrant stays dimensionally consistent regardless of backend.

    Backend order (when ``EMBEDDING_BACKEND=auto``):
      1. local llama.cpp at ``LLAMACPP_EMBED_URL`` (v0.9.58.2+;
         FIRST again) — Qwen3-Embedding-8B via Vulkan (native dim
         4096), free + private; skipped automatically by the
         v0.9.48.0 failure cooldown when the server degrades.
      2. OpenRouter at ``OPENROUTER_BASE_URL`` (v0.9.49.0+) —
         ``qwen/qwen3-embedding-8b`` (native dim 4096, the SAME
         model/dim as the local llama.cpp server, so vectors are
         drop-in compatible), reuses ``OPENROUTER_API_KEY``; model
         via ``OPENROUTER_EMBED_MODEL`` (v0.9.115.2: 4B-primary
         boxes set ``qwen/qwen3-embedding-4b`` — native 2560d,
         dimension-exact, no padding).
      3. OpenAI-compatible API at ``OPENAI_BASE_URL`` if
         ``OPENAI_API_KEY`` is set.
      4. Deterministic hash-based fallback.

    ``ollama`` is NOT in the auto chain since v0.9.50.0 (the ollama
    cloud endpoint 404/401'd on this box) — select it explicitly
    with ``EMBEDDING_BACKEND=ollama`` (per-collection switching)
    when its server is healthy.
    """

    # Cache size for in-process embedding memoisation.
    _CACHE_SIZE = 4096

    def __init__(self):
        self.dimension = config.EMBEDDING_DIM
        # Backend selection (explicit or auto).
        self.backend = (
            os.environ.get("EMBEDDING_BACKEND", "auto").strip().lower()
        )
        # Local llama.cpp server. The loopback NAME is normalised to
        # the 127.0.0.1 literal (v0.9.25.3): bare ``localhost``
        # resolves to ::1 first on this box and every requests-based
        # call re-opens its HTTP connection, so each embed/probe call
        # paid a ~2 s refused delay (same trap as QDRANT_URL in
        # v0.9.25.1). [::1] / LAN IPs / hostnames are left untouched.
        self.llamacpp_url = _normalize_loopback_url(
            os.environ.get(
                "LLAMACPP_EMBED_URL", "http://localhost:8080/v1/embeddings"
            )
        ).rstrip("/")
        # Ollama (Cloud or local server).
        self.ollama_base = (
            os.environ.get("OLLAMA_BASE_URL", "https://ollama.com/v1")
            .rstrip("/")
        )
        self.ollama_url = self.ollama_base + "/embeddings"
        # v0.9.48.0: ollama.com cloud does NOT serve /v1/embeddings
        # (404 in the live log) — its native embeddings API is POST
        # ``{server}/api/embed`` with the same ``{"embeddings": [...]}``
        # response shape. Tried as a fallback when the OpenAI-shaped
        # path 404s.
        self.ollama_native_url = (
            self.ollama_base.rsplit("/v1", 1)[0] + "/api/embed"
        )
        self.ollama_api_key = os.environ.get("OLLAMA_API_KEY", "")
        self.ollama_model = os.environ.get(
            "OLLAMA_EMBED_MODEL", "nomic-embed-text"
        )
        # OpenAI.
        self.openai_api_key = config.OPENAI_API_KEY
        self.openai_model = os.environ.get(
            "OPENAI_EMBED_MODEL", "text-embedding-3-small"
        )
        self._openai_client = None
        # v0.9.49.0 — OpenRouter embeddings (live-verified 2026-09-14:
        # POST https://openrouter.ai/api/v1/embeddings with
        # ``qwen/qwen3-embedding-8b`` → 200, native dim 4096 — the
        # SAME model/dim as the local llama.cpp server, so an
        # OpenRouter-produced vector is drop-in compatible inside a
        # collection built for ``local``). Reuses OPENROUTER_API_KEY.
        self.openrouter_api_key = os.environ.get("OPENROUTER_API_KEY", "")
        self.openrouter_base = _normalize_loopback_url(
            os.environ.get(
                "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1",
            )
        ).rstrip("/")
        self.openrouter_url = self.openrouter_base + "/embeddings"
        self.openrouter_model = os.environ.get(
            "OPENROUTER_EMBED_MODEL", "qwen/qwen3-embedding-8b",
        )
        # v0.9.52.0 — OpenRouter PROVIDER ROUTING for embeddings (the
        # embeddings endpoint accepts the same ``provider`` object as
        # chat — docs: "provider object | null — Provider routing
        # preferences for the request"). Live-verified 2026-09-14:
        # order=[deepinfra] + allow_fallbacks=false → 200 with
        # ``"provider": "DeepInfra"`` in the response.
        #   OPENROUTER_EMBED_PROVIDER_ORDER  comma list, e.g.
        #                                    nebius,deepinfra
        #   OPENROUTER_EMBED_ALLOW_FALLBACKS  default true; false pins
        #                                    ONLY the ordered providers
        self.openrouter_embed_provider_order = _parse_provider_slugs(
            os.environ.get("OPENROUTER_EMBED_PROVIDER_ORDER", ""),
            "OPENROUTER_EMBED_PROVIDER_ORDER",
        )
        self.openrouter_embed_allow_fallbacks = _resolve_bool_env(
            "OPENROUTER_EMBED_ALLOW_FALLBACKS", True,
        )
        self._cache: dict[str, tuple[float, ...]] = {}
        self._cache_max = self._CACHE_SIZE
        # Persistent disk cache (SQLite). Honours EMBEDDING_CACHE env var
        # (``disk`` default / ``memory`` / ``off``). Survives process
        # restarts so re-ingesting the same file or asking the same chat
        # question twice doesn't re-call the embedding API.
        self._disk_cache: DiskEmbeddingCache | None = build_cache()
        # Probe caches (lazy).
        self._llamacpp_ok: bool | None = None
        self._ollama_ok: bool | None = None
        self._openrouter_ok: bool | None = None
        # v0.9.48.0 — failure cooldown (circuit breaker). Live log
        # 2026-09-14: a llama.cpp Vulkan ErrorDeviceLost state kept
        # the probe green (models 200) while every embeddings POST
        # 500'd, so EVERY embed paid a failed HTTP round-trip per
        # backend. After a hard failure the backend is skipped for
        # ``EMBEDDER_BACKEND_COOLDOWN`` seconds (0 = legacy always-
        # retry); a success clears the cooldown immediately.
        self._backend_cooldown = _resolve_backend_cooldown()
        self._fail_until: dict[str, float] = {}
        # The backend that ACTUALLY produced the last vector (set by
        # _embed_uncached). Disk-cache rows are keyed by it so a
        # hash-fallback vector is never stored under a healthy
        # backend's key — the old code cached degraded vectors as
        # ("local", "llama.cpp"), which kept serving them even after
        # the backend recovered (cache poisoning).
        self._last_backend = "hash"
        # v0.9.66.1 — local llama.cpp ctx guard. Live log 2026-09-16:
        # the scheduler's 1240-char CJK answer (~1201 tokens) exceeded
        # the llama.cpp embedding slot context (n_ctx=1024) -> HTTP 400
        # exceed_context_size_error -> EMBEDDER_BACKEND_COOLDOWN put
        # local to sleep for 300 s and every embed in that window fell
        # to OpenRouter. Clip OVER-LONG inputs before the local POST
        # (normal-size texts are untouched; the vector is still keyed
        # by the FULL text so cache semantics are unchanged).
        # QDRANT_EMBED_LOCAL_MAX_CHARS: default 950 (~1024 tokens of
        # mixed CJK/EN at the measured 0.78-1.0 tok/char ratio); 0
        # disables clipping.
        self._local_max_chars = _resolve_local_max_chars()

    def _clip_local(self, text: str) -> str:
        """v0.9.66.1 — clip an input for the LOCAL llama.cpp embedder
        so it can never exceed the server's embedding slot ctx
        (n_ctx=1024 → HTTP 400 exceed_context_size_error → backend
        cooldown). Normal-size texts pass through byte-identical;
        clipping logs at INFO once per clipped call. 0 disables."""
        cap = self._local_max_chars
        if cap <= 0 or len(text) <= cap:
            return text
        logger.info(
            "[Embedder] local input clipped %d -> %d chars "
            "(llama.cpp n_ctx=1024 guard; set "
            "QDRANT_EMBED_LOCAL_MAX_CHARS=0 to disable)",
            len(text), cap,
        )
        return text[:cap]

    # ── backend availability probes ──────────────────────────

    def _llamacpp_available(self) -> bool:
        """Probe the llama.cpp server. Cache the result; a hard
        failure cooldown (v0.9.48.0) forces False without network."""
        if self._in_cooldown("local"):
            return False
        if self._llamacpp_ok is not None:
            return self._llamacpp_ok
        try:
            base = self.llamacpp_url.rsplit("/v1", 1)[0]
            r = requests.get(f"{base}/v1/models", timeout=2)
            self._llamacpp_ok = r.status_code == 200
        except Exception:
            self._llamacpp_ok = False
        return self._llamacpp_ok

    def _ollama_available(self) -> bool:
        """Probe Ollama (Cloud or local). Cache the result; honors the
        failure cooldown (v0.9.48.0)."""
        if self._in_cooldown("ollama"):
            return False
        if self._ollama_ok is not None:
            return self._ollama_ok
        try:
            # Ollama exposes /api/tags on the base server (no /v1).
            base = self.ollama_url.rsplit("/v1", 1)[0]
            url = f"{base}/api/tags"
            headers = {}
            if self.ollama_api_key:
                headers["Authorization"] = f"Bearer {self.ollama_api_key}"
            r = requests.get(url, headers=headers, timeout=3)
            self._ollama_ok = r.status_code == 200
        except Exception:
            self._ollama_ok = False
        return self._ollama_ok

    def _openrouter_available(self) -> bool:
        """Probe OpenRouter's embeddings endpoint. ``False`` without a
        key (no pointless probe). Honors the failure cooldown
        (v0.9.48.0). A POSITIVE result is cached for the instance
        lifetime; a FAILED probe is NOT cached (v0.9.51.0) — a single
        transient network glitch must not lock the first-choice
        backend out permanently (the next embed re-probes; a truly
        down backend gets caught by the POST-failure cooldown
        instead)."""
        if not self.openrouter_api_key:
            return False
        if self._in_cooldown("openrouter"):
            return False
        if self._openrouter_ok is not None:
            return self._openrouter_ok
        try:
            headers = {"Authorization": f"Bearer {self.openrouter_api_key}"}
            r = requests.get(
                f"{self.openrouter_base}/models", headers=headers, timeout=8,
            )
            self._openrouter_ok = r.status_code == 200
        except Exception as e:
            # Transient network failure — do NOT cache. The next embed
            # re-probes; a genuinely down endpoint is handled by the
            # POST-failure cooldown.
            logger.debug(
                "[Embedder] OpenRouter probe failed (%r) — will "
                "re-probe on the next embed", e,
            )
            return False
        return self._openrouter_ok

    def _get_openai_client(self):
        if self._openai_client is None and self.openai_api_key:
            from openai import OpenAI
            self._openai_client = OpenAI(
                api_key=self.openai_api_key,
                base_url=config.OPENAI_BASE_URL,
            )
        return self._openai_client

    # ── failure cooldown (v0.9.48.0) ─────────────────────────
    def _in_cooldown(self, backend: str) -> bool:
        until = self._fail_until.get(backend)
        return until is not None and time.time() < until

    def _mark_failed(self, backend: str, err: Exception) -> None:
        """Put a backend on cooldown after a hard failure. Logs at
        WARNING — at most once per cooldown window (the skipped calls
        never reach the backend)."""
        self._fail_until[backend] = time.time() + self._backend_cooldown
        logger.warning(
            "[Embedder] backend %r failed (%.200s) — skipping it for "
            "%.0fs (EMBEDDER_BACKEND_COOLDOWN); success clears the "
            "cooldown immediately",
            backend, repr(err), self._backend_cooldown,
        )

    def _mark_ok(self, backend: str) -> None:
        self._fail_until.pop(backend, None)

    # ── Ollama embeddings request (both endpoint shapes) ─────
    def _ollama_post(self, payload: dict) -> list[list[float]]:
        """POST one Ollama embeddings request and return the
        ``embeddings`` rows. First tries the OpenAI-shaped
        ``{OLLAMA_BASE_URL}/embeddings``; on HTTP 404 (ollama.com
        cloud does not serve that path — live log 2026-09-14) retries
        Ollama's NATIVE ``{server}/api/embed``, which answers with the
        same ``{"embeddings": [[...]]}`` shape. A transport-level
        failure is not retried (same host)."""
        errors: list[str] = []
        for url in (self.ollama_url, self.ollama_native_url):
            headers = {"Content-Type": "application/json"}
            if self.ollama_api_key:
                headers["Authorization"] = f"Bearer {self.ollama_api_key}"
            try:
                resp = requests.post(
                    url, headers=headers, json=payload, timeout=120,
                )
            except Exception as e:
                errors.append(f"{url}: {e!r}")
                break
            if resp.status_code == 404 and url == self.ollama_url:
                errors.append(f"404 on {url}")
                logger.info(
                    "[Embedder] Ollama %s returned 404 — retrying the "
                    "native /api/embed endpoint", url,
                )
                continue
            resp.raise_for_status()
            return resp.json()["embeddings"]
        raise RuntimeError(
            f"ollama embeddings failed: {'; '.join(errors)}"
        )

    # ── backend dispatch ──────────────────────────────────────

    def _select_backend(self) -> str:
        """Resolve the effective backend (honour explicit choice).

        auto order (v0.9.58.2+): **local (llama.cpp) → openrouter →
        openai → hash** — the 5700 XT Vulkan pipeline is GPU-healthy
        again (v0.9.58.1: ggml-vulkan.dll restored into C:\\llama.cpp,
        37/37 layers offloaded, live bench 138.7 ms single p50 /
        44.7 s batch-64) so the FREE, private local server goes
        first. If it degrades again, the POST-failure cooldown
        (v0.9.48.0) skips it for EMBEDDER_BACKEND_COOLDOWN seconds
        and the chain falls through to openrouter — the same
        resilience that removed it in v0.9.51.0 keeps the swap
        automatic. ``ollama`` stays explicit-only (ollama.com
        embeddings kept 404/401-ing on this box).
        """
        if self.backend != "auto":
            return self.backend
        # auto = probe in priority order (the local probe returns
        # False during cooldown and caches; the POST-failure
        # cooldown is what actually skips a degraded llama.cpp).
        if self._llamacpp_available():
            return "local"
        if self._openrouter_available():
            return "openrouter"
        if self.openai_api_key:
            return "openai"
        return "hash"

    def embed(self, text: str) -> list[float]:
        """Return a normalized embedding vector for `text`.

        Cache lookup order:
          1. In-process LRU (fastest, dies on exit)
          2. Disk cache (SQLite, persists across processes)
          3. Backend API call
        """
        key = self._cache_key(text)
        hit = self._cache_get(key)
        if hit is not None:
            return list(hit)
        # Disk cache (one extra round-trip but survives restarts).
        dkey, be, model = self._disk_key(text)
        if self._disk_cache is not None:
            dvec = self._disk_cache.get(dkey)
            if dvec is not None:
                vec = self._normalize(dvec)
                self._cache_put(key, vec)
                return vec
        vec = self._embed_uncached(text)
        # v0.9.48.0: key the disk row by the backend that ACTUALLY
        # produced the vector — a hash-fallback vector must never be
        # cached under (local, llama.cpp) where it would keep being
        # served after the healthy backend recovers (cache poisoning).
        used = getattr(self, "_last_backend", be) or be
        self._cache_put(key, vec)
        if self._disk_cache is not None:
            if used == be:
                self._disk_cache.put(dkey, be, model, vec)
            else:
                used_model = self._model_for(used)
                self._disk_cache.put(
                    DiskEmbeddingCache.make_key(text, used, used_model),
                    used, used_model, vec,
                )
            self._disk_cache.maybe_evict()
        return vec

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for a batch of texts.

        Splits the batch into cache hits and misses so we only call the
        remote API for chunks we haven't seen before. Three-tier cache:
        in-process LRU -> SQLite disk cache -> backend API.
        """
        if not texts:
            return []
        # v0.9.112.0 — per-item backend labels for pipeline payload
        # tagging. Default = the RESOLVED backend (best effort for
        # cache hits, whose rows carry no provenance); freshly
        # computed items are overridden with the backend that
        # ACTUALLY produced the vector below.
        backend0, _model0 = self._resolve_backend_model()
        self._last_backend_batch: dict[int, str] = dict.fromkeys(
            range(len(texts)), backend0
        )
        # 1. In-process LRU lookup (fastest path).
        keys = [self._cache_key(t) for t in texts]
        results: list[list[float] | None] = [None] * len(texts)
        missing_idx: list[int] = []
        for i, k in enumerate(keys):
            hit = self._cache_get(k)
            if hit is not None:
                results[i] = list(hit)
            else:
                missing_idx.append(i)

        # 2. Disk cache lookup (only for items that missed the LRU).
        if self._disk_cache is not None and missing_idx:
            still_missing: list[int] = []
            backend, model = self._resolve_backend_model()
            for i in missing_idx:
                dkey = DiskEmbeddingCache.make_key(
                    texts[i], backend, model
                )
                dvec = self._disk_cache.get(dkey)
                if dvec is not None:
                    vec = self._normalize(dvec)
                    self._cache_put(keys[i], vec)
                    results[i] = vec
                else:
                    still_missing.append(i)
            missing_idx = still_missing

        if not missing_idx:
            return [r for r in results if r is not None]  # type: ignore

        # 3. Batched dispatch by backend for items still missing.
        backend, model = self._resolve_backend_model()
        ok = False
        if backend == "local":
            ok = self._embed_batch_local(texts, missing_idx, keys, results)
        elif backend == "ollama":
            ok = self._embed_batch_ollama(texts, missing_idx, keys, results)
        elif backend == "openrouter":
            ok = self._embed_batch_openrouter(
                texts, missing_idx, keys, results,
            )
        elif backend == "openai":
            ok = self._embed_batch_openai(texts, missing_idx, keys, results)
        if ok:
            self._persist_batch(texts, missing_idx, results, backend, model)
            return [r for r in results if r is not None]  # type: ignore

        # 4. Per-item fallback (also runs when batched backend failed).
        for i in missing_idx:
            vec = self._embed_uncached(texts[i])
            # v0.9.48.0: key the disk row by the backend that
            # ACTUALLY produced the vector (never poison a healthy
            # backend's key with a degraded vector).
            used = getattr(self, "_last_backend", backend) or backend
            # v0.9.112.0: the payload label must follow reality too.
            self._last_backend_batch[i] = used
            used_model = self._model_for(used)
            self._cache_put(keys[i], vec)
            results[i] = vec
            if self._disk_cache is not None:
                dkey = DiskEmbeddingCache.make_key(
                    texts[i], used, used_model,
                )
                self._disk_cache.put(dkey, used, used_model, vec)
        if self._disk_cache is not None:
            self._disk_cache.maybe_evict()
        return [r for r in results if r is not None]  # type: ignore

    def _persist_batch(
        self,
        texts: list[str],
        missing_idx: list[int],
        results: list[list[float] | None],
        backend: str,
        model: str,
    ) -> None:
        """Persist freshly-computed vectors to disk cache."""
        if self._disk_cache is None:
            return
        for i in missing_idx:
            r = results[i]
            if r is None:
                continue
            dkey = DiskEmbeddingCache.make_key(texts[i], backend, model)
            self._disk_cache.put(dkey, backend, model, r)
        self._disk_cache.maybe_evict()

    def _disk_key(self, text: str) -> tuple[str, str, str]:
        """Convenience: build the (disk_key, backend, model) triple."""
        backend, model = self._resolve_backend_model()
        return DiskEmbeddingCache.make_key(text, backend, model), backend, model

    def _model_for(self, backend: str) -> str:
        """Model identifier used in disk-cache keys per backend.

        v0.9.113.0 — ``local`` and ``hash`` append the EFFECTIVE dim
        (``llama.cpp:2560``): the llama.cpp server does not expose the
        model id, so swapping the GGUF on :8080 (8B ↔ 4B) used to hit
        the same cache key and silently truncate a 4096-d vector into
        a 2560-d query (silent semantic corruption, 1-MISS eval
        session). The dim suffix isolates models per collection. The
        legacy 4096 rows become unreachable — the next ingest of every
        file re-embeds once; the disk cache is only an accelerator."""
        if backend == "local":
            return f"llama.cpp:{self.dimension}"
        if backend == "ollama":
            return self.ollama_model
        if backend == "openrouter":
            return self.openrouter_model
        if backend == "openai":
            return self.openai_model
        return f"hash:{self.dimension}"

    def _resolve_backend_model(self) -> tuple[str, str]:
        """Resolve current backend + model name for cache keying."""
        backend = self._select_backend()
        return backend, self._model_for(backend)

    @property
    def last_backends(self) -> dict[int, str]:
        """v0.9.112.0 — per-index backend label for the MOST RECENT
        ``embed_batch`` call (index → backend string, same length as
        the batch's ``texts``). Best effort: LRU/disk cache hits are
        labeled with the currently RESOLVED backend (cache rows carry
        no provenance); freshly computed items carry the backend that
        ACTUALLY produced the vector (the v0.9.48.0 ``_last_backend``
        machinery). The pipeline upserts this into the Qdrant payload
        as ``embedding_backend``; the reembed-hash audit does NOT
        trust this label — it re-derives hash vectors exactly."""
        mapping = getattr(self, "_last_backend_batch", None)
        return dict(mapping) if isinstance(mapping, dict) else {}

    def embed_fresh(self, text: str) -> list[float]:
        """v0.9.112.0 — one-shot embed BYPASSING both cache tiers
        (LRU + disk). Exists for the reembed audit/repair path: a
        normal ``embed()`` would happily return the poisoned HASH
        vector from a correctly-keyed cache row and "repair" nothing.
        ``self._last_backend`` records the backend that answered."""
        return self._embed_uncached(text)

    def hash_fallback_vector(self, text: str) -> list[float]:
        """v0.9.112.0 — the DETERMINISTIC hash vector for ``text``
        (no network, no cache, no backend probing). The reembed audit
        re-derives exactly this to compare against stored vectors."""
        return self._fallback_embed(text)

    def _embed_batch_local(
        self, texts, missing_idx, keys, results
    ) -> bool:
        try:
            resp = requests.post(
                self.llamacpp_url,
                json={"input": [
                    self._clip_local(texts[i]) for i in missing_idx]},
                timeout=120,
            )
            resp.raise_for_status()
            data = resp.json().get("data", [])
            for sorted_idx, original_idx in enumerate(missing_idx):
                emb = data[sorted_idx]["embedding"]
                if emb:
                    self._cache_put(keys[original_idx], emb)
                    results[original_idx] = self._normalize(emb)
            return True
        except Exception as e:
            self._mark_failed("local", e)
            logger.warning(
                "[Embedder] llama.cpp batch failed (%r); per-item "
                "fallback.", e,
            )
            return False

    def _embed_batch_ollama(
        self, texts, missing_idx, keys, results
    ) -> bool:
        try:
            emb_rows = self._ollama_post({
                "model": self.ollama_model,
                "input": [texts[i] for i in missing_idx],
            })
            # Ollama returns list[list[float]] in input order
            for sorted_idx, original_idx in enumerate(missing_idx):
                emb = emb_rows[sorted_idx]
                if emb:
                    self._cache_put(keys[original_idx], emb)
                    results[original_idx] = self._normalize(emb)
            self._mark_ok("ollama")
            return True
        except Exception as e:
            self._mark_failed("ollama", e)
            logger.warning(
                "[Embedder] Ollama batch failed (%r); per-item "
                "fallback.", e,
            )
            return False

    # ── OpenRouter headers (v0.9.49.0) ────────────────────────
    def _openrouter_headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.openrouter_api_key}",
            "Content-Type": "application/json",
        }

    def _openrouter_provider_body(self) -> dict:
        """The ``provider`` routing fragment for the embeddings
        request body (v0.9.52.0). Empty dict when no provider order is
        configured — byte-identical legacy request. The embeddings
        endpoint accepts the same ``provider`` object as chat
        (``order`` + ``allow_fallbacks``)."""
        if not self.openrouter_embed_provider_order:
            return {}
        return {"provider": {
            "order": list(self.openrouter_embed_provider_order),
            "allow_fallbacks": self.openrouter_embed_allow_fallbacks,
        }}

    def _embed_batch_openrouter(
        self, texts, missing_idx, keys, results
    ) -> bool:
        """OpenRouter embeddings (OpenAI-shaped response
        ``data[N].embedding``). Live-verified with
        ``qwen/qwen3-embedding-8b`` (native dim 4096). Honors the
        ``provider`` routing fragment (v0.9.52.0)."""
        try:
            resp = requests.post(
                self.openrouter_url,
                headers=self._openrouter_headers(),
                json={
                    "model": self.openrouter_model,
                    "input": [texts[i] for i in missing_idx],
                    **self._openrouter_provider_body(),
                },
                timeout=120,
            )
            resp.raise_for_status()
            j = resp.json()
            data = j.get("data", [])
            for sorted_idx, original_idx in enumerate(missing_idx):
                emb = data[sorted_idx]["embedding"]
                if emb:
                    self._cache_put(keys[original_idx], emb)
                    results[original_idx] = self._normalize(emb)
            self._mark_ok("openrouter")
            served_by = j.get("provider")
            if served_by:
                logger.info(
                    "[Embedder] OpenRouter batch embeddings served by %s",
                    served_by,
                )
            return True
        except Exception as e:
            self._mark_failed("openrouter", e)
            logger.warning(
                "[Embedder] OpenRouter batch failed (%r); per-item "
                "fallback.", e,
            )
            return False

    def _embed_batch_openai(
        self, texts, missing_idx, keys, results
    ) -> bool:
        if not self.openai_api_key:
            return False
        try:
            client = self._get_openai_client()
            if client is None:
                return False
            resp = client.embeddings.create(
                model=self.openai_model,
                input=[texts[i][:8000] for i in missing_idx],
            )
            for sorted_idx, original_idx in enumerate(missing_idx):
                emb = resp.data[sorted_idx].embedding
                if emb:
                    self._cache_put(keys[original_idx], emb)
                    results[original_idx] = self._normalize(emb)
            return True
        except Exception as e:
            self._mark_failed("openai", e)
            logger.warning(
                "[Embedder] OpenAI batch failed (%r); per-item "
                "fallback.", e,
            )
            return False

    def _embed_uncached(self, text: str) -> list[float]:
        """One-shot embed (no cache lookup). Backend priority order.

        Sets ``self._last_backend`` to the backend that ACTUALLY
        produced the vector (``hash`` when every remote leg failed) —
        disk-cache rows are keyed by it (v0.9.48.0) so a degraded
        vector is never stored under a healthy backend's key."""
        self._last_backend = "hash"
        backend = self._select_backend()
        if backend == "local" and not self._in_cooldown("local"):
            try:
                resp = requests.post(
                    self.llamacpp_url,
                    json={"input": self._clip_local(text)},
                    timeout=120,
                )
                resp.raise_for_status()
                emb = resp.json()["data"][0]["embedding"]
                if emb:
                    self._mark_ok("local")
                    self._last_backend = "local"
                    return self._normalize(emb)
            except Exception as e:
                self._mark_failed("local", e)
                logger.warning(
                    "[Embedder] llama.cpp failed (%r); trying fallback.",
                    e,
                )
        # v0.9.50.0: ollama is OUT of the auto chain — this leg fires
        # ONLY when the backend was explicitly selected via
        # ``EMBEDDING_BACKEND=ollama`` (no fall-through from auto; the
        # cloud endpoint kept 404/401-ing on this box).
        if backend == "ollama" and not self._in_cooldown("ollama"):
            try:
                emb = self._ollama_post(
                    {"model": self.ollama_model, "input": text},
                )[0]
                if emb:
                    self._mark_ok("ollama")
                    self._last_backend = "ollama"
                    return self._normalize(emb)
            except Exception as e:
                self._mark_failed("ollama", e)
                logger.warning(
                    "[Embedder] Ollama failed (%r); trying fallback.", e,
                )
        if (
            (
                backend == "openrouter"
                and not self._in_cooldown("openrouter")
            )
            or self._openrouter_available()
        ):
            try:
                resp = requests.post(
                    self.openrouter_url,
                    headers=self._openrouter_headers(),
                    json={
                        "model": self.openrouter_model,
                        "input": text,
                        **self._openrouter_provider_body(),
                    },
                    timeout=120,
                )
                resp.raise_for_status()
                j = resp.json()
                emb = j["data"][0]["embedding"]
                if emb:
                    self._mark_ok("openrouter")
                    self._last_backend = "openrouter"
                    served_by = j.get("provider")
                    if served_by:
                        logger.info(
                            "[Embedder] OpenRouter embeddings served "
                            "by %s", served_by,
                        )
                    return self._normalize(emb)
            except Exception as e:
                self._mark_failed("openrouter", e)
                logger.warning(
                    "[Embedder] OpenRouter failed (%r); trying fallback.",
                    e,
                )
        if self.openai_api_key and not self._in_cooldown("openai"):
            try:
                resp = self._get_openai_client().embeddings.create(
                    model=self.openai_model, input=text[:8000],
                )
                vec = resp.data[0].embedding
                self._mark_ok("openai")
                self._last_backend = "openai"
                return self._normalize(vec)
            except Exception as e:
                self._mark_failed("openai", e)
                logger.warning(
                    "[Embedder] OpenAI failed (%r); falling back to "
                    "hash.", e,
                )
        return self._fallback_embed(text)

    def _normalize(self, vec: list[float]) -> list[float]:
        arr = np.array(vec, dtype=np.float32)
        n = float(np.linalg.norm(arr))
        if n > 0:
            arr = arr / n
        # Pad / truncate to configured dimension so different backends
        # (Qwen3-Embedding 4096-dim, Ollama 768-dim, OpenAI 1536-dim,
        # fallback 384-dim) can coexist with one Qdrant collection.
        dim = self.dimension
        if len(arr) >= dim:
            return arr[:dim].tolist()
        return np.concatenate(
            [arr, np.zeros(dim - len(arr), dtype=np.float32)]
        ).tolist()

    def _fallback_embed(self, text: str) -> list[float]:
        """Deterministic hash-based fallback. Same input → same vector."""
        vec = np.zeros(self.dimension, dtype=np.float32)
        for word in text.lower().split()[:500]:
            h = hashlib.md5(word.encode()).digest()
            idx = int.from_bytes(h[:4], "little") % self.dimension
            vec[idx] += 1.0
        n = float(np.linalg.norm(vec))
        if n > 0:
            vec = vec / n
        return vec.tolist()

    # ── In-process LRU cache (FIFO) ─────────────────────────────
    @staticmethod
    def _static_cache_key(text: str) -> str:
        return hashlib.sha1(text.encode("utf-8", errors="replace")).hexdigest()

    def _cache_key(self, text: str) -> str:
        """v0.9.113.0 — the LRU key is namespaced by (backend, model):
        the old key was TEXT-ONLY sha1, so one process switching models
        (per-collection backend switch / multi-model eval) served the
        previous model's vector for the same text — the disk tier was
        already keyed correctly, the LRU was NOT. Resolving the (backend,
        model) pair here is cheap (explicit backends are a dict lookup;
        the auto chain's probe is a 300 s-cooldown cache)."""
        backend, model = self._resolve_backend_model()
        return (f"{self._static_cache_key(text)}:{backend}:{model}")

    def _cache_get(self, key: str) -> tuple[float, ...] | None:
        return self._cache.get(key)

    def _cache_put(self, key: str, embedding: list[float]) -> None:
        if len(self._cache) >= self._cache_max:
            try:
                oldest = next(iter(self._cache))
                self._cache.pop(oldest, None)
            except StopIteration:
                pass
        self._cache[key] = tuple(embedding)

    def cache_clear(self) -> None:
        self._cache.clear()

    def cache_info(self) -> dict:
        return {"size": len(self._cache), "max": self._cache_max}

    # ── diagnostics ──────────────────────────────────────────

    def _qdrant_dim(self) -> int | None:
        """Read the actual vector dim of the Qdrant collection (or None).

        Used by :meth:`backend_info` to report whether the current
        Embedder backend is dimension-compatible with what's already
        ingested in the collection — mismatches cause ``search`` /
        ``chat`` to silently return empty results.
        """
        try:
            from src.vector_store.qdrant_client import QdrantStore
            return QdrantStore().get_stats().get("dim")
        except Exception:
            return None

    def backend_info(self) -> dict:
        """Report active backend + native dim for the current config."""
        eff = self._select_backend()
        native_dim = _BACKEND_NATIVE_DIMS.get(eff, self.dimension)
        info = {
            "backend": eff,
            "configured_dim": self.dimension,
            "native_dim": native_dim,
            "collection": config.QDRANT_COLLECTION,
            "collection_dim": self._qdrant_dim(),
            "llamacpp_url": self.llamacpp_url,
            "llamacpp_available": self._llamacpp_available(),
            "openrouter_url": self.openrouter_url,
            "openrouter_model": self.openrouter_model,
            "openrouter_available": self._openrouter_available(),
            "ollama_url": self.ollama_url,
            "ollama_model": self.ollama_model,
            "ollama_available": self._ollama_available(),
            "openai_model": self.openai_model,
            "openai_available": bool(self.openai_api_key),
            "memory_cache": self.cache_info(),
        }
        if self._disk_cache is not None:
            try:
                info["disk_cache"] = self._disk_cache.stats()
            except Exception as e:
                info["disk_cache"] = {"enabled": False, "error": str(e)}
        else:
            info["disk_cache"] = {"enabled": False}
        return info

    def cache_clear_all(self) -> dict:
        """Wipe both LRU + disk cache. Returns a small summary."""
        n_lru = len(self._cache)
        self._cache.clear()
        n_disk = 0
        if self._disk_cache is not None:
            n_disk = self._disk_cache.clear()
        return {"lru_cleared": n_lru, "disk_cleared": n_disk}
