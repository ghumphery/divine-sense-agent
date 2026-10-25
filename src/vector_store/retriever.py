"""Retriever - search Qdrant and return results.

v0.9.83.0: optional second-stage rerank ("rank wide, read narrow").
When ``QDRANT_RERANK=on`` the recall pool widens to
``QDRANT_RERANK_CANDIDATES`` (default 20), the Jev Decisions API
reorders it by answerability in a single call (0.4-0.8 s, ~$0.0002)
and only the top ``top_k`` survive. Default (feature off) is the
byte-identical v0.9.82 path. Any rerank failure keeps the cosine
order — see :class:`src.agent.rerank.RerankClient`.

v0.9.83.1: low-confidence anti-hallucination gate — when the BEST
rerank probability is under ``QDRANT_RERANK_MIN_PROB`` (default 0.3,
0 disables), ``search`` returns NO results: the corpus has nothing
that can answer the query, and feeding near-miss chunks would only
invite hallucination. The rerank diagnostics of the LAST search are
kept on ``Retriever.last_rerank_meta`` for callers that want them.

v0.9.86.0: query-rewrite gate (Agentic-RAG slice ①) — when
``QDRANT_REWRITE=on`` ONE Jev call may decide the question needs a
retrieval-optimised rewrite (口語長句/多跳) and ONE cheap chat
completion produces it before embedding; shadow logs the would-be
rewrite without applying it; failures always keep the original query.
Diagnostics land on ``Retriever.last_rewrite_meta``.
"""
from __future__ import annotations

import logging
import os

from src.config import config
from src.vector_store.qdrant_client import QdrantStore

logger = logging.getLogger("qdrant.retriever")


class Retriever:
    """Retrieve relevant document chunks from Qdrant."""

    def __init__(self):
        self.store = QdrantStore()
        # Diagnostics of the last search's rerank stage (v0.9.83.1):
        # None until a rerank actually ran; {"reranked": ..., "gate": ...}.
        self.last_rerank_meta: dict | None = None

    def search(
        self,
        query: str,
        top_k: int = 5,
        filename_filter: str | None = None,
        min_score: float = 0.1,
    ) -> list[dict]:
        """Search for chunks similar to the query.

        With ``QDRANT_RERANK=on`` the fetch widens to the rerank
        candidate pool, the Jev noul rerank reorders it by
        answerability, and only ``top_k`` survive. If even the best
        chunk scores under ``QDRANT_RERANK_MIN_PROB`` the search
        returns an EMPTY list (low-confidence gate — v0.9.83.1).
        Failures degrade to the plain cosine top-``top_k`` (original
        behaviour).
        """
        from src.ingest.embedder import Embedder

        # v0.9.86.0 — query-rewrite gate (Agentic-RAG slice ①): ONE
        # Jev call may rewrite the question into a retrieval-optimised
        # form before embedding; off by default, fail-open.
        try:
            from src.agent.query_rewrite import maybe_rewrite

            query, self.last_rewrite_meta = maybe_rewrite(query)
        except Exception:
            pass

        embedder = Embedder()
        query_emb = embedder.embed(query)

        # v0.9.83.0 — widen the recall pool when rerank is on.
        fetch_k = top_k
        reranker = None
        try:
            from src.agent.rerank import RerankClient

            reranker = RerankClient()
            if reranker.enabled():
                fetch_k = max(int(top_k), reranker.candidates())
        except Exception:  # never let rerank wiring break the search
            reranker = None

        results = self.store.search(
            query_vector=query_emb,
            top_k=fetch_k,
            filename_filter=filename_filter,
            score_threshold=min_score,
            query_text=query,  # v0.9.87.0 — hybrid sparse leg input
        )

        # v0.9.113.0 — STAIR heading boost (opt-in, default OFF): hits
        # whose ``heading_path`` overlaps the query's tokens get a float
        # bonus and the list is re-sorted by adjusted score. Applied
        # BEFORE the rerank stage, so when rerank is on the (more
        # precise) Jev ordering wins. Off = byte-identical order.
        results = self.apply_heading_boost(query, results)

        if reranker is not None and reranker.enabled() and len(results) > 1:
            try:
                results, meta = reranker.rerank(query, results)
                self.last_rerank_meta = dict(meta)
                if meta.get("reranked") and results:
                    top_p = float(results[0].get("rerank_score") or 0)
                    min_p = reranker.min_prob()
                    if min_p > 0 and top_p < min_p:
                        # v0.9.83.1 anti-hallucination gate: nothing in
                        # the corpus can answer — return nothing instead
                        # of near-miss chunks.
                        logger.info(
                            "[Retriever] rerank low-confidence gate: "
                            "best=%.2f < min_prob=%.2f — returning 0 "
                            "results (cosine order withheld)", top_p, min_p)
                        self.last_rerank_meta = {
                            **meta, "gate": "low-confidence-empty",
                            "best_score": top_p, "min_prob": min_p,
                        }
                        return []
            except Exception as e:  # cosine order is the floor
                self.last_rerank_meta = {"reranked": False,
                                         "error": repr(e)}
                logger.warning("[Retriever] rerank failed (%.200r)", e)
            results = results[:top_k]
        return results

    # ── v0.9.113.0 — STAIR heading boost ──────────────────────────────
    @staticmethod
    def heading_boost_value() -> float:
        """``QDRANT_HEADING_BOOST`` (float, default 0 = OFF).

        Applied as ``adjusted = cosine + boost`` for hits whose
        ``heading_path`` overlaps the query (see ``apply_heading_boost``).
        Malformed values fall back to 0 (never raises)."""
        raw = (os.environ.get("QDRANT_HEADING_BOOST", "") or "").strip()
        if not raw:
            return 0.0
        try:
            return float(raw)
        except ValueError:
            return 0.0

    @staticmethod
    def _heading_overlap(heading_path: str, query: str) -> bool:
        """Bidirectional substring/token overlap (lightweight MVP).

        True when the heading path appears in the query, or any
        whitespace token of the query appears in the heading path
        (both lower-cased). CJK query without spaces = one token, so
        the hit pattern is keyword-phrase-like by design."""
        hp = (heading_path or "").strip().lower()
        if not hp:
            return False
        q = (query or "").strip().lower()
        if not q:
            return False
        if hp in q:
            return True
        return any(t and t in hp for t in q.split())

    def apply_heading_boost(self, query: str, results: list[dict]) -> list[dict]:
        """Resort ``results`` by ``cosine + boost`` for heading-overlapping
        hits. Always returns the same list (mutated order only); original
        cosine score preserved on ``score`` with the bonus visible on
        ``heading_boost``. No-op when the boost env is unset/zero."""
        boost = self.heading_boost_value()
        if boost <= 0 or not results:
            return results
        for h in results:
            # raw store-search dicts carry ``payload``; the mapped hit dicts
            # carried into the rerank path carry ``meta`` — read both.
            hp = str((((h.get("payload") or {}).get("heading_path"))
                      or ((h.get("meta") or {}).get("heading_path")) or ""))
            bump = boost if self._heading_overlap(hp, query) else 0.0
            h["heading_boost"] = bump
        results.sort(key=lambda h: h.get("score", 0.0) + h.get("heading_boost", 0.0),
                     reverse=True)
        return results