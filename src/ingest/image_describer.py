"""Image describer - generate bilingual paragraph descriptions via Ollama Cloud.

Uses the OpenAI-compatible chat.completions API of Ollama Cloud with
vision-capable model `gemma4:31b-cloud` to describe images extracted
from DOCX/PPTX. Output is bilingual (Chinese + English), 100-200 words.

v0.9.16.4 — vision FALLBACK chain (user request: 當 ollama cloud 無法
使用時使用 openrouter 替代):

  1. Ollama Cloud (``OLLAMA_API_KEY`` + ``OLLAMA_BASE_URL`` +
     ``OLLAMA_VISION_MODEL``) — unchanged primary.
  2. On ANY failure (connection error / 429 / 5xx / empty response)
     or when Ollama is unconfigured → the SAME OpenAI-compatible
     vision call runs against OpenRouter (``OPENROUTER_API_KEY`` +
     ``OPENROUTER_BASE_URL`` + ``OPENROUTER_VISION_MODEL``, default
     ``google/gemini-2.5-flash``).
  3. No ``OPENROUTER_API_KEY`` → generic ``OPENAI_API_KEY`` +
     ``OPENAI_BASE_URL`` endpoint.

Both fail → the existing ``[Image: ... | vision error: ...]`` marker
(uncached, retryable). ``describe()`` keeps its ``str`` contract;
``last_backend`` records which provider answered ("ollama-cloud" /
"openrouter" / "openai-compat") for the MCP tool result.

v0.9.18.0 — the vision OCR chain is an INDEPENDENT application
requirement from the chat LLM chain (Agent), so its provider order
is adjustable separately via ``VISION_PROVIDER_CHAIN`` (comma list;
empty/unset = the default chain above — zero behaviour change).
Unknown tokens are skipped with a warning, duplicates collapse, and
unlisted providers keep their default relative order after the
listed ones (the chain defines PRIORITY, never exclusion). Aliases
``ollama-cloud`` / ``openai-compat`` are accepted. Ollama Cloud is
no longer hard-coded as primary — it is simply FIRST in the default
chain; ``VISION_PROVIDER_CHAIN=openai,openrouter`` puts a generic
OpenAI-compatible endpoint in front.

Performance features (v0.1.8+):
  - Parallel batch via ThreadPoolExecutor (default 4 workers)
  - SHA-1 in-memory cache: identical images (e.g. shared logos across
    decks) skip the API call entirely
  - Configurable via env: VISION_WORKERS, VISION_CACHE_SIZE

Ported verbatim from milvus-mcp-agent/src/ingest/image_describer.py
(only the docstring is updated to mention the sibling project).
"""
from __future__ import annotations

import base64
import hashlib
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Iterable

# v0.9.104.5 — process-wide shared cache. The SHA-1 cache used to be a
# plain instance dict, and the MCP tool host (and every other call
# site) constructed a FRESH ImageDescriber per call — the cache never
# hit (live log 2026-10-02: the same screenshot described 3x in one
# turn, from_cache: False every time). The class-level dict + lock make
# repeated identical images (same sha1 + prompt) skip the vision API
# across instances. Prompt-sensitivity is preserved: the cache key
# already mixes the effective prompt (v0.9.61.0).
_SHARED_CACHE_LOCK = threading.Lock()
_SHARED_CACHE: dict[str, str] = {}
_SHARED_CACHE_MAX_DEFAULT = 1024


def _shared_cache_get(key: str) -> str | None:
    with _SHARED_CACHE_LOCK:
        return _SHARED_CACHE.get(key)


def _shared_cache_put(key: str, value: str, max_entries: int) -> None:
    with _SHARED_CACHE_LOCK:
        while len(_SHARED_CACHE) >= max_entries:
            try:
                oldest = next(iter(_SHARED_CACHE))
            except StopIteration:
                break
            _SHARED_CACHE.pop(oldest, None)
        _SHARED_CACHE[key] = value


def _shared_cache_clear() -> None:
    with _SHARED_CACHE_LOCK:
        _SHARED_CACHE.clear()


def _shared_cache_size() -> int:
    with _SHARED_CACHE_LOCK:
        return len(_SHARED_CACHE)


class ImageDescriber:
    """Generate bilingual paragraph descriptions of images via Ollama Cloud."""

    DEFAULT_MODEL = "gemma4:31b-cloud"
    DEFAULT_BASE_URL = "https://ollama.com/v1"
    MAX_IMAGE_BYTES = 4 * 1024 * 1024  # 4 MB safety cap before send

    # v0.9.16.4 — OpenRouter vision fallback (multi-modal, cheap,
    # widely available). Override with OPENROUTER_VISION_MODEL.
    FALLBACK_DEFAULT_MODEL = "google/gemini-2.5-flash"
    # v0.9.17.0 — SiliconFlow vision candidate (Qwen2.5-VL family).
    SILICONFLOW_DEFAULT_VISION_MODEL = "Qwen/Qwen2.5-VL-72B-Instruct"

    # v0.9.18.0 — VISION_PROVIDER_CHAIN tokens/aliases. Canonical
    # names match the config keys; the two legacy backend names are
    # accepted as aliases.
    CHAIN_NAMES = ("ollama", "openrouter", "siliconflow", "openai")
    CHAIN_ALIASES = {"ollama-cloud": "ollama", "openai-compat": "openai"}
    # error-marker labels: the ollama entry reports as "ollama(...)"
    # (pre-0.9.18.0 marker shape, locked by tests).
    _ERROR_LABELS = {"ollama-cloud": "ollama"}

    PROMPT = (
        "請用 100-200 字描述這張圖片的內容（中文 + 英文雙語，段落形式）。"
        "如圖中含可辨識文字，請一併列出。\n"
        "Please describe this image in 100-200 words, bilingual "
        "(Chinese + English), paragraph form. List any readable text."
    )

    # v0.9.61.0 — OCR transcription prompt for scanned-PDF ingestion.
    # Verbatim text extraction (the LLM is told to output ONLY the
    # text on the page, no commentary), so the recognized content can
    # be chunked + archived into the collection like normal text.
    OCR_PROMPT = (
        "將圖片中的所有文字完整逐字輸出（保持原始閱讀順序與分行，"
        "不要加任何註解、描述或翻譯；圖中若無文字則輸出空字串）。\n"
        "Transcribe ALL text visible in this image VERBATIM, preserving "
        "the original reading order and line breaks. Output ONLY the "
        "transcribed text — no commentary, no description, no "
        "translation. If there is no readable text, output an empty "
        "string."
    )

    def __init__(self, model: str | None = None, base_url: str | None = None):
        self.api_key = os.getenv("OLLAMA_API_KEY", "")
        self.model = model or os.getenv("OLLAMA_VISION_MODEL", self.DEFAULT_MODEL)
        self.base_url = base_url or os.getenv("OLLAMA_BASE_URL", self.DEFAULT_BASE_URL)
        # Parallelism (Ollama Cloud rate-limits aggressively, keep modest)
        self.workers = max(1, int(os.getenv("VISION_WORKERS", "4")))
        # SHA-1 cache of (image_bytes_hash) -> description
        # Bounded to keep memory in check; 1024 entries ~ tens of MB of text
        self._cache: dict[str, str] = {}
        self._cache_max = int(os.getenv("VISION_CACHE_SIZE", "1024"))
        # v0.9.16.4 — which provider answered the LAST describe()
        # ("ollama-cloud" | "openrouter" | "openai-compat" | None).
        # Diagnostic only (describe_batch workers race this field).
        self.last_backend: str | None = None
        # v0.9.104.5 — True when the last describe() short-circuited
        # on the (instance or shared) SHA-1 cache. Strict-bool check on
        # the host side so MagicMock fakes (auto-attrs) can't fake it.
        self.last_cache_hit: bool = False

    def _chain_names(self) -> tuple[str, ...]:
        """v0.9.18.0 — vision OCR chain order from
        ``VISION_PROVIDER_CHAIN`` (comma list; empty/unset = the
        default ollama → openrouter → siliconflow → openai chain,
        byte-identical to v0.9.17.0). Unknown tokens are skipped
        with a warning; duplicates collapse; a partial list takes
        priority and the unlisted providers keep their default
        relative order after it. Reads ``os.getenv`` directly
        (monkeypatch-friendly, no config reload needed).
        """
        from src.provider_chain import parse_provider_chain

        return parse_provider_chain(
            (os.getenv("VISION_PROVIDER_CHAIN", "") or "").strip(),
            valid_names=self.CHAIN_NAMES,
            default_names=self.CHAIN_NAMES,
            aliases=self.CHAIN_ALIASES,
            warn_label="VISION_PROVIDER_CHAIN",
        )

    def _vision_candidates(
        self,
    ) -> list[tuple[str, str, str, str]]:
        """FULL vision OCR chain — ``(api_key, base_url, model,
        backend_name)`` INCLUDING the Ollama Cloud entry — ordered
        by ``VISION_PROVIDER_CHAIN`` (v0.9.18.0).

        Pre-0.9.18.0 the Ollama entry was hard-coded as the
        separately-handled primary; it is now simply the FIRST
        entry of the default chain, so the chain env can put any
        provider in front. Empty list when NO key is configured.
        """
        by_backend: dict[str, tuple[str, str, str, str]] = {}
        if self.api_key:
            by_backend["ollama"] = (
                self.api_key, self.base_url, self.model, "ollama-cloud",
            )
        key = os.getenv("OPENROUTER_API_KEY", "")
        if key:
            by_backend["openrouter"] = (
                key,
                os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
                os.getenv("OPENROUTER_VISION_MODEL", self.FALLBACK_DEFAULT_MODEL),
                "openrouter",
            )
        key = os.getenv("SILICONFLOW_API_KEY", "")
        if key:
            by_backend["siliconflow"] = (
                key,
                os.getenv(
                    "SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1",
                ),
                os.getenv(
                    "SILICONFLOW_VISION_MODEL",
                    self.SILICONFLOW_DEFAULT_VISION_MODEL,
                ),
                "siliconflow",
            )
        key = os.getenv("OPENAI_API_KEY", "")
        if key:
            by_backend["openai"] = (
                key,
                os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
                # Pre-existing quirk kept verbatim (zero behaviour
                # change): the generic endpoint reuses
                # OPENROUTER_VISION_MODEL (both default to
                # FALLBACK_DEFAULT_MODEL anyway).
                os.getenv("OPENROUTER_VISION_MODEL", self.FALLBACK_DEFAULT_MODEL),
                "openai-compat",
            )
        return [
            by_backend[n] for n in self._chain_names() if n in by_backend
        ]

    def _fallback_vision_candidates(
        self,
    ) -> list[tuple[str, str, str, str]]:
        """Backward-compatible view of the chain: candidates
        EXCLUDING the Ollama Cloud entry (pre-v0.9.18.0 this was the
        whole fallback list — openrouter → siliconflow →
        openai-compat when unset). ``describe()`` now walks the FULL
        chain via ``_vision_candidates()`` so
        ``VISION_PROVIDER_CHAIN`` can move Ollama off the first
        slot.
        """
        return [
            c for c in self._vision_candidates() if c[3] != "ollama-cloud"
        ]

    @property
    def enabled(self) -> bool:
        # v0.9.16.4: True when the Ollama primary OR any
        # OpenRouter / SiliconFlow / OpenAI-compatible candidate is
        # configured (v0.9.18.0: chain-orderable, same semantics).
        return bool(self._vision_candidates())

    def _hash_bytes(self, data: bytes) -> str:
        return hashlib.sha1(data).hexdigest()

    def _cache_get(self, key: str) -> str | None:
        # v0.9.104.5 — the cache lives at CLASS level (shared across
        # instances; the MCP tool host constructs a fresh describer per
        # call). Per-instance view kept for backwards compatibility.
        if key in self._cache:
            return self._cache[key]
        return _shared_cache_get(key)

    def _cache_put(self, key: str, value: str) -> None:
        if len(self._cache) >= self._cache_max:
            # Drop oldest insertion (FIFO). OrderedDict would be cleaner
            # but dict preserves insertion order in Python 3.7+.
            try:
                oldest = next(iter(self._cache))
                self._cache.pop(oldest, None)
            except StopIteration:
                pass
        self._cache[key] = value
        _shared_cache_put(key, value, self._cache_max)

    def cache_clear(self) -> None:
        self._cache.clear()
        _shared_cache_clear()

    def cache_info(self) -> dict:
        return {
            "size": len(self._cache),
            "shared_size": _shared_cache_size(),
            "max": self._cache_max,
        }

    def describe(
        self, image_path: Path | str,
        prompt: str | None = None,
        max_tokens: int = 500,
    ) -> str:
        """Return a bilingual paragraph description. Falls back to a marker
        if every configured vision provider fails.

        v0.9.18.0 — provider chain orderable via VISION_PROVIDER_CHAIN
        (default Ollama Cloud → OpenRouter → SiliconFlow → generic
        OpenAI-compatible endpoint = the v0.9.16.4/17.0 chain). Uses
        SHA-1 cache so repeated calls for the same image skip the API.
        ``last_backend`` records the provider that answered.

        v0.9.61.0 — ``prompt`` overrides the default description prompt
        (the scanned-PDF OCR path passes ImageDescriber.OCR_PROMPT) and
        ``max_tokens`` raises the ceiling for transcription calls; the
        cache key includes the prompt so OCR and description results
        never collide."""
        path = Path(image_path)
        if not path.exists():
            return f"[Image: {path.name} | file not found]"

        try:
            data = path.read_bytes()
        except OSError as e:
            return f"[Image: {path.name} | read error: {e}]"

        # SHA-1 cache lookup (size cap is checked before send so even tiny
        # pathological files go through normal flow). The key includes
        # the prompt so an OCR pass never reads a cached description.
        # v0.9.104.5 — ``last_cache_hit`` records whether THIS describe
        # short-circuited (instance OR shared cache); the MCP host uses
        # it for ``from_cache`` because its own before/after diff only
        # sees the instance dict.
        eff_prompt = prompt or self.PROMPT
        key: str | None = None
        self.last_cache_hit = False
        if len(data) <= self.MAX_IMAGE_BYTES:
            key = self._hash_bytes(
                data + b"\x00" + eff_prompt.encode("utf-8"))
            hit = self._cache_get(key)
            if hit is not None:
                self.last_cache_hit = True
                return hit

        try:
            if len(data) > self.MAX_IMAGE_BYTES:
                result = f"[Image: {path.name} | too large ({len(data)} bytes)]"
                self.last_backend = None
            else:
                b64 = base64.b64encode(data).decode("ascii")
                mime = self._guess_mime(path)
                data_url = f"data:{mime};base64,{b64}"

                # ── v0.9.18.0 provider chain (VISION_PROVIDER_CHAIN-
                # orderable; default ollama-cloud → openrouter →
                # siliconflow → openai-compat = the v0.9.16.4/17.0
                # behaviour) ──
                errors: list[str] = []
                result: str | None = None
                backend: str | None = None
                candidates = self._vision_candidates()
                if not candidates:
                    self.last_backend = None
                    return (
                        f"[Image: {path.name} | no vision provider "
                        "configured — set OLLAMA_API_KEY, "
                        "OPENROUTER_API_KEY, SILICONFLOW_API_KEY or "
                        "OPENAI_API_KEY]"
                    )
                for c_key, c_url, c_model, c_name in candidates:
                    try:
                        result = self._call_openai_compat(
                            data_url, c_key, c_url, c_model,
                            prompt=eff_prompt, max_tokens=max_tokens,
                        )
                        backend = c_name
                        break
                    except Exception as e:
                        label = self._ERROR_LABELS.get(c_name, c_name)
                        errors.append(f"{label}({c_model}): {e}")
                if result is None:
                    self.last_backend = None
                    return (
                        f"[Image: {path.name} | vision error: "
                        + "; ".join(errors) + "]"
                    )
                self.last_backend = backend

            # Cache successful results (skip fallback markers).
            # Fallback markers all start with "[Image:" (e.g. "...not set",
            # "...file not found", "...too large (...)", "...vision error: ...").
            if result.startswith("[Image:"):
                # Fallback marker — don't cache so a transient failure
                # can be retried later when the API recovers.
                pass
            else:
                self._cache_put(key, result)
            return result
        except Exception as e:
            return f"[Image: {path.name} | vision error: {e}]"

    def _call_openai_compat(
        self, data_url: str, api_key: str, base_url: str, model: str,
        prompt: str | None = None,
        max_tokens: int = 500,
    ) -> str:
        """One OpenAI-compatible vision call (shared by the Ollama
        primary and the OpenRouter / OpenAI fallback). Raises on ANY
        failure — network / HTTP error / empty content — so the
        caller can fall through to the next provider in the chain.
        v0.9.61.0: ``prompt``/``max_tokens`` overridable (OCR)."""
        from openai import OpenAI

        client = OpenAI(api_key=api_key, base_url=base_url)
        resp = client.chat.completions.create(
            model=model,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt or self.PROMPT},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }],
            max_tokens=max_tokens,
        )
        content = (resp.choices[0].message.content or "").strip()
        if not content:
            raise ValueError("empty response")
        return content

    def describe_batch(self, images: Iterable[Path | str]) -> list[str]:
        """Describe a batch of images in parallel using a thread pool.

        Order is preserved (i-th input -> i-th output). Each worker uses
        the same SHA-1 cache, so concurrent calls to the same image
        deduplicate naturally.
        """
        paths = [Path(p) for p in images]
        if not paths:
            return []
        if self.workers == 1 or not self.enabled:
            # Fast path: no parallelism
            return [self.describe(p) for p in paths]

        # Parallel path
        results: list[str | None] = [None] * len(paths)
        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            future_to_idx = {ex.submit(self.describe, p): i for i, p in enumerate(paths)}
            for fut in as_completed(future_to_idx):
                i = future_to_idx[fut]
                try:
                    results[i] = fut.result()
                except Exception as e:
                    results[i] = f"[Image: {paths[i].name} | worker error: {e}]"
        return [r if r is not None else "[Image: unknown]" for r in results]

    @staticmethod
    def _guess_mime(path: Path) -> str:
        ext = path.suffix.lower()
        return {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".gif": "image/gif",
            ".webp": "image/webp",
            ".bmp": "image/bmp",
            ".svg": "image/svg+xml",
        }.get(ext, "application/octet-stream")