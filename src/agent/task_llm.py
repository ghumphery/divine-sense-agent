# -*- coding: utf-8 -*-
"""Task-level LLM model pinning (v0.9.110.0).

A background task (``run_task_once``) or a compiled skill stage can pin
its OWN chat model, chosen from the catalogs of the providers this
box actually holds keys for — **openrouter**, **ollama** (cloud or
local Ollama server) and **llamacpp** (v0.9.133.11 — the LOCAL
llama.cpp chat server, e.g. the qwen2.5 CPU summary server on :8090).
Format (ONE colon separator, everything after it
is the model id — ollama model names like ``gpt-oss:120b-cloud``
contain colons themselves, which is why the split is limited to 1):

    openrouter:z-ai/glm-4.6        # an OpenRouter model slug
    ollama:gpt-oss:120b-cloud      # an Ollama(-cloud) model name
    llamacpp:qwen2.5-7b-cpu        # the LOCAL llama.cpp chat server

Unspecified -> the default provider chain runs exactly as before
(zero behaviour change). The pin is PROVIDER-EXCLUSIVE: no
cross-provider failover walk for that turn — fail-closed (the task
fails with a clear error instead of silently answering on another
model).

Zero-side-effect module (mirrors provider_chain.py discipline): reads
``os.environ`` directly, imports nothing from src.config, performs NO
network I/O at import time. Catalog fetches are TTL-cached
(``clear_catalog_cache`` resets; tests monkeypatch ``urllib`` and call
it). Catalog UNREACHABLE degrades gracefully: validation PASSES with a
warning — the provider itself will 404 at turn time if the model is
truly wrong.
"""
from __future__ import annotations

import difflib
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)  # type: ignore[name-defined]

ALLOWED_PROVIDERS = ("openrouter", "ollama", "llamacpp")

_OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
_OLLAMA_TAGS_PATH = "/api/tags"

_CATALOG_TTL_S = 600.0
_CATALOGS: dict[str, tuple[float, dict[str, Any]]] = {}
_CATALOG_LOCK = threading.Lock()


class TaskLlmError(ValueError):
    """Raised by :func:`parse_task_llm` for malformed task model pins."""


def parse_task_llm(spec: str) -> tuple[str, str]:
    """``"provider:model"`` -> ``(provider, model)`` (strict).

    ONE colon splits provider from model (``ollama:gpt-oss:120b-cloud``
    -> ``("ollama", "gpt-oss:120b-cloud")``). Raises
    :class:`TaskLlmError` on syntax/provider errors with usage help.
    """
    s = str(spec or "").strip()
    if not s:
        raise TaskLlmError(
            "task llm pin is empty — use 'provider:model' where provider "
            "is one of " + " | ".join(ALLOWED_PROVIDERS) + "."
        )
    if ":" not in s:
        raise TaskLlmError(
            f"task llm pin {s!r} has no 'provider:model' separator — "
            "write e.g. 'openrouter:z-ai/glm-4.6' or "
            "'ollama:gpt-oss:120b-cloud'."
        )
    name, model = s.split(":", 1)
    name = name.strip().lower()
    model = model.strip()
    if not model:
        raise TaskLlmError(
            f"task llm pin {s!r}: the model id after ':' is empty."
        )
    if name not in ALLOWED_PROVIDERS:
        raise TaskLlmError(
            f"task llm pin {s!r}: provider {name!r} is not allowed — "
            "task/stage model pins support "
            + " | ".join(ALLOWED_PROVIDERS) + " only."
        )
    return name, model


def _http_json(
    url: str,
    bearer: str = "",
    timeout_s: float = 10.0,
) -> dict:
    """GET a JSON document (stdlib only; raises on any failure)."""
    req = urllib.request.Request(url)
    if bearer:
        req.add_header("Authorization", f"Bearer {bearer}")
    req.add_header(
        "User-Agent",
        "divine-sense-agent/0.9.110 (task-llm catalog probe)",
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as r:
        return json.loads(r.read().decode("utf-8"))


def _catalog_uncached_openrouter(timeout_s: float) -> dict[str, Any]:
    try:
        data = _http_json(_OPENROUTER_MODELS_URL, "", timeout_s)
    except Exception as e:  # noqa: BLE001 — network failures degrade
        return {"ok": False, "error": repr(e)[:200], "models": []}
    ids = []
    for m in (data or {}).get("data") or []:
        mid = str((m or {}).get("id") or "").strip()
        if mid:
            ids.append(mid)
    return {"ok": True, "models": sorted(set(ids)), "error": None}


def _catalog_uncached_ollama(timeout_s: float) -> dict[str, Any]:
    # v0.9.110.0 live-E2E lesson: OLLAMA_BASE_URL may be an
    # OpenAI-compat base ending in '/v1' (used by the ollama chat
    # provider) — the tags endpoint lives on the ROOT, so strip it or
    # cloud /api/tags resolves to /v1/api/tags -> 404.
    base = (os.environ.get("OLLAMA_BASE_URL", "") or
            "https://ollama.com").strip().rstrip("/")
    if base.endswith("/v1"):
        base = base[: -len("/v1")].rstrip("/")
    url = base + _OLLAMA_TAGS_PATH
    key = (os.environ.get("OLLAMA_API_KEY", "") or "").strip()
    try:
        data = _http_json(url, key, timeout_s)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": repr(e)[:200], "models": []}
    ids: list[str] = []
    for m in (data or {}).get("models") or []:
        m = m or {}
        for field in ("name", "model"):
            mid = str(m.get(field) or "").strip()
            if mid and mid not in ids:
                ids.append(mid)
    return {"ok": True, "models": sorted(set(ids)), "error": None}


def _llamacpp_chat_base() -> str:
    """v0.9.133.11 — the LOCAL llama.cpp chat server base for *pins*.
    The SAME env key the session compactor uses (one env line drives
    both the summary pass and task pins); default :8090/v1."""
    return (os.environ.get("QDRANT_SESSION_SUMMARY_LLAMACPP_URL", "")
            or "http://127.0.0.1:8090/v1").strip().rstrip("/")


def _catalog_uncached_llamacpp(timeout_s: float) -> dict[str, Any]:
    # v0.9.133.11 — local llama.cpp chat catalog probe. Its /v1/models
    # is OpenAI-compatible (verified live: data[0].id == "qwen2.5-7b-
    # cpu"), so the data[].id mining works. Accepts either a bare base
    # or one already ending in /v1.
    base = _llamacpp_chat_base()
    url = (base if base.endswith("/v1") else base + "/v1") + "/models"
    try:
        data = _http_json(url, "", timeout_s)
    except Exception as e:  # noqa: BLE001 — local server down degrades
        return {"ok": False, "error": repr(e)[:200], "models": []}
    ids: list[str] = []
    for m in (data or {}).get("data") or []:
        mid = str((m or {}).get("id") or "").strip()
        if mid:
            ids.append(mid)
    return {"ok": True, "models": sorted(set(ids)), "error": None}


_UNCACHED = {
    "openrouter": _catalog_uncached_openrouter,
    "ollama": _catalog_uncached_ollama,
    # v0.9.133.11 — the LOCAL llama.cpp chat server (:8090, started by
    # C:\llama.cpp\start-llamacpp-chat-cpu.bat) as a pin target.
    # Deliberately NOT part of the default chat chain — it only exists
    # for explicit pins (a weak CPU summary model must never be
    # auto-detected as the chat primary).
    "llamacpp": _catalog_uncached_llamacpp,
}


def catalog_for(provider: str, timeout_s: float = 10.0) -> dict[str, Any]:
    """``{"ok": bool, "models": [...], "error"?}`` for one provider,
    TTL-cached (per-process; ``clear_catalog_cache()`` resets)."""
    name = str(provider or "").strip().lower()
    now = time.time()
    with _CATALOG_LOCK:
        hit = _CATALOGS.get(name)
        if hit is not None and now - hit[0] < _CATALOG_TTL_S:
            return dict(hit[1])
    fn = _UNCACHED.get(name)
    if fn is None:
        return {"ok": False, "error": f"unknown provider {name!r}",
                "models": []}
    out = fn(timeout_s)
    with _CATALOG_LOCK:
        _CATALOGS[name] = (now, dict(out))
    return dict(out)


def clear_catalog_cache() -> None:
    """TEST/utility hook — wipe the TTL cache."""
    with _CATALOG_LOCK:
        _CATALOGS.clear()


def _suggestions(model: str, pool: list[str], n: int = 8) -> list[str]:
    """Close-match candidates for a rejected model id."""
    m = str(model or "")
    text = [
        p for p in pool
        if m.lower() in p.lower() or p.lower().startswith(m.lower()[:6])
    ]
    near = difflib.get_close_matches(m, pool, n=n, cutoff=0.4)
    out: list[str] = []
    for cand in list(dict.fromkeys(text + near)):
        out.append(cand)
        if len(out) >= n:
            break
    return sorted(set(out))


def validate_task_llm(spec: str) -> dict[str, Any]:
    """Validate a task llm pin against the LIVE provider catalogs.

    Returns ``{"ok": bool, "provider"?, "model"?, "error"?, "warning"?,
    "suggestions"?}``. Catalog UNREACHABLE degrades to ok+warning
    (the provider will surface its own 404 at turn time).
    """
    try:
        name, model = parse_task_llm(spec)
    except TaskLlmError as e:
        return {"ok": False, "error": str(e),
                "provider": None, "model": None}
    cat = catalog_for(name)
    if cat.get("ok"):
        models = list(cat.get("models") or [])
        # v0.9.110.0 live-lesson: Ollama cloud additionally serves
        # "-cloud" aliases (``gpt-oss:120b-cloud`` ⇔ ``gpt-oss:120b``)
        # — accept the alias when its base id is in the catalog.
        aliases = [model]
        if model.endswith("-cloud"):
            aliases.append(model[: -len("-cloud")])
        else:
            aliases.append(model + "-cloud")
        hit = next((a for a in aliases if a in models), None)
        if hit is not None:
            return {"ok": True, "provider": name, "model": hit}
        sug = _suggestions(model, models)
        return {
            "ok": False,
            "provider": name,
            "model": model,
            "error": (
                f"model {model!r} is not in the {name} catalog"
                + (f" — closest: {', '.join(sug)}" if sug else "")
                + ". Call list_task_models to see what is available."
            ),
            "suggestions": sug,
        }
    logger.warning(
        "[task-llm] %s catalog unreachable (%r) — pin accepted "
        "unverified", name, cat.get("error"),
    )
    return {
        "ok": True, "provider": name, "model": model,
        "warning": (
            f"{name} model catalog unreachable — 'provider:model' "
            f"{model!r} accepted UNVERIFIED (the provider will reject "
            "an unknown model at turn time)."
        ),
    }


def coerce_llm_pin(spec: Any) -> tuple[str | None, str | None]:
    """Coerce an OPTIONAL ``llm`` field (schedule / webhook / plan add
    path, v0.9.111.0) into ``(canonical_pin_or_None, error_or_None)``.

    * empty / whitespace / None -> ``(None, None)`` — no pin, the
      default provider chain (zero behaviour change).
    * a valid catalog-backed pin -> the canonical
      ``f"{provider}:{model}"`` (``-cloud`` alias normalised).
    * a malformed / wrong-provider / unknown-model spec ->
      ``(None, error_message)`` — the caller turns it into a clean
      tool error (fail-closed ADD semantics).

    Fire paths use :func:`validate_task_llm` directly instead — a
    schedule with a stale pin DEGRADES to the default chain at fire
    time (schedule robustness) rather than refusing to run.
    """
    s = str(spec or "").strip()
    if not s:
        return None, None
    verdict = validate_task_llm(s)
    if not verdict.get("ok"):
        return None, (
            "task llm pin rejected: " + str(verdict.get("error"))
            + (f" (suggestions: {', '.join(verdict.get('suggestions') or [])}"
               if verdict.get("suggestions") else "")
        )
    return f"{verdict['provider']}:{verdict['model']}", None


def list_task_models(
    provider: str | None = None,
    contains: str | None = None,
    per_provider_cap: int = 200,
) -> dict[str, Any]:
    """Discovery payload for the LLM-facing ``list_task_models`` tool."""
    wanted = ([str(provider).strip().lower()]
              if str(provider or "").strip()
              else list(ALLOWED_PROVIDERS))
    filt = str(contains or "").strip().lower()
    out: dict[str, Any] = {"ok": True, "contains": filt or None}
    for name in wanted:
        if name not in ALLOWED_PROVIDERS:
            out[name] = {"ok": False,
                         "error": f"provider {name!r} not allowed"}
            continue
        cat = catalog_for(name)
        models = list(cat.get("models") or [])
        total_raw = len(models)
        if filt:
            models = [m for m in models if filt in m.lower()]
        truncated = len(models) > per_provider_cap
        out[name] = {
            "ok": bool(cat.get("ok")),
            "error": cat.get("error"),
            "models": models[:max(1, per_provider_cap)],
            "total_after_filter": len(models),
            "unfiltered_total": total_raw,
            "truncated": truncated,
        }
    return out