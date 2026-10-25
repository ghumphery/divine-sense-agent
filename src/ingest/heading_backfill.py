"""Heading-path backfill for existing Qdrant points (v0.9.113.0 — STAIR).

``backfill_collection`` walks every point of a collection grouped by
``(source, chunk_index)`` and fills the missing ``heading_path`` payload
from the chunk TEXT itself: the LAST ATX heading line inside the chunk
wins; a chunk with no heading line inherits the previous chunk's path
(sequential carry-over — correct for sections split across chunks except
the leading fragment of a mid-chunk heading, documented best-effort).

* Updates are metadata-only (``client.set_payload``) — vectors, ids and
  chunk boundaries are NEVER touched, no re-embed.
* Points that already carry a non-empty ``heading_path`` are skipped
  (honor-existing); ``force=True`` recomputes them instead.
* A KEYWORD payload index on ``heading_path`` is created best-effort so
  ``heading_path ==`` filters are fast.
* Every step never raises to the caller — failures are counted into the
  returned ``errors`` summary (audit-backends / reembed-hash sibling).
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from src.ingest.headings import heading_for_text


def _force() -> bool:
    return True


def backfill_collection(
    store: Any,
    collection: str | None = None,
    limit: int = 0,
    force: bool = False,
    log=None,
) -> dict:
    """Backfill ``heading_path`` payload. Returns a summary dict.

    Args:
        store: ``QdrantStore`` (client reused; ``collection`` default).
        collection: override collection name.
        limit: stop after updating N points (0 = all).
        force: also recompute points that already carry a non-empty value.
        log: callable for progress lines (``print`` by default); ``None``
            silences progress.
    """
    say = log or (lambda *_a, **_k: None)
    name = collection or store.collection
    client = store.client

    from qdrant_client.http import models as qmodels

    # Best-effort payload index (idempotent for an existing same-schema one).
    index_ok = False
    try:
        client.create_payload_index(
            collection_name=name,
            field_name="heading_path",
            field_schema=qmodels.PayloadSchemaType.KEYWORD,
        )
        index_ok = True
    except Exception as e:
        say(f"  [backfill-headings] payload index skipped: {e!r:.120}")

    # 1. Scroll ALL points (id / source / chunk_index / text / existing).
    rows: list[dict] = []
    offset = None
    while True:
        pts, offset = client.scroll(
            collection_name=name,
            limit=500,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        for p in pts:
            payload = p.payload or {}
            text = payload.get("text") or ""
            if not text:
                continue
            rows.append({
                "id": str(p.id),
                "source": payload.get("source") or payload.get("filename") or "",
                "chunk_index": int(payload.get("chunk_index") or 0),
                "existing": str(payload.get("heading_path") or ""),
                "text": text,
            })
        if offset is None:
            break

    rows.sort(key=lambda r: (r["source"], r["chunk_index"]))

    # 2. Sequential walk — compute the target path per chunk.
    updates: dict[str, str] = {}
    errors = 0
    last = ""
    last_source = None
    for r in rows:
        if r["source"] != last_source:
            last = ""
            last_source = r["source"]
        if r["existing"] and not force:
            last = r["existing"]
            continue
        try:
            path, _title = heading_for_text(r["text"], last)
        except Exception:
            errors += 1
            path = last
        path = path or ""
        if path and (force or not r["existing"]):
            updates[r["id"]] = path
        if path:
            last = path
        if limit and len(updates) >= limit:
            break

    # 3. set_payload grouped by target value (metadata-only, ids untouched).
    written = 0
    groups: dict[str, list[str]] = defaultdict(list)
    for pid, path in updates.items():
        groups[path].append(pid)
    for path, ids in groups.items():
        for i in range(0, len(ids), 256):
            batch = ids[i:i + 256]
            try:
                client.set_payload(
                    collection_name=name,
                    payload={"heading_path": path},
                    points=batch,
                    wait=True,
                )
                written += len(batch)
            except Exception as e:
                errors += 1
                say(f"  [backfill-headings] set_payload failed: {e!r:.140}")

    summary = {
        "collection": name,
        "scanned": len(rows),
        "updated": written,
        "targets": len(updates),
        "skipped_existing": len(rows) - len(updates),
        "sources": len({r["source"] for r in rows}),
        "heading_index": index_ok,
        "force": force,
        "errors": errors,
    }
    say(f"  [backfill-headings] {summary}")
    return summary