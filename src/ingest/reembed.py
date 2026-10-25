"""v0.9.112.0 — hash-vector audit + re-embed for existing collections.

The embed chain's last resort is the DETERMINISTIC hash backend
(``Embedder._fallback_embed`` — md5-per-word count vector, non-
semantic). Before v0.9.112.0 nothing recorded which backend produced
an ingested vector, so a DEGRADED run (llama.cpp down + OpenRouter
unreachable → every chunk hashed, e.g. the 2026-09-14 ErrorDeviceLost
session) silently polluted the collection and there was NO way to
detect or undo the damage afterwards.

Two mechanisms close that gap:

* **TAG** — the pipeline now upserts ``payload.embedding_backend``
  from ``Embedder.last_backends``; NEW points are self-describing.
* **AUDIT / RE-EMBED** (this module, works for points old AND new):
  the hash vector of a text is a deterministic FUNCTION of the text,
  so the audit recomputes it (``Embedder.hash_fallback_vector``) and
  compares EXACTLY with the stored vector (np.allclose at float32
  tolerance). A tag can be wrong or absent; the recomputation
  cannot — a real Qwen3/OpenRouter vector essentially never equals a
  word-count hash at 4096 dims. Flagged points are re-embedded with
  ``Embedder.embed_fresh`` (bypasses BOTH cache tiers — a normal
  ``embed`` would return the poisoned hash vector from its correctly-
  keyed cache row and repair nothing) and upserted with their payload
  + an updated tag.

Fail-open discipline: the audit never mutates; the re-embed only
upserts points it actually re-embedded (backend != "hash" — when EVERY
backend is down even the repair must not write hash vectors with a
tag claiming they are healthy; those points are reported as failed).
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

DEFAULT_SCAN_BATCH = 256
DEFAULT_UPSERT_BATCH = 100
_REEMBED_CAP = 20000  # points per run — a runaway repair cannot loop


def _same_vector(a: Any, b: Any, atol: float = 1e-6,
                 rtol: float = 1e-5) -> bool:
    """Exact-enough float32 comparison (Qdrant stores the row back as
    f32; the recomputation is the same f32 pipeline)."""
    if not isinstance(a, (list, tuple)) or not isinstance(b, (list, tuple)):
        return False
    if len(a) != len(b):
        return False
    return bool(np.allclose(
        np.asarray(a, dtype=np.float64),
        np.asarray(b, dtype=np.float64),
        rtol=rtol, atol=atol,
    ))


def _point_to_dict(p: Any) -> dict:
    """Normalize a qdrant-client scroll Point (``.id`` / ``.payload``
    / ``.vector``) into a plain dict. Handles the hybrid-collection
    named-vector shape: the dense leg is the ``""`` entry; any sparse
    or unresolvable shape is skipped (cannot be hash-compared)."""
    pid = getattr(p, "id", None)
    payload = getattr(p, "payload", None) or {}
    vec = getattr(p, "vector", None)
    if isinstance(vec, dict):
        vec = vec.get("")
        if isinstance(vec, dict):
            vec = None  # {"indices": [...], "values": [...]} sparse leg
    return {"id": str(pid) if pid is not None else "", "payload": payload,
            "vector": vec}


def _scan(store: Any, embedder: Any, keep_payload: bool = False,
          batch: int = DEFAULT_SCAN_BATCH
          ) -> tuple[dict, dict[str, dict]]:
    """Scroll the whole collection and flag hash vectors.

    Returns ``(report, {"<id>": payload})`` — the payload map is only
    populated when ``keep_payload`` (the re-embed path; the audit
    report itself never carries full text payloads).
    """
    dim = int(getattr(embedder, "dimension", 0) or 0)
    flagged: list[dict[str, str]] = []
    payloads: dict[str, dict] = {}
    scanned = 0
    tagged_hash = 0
    matched_hash = 0
    skipped_dim_mismatch = 0
    offset = None
    collection = getattr(store, "collection", "")
    while True:
        results, offset = store.client.scroll(
            collection_name=collection,
            limit=max(1, min(int(batch), 512)),
            with_payload=True,
            with_vectors=True,
            offset=offset,
        )
        if not results:
            break
        for raw in results:
            p = _point_to_dict(raw)
            scanned += 1
            pid = p["id"]
            payload = p["payload"]
            vec = p["vector"]
            text = (payload.get("text") or "") if isinstance(
                payload, dict) else ""
            tag = (payload.get("embedding_backend", "") or ""
                   ) if isinstance(payload, dict) else ""
            is_hash = False
            if str(tag).strip().lower() == "hash":
                is_hash = True
                tagged_hash += 1
            elif text and vec and dim:
                if len(vec) != dim:
                    skipped_dim_mismatch += 1
                elif _same_vector(vec, embedder.hash_fallback_vector(text)):
                    is_hash = True
                    matched_hash += 1
            if is_hash and pid:
                row = {"id": pid, "tag": str(tag),
                       "filename": payload.get("filename", "")
                       if isinstance(payload, dict) else "",
                       "source": payload.get("source", "")
                       if isinstance(payload, dict) else ""}
                flagged.append(row)
                if keep_payload and isinstance(payload, dict):
                    payloads[pid] = payload
        if offset is None:
            break
    report = {
        "collection": collection,
        "scanned": scanned,
        "tagged_hash": tagged_hash,
        "matched_hash": matched_hash,
        "skipped_dim_mismatch": skipped_dim_mismatch,
        "hash_points": len(flagged),
        "flagged": flagged,
    }
    return report, payloads
def _embedder_factory():
    """Embedder construction hook (tests monkeypatch this to inject a
    healthy fake — the real factory builds a plain Embedder)."""
    from src.ingest.embedder import Embedder

    return Embedder()


def audit_collection(store: Any, batch: int = DEFAULT_SCAN_BATCH) -> dict:
    """Read-only scan (the GUI Audit button / ``cli.py audit-backends``).

    Never mutates and never raises into the caller — the re-embed
    path needs the SAME signature, so heavy failures surface as
    ``{"ok": False, "error": ...}`` envelopes here too."""
    try:
        embedder = _embedder_factory()
        report, _ = _scan(store, embedder, keep_payload=False,
                          batch=batch)
        report["ok"] = True
        return report
    except Exception as e:  # noqa: BLE001 — errors are envelopes
        logger.error("audit_collection failed: %r", e)
        return {"ok": False, "error": repr(e)[:300]}


def reembed_flagged(store: Any, limit: int | None = None,
                    upsert_batch: int = DEFAULT_UPSERT_BATCH) -> dict:
    """Re-embed every hash-flagged point with the CURRENT healthy
    backend and upsert (payload preserved + tag updated).

    ``limit`` caps points per run (default: the module cap). Points
    whose fresh embed STILL returns a hash vector (every backend
    down) are NOT upserted — they are reported as ``still_hash`` so
    the repair cannot paint pollution over with a healthy tag."""
    embedder = _embedder_factory()
    report, payloads = _scan(store, embedder, keep_payload=True)
    flagged = report["flagged"]
    if limit is not None:
        flagged = flagged[:max(0, int(limit))]
    if len(flagged) > _REEMBED_CAP:
        flagged = flagged[:_REEMBED_CAP]
    still_hash: list[dict[str, str]] = []
    failed: list[dict[str, str]] = []
    reembedded = 0
    backends_used: dict[str, int] = {}
    pending: list[dict[str, Any]] = []

    def _flush(points: list[dict[str, Any]]) -> None:
        if points:
            store.upsert(points)
            points.clear()

    for row in flagged:
        pid = row["id"]
        payload = payloads.get(pid) or {}
        text = payload.get("text") or ""
        if not text:
            continue
        try:
            vec = embedder.embed_fresh(text)
        except Exception as e:  # noqa: BLE001 — per-point contained
            failed.append({"id": pid, "error": repr(e)[:200]})
            continue
        backend = (getattr(embedder, "_last_backend", "") or "unknown")
        if backend == "hash":
            # All real backends down — do NOT rewrite the same hash
            # vector with a lying tag; leave the point untouched.
            still_hash.append({"id": pid})
            continue
        backends_used[backend] = backends_used.get(backend, 0) + 1
        out_payload = dict(payload)
        out_payload["embedding_backend"] = backend
        pending.append({"id": pid, "vector": vec,
                        "payload": out_payload})
        reembedded += 1
        if len(pending) >= max(1, int(upsert_batch)):
            _flush(pending)
    _flush(pending)
    report["reembedded"] = reembedded
    report["still_hash"] = still_hash
    report["failed"] = failed
    report["backends_used"] = backends_used
    report["ok"] = True
    logger.info(
        "[reembed] collection=%s scanned=%d flagged=%d reembedded=%d "
        "still_hash=%d failed=%d backends=%s",
        report["collection"], report["scanned"], len(flagged),
        reembedded, len(still_hash), len(failed), backends_used,
    )
    return report


def _require_store(store: Any) -> dict:
    if store is None or not getattr(store, "client", None):
        return {"ok": False, "error": "no Qdrant store (server down?)"}
    return {}


def audit_or_reembed(store: Any, *, reembed: bool = False,
                     limit: int | None = None) -> dict:
    """Single dispatch used by the GUI + CLI wrappers (one error
    envelope shape for both)."""
    guard = _require_store(store)
    if guard:
        return guard
    if reembed:
        return reembed_flagged(store, limit=limit)
    return audit_collection(store)