"""MemoryStore — Qdrant collection dedicated to the LLM's long-term
memory (v0.9.59.0, user: 「session內容和對外查詢的資訊應存放於qdrant中
作為LLM長期記憶…存入的資訊需要標記取得時間作為資料life cycle管理和
引用使用」).

Structurally parallel to :class:`src.log_analyzer.store.LogStore` /
:class:`src.stock_store.StockStore`, pointed at its own collection
(``QDRANT_MEMORY_COLLECTION``, default ``memory_docs``):

* One memory == one point. What flows in:
  - kind=``chat``    — the last Q/A pair of every persisted session
    (GUI / telegram / scheduler / webhook), hooked from
    ``SessionManager.save`` (deterministic uuid5 id → re-saves
    OVERWRITE instead of duplicating).
  - kind=``query``   — the result text of whitelisted "external
    lookup" MCP tools (``QDRANT_MEMORY_TOOLS``, default
    ``web_search,web_fetch``), captured in ``Agent._run_tool_call``.
  - kind=``fact``    — explicit ``remember_info`` MCP calls.
  - kind=``task-result`` — v0.9.111.0: background-task / skill-plan
    conclusions via :func:`store_task_result`. STORE-NOT-RECALL:
    the auto-recall path excludes this kind; discovery is on-demand
    (``search_memory(kind='task-result')``).
* Every point carries ``stored_at`` (ISO 8601 local) +
  ``stored_at_epoch`` (float) — the LLM must CITE the acquisition
  time when it uses a memory (引用), and lifecycle management
  (``purge(older_than_s)``) keys on it. v0.9.111.0 adds
  ``hit_count`` / ``last_hit_at`` / ``last_hit_epoch`` — bumped by
  :meth:`MemoryStore.touch_hits` when a point is actually injected
  into a turn's LONG-TERM MEMORY block (fire-and-forget, gated by
  ``QDRANT_MEMORY_HIT_STATS``); ``usage_report`` powers the
  ``list_recent_memories`` usage summary (zero-hit cleanup
  candidates).
* Vectors come from the SAME Embedder as the RAG pipeline (auto
  chain: local llama.cpp → openrouter → …), so memory recall is
  dimensionally consistent with the configured collection dim.

Env vars:
* ``QDRANT_MEMORY_COLLECTION`` (default ``memory_docs``)
* ``QDRANT_MEMORY_ENABLED``    (default on — recall + ingest)
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time
import uuid
from datetime import datetime
from typing import Any

from qdrant_client.http import models as qmodels

from src.config import config

__all__ = ["MemoryStore", "memory_enabled"]

_NS = uuid.uuid5(uuid.NAMESPACE_URL, "qdrant-rag-agent/memory-store")
_MAX_CONTENT_CHARS = 4000

logger = logging.getLogger(__name__)


def memory_enabled() -> bool:
    v = (os.environ.get("QDRANT_MEMORY_ENABLED", "on") or "on"
         ).strip().lower()
    return v not in ("off", "0", "no", "false")


def memory_hit_stats_enabled() -> bool:
    """v0.9.111.0 — recall-hit bookkeeping gate (default on; writes are
    fire-and-forget and never block a chat turn)."""
    v = (os.environ.get("QDRANT_MEMORY_HIT_STATS", "on") or "on"
         ).strip().lower()
    return v not in ("off", "0", "no", "false")


def task_sink_enabled() -> bool:
    """v0.9.111.0 — background-task/plan conclusion sink gate (default
    on; the sink stores kind="task-result" which auto-recall EXCLUDES —
    the v0.9.67.2 stale-snapshot lesson, see ``_memory_block``)."""
    v = (os.environ.get("QDRANT_TASK_MEMORY_SINK", "on") or "on"
         ).strip().lower()
    return v not in ("off", "0", "no", "false")


def _iso(ts_epoch: float) -> str:
    return datetime.fromtimestamp(
        float(ts_epoch), tz=None,
    ).astimezone().isoformat(timespec="seconds")


class MemoryStore:
    """Thin Qdrant wrapper for the long-term-memory collection."""

    _write_lock = threading.Lock()

    def __init__(
        self,
        url: str | None = None,
        collection: str | None = None,
        embedder: Any = None,
    ):
        self.url = url or config.QDRANT_URL
        self.collection = collection or os.environ.get(
            "QDRANT_MEMORY_COLLECTION", "memory_docs",
        )
        self.dim = config.EMBEDDING_DIM
        self._client: Any | None = None
        self._embedder: Any | None = embedder

    # ─── embedder (lazy — the auto chain keeps its own cooldown) ──
    @property
    def embedder(self) -> Any:
        if self._embedder is None:
            from src.ingest.embedder import Embedder

            self._embedder = Embedder()
        return self._embedder

    # ─── connection lifecycle (LogStore/StockStore pattern) ───────
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
        self._client = QdrantClient(**kwargs)
        self._ensure_collection()

    def _ensure_collection(self) -> None:
        c = self.client
        if not c.collection_exists(self.collection):
            c.create_collection(
                collection_name=self.collection,
                vectors_config=qmodels.VectorParams(
                    size=self.dim,
                    distance=qmodels.Distance.COSINE,
                ),
                hnsw_config=qmodels.HnswConfigDiff(
                    m=16, ef_construct=100,
                ),
            )
        for field, schema in (
            ("kind", qmodels.PayloadSchemaType.KEYWORD),
            ("source", qmodels.PayloadSchemaType.KEYWORD),
            ("stored_at_epoch", qmodels.PayloadSchemaType.FLOAT),
        ):
            try:
                c.create_payload_index(
                    collection_name=self.collection,
                    field_name=field,
                    field_schema=schema,
                )
            except Exception:
                pass

    # ─── ids ─────────────────────────────────────────────────────
    @staticmethod
    def make_memory_id(kind: str, source: str, content: str) -> str:
        """Deterministic uuid5 — the same memory re-stored OVERWRITES
        the same point (idempotent re-saves, no duplicates)."""
        return str(uuid.uuid5(_NS, f"{kind}|{source}|{content}"))

    # ─── write ────────────────────────────────────────────────────
    def remember(
        self,
        text: str,
        kind: str = "fact",
        source: str = "",
        session_id: str = "",
        meta: dict | None = None,
        ts_epoch: float | None = None,
    ) -> dict:
        """Store one memory. Returns ``{id, stored_at, stored_at_epoch,
        deduplicated}`` — an identical (kind, source, text) payload
        overwrites the previous point (idempotent)."""
        text = str(text or "").strip()[:_MAX_CONTENT_CHARS]
        if not text:
            raise ValueError("memory text is empty")
        ts = float(ts_epoch if ts_epoch is not None else time.time())
        mem_id = self.make_memory_id(kind, source, text)
        payload: dict[str, Any] = {
            "kind": str(kind or "fact"),
            "source": str(source or ""),
            "session_id": str(session_id or ""),
            "content": text,
            "stored_at": _iso(ts),
            "stored_at_epoch": float(ts),
            # v0.9.111.0 — recall-hit bookkeeping (bumped by
            # ``touch_hits`` when the point is actually injected into a
            # turn's LONG-TERM MEMORY block; pre-existing points load
            # as hit_count=0).
            "hit_count": 0,
            "meta": meta or {},
        }
        vec = self.embedder.embed(text)
        with MemoryStore._write_lock:
            self.client.upsert(
                collection_name=self.collection,
                points=[qmodels.PointStruct(
                    id=mem_id, vector=vec, payload=payload,
                )],
            )
        return {
            "id": mem_id,
            "stored_at": payload["stored_at"],
            "stored_at_epoch": float(ts),
        }

    # ─── search / list ────────────────────────────────────────────
    def search(
        self,
        query: str,
        top_k: int = 5,
        kind: str | None = None,
        source: str | None = None,
        score_threshold: float = 0.2,
        exclude_kinds: Any = None,
    ) -> list[dict[str, Any]]:
        """Semantic recall; hits carry ``stored_at`` so the LLM can
        cite the acquisition time.

        v0.9.111.0: ``exclude_kinds`` — a ``must_not`` filter for kinds
        that must NEVER appear on the auto-recall path (today only the
        auto-sunk ``task-result``; ``search_memory`` keeps seeing them
        for on-demand discovery).
        """
        if not query:
            return []
        must: list[Any] = []
        must_not: list[Any] = []
        if kind:
            must.append(qmodels.FieldCondition(
                key="kind", match=qmodels.MatchValue(value=kind),
            ))
        if source:
            must.append(qmodels.FieldCondition(
                key="source", match=qmodels.MatchValue(value=source),
            ))
        excl = [str(x) for x in (exclude_kinds or []) if str(x)]
        if excl:
            must_not.append(qmodels.FieldCondition(
                key="kind", match=qmodels.MatchAny(any=excl),
            ))
        qfilter = (qmodels.Filter(must=must, must_not=must_not)
                   if (must or must_not) else None)
        qvec = self.embedder.embed(query)
        resp = self.client.query_points(
            collection_name=self.collection,
            query=qvec,
            limit=int(top_k),
            with_payload=True,
            query_filter=qfilter,
            score_threshold=float(score_threshold),
        )
        out: list[dict[str, Any]] = []
        for p in getattr(resp, "points", None) or []:
            payload = dict(p.payload or {})
            payload["id"] = str(p.id)
            payload["score"] = float(p.score)
            out.append(payload)
        return out

    def recent(
        self, limit: int = 10, kind: str | None = None,
    ) -> list[dict[str, Any]]:
        """Newest-first memories (client-side sort by stored_at)."""
        rows: list[dict[str, Any]] = []
        offset = None
        for _ in range(5):  # 5 * 100 = 500-point scroll cap
            points, offset = self.client.scroll(
                collection_name=self.collection,
                scroll_filter=(
                    qmodels.Filter(must=[qmodels.FieldCondition(
                        key="kind",
                        match=qmodels.MatchValue(value=kind),
                    )]) if kind else None
                ),
                limit=100,
                with_payload=True,
                offset=offset,
            )
            for p in points:
                payload = dict(p.payload or {})
                payload["id"] = str(p.id)
                rows.append(payload)
            if not offset:
                break
        rows.sort(key=lambda r: float(r.get("stored_at_epoch") or 0),
                  reverse=True)
        return rows[:max(1, int(limit))]

    # ─── v0.9.111.0 — usage tracking (recall-hit bookkeeping) ─────
    def touch_hits(self, point_ids: Any) -> dict:
        """Bump ``hit_count`` / stamp ``last_hit_*`` for RECALLED points
        (fire-and-forget daemon thread — NEVER blocks or raises on the
        chat turn). Pre-existing points without the fields count as 0.
        Gated by ``memory_hit_stats_enabled()``."""
        ids = [str(x) for x in (point_ids or []) if str(x)]
        if not ids or not memory_hit_stats_enabled():
            return {"touched": 0}
        threading.Thread(
            target=self._touch_hits_impl, args=(ids,), daemon=True,
            name="memory-touch",
        ).start()
        return {"touched": len(ids)}

    def _touch_hits_impl(self, ids: list[str]) -> None:
        try:
            with MemoryStore._write_lock:
                pts = self.client.retrieve(
                    collection_name=self.collection, ids=ids,
                    with_payload=True, with_vectors=False,
                )
                for p in pts or []:
                    payload = dict(p.payload or {})
                    try:
                        n = int(payload.get("hit_count") or 0) + 1
                    except (TypeError, ValueError):
                        n = 1
                    payload["hit_count"] = n
                    payload["last_hit_epoch"] = float(time.time())
                    payload["last_hit_at"] = _iso(
                        payload["last_hit_epoch"])
                    self.client.set_payload(
                        collection_name=self.collection,
                        payload=payload,
                        points=[str(p.id)],
                    )
        except Exception as e:  # noqa: BLE001 — bookkeeping never breaks
            logger.debug("[memory] touch_hits failed: %r", e)

    def usage_report(
        self, older_than_s: float = 2_592_000.0, max_points: int = 2000,
    ) -> dict:
        """v0.9.111.0 — hit-usage snapshot for lifecycle cleanup:
        ``total`` / ``never_hit`` / ``never_hit_older_than_s`` (zero
        recall hits AND older than the threshold = the candidates a
        ``purge_memories`` round should look at first). Client-side
        scroll capped at ``max_points``; any failure degrades to an
        unreachable marker, never raises."""
        try:
            rows: list[dict] = []
            offset = None
            for _ in range(max(1, max_points // 100)):
                points, offset = self.client.scroll(
                    collection_name=self.collection,
                    limit=100, with_payload=True, offset=offset,
                )
                for p in points or []:
                    rows.append(dict(p.payload or {}))
                if not offset:
                    break
            cutoff = float(time.time()) - float(older_than_s)
            total = len(rows)
            never = [r for r in rows
                     if not (int(r.get("hit_count") or 0) > 0)]
            stale = [r for r in never
                     if float(r.get("stored_at_epoch") or 0) < cutoff]
            return {
                "total": total,
                "hit_tracked": total - len(never),
                "never_hit": len(never),
                "never_hit_older_than_s": {
                    "secs": int(older_than_s),
                    "count": len(stale),
                },
                "note": (
                    "never_hit_older_than_s 越大越應優先清理 — "
                    "purge_memories(older_than_s) / forget_memory(id)"
                ),
            }
        except Exception as e:  # noqa: BLE001 — report, not raise
            return {"error": repr(e)}

    # ─── lifecycle / count ────────────────────────────────────────
    def cleanup_candidates(
        self, older_than_s: float = 2_592_000.0, limit: int = 50,
    ) -> dict:
        """v0.9.127.0 — the DRY-RUN cleanup list for the maintenance
        loop: zero-recall-hit memories older than the threshold,
        oldest first (rows carry id/kind/text[:160]/age stats). NEVER
        deletes — the executor is ``purge_memories`` / per-id
        ``forget_memory`` with their own confirmation flow."""
        try:
            rows = []
            offset = None
            for _ in range(20):
                points, offset = self.client.scroll(
                    collection_name=self.collection,
                    limit=100, with_payload=True, offset=offset,
                )
                for p_ in points or []:
                    rows.append({**dict(p_.payload or {}),
                                 "_pid": str(getattr(p_, "id", ""))})
                if not offset:
                    break
            cutoff = float(time.time()) - float(older_than_s)
            out = []
            for r in rows:
                if int(r.get("hit_count") or 0) > 0:
                    continue
                ts = float(r.get("stored_at_epoch") or 0)
                if ts and ts >= cutoff:
                    continue
                out.append({
                    "id": str(r.get("mem_id") or r.get("id")
                              or r.get("_pid") or ""),
                    "kind": str(r.get("kind") or ""),
                    "text": str(r.get("text") or "")[:160],
                    "age_s": (int(time.time() - ts) if ts else None),
                    "stored_at": str(r.get("stored_at") or ""),
                })
            out.sort(key=lambda x: x.get("age_s") or 0, reverse=True)
            try:
                lim = max(1, min(int(limit or 50), 200))
            except (TypeError, ValueError):
                lim = 50
            return {
                "ok": True,
                "older_than_s": int(older_than_s),
                "total_never_hit": len(out),
                "candidates": out[:lim],
                "note": ("DRY-RUN report only — execute with "
                         "purge_memories(older_than_s=...) or "
                         "forget_memory(id); both confirm-gated"),
            }
        except Exception as e:  # noqa: BLE001 — report, not raise
            return {"error": repr(e)}
    def forget(self, mem_id: str) -> bool:
        """Delete one memory by point id."""
        if not mem_id:
            return False
        try:
            uuid.UUID(str(mem_id))
        except ValueError:
            return False
        self.client.delete(
            collection_name=self.collection,
            points_selector=qmodels.PointIdsList(points=[str(mem_id)]),
        )
        return True

    def purge(
        self,
        older_than_s: float | None = None,
        kind: str | None = None,
    ) -> int:
        """Delete memories by lifecycle age (``older_than_s`` seconds
        before now) and/or kind. Returns the number of deleted points
        (approximated by the count BEFORE the delete)."""
        must: list[Any] = []
        if older_than_s is not None and older_than_s > 0:
            must.append(qmodels.FieldCondition(
                key="stored_at_epoch",
                range=qmodels.Range(lt=float(time.time()
                                           - float(older_than_s))),
            ))
        if kind:
            must.append(qmodels.FieldCondition(
                key="kind", match=qmodels.MatchValue(value=kind),
            ))
        if not must:
            return 0  # refuse an unfiltered wipe — lifecycle only
        qfilter = qmodels.Filter(must=must)
        before = self.client.count(
            collection_name=self.collection,
            count_filter=qfilter, exact=True,
        ).count
        if before:
            self.client.delete(
                collection_name=self.collection,
                points_selector=qmodels.FilterSelector(filter=qfilter),
            )
        return int(before)

    def count(self) -> int:
        return int(self.client.count(
            collection_name=self.collection, exact=True,
        ).count)


# ─── session-ingest hook (SessionManager.save → chat Q/A pairs) ──
_SESSION_STORE: MemoryStore | None = None


def _shared_store() -> MemoryStore:
    global _SESSION_STORE
    if _SESSION_STORE is None:
        _SESSION_STORE = MemoryStore()
    return _SESSION_STORE


def session_ingest_enabled() -> bool:
    v = (os.environ.get("QDRANT_MEMORY_SESSION_INGEST", "on") or "on"
         ).strip().lower()
    return v not in ("off", "0", "no", "false")


def ingest_session_pair(session: Any) -> dict | None:
    """Ingest the LAST Q/A pair of a saved session into the memory
    collection. Deterministic uuid5 id → repeated saves of the same
    pair OVERWRITE the same point (no duplicates). Failures raise —
    the caller (SessionManager.save) swallows them.

    v0.9.83.5: an answer that is basically a tool-call ECHO (the model
    wrote ``search_documentsq…`` as text instead of dispatching) is
    NEVER stored — the live E2E loop proved a stored echo becomes the
    shape the NEXT turn imitates (self-reinforcing fake-calls)."""
    if not session_ingest_enabled():
        return None
    msgs = list(session.messages or [])
    # Last assistant answer that is not an error / guarded / empty.
    a_idx = None
    for i in range(len(msgs) - 1, -1, -1):
        m = msgs[i]
        if m.get("role") != "assistant":
            continue
        content = str(m.get("content") or "").strip()
        if (not content or content.startswith("[error]")
                or m.get("error") or m.get("hallucination_guarded")):
            continue
        a_idx = i
        break
    if a_idx is None:
        return None
    user_q = ""
    for m in reversed(msgs[:a_idx]):
        if m.get("role") == "user" and m.get("content"):
            user_q = str(m["content"]).strip()
            break
    if not user_q:
        return None
    answer = str(msgs[a_idx].get("content") or "").strip()
    if _is_echo_artifact(answer):
        return None
    text = (f"Q: {user_q}\nA: {answer}")[:_MAX_CONTENT_CHARS]
    return _shared_store().remember(
        text, kind="chat", source=str(session.id),
        session_id=str(session.id),
    )


_ECHO_CITE_SPLIT = "— 依長期記憶作答"


def _is_echo_artifact(answer: str) -> bool:
    """v0.9.83.5 — True when the answer is basically a tool-call echo:
    citation-stripped body ≤200 chars AND either ``tool(...)`` glued
    parens, or a latin tool-name run glued DIRECTLY to CJK
    (``search_documentsq中華電信…``), or the body is ONLY the tool
    name. A genuine answer that merely STARTS with a tool name but
    continues with prose (``get_current_time 的結果如下：…``) is
    stored; answers repaired by the echo guard (``系統代為執行``) carry
    real results and are stored."""
    body = (answer or "").split(_ECHO_CITE_SPLIT)[0].strip()
    if not body or len(body) > 200:
        return False
    if "系統代為執行" in body:
        return False
    if re.match(r"^[A-Za-z][\w\-]*\s*\(", body):
        return True
    if re.match(r"^[A-Za-z_\-0-9]{4,}[\u4e00-\u9fff]", body):
        return True
    return bool(re.fullmatch(r"[A-Za-z_\-0-9]{4,}", body))


# ─── v0.9.111.0 — task/plan conclusion sink (store-NOT-recall) ──────
TASK_RESULT_KIND = "task-result"


def store_task_result(
    title: Any = "",
    prompt: Any = "",
    answer: Any = "",
    *,
    source: str = "",
    session_id: str = "",
) -> dict | None:
    """Store ONE background-task / skill-plan conclusion into the
    long-term-memory collection (``kind=task-result``) for ON-DEMAND
    discovery (``search_memory(kind='task-result')``).

    Store-NOT-recall contract: the per-turn auto-recall
    (``Agent._memory_block``) EXCLUDES this kind — a stored task
    conclusion is a historical snapshot exactly like the v0.9.67.2
    fire-memory lesson, so it must never be silently injected into new
    turns where TODAY's data matters.

    Never raises (best-effort bookkeeping); disabled by
    ``QDRANT_TASK_MEMORY_SINK=off``. Returns the ``remember`` dict or
    ``None`` (disabled / failure)."""
    if not task_sink_enabled():
        return None
    try:
        text = (
            f"task: {str(title or '').strip()}\n"
            f"prompt: {str(prompt or '').strip()[:600]}\n"
            f"result: {str(answer or '').strip()[:2500]}"
        ).strip()
        if not text:
            return None
        return _shared_store().remember(
            text, kind=TASK_RESULT_KIND,
            source=str(source or ""), session_id=str(session_id or ""),
        )
    except Exception as e:  # noqa: BLE001 — sink never breaks the caller
        logger.debug("[memory] task-result sink failed: %r", e)
        return None