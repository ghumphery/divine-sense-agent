"""Hybrid-search sparse leg (v0.9.87.0) — BM25-style bag-of-words.

Qdrant 1.10+ natively fuses a dense vector search with a SPARSE vector
search (RRF fusion) inside one ``query_points`` call. Dense embeddings
are weak on EXACT tokens (stock tickers like 2344, person names,
product codes) — this module adds the keyword leg:

* ``tokenize(text)`` — dependency-free tokenizer: CJK **bigrams**
  (char pairs work well without jieba) + lower-cased latin/digit runs.
* ``sparse_vector(text)`` — deterministic ``token → index``
  (sha1-truncated), weight = term frequency. Qdrant's sparse index
  computes IDF server-side (``modifier=IDF`` in the collection config).
* ``collection_has_sparse()`` — whether a collection's schema carries
  the ``bm25`` sparse field (old corpora don't → callers fall back to
  dense-only, byte-identical).

Everything is env-gated by ``QDRANT_HYBRID`` (off default) and
fail-open: any error degrades to the plain dense path.
"""
from __future__ import annotations

import hashlib
import os
import re

# The named sparse vector field this feature owns.
SPARSE_FIELD = "bm25"

# payload keys the sparse vector is derived from (search/ingest share
# this — the SAME tokenizer must run at both times).
_TEXT_KEYS = ("text", "message", "content", "raw_line", "summary")


def hybrid_enabled() -> bool:
    # tolerant parse: the first whitespace-separated token decides, so
    # a trailing comment/junk on the .env line cannot disable the flag
    raw = (os.environ.get("QDRANT_HYBRID", "") or "").strip().lower()
    first = raw.split(None, 1)[0] if raw else ""
    return first in ("on", "1", "true", "yes")


_TOKEN_RE = re.compile(r"[a-z0-9_]+|[\u4e00-\u9fff]")


def tokenize(text: str) -> dict[str, int]:
    """Deterministic bag-of-words: latin/digit words (lower-cased) +
    CJK single chars + CJK bigrams from contiguous runs (≥2 chars).
    Returns token → term frequency."""
    if not text:
        return {}
    tokens: dict[str, int] = {}
    for m in _TOKEN_RE.finditer(text.lower()):
        tok = m.group(0)
        tokens[tok] = tokens.get(tok, 0) + 1
    runs: list[str] = []
    cur: list[str] = []
    for ch in text or "":
        if "\u4e00" <= ch <= "\u9fff":
            cur.append(ch)
        else:
            if len(cur) >= 2:
                runs.append("".join(cur))
            cur = []
    if len(cur) >= 2:
        runs.append("".join(cur))
    for run in runs:
        for i in range(len(run) - 1):
            tok = run[i:i + 2]
            tokens[tok] = tokens.get(tok, 0) + 1
    return tokens


def sparse_vector(text: str) -> tuple[list[int], list[float]]:
    """Deterministic sparse vector for the ``bm25`` field. Token index
    = first 6 hex of sha1(token) (≤ 2**24 space; collisions only blend
    two tokens' TF). Weight = term frequency."""
    toks = tokenize(text)
    if not toks:
        return [], []
    indices: list[int] = []
    values: list[float] = []
    for tok, tf in toks.items():
        idx = int(hashlib.sha1(tok.encode("utf-8")).hexdigest()[:6], 16)
        indices.append(idx)
        values.append(float(tf))
    return indices, values


def sparse_from_payload(payload: dict | None) -> tuple[list[int], list[float]]:
    """Derive the sparse vector from a point's payload (first text-ish
    key present)."""
    p = payload or {}
    for k in _TEXT_KEYS:
        v = p.get(k)
        if isinstance(v, str) and v.strip():
            return sparse_vector(v)
    return [], []


def make_sparse_config():
    """The collection schema entry: sparse field with server-side IDF
    (proper BM25-ish weighting without client-side corpus stats)."""
    from qdrant_client.http import models as qmodels

    return qmodels.SparseVectorParams(
        modifier=qmodels.Modifier.IDF,
    )


def collection_has_sparse(client, collection: str) -> bool:
    """Does the collection schema carry the ``bm25`` sparse field?"""
    try:
        info = client.get_collection(collection)
        # qdrant-client: CollectionInfo.config.params.sparse_vectors
        params = getattr(getattr(info, "config", None), "params", None)
        sv = getattr(params, "sparse_vectors", None)
        if sv is None:  # older response shapes
            sv = getattr(getattr(info, "config", None),
                         "sparse_vectors_config", None)
        return SPARSE_FIELD in (sv or {})
    except Exception:
        return False


def backfill(client, collection: str, batch: int = 100,
             progress_every: int = 500) -> int:
    """Walk EVERY point of ``collection`` and add the ``bm25`` sparse
    vector derived from its payload text (idempotent — re-running just
    rewrites the same vectors). Returns the number of points updated.
    Raises on Qdrant errors (explicit capability op)."""
    from qdrant_client.http import models as qmodels

    if not collection_has_sparse(client, collection):
        raise RuntimeError(
            f"collection '{collection}' has no {SPARSE_FIELD} sparse "
            f"field — recreate the collection with QDRANT_HYBRID=on "
            f"first (recreate-collection), then re-run the backfill.")
    updated = 0
    offset = None
    buf: list = []
    while True:
        points, offset = client.scroll(
            collection_name=collection,
            limit=batch,
            with_payload=True,
            with_vectors=False,
            offset=offset,
        )
        if not points:
            break
        for pt in points:
            indices, values = sparse_from_payload(pt.payload)
            if not indices:
                continue
            buf.append(qmodels.PointVectors(
                id=pt.id,
                vector={SPARSE_FIELD: qmodels.SparseVector(
                    indices=indices, values=values)},
            ))
            if len(buf) >= batch:
                client.update_vectors(
                    collection_name=collection, points=buf)
                updated += len(buf)
                buf = []
                if progress_every and updated % progress_every < batch:
                    print(f"  [sparse-backfill] {updated} points…",
                          flush=True)
        if offset is None:
            break
    if buf:
        client.update_vectors(collection_name=collection, points=buf)
        updated += len(buf)
    return updated