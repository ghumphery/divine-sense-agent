"""Ingest pipeline: load → fill image descriptions → chunk → embed → upsert.

v0.2.3: full rewrite mirroring milvus-mcp-agent's v0.1.19 ingest flow,
with these qdrant-specific additions kept from v0.2.2:
  - ``_SKIP_EXTS`` (binary / unsupported formats)
  - ``_max_file_size_bytes()`` (per-file cap, ``MAX_FILE_SIZE_MB``)
  - ``~$xxx.pptx`` lock file skip (in loader.load_file)
  - graceful exception handling per file (skip + print, never abort)

Differences from milvus-mcp-agent:
  - We delete old chunks by ``source`` before re-ingest (milvus
    pymilvus's `delete_by_source` is structurally identical but Qdrant
    uses payload filter on `source` field).
  - Qdrant HTTP server is always-loaded, so we skip the
    ``release_collection()`` step that milvus does at the end (no-op).
"""
from __future__ import annotations

import hashlib
import os
import re
import uuid
from pathlib import Path
from typing import Any, Callable

from src.config import config
from src.ingest.chunker import TextChunker, DocumentChunk
from src.ingest.embedder import Embedder
from src.ingest.image_describer import ImageDescriber
from src.ingest.loader import FileLoader, load_file
from src.vector_store.qdrant_client import QdrantStore


# File extensions we deliberately ignore during directory ingest.
_SKIP_EXTS = frozenset({
    # archives / binaries
    ".zip", ".tar", ".gz", ".tgz", ".7z", ".rar",
    # images (we don't extract or describe yet)
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".ico", ".tiff",
    # vector / drawio / fonts / executables
    ".svg", ".drawio", ".ttf", ".otf", ".woff", ".woff2",
    ".exe", ".dll", ".so", ".dylib", ".bin", ".iso", ".img",
    # encrypted / compressed payloads (useless to chunker)
    ".encrypted",
    # unsupported office formats
    ".xls", ".xlsm",  # v0.9.120.1: .xlsx is now loaded (openpyxl);
                      # .xls/.xlsm stay unsupported (BIFF/macros)
})


def _max_file_size_bytes() -> int:
    """Per-file size cap (env: ``MAX_FILE_SIZE_MB``, default 50 MB)."""
    return int(os.environ.get("MAX_FILE_SIZE_MB", "50")) * 1024 * 1024


def _embed_fail_closed() -> bool:
    """``QDRANT_EMBED_FAIL_CLOSED`` (v0.9.112.0, default OFF = the
    tag-and-continue behaviour). When ON, a batch whose embedding fell
    back to the HASH backend is refused BEFORE upsert — ingest skips
    the file with a clear error instead of silently polluting the
    collection with non-semantic vectors."""
    return ((os.environ.get("QDRANT_EMBED_FAIL_CLOSED", "") or "")
            .strip().lower() in ("1", "true", "yes", "on"))


def _media_embedding_enabled() -> bool:
    """Thin re-export so the pipeline module doesn't re-read the env
    (v0.9.114.0 media side-car gate lives in ``src/ingest/media_embed``)."""
    from src.ingest.media_embed import media_embedding_enabled
    return media_embedding_enabled()


def _media_collection() -> str:
    from src.ingest.media_embed import media_collection
    return media_collection()


def _media_ingest_files_enabled() -> bool:
    from src.ingest.media_embed import media_ingest_files_enabled
    return media_ingest_files_enabled()


def _heading_metadata_enabled() -> bool:
    """``QDRANT_HEADING_METADATA`` (v0.9.113.0, default OFF).

    When ON the ingest goes heading-aware: the DOCX loader prefixes ATX
    ``#`` heading lines, chunking splits on heading sections and every
    chunk payload carries ``heading_path`` (the accumulated "H1 > H2"
    stack). OFF = byte-identical legacy ingest (no loader change, no
    chunk-boundary change, no new payload field — zero re-embed cost).
    Enabling it changes chunk boundaries → content hashes → the next
    ingest of every affected file re-embeds once."""
    return ((os.environ.get("QDRANT_HEADING_METADATA", "") or "")
            .strip().lower() in ("1", "true", "yes", "on"))


class IngestPipeline:
    """End-to-end file ingestion with image description + dedup + batching.

    Workflow (mirrors milvus-mcp-agent):
      1. ``FileLoader.load_file`` → text + image placeholders
      2. ``ImageDescriber.describe_batch`` → swap placeholders for
         vision-generated bilingual paragraphs (Ollama Cloud)
      3. ``TextChunker.chunk_document`` → DocumentChunk list
      4. ``Embedder.embed_batch`` → vectors (cache-aware)
      5. ``QdrantStore.upsert`` → batched 500/batch
    """

    # Image marker regexes (same as milvus-mcp-agent).
    IMAGE_MARKER_RE = re.compile(r"\[Image:\s*([^\]|]+?)(?:\s*\|\s*alt='([^']*)')?\s*\]")
    IMAGE_MARKER_RE_V2 = re.compile(
        r"(?:^|\n)>?\s*\[image:\s*([^\]|]+?)(?:\s*\|\s*alt='([^']*)')?\s*\]"
    )

    # Process chunks in fixed-size batches to keep per-file peak memory bounded.
    EMBED_BATCH_SIZE = 64
    INSERT_BATCH_SIZE = 500

    # v0.9.114.0 — media side-channel: standalone media files may be
    # ingested when QDRANT_MEDIA_INGEST=on (describer text → Qwen3 pool
    # + EmbeddingGemma2 media vector → media side collection).
    _MEDIA_IMAGE_EXTS = frozenset(
        {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff"})
    _MEDIA_AV_EXTS = frozenset(  # wire-up pending upstream; still skipped
        {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".mp4", ".mov", ".webm"})

    @staticmethod
    def _normalize_source(path: Path) -> str:
        """POSIX-style source key (avoids Windows backslash escape issues)."""
        return path.resolve().as_posix()

    def __init__(self):
        self.loader = FileLoader()
        self.chunker = TextChunker()
        self.embedder = Embedder()
        self.store = QdrantStore()
        # ImageDescriber is optional — only used when DESCRIBE_IMAGES is on
        # AND OLLAMA_API_KEY is set (its own `enabled` check).
        self.describer = (
            ImageDescriber() if config.ingest.describe_images else None
        )

    # ── Image placeholder filling ────────────────────────────────────
    def _fill_image_descriptions(self, doc_path: Path, raw_text: str) -> str:
        """Swap ``[Image: name]`` markers for vision descriptions.

        Marker-to-image mapping is positional: the N-th ``[Image:]`` marker
        in the text corresponds to the N-th image extracted from the
        archive (DOCX/PPTX store media in document order).
        """
        if self.describer is None or doc_path.suffix.lower() not in (".docx", ".pptx"):
            return raw_text
        if not self.describer.enabled:
            return raw_text

        cache_dir = config.ingest.image_cache_dir / doc_path.stem
        images = self.loader.extract_media(doc_path, cache_dir)
        if not images:
            return raw_text

        # Pre-compute descriptions in parallel (workers + SHA-1 cache)
        descs = self.describer.describe_batch(images)

        # v0.9.114.0 — MEDIA side-car (rule: "圖/音訊用 EmbeddingGemma，
        # 文字走 Qwen3"): each embedded picture ALSO gets a media-vector
        # point on EmbeddingGemma 2's shared 768-d space (separate
        # collection) for cross-modal retrieval. Fail-open + sanity
        # gated (fake-success guarded — see media_embed.py) — the
        # description text below keeps flowing to the Qwen3 text pool.
        if _media_embedding_enabled():
            try:
                from src.ingest.media_embed import index_media_batch

                src = self._normalize_source(doc_path)
                rep = index_media_batch(self.store, [
                    {"path": str(img), "kind": "image",
                     "source": src, "filename": doc_path.name,
                     "note": desc, "position": f"media #{n + 1}"}
                    for n, (img, desc) in enumerate(zip(images, descs))
                ])
                if rep.get("indexed"):
                    print(f"  [media] side-car indexed {rep['indexed']} "
                          f"media vector(s) into "
                          f"{_media_collection()}")
                elif rep.get("unavailable"):
                    print(f"  [media] side-car unavailable "
                          f"({(rep.get('note') or '')[:90]})")
            except Exception as e:
                print(f"  [pipeline] media side-car skipped: {e!r:.100}")

        # Find all image markers in document order.
        pattern = (
            self.IMAGE_MARKER_RE_V2 if doc_path.suffix.lower() == ".pptx"
            else self.IMAGE_MARKER_RE
        )
        matches = list(pattern.finditer(raw_text))
        if not matches:
            return raw_text

        # Walk matches right-to-left so each replacement preserves earlier
        # indices in the original string (no offset bookkeeping needed).
        result = raw_text
        for idx, match in enumerate(reversed(matches)):
            pos = len(matches) - 1 - idx
            name = match.group(1).strip()
            alt = match.group(2) or ""
            if pos >= len(descs):
                continue
            header = f"[Image: {name}"
            if alt:
                header += f" | alt='{alt}'"
            header += "]\n" + descs[pos]
            result = result[:match.start()] + header + result[match.end():]
        return result

    def _ingest_media_file(self, p: Path) -> int:
        """Standalone media file ingest (v0.9.114.0, gated).

        1. Vision-describe the image → ONE text point through the normal
           chunk→embed→upsert chain (Qwen3 primary pool — the media's
           semantic carrier for text search).
        2. Media side-car: EmbeddingGemma2 vector of the raw image into
           the media side collection (fail-open, sanity-gated).
        """
        try:
            size = p.stat().st_size
        except OSError as e:
            print(f"  [pipeline] media stat failed: {p.name}: {e!r}")
            return 0
        cap = _max_file_size_bytes()
        if size > cap:
            print(f"  [pipeline] skip oversize media ({size} > cap): "
                  f"{p.name}")
            return 0

        desc = ""
        if self.describer is not None and self.describer.enabled:
            try:
                descriptions = self.describer.describe_batch([str(p)])
                if descriptions:
                    desc = str(descriptions[0] or "")
            except Exception as e:
                print(f"  [pipeline] media describe failed: {p.name}: "
                      f"{e!r:.120}")
        text = (f"[media image: {p.name}]\n{desc}").strip() if desc else ""
        if not text:
            print(f"  [pipeline] media has no description (vision "
                  f"backend off/failed), nothing indexed: {p.name}")
            return 0

        src = self._normalize_source(p)
        chunks = self.chunker.chunk_document(
            text=text,
            file_path=str(p),
            metadata={
                "filename": p.name,
                "extension": p.suffix.lower(),
                "file_size": size,
                "last_modified": str(p.stat().st_mtime),
            },
        )
        if not chunks:
            return 0
        new_hashes = [hashlib.sha256(c.text.encode("utf-8")).hexdigest()[:32]
                      for c in chunks]
        existing = self.store.get_existing_hashes(src)
        if existing and existing >= set(new_hashes):
            print(f"  ↻ {p.name}: media unchanged, skipping")
        else:
            if existing:
                self.store.delete_by_source(src)
        inserted = self._embed_and_upsert(p, chunks, new_hashes, src)

        # media side-car (EmbeddingGemma2 vector of the raw image)
        if _media_embedding_enabled():
            try:
                from src.ingest.media_embed import index_media_batch

                rep = index_media_batch(self.store, [{
                    "path": str(p), "kind": "image", "source": src,
                    "filename": p.name, "note": desc, "position": "",
                }])
                if rep.get("indexed"):
                    print(f"  [media] side-car indexed 1 media "
                          f"vector → {_media_collection()}")
                elif rep.get("unavailable"):
                    print(f"  [media] side-car unavailable "
                          f"({(rep.get('note') or '')[:90]})")
            except Exception as e:
                print(f"  [pipeline] media side-car skipped: {e!r:.100}")
        return inserted

    # ── Per-file ingest ──────────────────────────────────────────────
    def ingest_file(self, file_path: str, *,
                    stop_check: Callable[[], bool] | None = None) -> int:
        """Ingest a single file. Returns chunks upserted.

        Mirrors milvus-mcp-agent dedup flow:
          1. Load + chunk
          2. Compute content_hash for each chunk
          3. If all hashes already in Qdrant for this source → skip
          4. Else delete old chunks for this source, then upsert

        v0.9.133.5 — ``stop_check``: an optional zero-arg callable polled
        BETWEEN embed batches (v0.9.133.5 background-ingest stop chain:
        the task-registry stop flag). ``True`` → the batch loop breaks
        EARLY and the already-upserted prefix stays (dedup-aware
        re-runs overwrite the same ids). Default ``None`` = legacy
        uninterruptible behaviour, byte-identical.
        """
        p = Path(file_path)
        if not p.exists():
            raise FileNotFoundError(str(p))

        # ── Cheap pre-filters (v0.2.2) ──
        ext = p.suffix.lower()
        # v0.9.114.0 — standalone media files: ingestable when the media
        # side-channel ingest gate is ON (image describer text → Qwen3
        # text pool + EmbeddingGemma2 media vector → media side
        # collection). Audio/video wiring is pending upstream — skipped
        # with a note until then.
        if ext in self._MEDIA_IMAGE_EXTS:
            if _media_ingest_files_enabled():
                return self._ingest_media_file(p)
            print(f"  [pipeline] skip media file (QDRANT_MEDIA_INGEST="
                  f"off): {p.name}")
            return 0
        if ext in self._MEDIA_AV_EXTS:
            print(f"  [pipeline] skip audio/video file (media-embed "
                  f"wiring pending upstream, see media_embed.py): "
                  f"{p.name}")
            return 0
        if ext in _SKIP_EXTS:
            print(f"  [pipeline] skip unsupported ext: {p.name}")
            return 0
        try:
            size = p.stat().st_size
        except OSError as e:
            print(f"  [pipeline] stat failed: {p.name}: {e!r}")
            return 0
        cap = _max_file_size_bytes()
        if size > cap:
            print(f"  [pipeline] skip oversize ({size // (1024*1024)} MB > "
                  f"{cap // (1024*1024)} MB cap): {p.name}")
            return 0

        # 1. Load text (with image placeholders for docx/pptx)
        raw_text = self.loader.load_file(p)
        if not raw_text.strip():
            return 0

        # 2. Fill image placeholders with vision descriptions
        raw_text = self._fill_image_descriptions(p, raw_text)

        # 3. Chunk with metadata (v0.9.113.0 — heading-aware segmentation
        #    when QDRANT_HEADING_METADATA is on; fail-open to the legacy
        #    path on any heading machinery error).
        chunk_meta = {
            "filename": p.name,
            "extension": p.suffix.lower(),
            "file_size": size,
            "last_modified": str(p.stat().st_mtime),
        }
        if _heading_metadata_enabled():
            try:
                chunks: list[DocumentChunk] = self.chunker.chunk_document_with_headings(
                    text=raw_text,
                    file_path=str(p),
                    metadata=chunk_meta,
                )
            except Exception as e:
                print(f"  [pipeline] heading-aware chunking failed, "
                      f"falling back ({e!r:.120})")
                chunks = self.chunker.chunk_document(
                    text=raw_text, file_path=str(p), metadata=chunk_meta)
        else:
            chunks = self.chunker.chunk_document(
                text=raw_text, file_path=str(p), metadata=chunk_meta)
        if not chunks:
            return 0

        # 4. Compute content_hash per chunk
        new_hashes = [
            hashlib.sha256(c.text.encode("utf-8")).hexdigest()[:32]
            for c in chunks
        ]

        # 5. Dedup probe — if every hash already exists, skip
        src = self._normalize_source(p)
        existing = self.store.get_existing_hashes(src)
        new_set = set(new_hashes)
        if existing and existing >= new_set:
            print(f"  ↻ {p.name}: unchanged, skipping "
                  f"({len(chunks)} chunks already indexed)")
            return 0

        # 6. If some chunks changed, delete old ones for clean replace
        if existing:
            deleted = self.store.delete_by_source(src)
            print(f"  ↻ {p.name}: content changed, replacing "
                  f"{deleted} old chunks")

        # 7. Embed in batches (with LRU cache)
        return self._embed_and_upsert(p, chunks, new_hashes, src,
                                      stop_check=stop_check)

    def _embed_and_upsert(
        self,
        p: Path,
        chunks: list[DocumentChunk],
        hashes: list[str],
        src: str,
        *,
        stop_check: Callable[[], bool] | None = None,
    ) -> int:
        """Run batched embed + upsert. Returns total chunks inserted.

        v0.9.133.5 — ``stop_check`` (the background-ingest stop chain)
        is polled at each batch boundary; ``True`` breaks the loop and
        the already-upserted prefix stays (later dedup re-runs rewrite
        the same deterministic ids, so a partial ingest is safe to
        resume by simply re-adding the document).
        """
        file_inserted = 0
        for batch_start in range(0, len(chunks), self.EMBED_BATCH_SIZE):
            if stop_check is not None and stop_check():
                print(f"  ⏹ {p.name}: stop requested after "
                      f"{file_inserted}/{len(chunks)} chunks — "
                      f"partial ingest (idempotent re-run resumes)")
                break
            batch_chunks = chunks[batch_start:batch_start + self.EMBED_BATCH_SIZE]
            batch_hashes = hashes[batch_start:batch_start + self.EMBED_BATCH_SIZE]
            texts = [c.text for c in batch_chunks]
            embeddings = self.embedder.embed_batch(texts)
            # v0.9.112.0 — per-chunk backend labels + optional
            # hash-pollution fail-closed (checked BEFORE any upsert,
            # never after part of the batch is written).
            used_map = (
                getattr(self.embedder, "last_backends", {}) or {}
            )
            if isinstance(used_map, dict):
                used_map = dict(used_map)
            else:
                used_map = {}
            n_hash = sum(
                1 for b in used_map.values() if b == "hash"
            )
            if n_hash and _embed_fail_closed():
                raise RuntimeError(
                    f"embedding fell back to the hash backend for "
                    f"{n_hash}/{len(texts)} chunks "
                    f"(QDRANT_EMBED_FAIL_CLOSED=on) — '{p.name}' "
                    f"skipped to keep the collection pollution-free; "
                    f"fix the embedding backend (watchdog logs: "
                    f"embed-watchdog) or set QDRANT_EMBED_FAIL_CLOSED"
                    f"=off to tag-and-continue"
                )
            texts = None  # free

            for ins_start in range(0, len(batch_chunks), self.INSERT_BATCH_SIZE):
                sub_chunks = batch_chunks[ins_start:ins_start + self.INSERT_BATCH_SIZE]
                sub_hashes = batch_hashes[ins_start:ins_start + self.INSERT_BATCH_SIZE]
                sub_embs = embeddings[ins_start:ins_start + self.INSERT_BATCH_SIZE]
                points: list[dict[str, Any]] = []
                for off, (chunk, emb, content_hash) in enumerate(
                        zip(sub_chunks, sub_embs, sub_hashes)):
                    points.append({
                        "id": uuid.uuid4().hex,
                        "vector": emb,
                        "payload": {
                            "text": chunk.text,
                            "filename": chunk.metadata.get("filename", p.name),
                            "source": src,
                            "content_hash": content_hash,
                            "chunk_index": chunk.metadata.get("chunk_index", 0),
                            "total_chunks": chunk.metadata.get("total_chunks", 0),
                            "extension": chunk.metadata.get("extension", ""),
                            "file_size": chunk.metadata.get("file_size", 0),
                            "last_modified": chunk.metadata.get("last_modified", ""),
                            # v0.9.113.0 — STAIR heading path (empty for the
                            # legacy path / preamble; backfill-headings
                            # retro-fills missing values from payload text).
                            "heading_path": chunk.metadata.get(
                                "heading_path", ""),
                            # v0.9.112.0 — which backend produced this
                            # vector (reembed-hash audit input; the
                            # heuristic re-check never trusts it).
                            "embedding_backend": used_map.get(
                                ins_start + off, ""),
                        },
                    })
                self.store.upsert(points)
                file_inserted += len(points)
                sub_chunks = sub_hashes = sub_embs = points = None

            batch_chunks = batch_hashes = embeddings = None

        return file_inserted

    # ── Directory ingest ─────────────────────────────────────────────
    def ingest_directory(self, dir_path: str) -> dict[str, int]:
        """Recursively ingest a directory. Returns {filename: chunks_added}."""
        import gc

        root = Path(dir_path)
        results: dict[str, int] = {}
        total_chunks = 0
        skipped = 0

        # Use FileLoader.scan_directory (filters by supported_extensions)
        for fpath in self.loader.scan_directory(root):
            try:
                n = self.ingest_file(str(fpath))
                results[fpath.name] = n
                if n == 0:
                    skipped += 1
                else:
                    total_chunks += n
                    print(f"  ✓ {fpath.name}: {n} chunks "
                          f"({total_chunks} total)")
            except Exception as e:
                print(f"  ✗ {fpath.name}: {e!r}")
                results[fpath.name] = 0
                skipped += 1
            finally:
                # Force Python to release intermediate vectors between files.
                gc.collect()

        if skipped:
            print(f"  [pipeline] skipped {skipped} file(s) "
                  f"(unchanged / size / format / extension)")
        ok = sum(1 for v in results.values() if v > 0)
        print(f"  [Qdrant] Ingestion complete: {total_chunks} chunks "
              f"from {ok} files")
        return results


# Keep the old module-level name for any direct callers (none in our
# codebase, but harmless to expose for downstream).
__all__ = ["IngestPipeline", "_SKIP_EXTS", "_max_file_size_bytes"]