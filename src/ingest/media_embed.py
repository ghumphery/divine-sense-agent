"""Multimodal media→vector side-channel (EmbeddingGemma 2) — v0.9.114.0.

**Rule (user decision):** TEXT chunks keep the Qwen3 primary embedding
path (``file_docs_4b``, 2560-d). MEDIA — pictures attached inside
documents (DOCX/PPTX media) or standalone media files — is embedded by
EmbeddingGemma 2's **shared 768-dim text+image+audio space** through its
own llama.cpp server (default ``http://127.0.0.1:8081/v1/embeddings``,
unsloth build with ``--mmproj``) into a separate side collection
(``media_docs_gemma2``). Cross-modal retrieval: a plain TEXT query is
embedded with the ggema TEXT tower and searches the media collection —
the same space the image/audio vectors live in.

**STATUS (v0.9.122.1, re-verified 2026-10-13):** ggml-org#30082 was
RETRACTED by its own author — image embeddings ARE wired since llama.cpp
#30054 (merged 2026-10-06, builds b11457+): ``/v1/embeddings`` accepts
NESTED typed content parts. Correct request shape (reporter's live
verification on b11457 + mmproj: distinct images cos 0.78–0.89,
cross-modal 0.73):

    {"model": "...", "input": [
        {"content": [{"type": "image_url",
                      "image_url": {"url": "data:image/jpeg;base64,…"}}]}]}

(each input ITEM is ``{"content": [parts...]}`` — the SHALLOW
``input:[{type:…}]`` shape 400s).

**FAIL-CLOSED (the v0.9.112 hash-pollution lesson repeated):** the trap
is the LEGACY request shapes — internal ``/embedding`` + ``image_data``
may 200 with the TEXT-ONLY embedding of the literal media marker
(byte-identical between different images; still present on new builds)
and the shallow shape 400s. This module therefore:

1. posts the correct NESTED content shape FIRST;
2. probes the media path ONCE per process with two synthetic reference
   patterns (solid red vs solid blue) — ``cos > 0.999`` ⇒ the media
   path is JUDGED UNAVAILABLE (catches pre-#30054 server builds — e.g.
   the unsloth b11443-mix gemma2 server currently installed — AND any
   legacy-shape fakesuccess);
3. while unavailable, NEVER writes media-embedding points (the text
   description path — ``DESCRIBE_IMAGES`` → Qwen3 — keeps working as
   the semantic carrier for media content);
4. re-checks on a cooldown so a SERVER-BINARY UPGRADE lights the path
   up without code changes.

Public surface:
    media_embedding_enabled()            QDRANT_MEDIA_EMBEDDING (default off)
    media_collection()                   QDRANT_MEDIA_COLLECTION (media_docs_gemma2)
    media_embed_url()                    GEMMA2_EMBED_URL (…:8081/v1/embeddings)
    media_path_available() -> (ok, note) sanity self-check (cooled)
    embed_media_vector(path, kind) -> list|None   None = unavailable/failure
    index_media_batch(store, items) -> dict       side-collection upsert
    reset_media_probe()                           drop the cached verdict
"""
from __future__ import annotations

import base64
import io
import os
import time

_requests = None


def _req():
    global _requests
    if _requests is None:
        import requests
        _requests = requests
    return _requests


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name, "") or "").strip() or default


def media_embedding_enabled() -> bool:
    """``QDRANT_MEDIA_EMBEDDING`` (default OFF). When ON, document
    media and standalone media files get a second, media-side embedding
    into ``media_collection()`` IN ADDITION to the existing description
    text path."""
    return _env("QDRANT_MEDIA_EMBEDDING").lower() in ("1", "true", "yes", "on")


def media_ingest_files_enabled() -> bool:
    """``QDRANT_MEDIA_INGEST`` (default OFF): standalone media files
    (png/jpg/webp/gif/wav/mp3/mp4…) are ALSO ingested standalone (their
    description text into the text pool via the describer + their vector
    into the media pool when the media endpoint is available)."""
    return _env("QDRANT_MEDIA_INGEST").lower() in ("1", "true", "yes", "on")


def media_collection() -> str:
    return _env("QDRANT_MEDIA_COLLECTION", "media_docs_gemma2")


def media_embed_url() -> str:
    url = _env("GEMMA2_EMBED_URL", "")
    if not url:
        base = _env("OLLAMA_BASE_URL", "").rstrip("/")
        if base.endswith(":8081"):
            url = base.rstrip("/") + "/v1/embeddings"
        else:
            url = "http://127.0.0.1:8081/v1/embeddings"
    if "localhost" in url.split("//", 1)[-1].split("/")[0]:
        # same ::1 loopback trap as every other local URL in this repo
        url = url.replace("localhost", "127.0.0.1", 1)
    return url


media_model = "embeddinggemma-2:Q8_0"
_PROBE_COOLDOWN = 3600.0
_state = {"checked_ts": 0.0, "ok": False, "note": ""}


def reset_media_probe() -> None:
    """Drop the cached sanity verdict (tests / config reload)."""
    _state.update({"checked_ts": 0.0, "ok": False, "note": ""})


# ── low-level HTTP ───────────────────────────────────────────────────
def _post(url: str, payload: dict, timeout: float = 180):
    return _req().post(url, json=payload, timeout=timeout)


def _embed_texts_server(texts: list[str]) -> list[list[float]]:
    """TEXT embeddings through the ggema server's /v1/embeddings."""
    r = _post(media_embed_url(), {"input": texts})
    r.raise_for_status()
    return [d["embedding"] for d in r.json()["data"]]


def _media_internal_url() -> str:
    """llama.cpp internal ``/embedding`` endpoint on the same server."""
    return media_embed_url().rsplit("/v1/", 1)[0] + "/embedding"


def _ref_pattern(rgb: tuple[int, int, int]) -> bytes | None:
    """A tiny solid-colour PNG for the fake-success self-check."""
    try:
        from PIL import Image
    except ImportError:
        return None
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), rgb).save(buf, format="PNG")
    return buf.getvalue()


def _row_from_body(body) -> list | None:
    """Tolerant embedding-row extraction: the llama.cpp internal
    ``embedding`` key or the OpenAI-ish ``data[0].embedding`` /
    ``embeddings[0]`` envelope."""
    if not isinstance(body, dict):
        return None
    row = body.get("embedding")
    if row is None:
        rows = body.get("data") or body.get("embeddings") or []
        if rows:
            first = rows[0]
            row = (first.get("embedding")
                   if isinstance(first, dict) else first)
    return row if isinstance(row, list) and row else None


def _embed_image_bytes(png_b64: str,
                       mime: str = "image/png") -> list[float] | None:
    """IMAGE embedding attempt — v0.9.122.1 ORDER: the NESTED typed
    content-parts shape FIRST, the shape proven by the retracted
    ggml-org#30082 ("mtmd is wired into /v1/embeddings for this
    architecture"; llama.cpp #30054, builds b11457+):

        {"input": [{"content": [{"type": "image_url",
            "image_url": {"url": "data:<mime>;base64,…"}}]}]}

    Fallbacks (older server builds): the shallow OpenAI-ish image_url
    shape (400s on newer), then the internal ``/embedding`` +
    ``image_data`` shape. NOTE: the legacy shapes may 200 with a
    TEXT-ONLY vector on some builds — always pair with
    media_path_available()."""
    inner = {"type": "image_url",
             "image_url": {"url": f"data:{mime};base64," + png_b64}}
    try:
        r = _post(media_embed_url(), {
            "model": media_model,
            "input": [{"content": [inner]}]})
        if r.ok and (row := _row_from_body(r.json())) is not None:
            return row
    except Exception:
        pass
    try:
        r = _post(media_embed_url(),
                  {"model": media_model, "input": [inner]})
        if r.ok and (row := _row_from_body(r.json())) is not None:
            return row
    except Exception:
        pass
    try:
        r = _post(_media_internal_url(),
                  {"content": "<__image__>",
                   "image_data": [{"id": 10, "data": png_b64}]})
        if r.ok and (row := _row_from_body(r.json())) is not None:
            return row
    except Exception:
        pass
    return None


def _cos(a: list[float], b: list[float]) -> float:
    try:
        import numpy as np
        va, vb = (np.asarray(a, dtype="float32"),
                  np.asarray(b, dtype="float32"))
        n = float(np.linalg.norm(va) * np.linalg.norm(vb))
        return float(va @ vb / n) if n else 0.0
    except Exception:
        return 0.0


# ── sanity self-check (fake-success guard) ───────────────────────────
def media_path_available(force: bool = False) -> tuple[bool, str]:
    """True when the media endpoint embeds actual IMAGE DATA (not the
    text-only marker embedding). Two distinct reference images must come
    back cos ≤ MEDIA_FAKE_COS (0.999, ``QDRANT_MEDIA_FAKE_COS``); cos
    1.0 = the byte-identical marker embedding of ggml-org#30082.
    Verdict cached for a cooldown so an upstream fix lights the path up
    without restarting this process."""
    now = time.time()
    if force:
        reset_media_probe()
    if _state["checked_ts"] and now - _state["checked_ts"] < _PROBE_COOLDOWN:
        return _state["ok"], _state["note"]
    fake_cap = float(_env("QDRANT_MEDIA_FAKE_COS", "0.999") or 0.999)
    a, b = _ref_pattern((220, 30, 30)), _ref_pattern((30, 30, 220))
    if not a or not b:
        note = "Pillow unavailable — cannot synthesise reference images"
        _state.update({"checked_ts": now, "ok": False, "note": note})
        return False, note
    ok = False
    try:
        va = _embed_image_bytes(base64.b64encode(a).decode()) or []
        vb = _embed_image_bytes(base64.b64encode(b).decode()) or []
        if not va or not vb:
            note = "media endpoint returned no vector (embeddings API)"
        else:
            c = _cos(va, vb)
            ok = c <= fake_cap
            note = (f"image embed cos(red,blue)={c:.4f} ≤ {fake_cap} = REAL"
                    if ok else
                    f"image embed cos(red,blue)={c:.4f} > {fake_cap} = "
                    f"TEXT-ONLY MARKER FAKESUCCESS (ggml-org#30082) — "
                    f"media path judged UNAVAILABLE")
    except Exception as e:
        ok, note = False, f"probe failed: {e!r:.140}"
    _state.update({"checked_ts": now, "ok": ok, "note": note})
    return ok, note


_MEDIA_MIME = {".png": "image/png", ".jpg": "image/jpeg",
               ".jpeg": "image/jpeg", ".webp": "image/webp",
               ".gif": "image/gif", ".bmp": "image/bmp"}


def embed_media_vector(path: str, kind: str = "image") -> list[float] | None:
    """Media content embedding through the ggema server. Returns the
    raw 768-d row or None when unavailable (sanity probe gates every
    call — the unavailable verdict also fires when the endpoint only
    fakes success). ``kind`` = image|audio|video: IMAGE rides the
    nested #30054 content-parts shape (v0.9.122.1); AUDIO first tries
    the nested ``input_audio`` content part (not yet live-verified
    upstream) then the legacy internal shape — any failure stays a
    clean None (the pipeline still skips AV exts upstream of this
    call, wired pending verification); VIDEO keeps the legacy internal
    shape (no dedicated video content part documented for the
    embeddings endpoint)."""
    if not media_path_available()[0]:
        return None
    try:
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
    except Exception:
        return None
    if kind == "image":
        mime = _MEDIA_MIME.get(os.path.splitext(path)[1].lower(),
                               "image/jpeg")
        return _embed_image_bytes(b64, mime)
    if kind == "audio":
        fmt = os.path.splitext(path)[1].lower().lstrip(".") or "wav"
        try:
            r = _post(media_embed_url(), {
                "model": media_model,
                "input": [{"content": [
                    {"type": "input_audio",
                     "input_audio": {"data": b64, "format": fmt}}]}]})
            if r.ok and (row := _row_from_body(r.json())) is not None:
                return row
        except Exception:
            pass
    # audio/video legacy fallback (image never reaches here)
    try:
        r = _post(_media_internal_url(),
                  {"content": "<__audio__>",
                   "audio_data": [{"id": 10, "data": b64}]})
        r.raise_for_status()
        return _row_from_body(r.json())
    except Exception:
        return None


def _normalize(vec: list, dim: int) -> list[float]:
    """L2-normalise (ggema rows are near-unit already; keep parity with
    the repo's embedding convention)."""
    try:
        import numpy as np
        v = np.asarray(vec, dtype="float32").ravel()[:dim]
        n = float(np.linalg.norm(v))
        return (v / n if n else v).tolist()
    except Exception:
        return list(vec)[:dim]


# ── side-collection indexing ─────────────────────────────────────────
def index_media_batch(store, items: list[dict]) -> dict:
    """Upsert media-embedding side points into ``media_collection()``.

    ``items`` = dicts with:
        path      media file path (png/jpg/webp; audio/video when wired)
        kind      image|audio|video
        source    host document source (POSIX; the standalone file's own)
        filename  host document basename
        note      the vision description (or STT transcript) stored with
                  the point so a text-side search result can show context
        position  slide/paragraph/page marker text

    Gated by ``media_embedding_enabled()`` + the sanity verdict; NEVER
    raises to the ingest pipeline (fail-open — the description text path
    already carries the media semantics into the Qwen3 text pool).
    """
    if not items or not media_embedding_enabled():
        return {"indexed": 0, "unavailable": 0, "errors": 0}
    ok, note = media_path_available()
    if not ok:
        return {"indexed": 0, "unavailable": len(items), "errors": 0,
                "note": note}
    import uuid as _uuid
    try:
        from src.vector_store.qdrant_client import QdrantStore
    except Exception as e:  # store lib broken — stay silent
        return {"indexed": 0, "unavailable": 0, "errors": len(items),
                "note": repr(e)}
    try:
        side = (store if store is not None and store.collection ==
                media_collection() else QdrantStore(collection=media_collection()))
        side.collection = media_collection()
    except Exception as e:
        return {"indexed": 0, "unavailable": 0, "errors": len(items),
                "note": repr(e)}
    indexed = unavailable = errors = 0
    for it in items:
        try:
            vec = embed_media_vector(it.get("path", ""),
                                     it.get("kind", "image"))
            if vec is None:
                unavailable += 1
                continue
            side.upsert([{
                "id": _uuid.uuid4().hex,
                "vector": _normalize(vec, len(vec)),
                "payload": {
                    "text": (it.get("note") or "")[:1200],
                    "filename": it.get("filename", ""),
                    "media_name": (it.get("path") or "").rsplit("/", 1)[-1]
                    .rsplit("\\", 1)[-1],
                    "source": it.get("source", ""),
                    "media_kind": it.get("kind", "image"),
                    "media_path": it.get("path", ""),
                    "position": it.get("position", ""),
                    "content_hash": it.get("content_hash", ""),
                    "chunk_index": it.get("chunk_index", 0),
                    "total_chunks": it.get("total_chunks", 0),
                    "embedding_backend": ("embeddinggemma2-"
                                          + it.get("kind", "image")),
                },
            }])
            indexed += 1
        except Exception:
            errors += 1
    return {"indexed": indexed, "unavailable": unavailable,
            "errors": errors}