"""StockStore — Qdrant collection dedicated to stock query snapshots.

Structurally parallel to :class:`src.log_analyzer.store.LogStore` but
pointed at its own collection (``QDRANT_STOCK_COLLECTION``, default
``stock_docs``) so stock points never mix with document RAG chunks or
log points:

* One MCP query result == one point. The embedded text is a short
  human-readable summary (``build_summary_text``) so queries like
  「上次查的台積電收盤價」are semantically searchable.
* Point IDs are ``uuid5(tool | symbol | ts_epoch)`` — deterministic,
  so the same snapshot re-saved in the same second OVERWRITES the
  same point instead of duplicating it.
* Payload indexes on ``tool`` / ``symbol`` (keyword) + ``ts_epoch``
  (float) keep the per-tool / per-symbol filters O(log n).

The class is intentionally small: save / search / recent / count.
All the query interpretation lives in :mod:`src.mega_stock_tools`; the
auto-save hook lives in ``MegaStockLocalHost`` (src/mcp_client/host.py).
"""
from __future__ import annotations

import json
import os
import sys as _sys
import threading
import time
import uuid
from datetime import datetime
from typing import Any

from qdrant_client.http import models as qmodels

from src.config import config

__all__ = ["StockStore", "build_summary_text"]

_NS = uuid.uuid5(uuid.NAMESPACE_URL, "qdrant-rag-agent/stock-store")


def build_summary_text(tool: str, args: dict | None, result: Any) -> str:
    """Short human-readable text for the embedding + LLM snippets.

    Generic across the four stock query shapes: scalar fields first
    (name / prices / volume / account / equity), then structural
    counts (bars_n / orders_n) and a compact JSON tail so numbers
    stay in the semantic neighbourhood of the query.
    """
    args = args or {}
    parts: list[str] = [str(tool or "stock")]
    for key in ("symbol", "interval", "status", "order_type"):
        v = args.get(key)
        if v not in (None, ""):
            parts.append(f"{key}={v}")
    if isinstance(result, dict):
        for key in ("symbol", "name", "ts", "mode",
                    "last_price", "close", "open", "high", "low",
                    "volume", "bid_price", "ask_price",
                    "account", "equity", "open_orders"):
            v = result.get(key)
            if v not in (None, ""):
                parts.append(f"{key}={v}")
        for list_key, label in (("candles", "bars"),
                                ("orders", "orders"),
                                ("holdings", "holdings")):
            lst = result.get(list_key)
            if isinstance(lst, list):
                parts.append(f"{label}_n={len(lst)}")
                if lst and isinstance(lst[0], dict):
                    first = json.dumps(
                        lst[0], ensure_ascii=False, default=str,
                    )[:220]
                    parts.append(f"{list_key}_first={first}")
    text = " ".join(str(p) for p in parts)
    tail = json.dumps(result, ensure_ascii=False, default=str)[:800]
    return (text + " " + tail)[:2000]


def _iso(ts_epoch: float) -> str:
    return datetime.fromtimestamp(
        float(ts_epoch), tz=None,
    ).astimezone().isoformat(timespec="seconds")


class StockStore:
    """Thin Qdrant wrapper for stock query snapshots."""

    _write_lock = threading.Lock()

    def __init__(self, url: str | None = None, collection: str | None = None):
        self.url = url or config.QDRANT_URL
        self.collection = collection or os.environ.get(
            "QDRANT_STOCK_COLLECTION", "stock_docs",
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
            f"  [StockStore] Connecting to: {self.url} "
            f"(collection={self.collection})",
            file=_sys.stderr,
        )
        self._client = QdrantClient(**kwargs)
        self._ensure_collection()

    def _ensure_collection(self) -> None:
        c = self.client
        if not c.collection_exists(self.collection):
            print(
                f"  [StockStore] Collection '{self.collection}' not "
                f"found; creating with dim={self.dim} (HNSW + Cosine).",
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
        # Payload indexes for the filters (idempotent).
        for field in ("tool", "symbol"):
            try:
                c.create_payload_index(
                    collection_name=self.collection,
                    field_name=field,
                    field_schema=qmodels.PayloadSchemaType.KEYWORD,
                )
            except Exception:
                pass  # already exists / old server
        try:
            c.create_payload_index(
                collection_name=self.collection,
                field_name="ts_epoch",
                field_schema=qmodels.PayloadSchemaType.FLOAT,
            )
        except Exception:
            pass

    # ─── ids ─────────────────────────────────────────────────────
    @staticmethod
    def point_id(tool: str, symbol: str, ts_epoch: float) -> str:
        """Deterministic id — same tool+symbol in the same second
        overwrites instead of duplicating."""
        return str(uuid.uuid5(
            _NS, f"{tool}|{symbol or '-'}|{float(ts_epoch):.0f}",
        ))

    # ─── save ────────────────────────────────────────────────────
    def save_query(
        self,
        *,
        tool: str,
        args: dict | None,
        result: Any,
        text: str,
        vector: list[float],
        ts_epoch: float | None = None,
    ) -> str:
        """Upsert ONE point for this query snapshot. Returns the id.

        ``text`` is the embedded summary (``build_summary_text``).
        """
        ts_epoch = float(ts_epoch if ts_epoch is not None else time.time())
        symbol = ""
        if isinstance(args, dict):
            symbol = str(args.get("symbol") or "")
        elif isinstance(result, dict):
            symbol = str(result.get("symbol") or "")
        payload: dict[str, Any] = {
            "tool": str(tool or "stock"),
            "symbol": symbol,
            "ts": _iso(ts_epoch),
            "ts_epoch": float(ts_epoch),
            "args": json.dumps(args or {}, ensure_ascii=False,
                               default=str)[:2000],
            "data": json.dumps(result, ensure_ascii=False,
                               default=str)[:8000],
            "text": str(text)[:4000],
        }
        pid = self.point_id(payload["tool"], symbol, ts_epoch)
        with self._write_lock:
            self.client.upsert(
                collection_name=self.collection,
                points=[qmodels.PointStruct(
                    id=pid, vector=vector, payload=payload,
                )],
            )
        return pid

    # ─── search / recent / stats ─────────────────────────────────
    def _filter(
        self, tool: str | None = None, symbol: str | None = None,
    ) -> qmodels.Filter | None:
        must = []
        if tool:
            must.append(qmodels.FieldCondition(
                key="tool", match=qmodels.MatchValue(value=str(tool)),
            ))
        if symbol:
            must.append(qmodels.FieldCondition(
                key="symbol", match=qmodels.MatchValue(value=str(symbol)),
            ))
        return qmodels.Filter(must=must) if must else None

    def search(
        self,
        query_vector: list[float],
        top_k: int = 5,
        tool: str | None = None,
        symbol: str | None = None,
    ) -> list[dict[str, Any]]:
        res = self.client.query_points(
            collection_name=self.collection,
            query=list(query_vector),
            query_filter=self._filter(tool, symbol),
            limit=max(1, min(int(top_k), 50)),
            with_payload=True,
            with_vectors=False,
        )
        out: list[dict[str, Any]] = []
        for p in (getattr(res, "points", None) or []):
            row = dict(p.payload or {})
            row["score"] = getattr(p, "score", None)
            out.append(row)
        return out

    def recent(
        self,
        limit: int = 20,
        tool: str | None = None,
        symbol: str | None = None,
    ) -> list[dict[str, Any]]:
        """Newest-first snapshot list (client-side ts_epoch sort)."""
        limit = max(1, min(int(limit), 200))
        rows: list[dict[str, Any]] = []
        offset = None
        scan_cap = limit * 3
        while len(rows) < scan_cap:
            results, offset = self.client.scroll(
                collection_name=self.collection,
                scroll_filter=self._filter(tool, symbol),
                limit=min(256, scan_cap - len(rows)),
                with_payload=True,
                with_vectors=False,
                offset=offset,
            )
            for r in results:
                rows.append(dict(r.payload or {}))
            if offset is None:
                break
        rows.sort(key=lambda r: float(r.get("ts_epoch") or 0), reverse=True)
        return rows[:limit]

    def count_points(
        self, tool: str | None = None, symbol: str | None = None,
    ) -> int:
        try:
            res = self.client.count(
                collection_name=self.collection,
                count_filter=self._filter(tool, symbol),
                exact=True,
            )
            return int(getattr(res, "count", 0) or 0)
        except Exception:
            return -1