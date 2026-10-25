"""LogStore — Qdrant collection dedicated to log entries (v0.9.7+).

Structurally parallel to :class:`src.vector_store.qdrant_client.QdrantStore`
but pointed at its own collection (``QDRANT_LOG_COLLECTION``, default
``log_docs``) so log points never mix with document RAG chunks:

* One log line == one point. Log lines are short; per-line embedding
  is what makes semantic search ("找出所有 SQL injection 嘗試") and
  semantic outlier detection possible.
* Point IDs are ``sha1(source + raw_line)`` — deterministic, so
  re-ingesting the same file *overwrites* the same points instead of
  duplicating them (upsert semantics, mirrors the pipeline's
  dedup-by-content-hash philosophy).
* Payload indexes on ``source`` / ``log_type`` / ``level`` /
  ``src_ip`` (keyword) + ``status`` (integer) + ``ts_epoch`` (float)
  keep filtered analytics O(log n).

The class is intentionally small: ingest / search / scroll / count /
delete / distinct-sources. All the anomaly *interpretation* lives in
:mod:`src.log_analyzer.anomalies`; all the parsing in
:mod:`src.log_analyzer.parser`.
"""
from __future__ import annotations

import hashlib
import os
import sys as _sys
import threading
import uuid
from typing import Any

from qdrant_client.http import models as qmodels

from src.config import config

__all__ = ["LogStore"]


class LogStore:
    """Thin Qdrant wrapper for one-line-one-point log collections."""

    _write_lock = threading.Lock()

    def __init__(self, url: str | None = None, collection: str | None = None):
        self.url = url or config.QDRANT_URL
        self.collection = collection or os.environ.get(
            "QDRANT_LOG_COLLECTION", "log_docs",
        )
        self.dim = config.EMBEDDING_DIM
        self._client: Any | None = None

    # ─── connection lifecycle ────────────────────────────────────
    @property
    def client(self) -> Any:
        if self._client is None:
            self._connect()
        return self._client

    def _connect(self) -> None:
        from qdrant_client import QdrantClient

        kwargs: dict = {"url": self.url, "timeout": 30}
        if config.QDRANT_API_KEY:
            kwargs["api_key"] = config.QDRANT_API_KEY
        print(
            f"  [LogStore] Connecting to: {self.url} "
            f"(collection={self.collection})",
            file=_sys.stderr,
        )
        self._client = QdrantClient(**kwargs)
        self._ensure_collection()

    def _ensure_collection(self) -> None:
        c = self.client
        if not c.collection_exists(self.collection):
            print(
                f"  [LogStore] Collection '{self.collection}' not found; "
                f"creating with dim={self.dim} (HNSW + Cosine).",
                file=_sys.stderr,
            )
            c.create_collection(
                collection_name=self.collection,
                vectors_config=qmodels.VectorParams(
                    size=self.dim,
                    distance=qmodels.Distance.COSINE,
                ),
                hnsw_config=qmodels.HnswConfigDiff(
                    m=16,
                    ef_construct=100,
                ),
                optimizers_config=qmodels.OptimizersConfigDiff(
                    default_segment_number=2,
                ),
            )
        # Payload indexes for the analytics filters (idempotent).
        for field, schema in (
            ("source", qmodels.PayloadSchemaType.KEYWORD),
            ("log_type", qmodels.PayloadSchemaType.KEYWORD),
            ("level", qmodels.PayloadSchemaType.KEYWORD),
            ("src_ip", qmodels.PayloadSchemaType.KEYWORD),
            ("status", qmodels.PayloadSchemaType.INTEGER),
            ("ts_epoch", qmodels.PayloadSchemaType.FLOAT),
        ):
            try:
                c.create_payload_index(
                    collection_name=self.collection,
                    field_name=field,
                    field_schema=schema,
                )
            except Exception:
                # Index already exists (or server too old) — harmless.
                pass

    # ─── ids ─────────────────────────────────────────────────────
    @staticmethod
    def make_entry_id(source: str, raw_line: str) -> str:
        """Stable point id: UUID built from sha1(source + raw line).

        Qdrant point IDs must be an unsigned integer or a UUID — a
        bare truncated hex string is rejected with HTTP 400
        ("value ... is not a valid point ID", v0.9.9.1 regression),
        so the first 32 hex chars of the sha1 digest are formatted
        as a canonical UUID string. Deterministic → re-ingesting the
        same file overwrites the same points (upsert semantics).
        """
        h = hashlib.sha1(f"{source}\n{raw_line}".encode("utf-8"))
        return str(uuid.UUID(h.hexdigest()[:32]))

    # ─── filter builder ──────────────────────────────────────────
    def _build_filter(
        self,
        source: str | None = None,
        log_type: str | None = None,
        level: str | None = None,
        min_status: int | None = None,
    ) -> qmodels.Filter | None:
        must: list[Any] = []
        if source:
            must.append(qmodels.FieldCondition(
                key="source", match=qmodels.MatchValue(value=source),
            ))
        if log_type:
            must.append(qmodels.FieldCondition(
                key="log_type", match=qmodels.MatchValue(value=log_type),
            ))
        if level:
            must.append(qmodels.FieldCondition(
                key="level", match=qmodels.MatchValue(value=level),
            ))
        if min_status is not None and min_status > 0:
            must.append(qmodels.FieldCondition(
                key="status",
                range=qmodels.Range(gte=float(min_status)),
            ))
        return qmodels.Filter(must=must) if must else None

    # ─── ingest / search ─────────────────────────────────────────
    def upsert_entries(
        self,
        entries: list[dict[str, Any]],
        source: str,
        vectors: list[list[float]],
        batch_size: int = 256,
    ) -> int:
        """Upsert parsed log entries with their embeddings.

        ``entries`` are parser payloads; ``vectors`` are the matching
        embedding matrix (same order, same length). Point ids come
        from :meth:`make_entry_id` so a re-ingest of the same file
        replaces rather than duplicates.
        """
        if not entries:
            return 0
        points = [
            qmodels.PointStruct(
                id=self.make_entry_id(source, e.get("raw", "")),
                vector=vec,
                payload={**e, "source": source},
            )
            for e, vec in zip(entries, vectors)
        ]
        with self._write_lock:
            for start in range(0, len(points), batch_size):
                self.client.upsert(
                    collection_name=self.collection,
                    points=points[start:start + batch_size],
                    wait=True,
                )
        return len(points)

    def search(
        self,
        query_vector: list[float],
        top_k: int = 10,
        source: str | None = None,
        log_type: str | None = None,
        level: str | None = None,
        min_status: int | None = None,
        score_threshold: float = 0.2,
    ) -> list[dict[str, Any]]:
        """Semantic search over log lines with optional payload filters."""
        qfilter = self._build_filter(source, log_type, level, min_status)
        c = self.client
        if hasattr(c, "query_points"):
            resp = c.query_points(
                collection_name=self.collection,
                query=query_vector,
                limit=top_k,
                with_payload=True,
                with_vectors=False,
                query_filter=qfilter,
                score_threshold=score_threshold,
            )
            hits = resp.points
        else:  # pragma: no cover — older qdrant-client
            hits = c.search(
                collection_name=self.collection,
                query_vector=query_vector,
                limit=top_k,
                with_payload=True,
                with_vectors=False,
                query_filter=qfilter,
                score_threshold=score_threshold,
            )
        out: list[dict[str, Any]] = []
        for hit in hits:
            payload = getattr(hit, "payload", None) or {}
            out.append({
                "id": getattr(hit, "id", ""),
                "score": getattr(hit, "score", 0.0),
                **payload,
            })
        return out
    # ─── scroll / count / delete / sources ───────────────────────
    def scroll_points(
        self,
        source: str | None = None,
        log_type: str | None = None,
        level: str | None = None,
        min_status: int | None = None,
        limit: int = 10000,
        with_vectors: bool = False,
    ) -> list[dict[str, Any]]:
        """Scroll matching points (payload [+ vectors]) in one loop.

        Capped at ``limit`` — the caller (analyze_anomalies) passes
        ``LOG_ANOMALY_MAX_POINTS`` so a huge collection can't hang
        the analysis run.
        """
        qfilter = self._build_filter(source, log_type, level, min_status)
        out: list[dict[str, Any]] = []
        offset = None
        while len(out) < limit:
            page_cap = min(1000, limit - len(out))
            results, offset = self.client.scroll(
                collection_name=self.collection,
                scroll_filter=qfilter,
                limit=page_cap,
                with_payload=True,
                with_vectors=with_vectors,
                offset=offset,
            )
            for r in results:
                row: dict[str, Any] = dict(r.payload or {})
                if with_vectors:
                    row["_vector"] = list(r.vector or [])
                out.append(row)
            if offset is None:
                break
        return out

    def count_points(
        self,
        source: str | None = None,
        log_type: str | None = None,
    ) -> int:
        try:
            res = self.client.count(
                collection_name=self.collection,
                count_filter=self._build_filter(source, log_type),
                exact=True,
            )
            return int(getattr(res, "count", 0) or 0)
        except Exception:
            return -1

    def delete_by_source(self, source: str) -> int:
        """Delete all points from one source file. Returns count."""
        before = self.count_points(source=source)
        if before == 0:
            return 0
        self.client.delete(
            collection_name=self.collection,
            points_selector=qmodels.FilterSelector(
                filter=qmodels.Filter(
                    must=[qmodels.FieldCondition(
                        key="source",
                        match=qmodels.MatchValue(value=source),
                    )],
                ),
            ),
        )
        return before if before > 0 else -1

    def distinct_sources(self) -> list[dict[str, Any]]:
        """List ingested log sources with point counts.

        Prefers the native group-by endpoint (fast, walks the payload
        index) and falls back to a scroll aggregation on old servers.
        """
        c = self.client
        if hasattr(c, "query_points_groups"):
            try:
                resp = c.query_points_groups(
                    collection_name=self.collection,
                    group_by="source",
                    group_size=1,
                    limit=20_000,
                    with_payload=False,
                    with_vectors=False,
                )
                out = []
                for g in (getattr(resp, "groups", None) or []):
                    gid = getattr(g, "id", None)
                    if not gid:
                        continue
                    hits = getattr(g, "hits", None) or []
                    cnt = getattr(hits[0], "count", 0) if hits else 0
                    out.append({"source": gid, "count": cnt})
                if out:
                    return sorted(out, key=lambda r: r["source"])
            except Exception:
                pass
        # Fallback: full payload scroll + python-side counting.
        counts: dict[str, int] = {}
        for row in self.scroll_points(limit=50000):
            src = row.get("source", "")
            if src:
                counts[src] = counts.get(src, 0) + 1
        return [
            {"source": s, "count": c}
            for s, c in sorted(counts.items())
        ]