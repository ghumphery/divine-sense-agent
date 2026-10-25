# -*- coding: utf-8 -*-
"""Agent ToolRoute overrides — LLM-proposable trigger patterns.

v0.9.127.0, P2 of the self-improvement adoption: the harness's tool-
routing vocabulary (``Agent._TOOLROUTE_SERVER_TRIGGERS`` and friends) is
currently hand-edited ``.env``/code. This module lets the agent itself
PROPOSE additional (server → regex patterns) rows via the rulebook
pattern (evidence-gated write + a zero-dep snapshot ring), while the
harness decision stays deterministic:

* ``QDRANT_TOOLROUTE_OVERRIDES=off``    — feature off (no read, no mount)
* ``QDRANT_TOOLROUTE_OVERRIDES=shadow`` — DEFAULT: matched entries are
  LOGGED ([toolroute-override] shadow hit …) but NOT applied — observe
  before you unleash; the log trail measures what the proposal WOULD
  have mounted per turn.
* ``QDRANT_TOOLROUTE_OVERRIDES=on``     — matched servers union into the
  per-turn tool surface (union-only, like every other trigger row).

File: ``agent_toolroute.md`` (READ-sandbox sibling of agent_rules.md;
``QDRANT_TOOLROUTE_OVERRIDES_FILE`` overrides, ``~`` supported). Format
mirrors the rulebook::

    ### [t3] 加密貨幣新聞抓取走 firefox_craw
    - server:    firefox_craw
    - patterns:  加密貨幣|crypto|虛擬貨幣新聞
    - evidence:  2026-10-16 三次 crypto 題都漏掛 firefox_craw
    - updated:   2026-10-16T10:00:00+08:00

An entry with an uncompilable regex is SKIPPED (never poisons routing).
Never raises on read (empty = feature byte-identical).
"""
from __future__ import annotations

import os
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path

__all__ = [
    "toolroute_overrides_mode", "overrides_path", "load_overrides",
    "parse_overrides", "matches", "add_override", "overrides_history",
    "reset_overrides_cache",
]

DEFAULT_BASENAME = "agent_toolroute.md"
_HISTORY_DIRNAME = "toolroute_overrides_history"

_LOCK = threading.Lock()
_CACHE: dict = {"path": None, "mtime": None, "entries": None}

_HEAD_RE = re.compile(r"^###\s*\[([^\]\s]+)\]\s*(.*?)\s*$")
_META_RE = re.compile(r"^- *(server|patterns|evidence|updated) *: *(.*)$")


def mode() -> str:
    """``off`` | ``shadow`` (default) | ``on`` — anything else = shadow."""
    v = str(os.environ.get("QDRANT_TOOLROUTE_OVERRIDES", "shadow")
            or "shadow").strip().lower()
    return v if v in ("off", "shadow", "on") else "shadow"


def overrides_path() -> Path | None:
    env = (os.environ.get("QDRANT_TOOLROUTE_OVERRIDES_FILE", "") or "").strip()
    if env:
        try:
            return Path(os.path.expanduser(env))
        except Exception:  # noqa: BLE001 — never raise
            return None
    try:
        from src.mcp_client.host import _resolve_filesystem_root

        return (_resolve_filesystem_root("QDRANT_FILESYSTEM_READ_ROOT")
                / DEFAULT_BASENAME)
    except Exception:  # noqa: BLE001 — never raise
        return None


def parse_overrides(text: str) -> list[dict]:
    """Parse file text → entries ``{id,title,server,pattern,re,evidence,
    start,end}``. Uncompilable regexes are DROPPED (routing must never
    be poisoned by a bad row)."""
    out: list[dict] = []
    cur: dict | None = None
    for i, raw in enumerate((text or "").splitlines()):
        m = _HEAD_RE.match(raw)
        if m:
            if cur is not None:
                out.append(cur)
            cur = {"id": m.group(1), "title": m.group(2), "server": "",
                   "patterns": "", "re": None, "evidence": "",
                   "start": i, "end": i}
            continue
        if cur is None:
            continue
        mm = _META_RE.match(raw.strip())
        if mm:
            key, val = mm.group(1), mm.group(2).strip()
            if key == "server" and not cur["server"]:
                cur["server"] = val
            elif key == "patterns" and not cur["patterns"]:
                cur["patterns"] = val
            elif key == "evidence" and not cur["evidence"]:
                cur["evidence"] = val
    if cur is not None:
        out.append(cur)
    for e in out:
        pat = e.pop("patterns", "")
        e["pattern"] = pat
        try:
            e["re"] = re.compile(pat) if pat else None
        except re.error:
            e["re"] = None
    return [e for e in out if e.get("server") and e.get("re")]


def _history_max() -> int:
    try:
        v = int(os.environ.get("QDRANT_RULEBOOK_HISTORY_MAX", "") or 0)
    except ValueError:
        v = 0
    return v if v > 0 else 20


def _now_iso() -> str:
    try:
        return datetime.now().astimezone().isoformat(timespec="seconds")
    except Exception:  # noqa: BLE001
        return datetime.now().isoformat(timespec="seconds")


def _snapshot_file(path: Path):
    try:
        if not path.is_file():
            return None
        import shutil

        hdir = path.parent / _HISTORY_DIRNAME
        hdir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        snap = hdir / (
            f"{path.stem}_{ts}_{uuid.uuid4().hex[:4]}{path.suffix}"
        )
        shutil.copy2(str(path), str(snap))
        snaps = sorted(hdir.glob("*" + path.suffix))
        while len(snaps) > _history_max():
            try:
                snaps[0].unlink()
                snaps.pop(0)
            except OSError:
                break
        return snap
    except Exception:  # noqa: BLE001 — best effort
        return None


def _write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + f".tmp{uuid.uuid4().hex[:6]}")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def load_overrides() -> list[dict]:
    """Read + parse the override file (mtime-cached, never raises).
    Missing / empty file = [] (the built-in trigger rows untouched)."""
    try:
        path = overrides_path()
        if path is None or not path.is_file():
            return []
        with _LOCK:
            try:
                mtime = path.stat().st_mtime
            except OSError:
                return []
            if (_CACHE["path"] == str(path)
                    and _CACHE["mtime"] == mtime
                    and isinstance(_CACHE["entries"], list)):
                return _CACHE["entries"]
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                return []
            entries = parse_overrides(text)[:50]
            _CACHE.update(path=str(path), mtime=mtime, entries=entries)
            return entries
    except Exception:  # noqa: BLE001 — never raise
        return []


def reset_overrides_cache() -> None:
    with _LOCK:
        _CACHE.update(path=None, mtime=None, entries=None)


def matches(text: str) -> list[dict]:
    """Override entries whose pattern matches ``text`` (shadow/on; in
    ``off`` this is always [] — callers need no extra gate)."""
    if mode() == "off":
        return []
    s = str(text or "")
    if not s.strip():
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for e in load_overrides():
        srv = e["server"]
        if srv in seen:
            continue
        try:
            if e["re"].search(s):
                seen.add(srv)
                out.append({"id": e["id"], "server": srv,
                            "pattern": e["pattern"]})
        except Exception:  # noqa: BLE001 — matching never raises
            continue
    return out


def add_override(
    server: str, patterns: str, title: str = "", evidence: str = "",
) -> dict:
    """Append one entry (atomic + snapshot first). The MCP-side confirm
    gate lives in the host surface — this core validates fail-closed."""
    server = str(server or "").strip()
    patterns = str(patterns or "").strip()
    title = str(title or "").strip()
    evidence = str(evidence or "").strip()
    if mode() == "off":
        return {"error": (
            "toolroute overrides disabled "
            "(QDRANT_TOOLROUTE_OVERRIDES=off)")}
    if not server:
        return {"error": "server is required (e.g. firefox_craw)."}
    if not patterns:
        return {"error": "patterns is required (a regex alternation)."}
    try:
        re.compile(patterns)
    except re.error as e:
        return {"error": f"patterns is not a valid regex: {e!r}"}
    path = overrides_path()
    if path is None:
        return {"error": "overrides path unresolvable (env not set?)"}
    try:
        with _LOCK:
            cur = (path.read_text(encoding="utf-8", errors="replace")
                   if path.is_file() else "")
            entries = parse_overrides(cur)
            n = 0
            for r in entries:
                m = re.match(r"^[Tt](\d+)$", r["id"])
                if m:
                    n = max(n, int(m.group(1)))
            tid = f"t{n + 1}"
            _snapshot_file(path)
            header = (
                "# Agent ToolRoute Overrides\n\n"
                "LLM 提議的 ToolRoute 觸發列增補（evidence 門 + 快照 ring；\n"
                "QDRANT_TOOLROUTE_OVERRIDES=shadow|on 決定是否套用）。\n"
            )
            block = (f"### [{tid}] {title or server + ' patterns'}\n"
                     f"- server:    {server}\n"
                     f"- patterns:  {patterns}\n")
            if evidence:
                block += f"- evidence:  {evidence}\n"
            block += f"- updated:   {_now_iso()}\n"
            new = (cur.rstrip("\n") + "\n\n" + block) if cur.strip() \
                else (header + "\n" + block)
            _write(path, new)
        reset_overrides_cache()
        return {
            "ok": True,
            "id": tid,
            "server": server,
            "pattern": patterns,
            "mode_note": (
                "current mode = %s: %s" % (
                    mode(),
                    ("patterns WILL union into the per-turn surface"
                     if mode() == "on" else
                     "shadow — matches are LOGGED, not applied; flip "
                     "QDRANT_TOOLROUTE_OVERRIDES=on after observing "
                     "the trail")),
            ),
        }
    except Exception as e:  # noqa: BLE001
        return {"error": f"add_override failed: {e!r}"}


def overrides_history(limit: int = 10) -> dict:
    """Snapshot ring listing (rulebook.rules_history contract)."""
    path = overrides_path()
    if path is None:
        return {"error": "overrides path unresolvable (env not set?)"}
    try:
        hdir = path.parent / _HISTORY_DIRNAME
        snaps = sorted(
            hdir.glob("*" + path.suffix),
            key=lambda f: f.stat().st_mtime, reverse=True,
        ) if hdir.is_dir() else []
        try:
            lim = max(1, min(int(limit or 10), 50))
        except (TypeError, ValueError):
            lim = 10
        return {
            "ok": True,
            "path": path.as_posix(),
            "snapshots": len(snaps),
            "entries": [
                {"file": f.as_posix(), "chars": f.stat().st_size}
                for f in snaps[:lim]
            ],
        }
    except Exception as e:  # noqa: BLE001
        return {"error": f"overrides_history failed: {e!r}"}