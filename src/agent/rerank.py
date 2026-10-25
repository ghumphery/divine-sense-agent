"""RerankClient — Jev (TypeSafe) decision-rerank over the cosine recall
pool (v0.9.83.0, user: 「cosine 只看語意相近、不看能否回答 — Jev/Nimble
rerank 要補這點」).

"Rank wide, read narrow": ``Retriever.search`` already fetches the
top-N cosine hits; this client asks the Jev Decisions API ONE yes/no
question per chunk ("能直接回答查詢嗎？") in a SINGLE call, sorts by
the returned probability and the caller keeps the top-k. Cosine stays
the wide-recall stage; Jev adds the "CAN this chunk answer" signal
cosine cannot express.

Live-probed contract (2026-09-21, ``typesafe/jev-1.13-20260917`` served
by TypeSafe through the existing ``OPENROUTER_API_KEY``):

* Endpoint ``POST https://openrouter.ai/api/alpha/decisions`` (NO
  ``/v1`` segment — ``/api/v1/api/alpha/decisions`` and friends 404).
* Body ``{"model": "typesafe/jev-1.13",
          "state": {"query": ..., "chunks": ["1. ...", "2. ..."]},
          "questions": {"chunk_1": {"type": "noul",
                                    "instructions": "..."}, ...}}``
* 200 ``{"answers": {"chunk_1": {"type": "noul", "noul": 0.95}},
         "usage": {"input_tokens": 4693, "cost": 0.0002}}``
* 20 noul questions in ONE call measured 0.4-0.8 s (state 32k-token
  limit; 20 × 600-char CJK chunks ≈ 4.7k tokens — ample headroom).
* A query with NO answerable chunk returned max 0.17 while cosine
  still reported 0.517 — Jev honestly separates "semantically near"
  from "actually answerable".

Failure semantics: ANY error (feature off / single candidate / missing
key / HTTP error / timeout / bad JSON / unsupported backend) returns
the chunks in the ORIGINAL cosine order with ``reranked = False`` —
cosine recall is always the floor. Single attempt, no retries.

Env vars (resolvers below clamp bad values, never crash):

* ``QDRANT_RERANK``              off (default) | on — master switch
* ``QDRANT_RERANK_BACKEND``      jev (default) | anything else rejected
                                 at runtime — local Nimble prefill of
                                 20 chunks measured 40-100 s on the
                                 RX 5700 XT (RDNA1), unusable for
                                 interactive rerank
* ``QDRANT_RERANK_CANDIDATES``   20 (recall pool size, clamp 2-50)
* ``QDRANT_RERANK_TIMEOUT_S``    10 (HTTP timeout, clamp 2-60)
* ``QDRANT_RERANK_CHUNK_CHARS``  600 (per-chunk state truncation,
                                 clamp 100-4000)
* ``JEV_MODEL``                  ``typesafe/jev-1.13`` (OpenRouter
                                 catalog name; the bare ``jev-1.13``
                                 spelling 400s on the official API)
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from typing import Any

__all__ = ["RerankClient", "rerank_enabled"]

logger = logging.getLogger("qdrant.rerank")

_DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"
_INSTRUCTIONS = "編號 {i} 的段落能直接回答查詢嗎？"
_DEFAULT_CANDIDATES = 20
_DEFAULT_TIMEOUT_S = 10.0
_DEFAULT_CHUNK_CHARS = 600
_DEFAULT_MODEL = "typesafe/jev-1.13"


def _env_str(name: str, default: str) -> str:
    return (os.environ.get(name, default) or default).strip()


def _env_int(name: str, default: int, lo: int, hi: int) -> int:
    raw = (os.environ.get(name, "") or "").strip()
    if not raw:
        return default
    try:
        v = int(float(raw))
    except ValueError:
        logger.warning("[Rerank] %s=%r is not a number — using %d",
                       name, raw, default)
        return default
    if v < lo or v > hi:
        logger.warning("[Rerank] %s=%r out of range [%d, %d] — using %d",
                       name, raw, lo, hi, default)
        return default
    return v


def _decisions_post(body: dict, timeout_s: float,
                    url: str = _DECISIONS_URL) -> tuple[dict, str]:
    """Module-level Jev Decisions HTTP call — shared by the rerank
    client and the generic decision helpers (jev_noul). Returns
    ``(data, error)``: ``data`` is the parsed JSON dict ({} on
    failure) and ``error`` the human-readable reason ('' on success)."""
    key = _env_str("OPENROUTER_API_KEY", "")
    if not key:
        return {}, "OPENROUTER_API_KEY missing"
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + key,
            "Content-Type": "application/json",
            "HTTP-Referer": "http://127.0.0.1:8334",
            "X-OpenRouter-Title": "divine-sense-agent",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as r:
            return json.loads(r.read().decode("utf-8")), ""
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:200]
        except Exception:
            pass
        return {}, "HTTP %s: %s" % (e.code, detail)
    except Exception as e:  # timeout / DNS / JSON — all degrade
        return {}, repr(e)


def jev_noul(instructions: str, state: Any,
             timeout_s: float | None = None,
             ) -> tuple[float | None, dict[str, Any]]:
    """One yes/no (noul) decision about ``state`` — the generic entry
    point for the Jev decision layer beyond rerank (watch gate, time
    route, …). INDEPENDENT of ``QDRANT_RERANK`` — each feature gates
    itself with its own env switch; only the key + endpoint are shared.

    Returns ``(probability, meta)``; ``probability`` is ``None`` on ANY
    failure (missing key / HTTP / timeout / bad JSON) — the caller
    applies its own fail-open / fail-closed policy (plan §7).
    """
    meta: dict[str, Any] = {"backend": "jev", "elapsed_s": 0.0,
                            "error": ""}
    body = {
        "model": _env_str("JEV_MODEL", _DEFAULT_MODEL) or _DEFAULT_MODEL,
        "state": state,
        "questions": {"decision": {"type": "noul",
                                   "instructions": instructions}},
    }
    t0 = time.time()
    data, err = _decisions_post(
        body, float(timeout_s if timeout_s is not None
                    else _env_int("QDRANT_RERANK_TIMEOUT_S",
                                  int(_DEFAULT_TIMEOUT_S), 2, 60)),
    )
    meta["elapsed_s"] = round(time.time() - t0, 3)
    if err or not isinstance(data, dict):
        meta["error"] = err or "empty response"
        return None, meta
    meta["usage"] = data.get("usage") or {}
    return _noul(data.get("answers") or {}, "decision"), meta


def jev_choice(instructions: str, state: Any, options: list[str],
               timeout_s: float | None = None,
               ) -> tuple[str | None, float | None, dict[str, Any]]:
    """One choice decision about ``state`` — the message router entry
    point (plan §5.1 v2): ONE Jev call classifies the message into one
    of ``options`` (each option may carry a ``None`` description).

    Returns ``(choice, probability, meta)`` — ``choice`` is ``None``
    on ANY failure (missing key / HTTP / timeout / bad JSON / unknown
    label); ``probability`` is the winner's probability from the
    choice distribution (None when the response omits it)."""
    meta: dict[str, Any] = {"backend": "jev", "elapsed_s": 0.0,
                            "error": ""}
    body = {
        "model": _env_str("JEV_MODEL", _DEFAULT_MODEL) or _DEFAULT_MODEL,
        "state": state,
        "questions": {"decision": {
            "type": "choice",
            "instructions": instructions,
            "criteria": {o: None for o in options},
        }},
    }
    t0 = time.time()
    data, err = _decisions_post(
        body, float(timeout_s if timeout_s is not None
                    else _env_int("QDRANT_RERANK_TIMEOUT_S",
                                  int(_DEFAULT_TIMEOUT_S), 2, 60)),
    )
    meta["elapsed_s"] = round(time.time() - t0, 3)
    if err or not isinstance(data, dict):
        meta["error"] = err or "empty response"
        return None, None, meta
    meta["usage"] = data.get("usage") or {}
    ans = (data.get("answers") or {}).get("decision") or {}
    choice = str(ans.get("choice") or "") or None
    prob = None
    probs = ans.get("probabilities")
    if isinstance(probs, dict) and choice:
        try:
            prob = max(0.0, min(1.0, float(probs.get(choice, 0.0))))
        except (TypeError, ValueError):
            prob = None
    return choice, prob, meta


def rerank_enabled() -> bool:
    """``QDRANT_RERANK`` — on/true/1/yes enable the layer (default off
    so search behaviour is byte-identical until the user opts in)."""
    v = (os.environ.get("QDRANT_RERANK", "") or "").strip().lower()
    return v in ("on", "true", "1", "yes")


class RerankClient:
    """One-call noul rerank over the cosine recall pool (Jev Decisions)."""

    def __init__(self) -> None:
        self.url = _DECISIONS_URL

    # ── env resolution (per-call, monkeypatch-friendly) ──────────────
    def backend(self) -> str:
        return _env_str("QDRANT_RERANK_BACKEND", "jev").lower() or "jev"

    def candidates(self) -> int:
        return _env_int("QDRANT_RERANK_CANDIDATES",
                        _DEFAULT_CANDIDATES, 2, 50)

    def timeout_s(self) -> float:
        return float(_env_int("QDRANT_RERANK_TIMEOUT_S",
                              int(_DEFAULT_TIMEOUT_S), 2, 60))

    def chunk_chars(self) -> int:
        return _env_int("QDRANT_RERANK_CHUNK_CHARS",
                        _DEFAULT_CHUNK_CHARS, 100, 4000)

    def min_prob(self) -> float:
        """``QDRANT_RERANK_MIN_PROB`` — the v0.9.83.1 low-confidence
        gate threshold (0.0-1.0, default 0.3; 0.0 disables the gate).

        When the BEST chunk's noul probability is below this, the
        corpus has nothing that can actually answer the query —
        live-probed 2026-09-21: an unanswerable query scored ≤0.20
        while a real match scored 0.87-0.96, so 0.3 sits cleanly in
        the gap. ``Retriever.search`` then returns NO results (the
        LLM answers "corpus has no relevant content") instead of
        feeding near-miss chunks that invite hallucination."""
        raw = (os.environ.get("QDRANT_RERANK_MIN_PROB", "") or "").strip()
        if not raw:
            return 0.3
        try:
            v = float(raw)
        except ValueError:
            logger.warning(
                "[Rerank] QDRANT_RERANK_MIN_PROB=%r is not a number — "
                "using 0.3", raw)
            return 0.3
        if v < 0.0 or v > 1.0:
            logger.warning(
                "[Rerank] QDRANT_RERANK_MIN_PROB=%r out of range "
                "[0.0, 1.0] — using 0.3", raw)
            return 0.3
        return v

    def model(self) -> str:
        return _env_str("JEV_MODEL", _DEFAULT_MODEL) or _DEFAULT_MODEL

    def enabled(self) -> bool:
        """Master switch AND backend gate — anything but ``jev`` is
        rejected (local 9B rerank = 40-100 s prefill on RDNA1)."""
        if not rerank_enabled():
            return False
        backend = self.backend()
        if backend != "jev":
            logger.warning(
                "[Rerank] QDRANT_RERANK_BACKEND=%r is not supported "
                "(local Nimble prefill = 40-100 s for 20 chunks on "
                "RDNA1) — rerank disabled, cosine order kept", backend)
            return False
        return True

    # ── HTTP (tiny wrapper — tests monkeypatch this) ─────────────────
    def _post(self, body: dict) -> tuple[dict, str]:
        """POST the decisions request via the shared module-level
        helper (tests stub ``urllib.request.urlopen``)."""
        return _decisions_post(body, self.timeout_s(), self.url)

    # ── the rerank call ──────────────────────────────────────────────
    def rerank(self, query: str,
               chunks: list[dict]) -> tuple[list[dict], dict]:
        """Reorder ``chunks`` (cosine recall pool) by answerability.

        Returns ``(ordered_chunks, meta)``; on ANY failure the original
        order is returned unchanged with ``meta["reranked"] = False``.
        Each chunk gains a ``rerank_score`` (0..1 noul probability;
        missing answers count as 0.0).
        """
        meta = {"backend": self.backend(), "reranked": False,
                "error": "", "elapsed_s": 0.0}
        if not self.enabled() or len(chunks) < 2:
            meta["backend"] = "none" if not self.enabled() else self.backend()
            return list(chunks), meta
        n = len(chunks)
        char_cap = self.chunk_chars()
        state = {
            "query": query,
            "chunks": [
                "%d. %s" % (i + 1, str(c.get("text") or "")[:char_cap])
                for i, c in enumerate(chunks)
            ],
        }
        questions = {
            "chunk_%d" % (i + 1): {
                "type": "noul",
                "instructions": _INSTRUCTIONS.format(i=i + 1),
            }
            for i in range(n)
        }
        body: dict[str, Any] = {
            "model": self.model(), "state": state, "questions": questions,
        }
        t0 = time.time()
        data, err = self._post(body)
        meta["elapsed_s"] = round(time.time() - t0, 3)
        if err or not isinstance(data, dict):
            meta["error"] = err or "empty response"
            logger.info(
                "[Rerank] backend=%s FAILED (%.200s) — cosine order kept",
                meta["backend"], meta["error"])
            return list(chunks), meta
        answers = data.get("answers") or {}
        scored: list[dict] = []
        for i, c in enumerate(chunks):
            out = dict(c)
            out["rerank_score"] = _noul(answers, "chunk_%d" % (i + 1))
            scored.append(out)
        # Stable sort — equal probabilities keep the cosine order.
        scored.sort(key=lambda d: -float(d["rerank_score"]))
        meta["reranked"] = True
        meta["usage"] = data.get("usage") or {}
        logger.info(
            "[Rerank] backend=%s reordered %d chunks in %.2fs (top=%.2f)",
            meta["backend"], n, meta["elapsed_s"],
            scored[0]["rerank_score"])
        return scored, meta


def _noul(answers: dict, key: str) -> float:
    """Extract the noul probability for one question id (0.0 when the
    answer is missing/malformed — an unanswerable verdict, not a crash)."""
    v = answers.get(key) or {}
    if isinstance(v, dict):
        for k in ("noul", "probability", "score"):
            try:
                if k in v:
                    return max(0.0, min(1.0, float(v[k])))
            except (TypeError, ValueError):
                continue
    return 0.0