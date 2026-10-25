"""Persistent disk cache for embedding vectors.

Why this exists
---------------
``src.ingest.embedder.Embedder`` has an in-process LRU cache (size
4096) that dies when the process exits. That means:

  * ``cli.py ingest some.pdf`` computes 300 vectors, stores them, exits.
  * Next run re-ingests the same file -> 300 more API calls, all
    duplicates of the previous batch.
  * GUI ``chat`` questions that have been answered before also
    re-embed on every launch.

This module gives Embedder a SQLite-backed cache so hits survive
across processes. The matrix layout is the obvious one row = one
embedding, with SHA-1 of the text as PRIMARY KEY. We key on
``(backend, model)`` so switching between Qwen3-Embedding vs
nomic-embed-text doesn't collide.

Cache location
--------------
``$QDRANT_RAG_HOME/embed_cache.db`` — defaults to
``~/.qdrant-rag-agent/embed_cache.db``. Same dir as the session
JSON files so a user backing up one folder gets the other.

Configuration
-------------
* ``EMBEDDING_CACHE`` env var:
    - ``disk`` (default) - use SQLite, hits are persisted
    - ``memory`` - SQLite is held in RAM (``:memory:``), wiped on exit
    - ``off``   - skip persistent layer (in-process LRU still applies)
* ``EMBEDDING_CACHE_MAX_ENTRIES`` env var:
    - approximate cap; when exceeded the oldest entries by
      ``last_used_at`` are evicted in chunks (default 200_000)
"""
from __future__ import annotations

import hashlib
import logging
import os
import sqlite3
import struct
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)


_DEFAULT_MAX_ENTRIES = 200_000


def _cache_dir() -> Path:
    """Resolve the directory holding ``embed_cache.db``.

    Honours ``QDRANT_RAG_HOME`` (same as ``SessionManager``), falls
    back to ``~/.qdrant-rag-agent`` on Win/macOS/Linux.
    """
    base = Path(
        os.environ.get("QDRANT_RAG_HOME", Path.home() / ".qdrant-rag-agent")
    )
    p = base / "embed_cache.db"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p

class DiskEmbeddingCache:
    """SQLite-backed embedding cache.

    Thread-safe (one connection, guarded by a Lock). SQLite handles
    cross-process safety too (WAL mode, BUSY timeout).

    Parameters
    ----------
    db_path : Path | None
        Override the cache location. Defaults to
        ``~/.qdrant-rag-agent/embed_cache.db``.
    in_memory : bool
        Use ``:memory:`` (no persistence). Useful for tests.
    max_entries : int
        Soft cap on row count. When exceeded, oldest 10% by
        ``last_used_at`` are evicted in one transaction.
    """

    _SCHEMA_VERSION = 1

    def __init__(
        self,
        db_path: Path | None = None,
        in_memory: bool = False,
        max_entries: int = _DEFAULT_MAX_ENTRIES,
    ):
        self._lock = threading.Lock()
        self.in_memory = in_memory
        self.max_entries = max_entries
        self._path = ":memory:" if in_memory else str(db_path or _cache_dir())

        # ``check_same_thread=False`` so the embedding thread can call
        # us freely. We still serialise writes via the lock.
        self._conn = sqlite3.connect(
            self._path,
            check_same_thread=False,
            timeout=30.0,
            isolation_level=None,
        )
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA temp_store=MEMORY")
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS meta (
                    k TEXT PRIMARY KEY,
                    v TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS embeddings (
                    key TEXT PRIMARY KEY,
                    backend TEXT NOT NULL,
                    model   TEXT NOT NULL,
                    dim     INTEGER NOT NULL,
                    vec     BLOB NOT NULL,
                    n_hits  INTEGER NOT NULL DEFAULT 0,
                    created_at    INTEGER NOT NULL,
                    last_used_at  INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_emb_last_used
                    ON embeddings (last_used_at);
            """)
            cur = self._conn.execute(
                "SELECT v FROM meta WHERE k = 'schema_version'"
            )
            row = cur.fetchone()
            if row is None:
                self._conn.execute(
                    "INSERT INTO meta(k, v) VALUES ('schema_version', ?)",
                    (str(self._SCHEMA_VERSION),),
                )
            elif int(row["v"]) != self._SCHEMA_VERSION:
                logger.warning(
                    "embed cache schema version mismatch (have %s, want %s) - wiping",
                    row["v"], self._SCHEMA_VERSION,
                )
                self._conn.execute("DELETE FROM embeddings")
                self._conn.execute(
                    "UPDATE meta SET v = ? WHERE k = 'schema_version'",
                    (str(self._SCHEMA_VERSION),),
                )

    # ── public API ─────────────────────────────────────────────

    @staticmethod
    def make_key(text: str, backend: str, model: str) -> str:
        """Stable cache key: SHA-1 of (backend|model|text)."""
        h = hashlib.sha1()
        h.update(backend.encode("utf-8"))
        h.update(b"|")
        h.update(model.encode("utf-8"))
        h.update(b"|")
        h.update(text.encode("utf-8", errors="replace"))
        return h.hexdigest()

    def get(self, key: str) -> list[float] | None:
        """Return cached vector for ``key`` or ``None``.

        Side-effect: bumps ``n_hits`` and updates ``last_used_at``.
        """
        with self._lock:
            cur = self._conn.execute(
                "SELECT vec, dim FROM embeddings WHERE key = ?", (key,)
            )
            row = cur.fetchone()
            if row is None:
                return None
            try:
                self._conn.execute(
                    "UPDATE embeddings SET n_hits = n_hits + 1, last_used_at = ? WHERE key = ?",
                    (int(time.time()), key),
                )
            except sqlite3.Error:
                pass
            return _blob_to_vec(row["vec"])

    def put(self, key: str, backend: str, model: str, vec: list[float]) -> None:
        """Store ``vec`` under ``key``. Overwrites silently if key exists."""
        dim = len(vec)
        blob = _vec_to_blob(vec)
        now = int(time.time())
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO embeddings(key, backend, model, dim, vec,
                                      n_hits, created_at, last_used_at)
                VALUES (?, ?, ?, ?, ?, 0, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    backend = excluded.backend,
                    model   = excluded.model,
                    dim     = excluded.dim,
                    vec     = excluded.vec,
                    last_used_at = excluded.last_used_at
                """,
                (key, backend, model, dim, blob, now, now),
            )

    def has(self, key: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "SELECT 1 FROM embeddings WHERE key = ?", (key,)
            )
            return cur.fetchone() is not None

    def clear(self) -> int:
        """Wipe all cached vectors. Returns row count before wipe."""
        with self._lock:
            n = self._conn.execute(
                "SELECT COUNT(*) AS n FROM embeddings"
            ).fetchone()["n"]
            self._conn.execute("DELETE FROM embeddings")
            return n

    def stats(self) -> dict:
        """Summary dict suitable for the GUI admin tab."""
        with self._lock:
            cur = self._conn.execute("""
                SELECT
                    COUNT(*)                              AS n,
                    COALESCE(SUM(n_hits), 0)              AS total_hits,
                    MIN(created_at)                       AS oldest_ts,
                    MAX(last_used_at)                     AS newest_ts
                FROM embeddings
            """)
            row = cur.fetchone()
            n = int(row["n"]) if row and row["n"] is not None else 0
            total_hits = int(row["total_hits"]) if row and row["total_hits"] is not None else 0
            return {
                "enabled": True,
                "path": self._path,
                "in_memory": self.in_memory,
                "n_entries": n,
                "total_hits": total_hits,
                "hit_ratio": (total_hits / (total_hits + n)) if (total_hits + n) else 0.0,
                "max_entries": self.max_entries,
                "oldest_at": row["oldest_ts"] if row else None,
                "newest_at": row["newest_ts"] if row else None,
            }

    def evict_lru(self, target_entries: int) -> int:
        """Evict oldest-by-``last_used_at`` rows until under cap."""
        with self._lock:
            n = self._conn.execute(
                "SELECT COUNT(*) AS n FROM embeddings"
            ).fetchone()["n"]
            if n <= target_entries:
                return 0
            excess = n - target_entries
            cut = max(excess, self.max_entries // 10)
            self._conn.execute(
                """
                DELETE FROM embeddings WHERE key IN (
                    SELECT key FROM embeddings ORDER BY last_used_at ASC LIMIT ?
                )
                """,
                (cut,),
            )
            return cut

    def maybe_evict(self) -> int:
        """Evict if over soft cap. Called after every batch put."""
        return self.evict_lru(self.max_entries)

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass



# ─── serialisation helpers ─────────────────────────────────────


def _vec_to_blob(vec: list[float]) -> bytes:
    """Pack a list of floats into a little-endian float32 blob."""
    return struct.pack(f"<{len(vec)}f", *vec)


def _blob_to_vec(blob: bytes) -> list[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"<{n}f", blob))


# ─── mode resolution ────────────────────────────────────────────


def cache_mode_from_env() -> str:
    """Resolve ``EMBEDDING_CACHE`` env var to ``disk|memory|off``.

    Default: ``disk``.
    """
    m = (os.environ.get("EMBEDDING_CACHE", "disk") or "disk").strip().lower()
    if m not in ("disk", "memory", "off"):
        logger.warning("unknown EMBEDDING_CACHE=%r, falling back to 'disk'", m)
        m = "disk"
    return m


def build_cache(mode: str | None = None) -> "DiskEmbeddingCache | None":
    """Factory honouring ``EMBEDDING_CACHE`` env var.

    Returns ``None`` when mode is ``off`` so the Embedder can skip
    the layer without sprinkling env checks everywhere.
    """
    mode = mode or cache_mode_from_env()
    if mode == "off":
        return None
    in_mem = (mode == "memory")
    max_entries = int(
        os.environ.get("EMBEDDING_CACHE_MAX_ENTRIES", str(_DEFAULT_MAX_ENTRIES))
    )
    return DiskEmbeddingCache(in_memory=in_mem, max_entries=max_entries)



