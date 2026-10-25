"""Query-rewrite gate (v0.9.86.0) — the Agentic-RAG「查詢改寫」slice.

The raw user question goes straight to the embedding today; 口語化/
多跳問題的檢索品質因此受限. ONE jev_noul call (~0.25 s, ~$0.0002)
decides whether the question needs a retrieval-optimised rewrite;
when it does, ONE cheap chat completion produces the rewritten query
(Jev classifies, the LLM generates — same split as the router).

off (default) | shadow (rewrite computed + logged, original used) | on.
Every failure returns the ORIGINAL query (fail-open) — retrieval must
never break because the gate hiccupped.
"""
from __future__ import annotations

import logging
import os

import requests

logger = logging.getLogger("qdrant.rewrite")

_MIN_QUERY_CHARS = 12  # keyword-ish short queries never need rewriting
_REWRITE_SYSTEM = (
    "把使用者的問題改寫成適合向量檢索的查詢：保留原意、補上關鍵詞/"
    "產品或股票代號、去掉寒暄與贅字。只輸出改寫後的查詢本身，"
    "不要解釋。"
)
_OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"


def rewrite_mode() -> str:
    raw = (os.environ.get("QDRANT_REWRITE", "") or "").strip().lower()
    return raw if raw in ("off", "shadow", "on") else "off"


def _rewrite_prob() -> float:
    raw = (os.environ.get("QDRANT_REWRITE_PROB", "") or "").strip()
    if not raw:
        return 0.6
    try:
        v = float(raw)
    except ValueError:
        return 0.6
    if v < 0.0 or v > 1.0:
        return 0.6
    return v


def _rewrite_model() -> str:
    return (os.environ.get("QDRANT_REWRITE_MODEL", "")
            or "openai/gpt-4o-mini")


def _llm_rewrite(query: str) -> str:
    """ONE cheap chat completion that rewrites the question. Returns
    ``""`` on any failure (caller keeps the original)."""
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if not key:
        return ""
    try:
        resp = requests.post(
            _OPENROUTER_CHAT_URL,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json={
                "model": _rewrite_model(),
                "messages": [
                    {"role": "system", "content": _REWRITE_SYSTEM},
                    {"role": "user", "content": query},
                ],
                "max_tokens": 200,
                "temperature": 0,
            },
            timeout=30.0,
        )
        resp.raise_for_status()
        msg = (resp.json().get("choices") or [{}])[0].get("message") or {}
        rewritten = str(msg.get("content") or "").strip().splitlines()
        rewritten = next((l.strip() for l in reversed(rewritten)
                          if l.strip()), "")
        # strip wrapping quotes the model sometimes adds
        return rewritten.strip("「」\"'")[:300]
    except Exception as e:
        logger.warning("[Rewrite] LLM rewrite failed (%.150r)", e)
        return ""


def maybe_rewrite(query: str) -> tuple[str, dict]:
    """The gate. Returns ``(query_to_use, meta)`` — the original query
    unless the gate FIRES (mode=on + Jev p ≥ prob + a usable LLM
    rewrite). Shadow logs the would-be rewrite without applying it."""
    mode = rewrite_mode()
    if mode == "off" or not query or len(query.strip()) < _MIN_QUERY_CHARS:
        return query, {"mode": mode, "skipped": True}
    try:
        from src.agent.rerank import jev_noul

        p, meta = jev_noul(
            "這個使用者問題是否值得在檢索前先改寫成更適合向量檢索的"
            "形式？（簡短關鍵詞、已有明確代號/名詞 → false；口語長句、"
            "多跳、含寒暄贅字 → true）",
            str(query or "")[:1500],
        )
    except Exception as e:  # never break retrieval
        logger.warning("[Rewrite] jev gate failed (%.150r)", e)
        return query, {"mode": mode, "error": repr(e)}
    if p is None:
        return query, {"mode": mode, "error": "jev-failed"}
    fired = mode == "on" and p >= _rewrite_prob()
    out_meta = {"mode": mode, "p": p, "fired": False,
                "elapsed_s": meta.get("elapsed_s", 0)}
    if p < _rewrite_prob():
        if mode == "shadow":
            logger.info("[Rewrite] jev_p=%.2f (no rewrite) q=%.60r",
                        p, query)
        return query, out_meta
    # p ≥ prob — compute the would-be rewrite (needed for BOTH the
    # shadow log and the on-mode application).
    rewritten = _llm_rewrite(query)
    out_meta["rewritten"] = rewritten
    if not rewritten:
        return query, out_meta
    if mode == "shadow":
        logger.info("[Rewrite] jev_p=%.2f SHADOW orig=%.80r new=%.80r",
                    p, query, rewritten)
        return query, out_meta
    logger.info("[Rewrite] applied p=%.2f orig=%.60r new=%.60r",
                p, query, rewritten)
    out_meta["fired"] = True
    return rewritten, out_meta