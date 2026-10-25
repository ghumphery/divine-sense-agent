# -*- coding: utf-8 -*-
"""Multi-line snippet store — the ⚡ quick-phrase GLOBAL entries.

v0.9.122.0 — MCP-managed MULTI-LINE upgrade of the v0.9.100/101
snippets (user: 「現在的 snippet 只有單行，以 MCP 架構提供可以編輯
多行的 snippet 的功能和管理功能」).

STORE — ``$QDRANT_RAG_HOME/snippets.json`` → ``{"snippets": [str, ...]}``:
canonical entries are plain **MULTILINE strings** — the chat ⚡ panel
(Api snippets_get/save), THIS MCP host share ONE file, zero migration
(inner ``\\n`` kept verbatim; the GUI chip shows the first line; the
panel ➕ row is now a textarea).

CAPS — ``MAX_SNIPPETS = 100`` entries, ``MAX_CHARS = 2000`` per entry
(a full multi-line template); honoured by EVERY write path. Order
preserved; exact duplicates refused by add/edit; never raises on
read (corrupt → []).
"""
from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MAX_SNIPPETS = 100
MAX_CHARS = 2000

_LOCK = threading.RLock()


def snippets_path() -> Path:
    """The shared global store file (QDRANT_RAG_HOME/snippets.json)."""
    home = (os.environ.get("QDRANT_RAG_HOME", "") or "").strip()
    root = Path(home) if home else (
        Path(os.path.expanduser("~")) / ".qdrant-rag-agent")
    return root / "snippets.json"


def normalize(text: Any) -> str | None:
    """One entry: any → normalised str (CRLF→LF, outer strip, char
    cap) or None when it can't be one. INNER newlines are KEPT —
    that is the whole point of the multi-line upgrade."""
    if not isinstance(text, str):
        return None
    s = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not s:
        return None
    return s[:MAX_CHARS]


def load_snippets() -> list[str]:
    """Validating read (dedupe preserves order; corrupt file → [])."""
    with _LOCK:
        try:
            p = snippets_path()
            if not p.is_file():
                return []
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            logger.warning("[snippets] load failed: %r", e)
            return []
        items = data.get("snippets") if isinstance(data, dict) \
            else data
        if not isinstance(items, list):
            return []
    out: list[str] = []
    for s in items:
        v = normalize(s)
        if v and v not in out:
            out.append(v)
        if len(out) >= MAX_SNIPPETS:
            break
    return out


def save_snippets(items: Any) -> list[str]:
    """Validated whole-list replace (the GUI save path; atomic)."""
    with _LOCK:
        out: list[str] = []
        for s in items if isinstance(items, list) else []:
            v = normalize(s)
            if v and v not in out:
                out.append(v)
            if len(out) >= MAX_SNIPPETS:
                break
        p = snippets_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(f".json.tmp{uuid.uuid4().hex[:6]}")
        tmp.write_text(
            json.dumps({"snippets": out}, ensure_ascii=False, indent=1),
            encoding="utf-8")
        try:
            tmp.replace(p)
        except Exception as e:  # noqa: BLE001
            logger.warning("[snippets] save failed: %r", e)
            raise
    return out


def _snippet_view(items: list[str]) -> list[dict]:
    return [{
        "index": i,
        "preview": s.split("\n")[0][:80] + (" …" if "\n" in s else ""),
        "multi": "\n" in s,
        "lines": s.count("\n") + 1,
        "text": s,
    } for i, s in enumerate(items)]


def list_snippets(limit: int = 0) -> dict:
    """All snippets with per-entry index / preview / multi flags and
    the FULL text (an editor needs it to change one back)."""
    with _LOCK:
        items = load_snippets()
    lim = 0
    try:
        lim = max(0, min(int(limit or 0), MAX_SNIPPETS))
    except (TypeError, ValueError):
        lim = 0
    if lim:
        items = items[:lim]
    return {"ok": True, "count": len(items),
            "max": MAX_SNIPPETS, "snippets": _snippet_view(items)}


def add_snippet(text: Any, at: Any = None) -> dict:
    """Append (or insert at ``at``) one MULTI-LINE snippet."""
    v = normalize(text)
    if v is None:
        return {"error": "text is required (non-empty)."}
    with _LOCK:
        items = load_snippets()
        if v in items:
            return {"error": "duplicate snippet (already at index "
                             f"{items.index(v)})."}
        if len(items) >= MAX_SNIPPETS:
            return {"error": f"snippet cap {MAX_SNIPPETS} reached — "
                             "delete one first."}
        if at is None or (isinstance(at, str)
                          and str(at).strip() == ""):
            items.append(v)
            at_i = len(items) - 1
        else:
            try:
                at_i = max(0, min(int(at), len(items)))
            except (TypeError, ValueError):
                return {"error": "at must be an integer index."}
            items.insert(at_i, v)
        save_snippets(items)
    return {"ok": True, "at": at_i, "text": v,
            "multi": "\n" in v, "count": len(items)}


def set_snippet(index: Any, text: Any) -> dict:
    """Replace ONE snippet (index-based; multi-line text verbatim)."""
    v = normalize(text)
    if v is None:
        return {"error": "text is required (non-empty)."}
    with _LOCK:
        items = load_snippets()
        try:
            idx = int(index)
        except (TypeError, ValueError):
            return {"error": "index must be an integer."}
        if idx < 0 or idx >= len(items):
            return {"error": f"index {idx} out of range — use "
                             f"snippet_list (0..{len(items) - 1})."}
        dups = [i for i, s in enumerate(items) if s == v and i != idx]
        if dups:
            return {"error": f"duplicate snippet (#{dups[0]})."}
        items[idx] = v
        save_snippets(items)
    return {"ok": True, "index": idx, "text": v,
            "multi": "\n" in v}


def delete_snippet(index: Any) -> dict:
    """Remove ONE snippet by index (low-stakes op — no confirm gate;
    the GUI ✕ has none either)."""
    with _LOCK:
        items = load_snippets()
        try:
            idx = int(index)
        except (TypeError, ValueError):
            return {"error": "index must be an integer."}
        if idx < 0 or idx >= len(items):
            return {"error": f"index {idx} out of range — use "
                             f"snippet_list (0..{len(items) - 1})."}
        removed = items.pop(idx)
        save_snippets(items)
    return {"ok": True, "removed_index": idx, "removed": removed,
            "count": len(items)}


def move_snippet(index: Any, to: Any) -> dict:
    """Reorder: move the entry at ``index`` to position ``to``."""
    with _LOCK:
        items = load_snippets()
        try:
            idx = int(index)
            to_i = int(to)
        except (TypeError, ValueError):
            return {"error": "index / to must be integers."}
        if idx < 0 or idx >= len(items):
            return {"error": f"index {idx} out of range — use "
                             f"snippet_list (0..{len(items) - 1})."}
        to_i = max(0, min(to_i, len(items) - 1))
        moved = items.pop(idx)
        items.insert(to_i, moved)
        save_snippets(items)
    return {"ok": True, "from": idx, "to": to_i,
            "preview": moved.split("\n")[0][:80]}