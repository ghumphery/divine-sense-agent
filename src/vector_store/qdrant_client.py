"""Qdrant vector store client wrapper.

Uses `qdrant-client` 1.x. Talks to a Qdrant server over HTTP (default
`localhost:6333`). Compared to Milvus Lite, Qdrant gives us:

  - True server: no per-process file lock; multiple clients in parallel
  - HNSW + payload filtering (similar to Milvus IVF + expr filter)
  - GPU/HNSW acceleration via SIMD on native, scalar fallback in Docker
  - Persistent storage without exclusive locking

Reference (qdrant-client 1.x):
  https://python-client.qdrant.tech/

This module is the *structural replacement* for `milvus_client.py` from the
sibling `milvus-mcp-agent` project.
"""
from __future__ import annotations

import hashlib
import sys as _sys
import threading
import time
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

from src.config import config


def _hybrid_enabled() -> bool:
    try:
        from src.vector_store.sparse import hybrid_enabled

        return hybrid_enabled()
    except Exception:
        return False


class QdrantStore:
    """Thin wrapper around `QdrantClient` that keeps a stable API for the
    rest of the project (ingest pipeline, retriever, MCP server).

    All writes go through `self._lock` because Qdrant's HTTP client is
    thread-safe for reads but writes (especially upserts with payload)
    benefit from serialization to keep the response order predictable.
    """

    _write_lock = threading.Lock()

    def __init__(self, url: str | None = None, collection: str | None = None):
        self.url = url or config.QDRANT_URL
        self.collection = collection or config.QDRANT_COLLECTION
        self.dim = config.EMBEDDING_DIM
        self._client: QdrantClient | None = None
        # Per-instance list_documents cache (v0.6.1+). Lives for the
        # lifetime of this store object. The pipeline calls
        # ``invalidate_list_cache()`` after a successful upsert so the
        # Stats tab sees fresh data without restarting the process.
        self._list_cache_value: list[str] | None = None
        self._list_cache_ts: float = 0.0
        # Same idea for get_stats — separate cache slot so the two
        # can have different invalidation policies (a write refreshes
        # both, but `list_documents` could one day be invalidated on
        # disk failure only).
        self._stats_cache_value: dict | None = None
        self._stats_cache_ts: float = 0.0

    # ─── connection lifecycle ────────────────────────────────────
    @property
    def client(self) -> QdrantClient:
        if self._client is None:
            self._connect()
        return self._client

    def _connect(self) -> None:
        kwargs: dict = {"url": self.url, "timeout": 30}
        if config.QDRANT_API_KEY:
            kwargs["api_key"] = config.QDRANT_API_KEY
        print(f"  [Qdrant] Connecting to: {self.url}", file=_sys.stderr)
        self._client = QdrantClient(**kwargs)
        # Ensure collection exists with the right vector config.
        self._ensure_collection()

    def _ensure_collection(self) -> None:
        c = self.client
        if not c.collection_exists(self.collection):
            print(
                f"  [Qdrant] Collection '{self.collection}' not found; "
                f"creating with dim={self.dim} (HNSW + Cosine).",
                file=_sys.stderr,
            )
            create_kwargs: dict = dict(
                collection_name=self.collection,
                vectors_config=qmodels.VectorParams(
                    size=self.dim,
                    distance=qmodels.Distance.COSINE,
                ),
                # HNSW config: good defaults for 10K-100K vectors.
                # `m=16` is the standard HNSW M; ef_construct=100 trades
                # build time for recall.
                hnsw_config=qmodels.HnswConfigDiff(
                    m=16,
                    ef_construct=100,
                ),
                # Per-payload indexes for the keyword-fallback filter.
                # Without these, Qdrant falls back to a full scan.
                optimizers_config=qmodels.OptimizersConfigDiff(
                    default_segment_number=2,
                ),
            )
            # v0.9.87.0 — hybrid search: create the named sparse (BM25,
            # server-side IDF) vector field when QDRANT_HYBRID=on, so
            # ingest stores a keyword leg alongside the dense embedding
            # and search can fuse both legs with RRF. Dense-only schema
            # is the floor — a sparse-config failure must not block
            # creation.
            if _hybrid_enabled():
                try:
                    from src.vector_store.sparse import (
                        SPARSE_FIELD, make_sparse_config,
                    )

                    create_kwargs["sparse_vectors_config"] = {
                        SPARSE_FIELD: make_sparse_config(),
                    }
                except Exception as e:  # dense-only schema is the floor
                    print(f"  [Qdrant] sparse config skipped: {e!r:.120}",
                          file=_sys.stderr)
            c.create_collection(**create_kwargs)
            # Create payload indexes for filename and content_hash so
            # `text like` / `filename == X` filters are fast.
            # v0.9.113.0 — heading_path (STAIR) joins the same fast set.
            for field, schema in (
                ("filename", qmodels.PayloadSchemaType.KEYWORD),
                ("source", qmodels.PayloadSchemaType.KEYWORD),
                ("content_hash", qmodels.PayloadSchemaType.KEYWORD),
                ("heading_path", qmodels.PayloadSchemaType.KEYWORD),
            ):
                c.create_payload_index(
                    collection_name=self.collection,
                    field_name=field,
                    field_schema=schema,
                )
        # Qdrant does not have an explicit `load_collection` (always loaded).
        # No-op kept here for parity with milvus_client API.

    # ─── ingest / search / query ─────────────────────────────────
    def upsert(self, points: list[dict[str, Any]]) -> None:
        """Upsert points to Qdrant.

        Each `point` must contain: id, vector, payload. Payload is a
        free-form dict (filename, text, source, content_hash, etc.).
        """
        if not points:
            return
        with self._write_lock:
            structured = [
                qmodels.PointStruct(
                    id=str(p["id"]),
                    vector=p["vector"],
                    payload=p.get("payload", {}),
                )
                for p in points
            ]
            # v0.9.87.0 — hybrid ingest: when hybrid is on AND the
            # collection schema carries the sparse field, send BOTH
            # legs (dense + bm25 bag-of-words from the payload text).
            if _hybrid_enabled():
                try:
                    from src.vector_store.sparse import (
                        SPARSE_FIELD, collection_has_sparse,
                        sparse_from_payload,
                    )

                    if collection_has_sparse(
                            self.client, self.collection):
                        for st in structured:
                            indices, values = sparse_from_payload(
                                st.payload)
                            if indices:
                                st.vector = {
                                    "": st.vector,
                                    SPARSE_FIELD: qmodels.SparseVector(
                                        indices=indices, values=values),
                                }
                except Exception as e:
                    print(f"  [Qdrant] hybrid upsert degraded to dense: "
                          f"{e!r:.120}", file=_sys.stderr)
            self.client.upsert(
                collection_name=self.collection,
                points=structured,
                wait=True,
            )
            # v0.6.1+: any successful write may introduce a new
            # filename (or delete one if the caller does delete +
            # re-ingest in one go). Drop the list_documents cache so
            # the next Stats refresh reflects reality immediately.
            self.invalidate_list_cache()

    def search(
        self,
        query_vector: list[float],
        top_k: int = 5,
        filename_filter: str | None = None,
        score_threshold: float = 0.0,
        query_text: str | None = None,
    ) -> list[dict[str, Any]]:
        """Vector search. Returns list of dicts with id/score/payload.

        qdrant-client >=1.10 deprecated ``client.search()`` in favor of
        ``client.query_points(query=...)``. We use the new API here, but
        accept either result shape so older clients would also work.

        v0.9.87.0 — hybrid search: when ``query_text`` is provided AND
        ``QDRANT_HYBRID=on`` AND the collection schema carries the
        ``bm25`` sparse field, the search runs BOTH legs (dense +
        BM25-ish sparse) fused with RRF in one ``query_points`` call.
        Any failure degrades to the plain dense search (original
        behaviour); RRF rank-scores replace the cosine threshold, so
        ``score_threshold`` is skipped in fused mode (the downstream
        rerank stage does the real grading).
        """
        qfilter = None
        if filename_filter:
            qfilter = qmodels.Filter(
                must=[
                    qmodels.FieldCondition(
                        key="filename",
                        match=qmodels.MatchValue(value=filename_filter),
                    ),
                ],
            )
        # v0.9.87.0 — try the hybrid RRF fusion first.
        if query_text and _hybrid_enabled():
            try:
                from src.vector_store.sparse import (
                    SPARSE_FIELD, collection_has_sparse,
                    sparse_vector,
                )

                if collection_has_sparse(self.client, self.collection):
                    indices, values = sparse_vector(query_text or "")
                    if indices:
                        prefetch = [
                            qmodels.Prefetch(
                                query=query_vector,
                                limit=max(top_k * 2, 20),
                                using="",
                            ),
                            qmodels.Prefetch(
                                query=qmodels.SparseVector(
                                    indices=indices, values=values),
                                limit=max(top_k * 2, 20),
                                using=SPARSE_FIELD,
                            ),
                        ]
                        resp = self.client.query_points(
                            collection_name=self.collection,
                            prefetch=prefetch,
                            query=qmodels.FusionQuery(
                                fusion=qmodels.Fusion.RRF,
                            ),
                            limit=top_k,
                            with_payload=True,
                            with_vectors=False,
                            query_filter=qfilter,
                        )
                        return self._map_hits(getattr(resp, "points", []))
            except Exception as e:
                # fail-open: any fusion error falls back to dense-only
                print(f"  [Qdrant] hybrid fusion failed → dense fallback: "
                      f"{e!r:.140}", file=_sys.stderr)
        # Prefer the new query_points API (qdrant-client 1.10+).
        if hasattr(self.client, "query_points"):
            resp = self.client.query_points(
                collection_name=self.collection,
                query=query_vector,
                limit=top_k,
                with_payload=True,
                with_vectors=False,
                query_filter=qfilter,
                score_threshold=score_threshold,
            )
            results = resp.points
        else:  # pragma: no cover — older client fallback
            results = self.client.search(
                collection_name=self.collection,
                query_vector=query_vector,
                limit=top_k,
                with_payload=True,
                with_vectors=False,
                query_filter=qfilter,
                score_threshold=score_threshold,
            )
        return self._map_hits(results)

    def _map_hits(self, results: list) -> list[dict[str, Any]]:
        """Shared hit → dict mapping (dense AND fused paths)."""
        out: list[dict[str, Any]] = []
        # v0.9.60.3 — cross-collection result mapping: non-RAG
        # collections carry different payload shapes (log_docs =
        # message/raw_line/level, memory_docs = content/kind/stored_at,
        # stock_docs = text/symbol/tool), so each hit derives a
        # sensible display label + snippet + meta chips instead of the
        # file_docs-only "filename" (which rendered as 「#1 unknown」).
        text_keys = ("text", "message", "content", "raw_line", "summary")
        name_keys = ("filename", "source", "symbol", "title")
        meta_keys = ("kind", "stored_at", "ts", "level", "log_type",
                     "tool", "symbol", "session_id", "heading_path")
        for hit in results:
            payload = getattr(hit, "payload", None) or {}

            def _first(keys: tuple[str, ...]) -> str:
                for k in keys:
                    v = payload.get(k)
                    if isinstance(v, str) and v.strip():
                        return v.strip()
                return ""

            text = _first(text_keys)
            name = _first(name_keys)
            if not name:
                kind = payload.get("kind") or payload.get("tool") or ""
                name = (f"{kind} #{str(hit.id)[:8]}"
                        if kind else f"#{str(hit.id)[:8]}")
            meta = {
                k: payload[k]
                for k in meta_keys
                if isinstance(payload.get(k), (str, int, float))
                and payload[k] != ""
            }
            out.append({
                "id": hit.id,
                "score": hit.score,
                "filename": name or "unknown",
                "text": text,
                "source": payload.get("source", ""),
                "content_hash": payload.get("content_hash", ""),
                "meta": meta,
            })
        return out

    def scroll_by_source(self, source: str, limit: int = 10000) -> list[dict]:
        """Scroll all chunks whose `source == source` (for dedup / delete)."""
        results, _ = self.client.scroll(
            collection_name=self.collection,
            scroll_filter=qmodels.Filter(
                must=[
                    qmodels.FieldCondition(
                        key="source",
                        match=qmodels.MatchValue(value=source),
                    ),
                ],
            ),
            limit=limit,
            with_payload=True,
            with_vectors=False,
        )
        return [{"id": r.id, **(r.payload or {})} for r in results]

    def delete_by_source(self, source: str) -> int:
        """Delete all points with the given source. Returns count deleted."""
        before = len(self.scroll_by_source(source))
        if before == 0:
            return 0
        self.client.delete(
            collection_name=self.collection,
            points_selector=qmodels.FilterSelector(
                filter=qmodels.Filter(
                    must=[
                        qmodels.FieldCondition(
                            key="source",
                            match=qmodels.MatchValue(value=source),
                        ),
                    ],
                ),
            ),
        )
        # v0.6.1+: may have wiped the last chunk for a filename.
        self.invalidate_list_cache()
        return before

    # ── public, v0.7.0+ — deletion by user-facing filename ───────────
    # The two-axis deletion strategy:
    #   * ``source``     is the absolute POSIX path. Used internally by
    #     re-ingest (delete old chunks before upserting the modified
    #     version). Two distinct paths can share a basename (e.g.
    #     ``a/report.pdf`` and ``b/report.pdf``), so deleting a path
    #     is precise but inconvenient for the user.
    #   * ``filename``   is the basename only (``report.pdf``). What
    #     humans remember; what the GUI "Indexed documents" list
    #     shows. Filename may legitimately appear more than once in
    #     the collection (if the same doc lives in two folders).
    # ``delete_by_filename`` cleans every chunk sharing that name,
    # across all source paths. Documented in ARCHITECTURE.md §2.2.
    def _delete_by_field(self, key: str, value: str) -> int:
        """Shared core for delete_by_* helpers.

        Returns the number of points deleted (0 if nothing matched,
        -1 if we couldn't determine the count due to a transient
        count() failure but the delete was still attempted). Always
        invalidates both caches when a delete actually fired, which
        is what callers downstream rely on for stats consistency.
        """
        # Pre-flight: cheaper to skip the round-trip when nothing matches.
        # The user-facing APIs already pre-filter with a scroll; this
        # helper is the workhorse so we keep the extra check here.
        try:
            count_check = self.client.count(
                collection_name=self.collection,
                count_filter=qmodels.Filter(
                    must=[
                        qmodels.FieldCondition(
                            key=key,
                            match=qmodels.MatchValue(value=value),
                        ),
                    ],
                ),
            )
            n = int(getattr(count_check, "count", 0) or 0)
            count_unknown = False
        except Exception:
            n = -1
            count_unknown = True
        if n == 0 and not count_unknown:
            return 0
        self.client.delete(
            collection_name=self.collection,
            points_selector=qmodels.FilterSelector(
                filter=qmodels.Filter(
                    must=[
                        qmodels.FieldCondition(
                            key=key,
                            match=qmodels.MatchValue(value=value),
                        ),
                    ],
                ),
            ),
        )
        self.invalidate_list_cache()
        # Return -1 when count was unknown: caller can treat as
        # "unknown — verify with get_stats() afterwards" but still
        # know the delete call succeeded.
        return -1 if count_unknown else max(n, 0)

    def delete_by_filename(self, filename: str) -> int:
        """Delete every chunk whose ``filename == filename``.

        Filename matching is exact, case-sensitive (Qdrant
        ``MatchValue`` semantics). To delete case-insensitively across
        platforms, callers should normalise upstream — basename
        returned by :pymeth:`list_documents` is already canonical
        (``Path(file).name``).
        """
        if not filename:
            return 0
        return self._delete_by_field("filename", filename)

    def delete_by_filenames(self, filenames: list[str]) -> dict[str, int]:
        """Delete multiple filenames in one call (one round-trip each).

        Returns ``{filename: deleted_count}`` with 0 entries for
        filenames that didn't exist in the collection. The order is
        preserved; failures short-circuit (subsequent filenames not
        attempted) and surface as an exception. Each successful
        delete invalidates the per-store caches, so the GUI's
        next ``refreshStats`` is fresh.

        For > ~50 filenames, prefer looping outside this method so
        a partial failure doesn't strand things half-deleted.
        """
        results: dict[str, int] = {}
        for name in filenames:
            results[name] = self.delete_by_filename(name)
        return results

    def get_existing_hashes(self, source: str) -> set[str]:
        """Return set of `content_hash` already stored for `source`."""
        rows = self.scroll_by_source(source)
        return {r.get("content_hash", "") for r in rows if r.get("content_hash")}

    # ── list_documents cache (v0.6.1+) ──
    # Used to be a full scroll loop (~168 round-trips on the 167k-pt
    # collection, blocking the GUI for minutes). Replaced with the
    # Qdrant native group-by API which builds the distinct set in ~3 s
    # by walking the payload index instead of every point's payload.
    # Cache state lives on the instance (see __init__); this constant
    # controls the TTL across the whole class.
    _LIST_CACHE_TTL = 60.0   # seconds; refreshed on every store mutation

    def list_documents(self) -> list[str]:
        """Return unique ``filename`` values in the collection, sorted.

        Implementation note
        --------------------
        Previously used ``client.scroll(limit=1000, with_payload=True)``
        in a loop until the offset drained. On the test collection that
        meant 168 scroll round-trips, each shipping 1000 full payloads
        (including chunk text), which made the GUI Stats tab time out.

        Now we use ``query_points_groups(group_by='filename',
        group_size=1)`` which is O(distinct values) — Qdrant walks the
        ``filename`` payload index, returning one entry per unique
        value. Result for the 167k-pt test collection: 174 entries in
        ~3 s instead of minutes.

        The result is cached for ``_LIST_CACHE_TTL`` so a tab switch
        doesn't re-fetch within a minute.
        """
        now = time.time()
        if (
            self._list_cache_value is not None
            and now - self._list_cache_ts < self._LIST_CACHE_TTL
        ):
            return list(self._list_cache_value)

        filenames: list[str] = []
        # Prefer the native group-by endpoint (qdrant 1.10+, server
        # 1.7+). Returns groups whose ``id`` is the distinct payload
        # value; ``group_size=1`` keeps the payload transfer minimal.
        if hasattr(self.client, "query_points_groups"):
            try:
                resp = self.client.query_points_groups(
                    collection_name=self.collection,
                    group_by="filename",
                    group_size=1,
                    limit=20_000,           # safety cap; 174 fits in 1 page
                    with_payload=False,     # we only want the id
                    with_vectors=False,
                )
                groups = getattr(resp, "groups", None) or []
                filenames = [
                    g.id for g in groups
                    if getattr(g, "id", None)
                ]
            except Exception:
                # Server doesn't support group-by (older build) or the
                # collection is empty. Fall through to a minimal
                # single-page scroll so we still return *something*.
                filenames = self._list_documents_scroll_fallback()

        else:  # pragma: no cover — qdrant-client < 1.10
            filenames = self._list_documents_scroll_fallback()

        filenames.sort(key=lambda s: s.lower())
        self._list_cache_value = list(filenames)
        self._list_cache_ts = now
        return list(filenames)

    def _list_documents_scroll_fallback(self) -> list[str]:
        """Last-resort scroll loop. Slow but works on old servers."""
        seen: set[str] = set()
        out: list[str] = []
        offset = None
        # Hard cap so a runaway collection doesn't hang the GUI forever.
        page = 0
        while page < 50:  # 50 * 1000 = 50k points cap
            results, offset = self.client.scroll(
                collection_name=self.collection,
                limit=1000,
                with_payload=True,
                with_vectors=False,
                offset=offset,
            )
            for r in results:
                fn = (r.payload or {}).get("filename", "")
                if fn and fn not in seen:
                    seen.add(fn)
                    out.append(fn)
            if offset is None:
                break
            page += 1
        return out

    def invalidate_list_cache(self) -> None:
        """Drop both documents + stats caches so the next call re-fetches.

        Called by the ingest pipeline after a successful write, and by
        the destructive collection ops (delete_by_source,
        delete_collection). The GUI's refreshStats button takes a
        separate path: it just shows whatever the API returns, with
        at most a TTL window of staleness — much smaller than the
        old "hang the whole UI for minutes".
        """
        self._list_cache_value = None
        self._list_cache_ts = 0.0
        self._stats_cache_value = None
        self._stats_cache_ts = 0.0

    def get_stats(self) -> dict:
        """Return collection-level stats.

        Cached for ``_LIST_CACHE_TTL`` because Qdrant's
        ``get_collection`` call (which provides ``points_count``)
        takes ~2 s on a busy collection (optimiser / background
        indexing). Without a cache, every Stats tab switch re-paid
        that 2 s even when nothing changed.
        """
        now = time.time()
        if (
            self._stats_cache_value is not None
            and now - self._stats_cache_ts < self._LIST_CACHE_TTL
        ):
            return dict(self._stats_cache_value)

        info = self.client.get_collection(self.collection)
        # qdrant-client <1.10: info.vectors_count
        # qdrant-client >=1.10: info.points_count (renamed in 1.10+)
        vec_count = (
            getattr(info, "points_count", None)
            or getattr(info, "vectors_count", None)
            or 0
        )
        out = {
            "collection": self.collection,
            "num_entities": vec_count,
            "dim": self.dim,
            "mode": "Qdrant HTTP server",
        }
        self._stats_cache_value = dict(out)
        self._stats_cache_ts = now
        return out

    def delete_collection(self) -> None:
        if self.client.collection_exists(self.collection):
            self.client.delete_collection(self.collection)

    def recreate_collection(self) -> None:
        """Drop and recreate. Used to migrate index params (e.g. HNSW ef)."""
        self.delete_collection()
        self._connect()  # reconnect will create fresh

    def backfill_sparse(self, batch: int = 100) -> int:
        """v0.9.87.0 — add the ``bm25`` sparse vector to EVERY existing
        point of this collection (the hybrid-search migration path for
        old corpora). Requires the collection schema to already carry
        the sparse field (recreate with QDRANT_HYBRID=on first).
        Explicit op — Qdrant errors propagate."""
        from src.vector_store.sparse import backfill

        return backfill(self.client, self.collection, batch=batch)

    # ─── all-collections inventory (v0.9.59.1) ─────────────────────
    def list_all_collections(self) -> list[dict]:
        """Every collection in the Qdrant instance with per-collection
        info: points count, vector dim, status. Used by the GUI Admin
        tab's Collections table (file_docs / log_docs / stock_docs /
        memory_docs / ...). Read-only; failures per collection are
        contained into an ``error`` row instead of failing the list."""
        names = [
            c.name for c in self.client.get_collections().collections
        ]
        out: list[dict] = []
        for name in sorted(names):
            row: dict = {"name": name}
            try:
                info = self.client.get_collection(name)
                vec_count = (
                    getattr(info, "points_count", None)
                    or getattr(info, "vectors_count", None)
                    or 0
                )
                row["points"] = vec_count
                row["dim"] = self._collection_dim(info)
                row["status"] = str(
                    getattr(getattr(info, "status", None), "name",
                            info.status) if getattr(info, "status",
                                                    None) else ""
                )
            except Exception as e:
                row["points"] = 0
                row["error"] = repr(e)[:200]
            out.append(row)
        return out

    @staticmethod
    def _collection_dim(info) -> int | None:
        """Extract the vector dim from a collection info object —
        handles both the single-vector (``VectorParams``) and the
        named-vectors (dict) shapes."""
        try:
            params = info.config.params
            vectors = getattr(params, "vectors", None)
            if vectors is None:
                return None
            if hasattr(vectors, "size"):  # VectorParams
                return int(vectors.size)
            if hasattr(vectors, "values") and vectors:
                first = next(iter(vectors.values()), None)
                if first is not None and hasattr(first, "size"):
                    return int(first.size)
        except Exception:
            pass
        return None

    def delete_collection_by_name(self, name: str) -> bool:
        """Delete ANY collection by exact name (the GUI Collections
        table's per-row destructive button). Returns False when the
        collection does not exist."""
        name = (name or "").strip()
        if not name:
            return False
        if not self.client.collection_exists(name):
            return False
        self.client.delete_collection(name)
        self.invalidate_list_cache()
        return True

    # ─── paged point browser + per-point delete (v0.9.60.0) ────────
    def browse_points(
        self,
        limit: int = 50,
        offset: str | None = None,
        payload_chars: int = 260,
    ) -> tuple[list[dict], str | None]:
        """ONE paged scroll for the GUI data browser (v0.9.60.0).

        Returns ``(rows, next_offset)`` — the caller re-scrolls with
        ``next_offset`` for the next page (native Qdrant pagination,
        O(page) per request instead of dumping a 167k-point
        collection). Rows carry ``id`` + a payload with string values
        truncated to ``payload_chars`` (vectors never leave the
        server). Works on ANY collection shape (file_docs / log_docs
        / stock_docs / memory_docs / ...)."""
        results, next_offset = self.client.scroll(
            collection_name=self.collection,
            limit=max(1, min(int(limit), 200)),
            with_payload=True,
            with_vectors=False,
            offset=offset,
        )
        rows: list[dict] = []
        for r in results:
            payload: dict = {}
            for k, v in (r.payload or {}).items():
                if isinstance(v, str) and len(v) > payload_chars:
                    payload[k] = v[:payload_chars] + f"…[{len(v)} chars]"
                elif isinstance(v, (dict, list)):
                    import json as _json

                    s = _json.dumps(v, ensure_ascii=False, default=str)
                    payload[k] = (s[:payload_chars]
                                  + f"…[{len(s)} chars]"
                                  if len(s) > payload_chars else s)
                else:
                    payload[k] = v
            rows.append({"id": str(r.id), "payload": payload})
        return rows, next_offset

    def delete_points(self, ids: list[str]) -> int:
        """Delete points by id (GUI per-row data management). Returns
        the number of ids accepted by the server."""
        clean = [str(i) for i in (ids or []) if str(i or "").strip()]
        if not clean:
            return 0
        self.client.delete(
            collection_name=self.collection,
            points_selector=qmodels.PointIdsList(points=clean),
        )
        self.invalidate_list_cache()
        return len(clean)

    # ─── helper for ID hashing (kept from milvus_client) ────────────
    @staticmethod
    def make_id(text: str) -> str:
        """Stable ID for a chunk (sha256 of text + source, truncated)."""
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


store = QdrantStore()