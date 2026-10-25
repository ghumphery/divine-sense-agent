# -*- coding: utf-8 -*-
"""Skill FILE library — import / query / remove / reference (引用)
manager for Claude-style ``SKILL.md`` skill bundles (v0.9.116.0 —
in-process core; MCP surface = ``SkillLibraryLocalHost``).

User ask: 「提供一機制 做為 skill文件 匯入、查詢、刪除、引用 等管理機制」.

FILE-side counterpart of ``src/agent/skill_compiler.py`` (plans =
executable wave-plans; THIS module = durable library of skill document
bundles, one folder per skill in the Anthropic Agent-Skills style):

    <root>/<name>/SKILL.md           the skill document (verbatim)
    <root>/<name>/references/…       companion files (copied on import)
    <root>/<name>/_skill_meta.json   provenance (source / imported_at)

Storage root: ``QDRANT_SKILLS_DIR`` (default ``$QDRANT_RAG_HOME/skills``).

Tools (thin surfaces over the functions below; host =
``SkillLibraryLocalHost``, server ``skill-library-local``):

* ``skill_import(source, name?, overwrite?, confirm?)`` — local FOLDER
  (SKILL.md + companions ride along) / single FILE / URL (github blob →
  raw auto-convert; SKILL.md only). FAIL-CLOSED: ``confirm=true``
  required; existing name needs ``overwrite=True``; sources inside the
  library refused.
* ``skill_list()`` — catalog (name / description / files / bytes).
* ``skill_get(name)`` — frontmatter + raw SKILL.md text + file map.
* ``skill_search(query)`` — keyword scan over name+description+body
  (case-insensitive, zero-dep, scored, snippets).
* ``skill_remove(name, confirm?)`` — delete one skill folder
  (confirm-gated; library-root guarded).
* ``skill_use(name)`` — the 引用 path: SKILL.md body INLINE into the
  current tool result + companion map + a ready one-time usage note.

Zero side effects at import; public functions return plain dicts.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_SKILL_DOC = "SKILL.md"
_META_FILE = "_skill_meta.json"
_NAME_MAX = 64
_DEFAULT_FILE_MAX_BYTES = 2_000_000   # per-file cap (import copy)
_DEFAULT_MAX_FILES = 64               # per-bundle file cap
_DEFAULT_BODY_MAX_CHARS = 20_000      # skill_get / skill_use text cap
_URL_TIMEOUT_S = 20
_URL_MAX_BYTES = 5_000_000


class SkillError(ValueError):
    """Raised by the skill-library core for invalid operations
    (never a silent pass). Host handlers convert to error dicts."""


def _rag_home() -> Path:
    env = (os.environ.get("QDRANT_RAG_HOME", "") or "").strip()
    if env:
        return Path(env)
    return Path(os.path.expanduser("~")) / ".qdrant-rag-agent"


def skills_dir() -> Path:
    """The library root — ``QDRANT_SKILLS_DIR`` override or
    ``$QDRANT_RAG_HOME/skills``; mkdir'd lazily on every call."""
    d = (os.environ.get("QDRANT_SKILLS_DIR", "") or "").strip()
    p = Path(d) if d else _rag_home() / "skills"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _env_int(name: str, default: int) -> int:
    raw = (os.environ.get(name, "") or "").strip()
    if not raw:
        return default
    try:
        v = int(raw)
    except ValueError:
        return default
    return v if v >= 0 else default


def _file_max_bytes() -> int:
    return _env_int("QDRANT_SKILL_FILE_MAX_BYTES", _DEFAULT_FILE_MAX_BYTES)


def _max_files() -> int:
    return _env_int("QDRANT_SKILL_LIB_MAX_FILES", _DEFAULT_MAX_FILES)


def _body_max_chars() -> int:
    return _env_int("QDRANT_SKILL_LIB_BODY_CHARS", _DEFAULT_BODY_MAX_CHARS)


def sanitize_skill_name(raw: str) -> str:
    """Filesystem-safe lowercase skill name (the folder name). Path
    separators / traversal dots raise SkillError; an otherwise-empty
    result RETURNS '' (caller falls back to source-derived names)."""
    s = str(raw or "").strip()
    if not s:
        return ""
    if "/" in s or "\\" in s or s in (".", ".."):
        raise SkillError(f"invalid skill name: {raw!r}")
    s = s.lower().replace(" ", "-")
    s = re.sub(r"[^a-z0-9._-]+", "-", s).strip("-.")
    if ".." in s:
        raise SkillError(f"invalid skill name: {raw!r}")
    return s[:_NAME_MAX]


# ── frontmatter (zero-dep) ─────────────────────────────────────────
def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Minimal YAML-frontmatter reader for SKILL.md-style documents.
    Handles ``key: value`` scalars (one quote layer stripped) and
    block scalars (``key: >`` folded / ``key: |`` literal). A file
    with no frontmatter / an unclosed fence parses as
    ``({}, full_text)`` — import proceeds with honest notes."""
    text = str(text or "")
    lines = text.splitlines()
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i >= len(lines) or lines[i].strip() != "---":
        return {}, text
    i += 1
    meta: dict[str, str] = {}
    closing: int | None = None
    while i < len(lines):
        line = lines[i]
        if line.strip() == "---":
            closing = i
            break
        m = re.match(r"^([A-Za-z0-9_-]+)\s*:\s*(.*)$", line)
        if not m:
            i += 1
            continue
        key, val = m.group(1).lower(), m.group(2).strip()
        if val in (">", ">-", "|", "|-"):
            buf: list[str] = []
            i += 1
            while i < len(lines):
                nxt = lines[i]
                if not nxt.strip():
                    i += 1
                    continue
                if nxt.startswith(("  ", "\t")):
                    buf.append(nxt.strip())
                    i += 1
                    continue
                break
            if val.startswith(">"):
                joined = " ".join(buf)
            else:
                joined = "\n".join(buf).rstrip("\n")
            meta[key] = joined.strip()
            continue
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        meta[key] = val
        i += 1
    if closing is None:
        return {}, text
    body = "\n".join(lines[closing + 1:]).lstrip("\n")
    return meta, body


def github_raw_url(url: str) -> str:
    """github.com ``blob``/``raw`` page URL → the
    raw.githubusercontent.com URL; anything else passes through."""
    u = str(url or "").strip()
    m = re.match(
        r"^https?://(?:www\.)?github\.com/([^/]+)/([^/]+)/"
        r"(?:blob|raw)/([^/]+)/(.+)$", u)
    if not m:
        return u
    owner, repo, branch, path = m.groups()
    return (f"https://raw.githubusercontent.com/{owner}/{repo}/"
            f"{branch}/{path}")


def fetch_url_text(url: str) -> str:
    """Fetch a text document over HTTP (the SKILL.md path). Size /
    timeout caps per module constants; SkillError on transport."""
    u = github_raw_url(url)
    try:
        req = urllib.request.Request(
            u, headers={"User-Agent": "qdrant-rag-agent/skill-library"})
        with urllib.request.urlopen(req, timeout=_URL_TIMEOUT_S) as resp:
            data = resp.read(_URL_MAX_BYTES + 1)
    except Exception as e:
        raise SkillError(f"URL fetch failed for {u!r}: {e}") from e
    if len(data) > _URL_MAX_BYTES:
        raise SkillError(
            f"URL document too large (> {_URL_MAX_BYTES} bytes): {u!r}")
    return data.decode("utf-8", errors="replace")


def _read_text(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        raise SkillError(f"cannot read {p.as_posix()!r}: {e}") from e


def _file_map(folder: Path) -> list[dict[str, Any]]:
    """Relative file map of a skill folder: [{rel_path, size_bytes}]
    for visible files (hidden names / ``_skill_meta.json`` excluded)."""
    out: list[dict[str, Any]] = []
    for cur, dirs, files in os.walk(folder):
        dirs[:] = sorted(
            d for d in dirs
            if not d.startswith(".") and d != "__pycache__")
        for f in sorted(files):
            if f.startswith(".") or f == _META_FILE:
                continue
            fp = Path(cur) / f
            try:
                size = fp.stat().st_size
            except OSError:
                size = -1
            out.append({
                "rel_path": fp.relative_to(folder).as_posix(),
                "size_bytes": size,
            })
    return out


def _meta_read(folder: Path) -> dict:
    mp = folder / _META_FILE
    try:
        if mp.is_file():
            data = json.loads(mp.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


def _resolve_import_name(
    explicit: str | None, meta: dict[str, str],
    fallbacks: list[str],
) -> str:
    """explicit arg > frontmatter ``name`` > source-derived fallbacks >
    timestamped default. Sanitized throughout."""
    n = ""
    if explicit:
        n = sanitize_skill_name(explicit)
        if not n:
            raise SkillError(
                f"skill name {explicit!r} sanitizes to nothing — "
                "pick an explicit name (letters/digits/_.-).")
        return n
    fm_name = str(meta.get("name") or "").strip()
    if fm_name:
        n = sanitize_skill_name(fm_name)
    if not n:
        for cand in fallbacks:
            try:
                cand_n = sanitize_skill_name(cand)
            except SkillError:
                continue
            if cand_n:
                n = cand_n
                break
    return n or f"skill-{datetime.now().strftime('%Y%m%d-%H%M%S')}"


def _check_conflict(target: Path, name: str, overwrite: bool) -> None:
    if not target.exists():
        return
    if not overwrite:
        raise SkillError(
            f"skill {name!r} already exists at "
            f"{target.as_posix()!r}; pass overwrite=true to replace "
            "(the existing folder is deleted on overwrite).")


def _write_meta(
    folder: Path, *, name: str, description: str,
    source: str, source_type: str, files: list[dict[str, Any]],
) -> None:
    payload = {
        "name": name,
        "description": description[:2000],
        "source": source,
        "source_type": source_type,
        "imported_at": datetime.now().isoformat(timespec="seconds"),
        "files": {str(f["rel_path"]): f["size_bytes"] for f in files},
    }
    (folder / _META_FILE).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8")


def import_skill(
    source: str,
    name: str | None = None,
    *,
    overwrite: bool = False,
    confirm: bool = False,
) -> dict:
    """Import a skill bundle into the library. Fail-closed write op:
    ``confirm=True`` required BEFORE any side effect; name conflicts
    refused without ``overwrite=True``; sources inside the library
    root refused."""
    if confirm is not True:
        return {
            "error": "refused: skill_import writes into the skill "
                     "library; pass confirm=true to proceed.",
            "source": str(source or ""),
        }
    src = str(source or "").strip()
    if not src:
        raise SkillError("source is required (local path or URL).")
    t0 = time.time()
    if src.startswith(("http://", "https://")):
        result = _import_from_url(src, name=name, overwrite=overwrite)
    else:
        result = _import_from_local(src, name=name, overwrite=overwrite)
    result["elapsed_s"] = round(time.time() - t0, 3)
    logger.info("[skill_library] imported %s (%s) %.3fs",
                result.get("name"), result.get("source_type"),
                result.get("elapsed_s"))
    return result


def _import_from_local(source: str, *, name=None, overwrite=False) -> dict:
    root = skills_dir()
    sp = Path(source).expanduser()
    try:
        sp = sp.resolve()
    except OSError as e:
        raise SkillError(f"cannot resolve source path: {e}") from e
    if not sp.exists():
        raise SkillError(f"source not found: {sp.as_posix()!r}")
    try:
        sp.relative_to(root)
        raise SkillError(
            f"source {source!r} is inside the library root — "
            "re-importing from the library is refused.")
    except ValueError:
        pass
    notes: list[str] = []

    if sp.is_dir():
        doc = sp / _SKILL_DOC
        if not doc.is_file():
            raise SkillError(
                f"no {_SKILL_DOC} in folder {sp.as_posix()!r}")
        doc_text = _read_text(doc)
        fm, _ = parse_frontmatter(doc_text)
        copy_pairs: list[tuple[Path, str]] = [(doc, _SKILL_DOC)]
        seen = {_SKILL_DOC}
        for cur, dirs, files in os.walk(sp):
            dirs[:] = sorted(d for d in dirs
                             if not d.startswith(".")
                             and d != "__pycache__")
            for f in sorted(files):
                if f.startswith(".") or f == _META_FILE:
                    continue
                fp = Path(cur) / f
                rel = fp.relative_to(sp).as_posix()
                if rel in seen:
                    continue
                seen.add(rel)
                copy_pairs.append((fp, rel))
        source_type = "local-folder"
    else:
        doc_text = _read_text(sp)
        fm, _ = parse_frontmatter(doc_text)
        copy_pairs = [(sp, _SKILL_DOC)]
        notes.append(
            "single-file import: companion folders (references/ …) of "
            "the source file were not included — import the whole "
            "folder to carry them.")
        source_type = "local-file"

    if len(copy_pairs) > _max_files():
        raise SkillError(
            f"bundle has {len(copy_pairs)} files > cap {_max_files()}; "
            "trim the folder first (QDRANT_SKILL_LIB_MAX_FILES).")

    skill_name = _resolve_import_name(
        (str(name) if name else None), fm,
        fallbacks=[sp.name if sp.is_dir() else sp.stem])
    target = root / skill_name
    _check_conflict(target, skill_name, overwrite)
    if target.exists() and overwrite:
        shutil.rmtree(target)

    kept: list[dict[str, Any]] = []
    for fp, rel in copy_pairs:
        try:
            fsize = fp.stat().st_size
        except OSError:
            notes.append(f"skipped {rel}: inaccessible.")
            continue
        if fsize > _file_max_bytes():
            notes.append(
                f"skipped {rel}: {fsize} bytes > "
                f"{_file_max_bytes()} (QDRANT_SKILL_FILE_MAX_BYTES).")
            continue
        tp = target / rel
        tp.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(fp, tp)
        kept.append({"rel_path": rel, "size_bytes": fsize})
    _write_meta(target, name=skill_name,
                description=fm.get("description") or "",
                source=sp.as_posix(), source_type=source_type,
                files=kept)
    return {
        "ok": True, "name": skill_name,
        "path": target.as_posix(), "source": sp.as_posix(),
        "source_type": source_type,
        "files": kept, "notes": notes,
        "skills_dir": root.as_posix(),
    }


def _import_from_url(source: str, *, name=None, overwrite=False) -> dict:
    text = fetch_url_text(source)
    fm, body = parse_frontmatter(text)
    raw = github_raw_url(source)
    slug = re.sub(r"[?#].*$", "", raw).rstrip("/").rsplit("/", 1)[-1]
    if slug.lower().endswith(".md"):
        slug = slug[:-3]
    skill_name = _resolve_import_name(
        (str(name) if name else None), fm, fallbacks=[slug])
    if not body.strip():
        raise SkillError(
            f"fetched document from {source!r} has no markdown body "
            "— refusing to import an empty skill.")
    root = skills_dir()
    target = root / skill_name
    _check_conflict(target, skill_name, overwrite)
    if target.exists() and overwrite:
        shutil.rmtree(target)
    target.mkdir(parents=True)
    (target / _SKILL_DOC).write_text(text, encoding="utf-8")
    kept = [{"rel_path": _SKILL_DOC,
             "size_bytes": len(text.encode("utf-8"))}]
    _write_meta(target, name=skill_name,
                description=fm.get("description") or "",
                source=source, source_type="url", files=kept)
    return {
        "ok": True, "name": skill_name,
        "path": target.as_posix(), "source": source,
        "source_type": "url", "files": kept,
        "notes": [
            "URL import fetched SKILL.md ONLY — companion files "
            "(references/ examples/ …) were NOT fetched; import a "
            "local folder clone to carry them.",
            f"resolved URL: {raw}",
        ],
        "skills_dir": root.as_posix(),
    }


# ── query ────────────────────────────────────────────────────────
def _iter_skill_dirs() -> list[Path]:
    root = skills_dir()
    try:
        entries = sorted(root.iterdir(), key=lambda p: p.name.lower())
    except OSError as e:
        raise SkillError(f"cannot list {root.as_posix()!r}: {e}") from e
    return [p for p in entries
            if p.is_dir() and not p.name.startswith(".")]


def list_skills(limit: int = 50) -> dict:
    """The catalog: one entry per folder that carries a SKILL.md
    (folders WITHOUT one are listed as invalid — never silent)."""
    cap = max(1, min(int(limit or 50), 200))
    out: list[dict[str, Any]] = []
    for p in _iter_skill_dirs():
        if len(out) >= cap:
            break
        doc = p / _SKILL_DOC
        if not doc.is_file():
            out.append({"name": p.name,
                        "invalid": f"no {_SKILL_DOC} inside"})
            continue
        fm, _body = parse_frontmatter(_read_text(doc))
        m = _meta_read(p)
        fmap = _file_map(p)
        try:
            mtime = doc.stat().st_mtime
        except OSError:
            mtime = None
        out.append({
            "name": p.name,
            "description": (fm.get("description")
                            or m.get("description") or "")[:200],
            "files": fmap,
            "total_bytes": sum(f["size_bytes"] for f in fmap),
            "mtime": (datetime.fromtimestamp(mtime).isoformat(
                timespec="seconds") if mtime else None),
            "source": m.get("source") or "",
            "source_type": m.get("source_type") or "",
            "path": p.as_posix(),
        })
    return {"ok": True, "root": skills_dir().as_posix(),
            "count": len(out), "skills": out}


def _resolve(name: str) -> tuple[str, Path]:
    n = sanitize_skill_name(name)
    if not n or n == _META_FILE:
        raise SkillError(f"invalid or empty skill name: {name!r}")
    p = skills_dir() / n
    if p.parent != skills_dir() or not p.is_dir():
        raise SkillError(
            f"skill {n!r} not found (skill_list enumerates the library)")
    return n, p


def _use_hint(n: str, doc_p: Path) -> str:
    return (
        f"技能 {n!r} 規範已載入本回合：接下來的回覆與要產出的文件（含"
        f"存沙盒的檔案）必須遵守以上規範；需要術語/參考檔時，read_file "
        f"讀 {doc_p.as_posix()} 同層資料夾內的檔案。"
    )


def get_skill(name: str, max_chars: int | None = None) -> dict:
    """One skill's detail: frontmatter + raw SKILL.md text (capped) +
    file map + provenance + the ready 引用提示 (use_hint)."""
    n, p = _resolve(name)
    doc = p / _SKILL_DOC
    text = _read_text(doc)
    fm, _body = parse_frontmatter(text)
    cap = (_body_max_chars() if max_chars is None
           else max(500, int(max_chars)))
    full_chars = len(text)
    truncated = full_chars > cap
    text_out = (text[:cap]
                + (f"\n…[SKILL.md truncated at {cap} chars — read_file "
                   f"{doc.as_posix()} directly for the full text]"
                   if truncated else ""))
    m = _meta_read(p)
    return {
        "ok": True, "name": n, "path": p.as_posix(),
        "skill_md_path": doc.as_posix(),
        "description": fm.get("description")
        or m.get("description") or "",
        "frontmatter": fm,
        "files": _file_map(p),
        "source": m.get("source") or "",
        "source_type": m.get("source_type") or "",
        "imported_at": m.get("imported_at") or "",
        "full_chars": full_chars, "truncated": truncated,
        "text": text_out,
        "use_hint": _use_hint(n, doc),
    }


def _snippet(body: str, needle: str, span: int = 120) -> str:
    idx = str(body or "").lower().find(str(needle or "").lower())
    if idx < 0:
        return ""
    start = max(0, idx - span // 2)
    end = min(len(body), idx + max(len(needle), span))
    frag = body[start:end].replace("\n", " ")
    return (f"…{frag}…" if (start > 0 or end < len(body)) else frag)


def search_skills(query: str, limit: int = 10) -> dict:
    """Case-insensitive keyword scan over name + description + body.
    Score = name hit ×10 + description hit ×5 + min(body hits, 20)."""
    q = str(query or "").strip()
    if not q:
        raise SkillError("query is required.")
    ql = q.lower()
    matches: list[dict[str, Any]] = []
    for p in _iter_skill_dirs():
        doc = p / _SKILL_DOC
        if not doc.is_file():
            continue
        fm, body = parse_frontmatter(_read_text(doc))
        in_name = ql in p.name.lower()
        in_desc = ql in (fm.get("description") or "").lower()
        n_body = body.lower().count(ql)
        if not (in_name or in_desc or n_body):
            continue
        matches.append({
            "name": p.name,
            "score": ((10 if in_name else 0)
                      + (5 if in_desc else 0)
                      + min(n_body, 20)),
            "where": [w for w, hit in (
                ("name", in_name), ("description", in_desc),
                ("body", n_body > 0)) if hit],
            "snippet": _snippet(body, q),
        })
    matches.sort(key=lambda m: (-m["score"], m["name"]))
    matches = matches[:max(1, min(int(limit or 10), 50))]
    return {"ok": True, "query": q, "count": len(matches),
            "matches": matches}


# ── remove / 引用 ────────────────────────────────────────────────
def remove_skill(name: str, confirm: bool = False) -> dict:
    """Delete ONE skill folder from the library. Fail-closed:
    ``confirm=True`` required; the target must resolve DIRECTLY under
    the library root (no traversal, no deleting the root itself)."""
    if confirm is not True:
        return {
            "error": "refused: skill_remove deletes a whole skill "
                     "folder (irreversible); pass confirm=true.",
            "name": str(name or ""),
        }
    n, p = _resolve(name)
    if p.parent != skills_dir():
        raise SkillError(
            f"refusing to remove {p.as_posix()!r} — not a library "
            "member.")
    files = _file_map(p)
    shutil.rmtree(p)
    logger.info("[skill_library] removed %s (%d files)", n, len(files))
    return {"ok": True, "removed": n, "path": p.as_posix(),
            "files_count": len(files), "files": files}


def use_skill(name: str, max_chars: int | None = None) -> dict:
    """The 引用 (reference) path: return the SKILL.md body INLINE plus
    companion map + the ready one-time usage note, so the CURRENT
    turn applies the rules with zero extra read_file round-trips."""
    n, p = _resolve(name)
    doc = p / _SKILL_DOC
    fm, body = parse_frontmatter(_read_text(doc))
    cap = (_body_max_chars() if max_chars is None
           else max(500, int(max_chars)))
    full_chars = len(body)
    truncated = full_chars > cap
    body_out = (body[:cap]
                + (f"\n…[skill body truncated at {cap} chars — read_file "
                   f"{doc.as_posix()} for the full text]"
                   if truncated else ""))
    return {
        "ok": True, "name": n, "path": p.as_posix(),
        "skill_md_path": doc.as_posix(),
        "description": fm.get("description") or "",
        "files": _file_map(p),
        "full_chars": full_chars, "truncated": truncated,
        "body": body_out,
        "usage_note": _use_hint(n, doc),
    }

# ── auto-draft from a finished TACTI plan (v0.9.127.0) ─────────────
def plan_skill_draft_enabled() -> bool:
    """A DONE plan with enough stages is auto-drafted as a SKILL.md
    bundle under ``$QDRANT_RAG_HOME/skill_drafts/`` — NEVER auto-imported
    (the confirm gate stays with skill_import after review).
    ``QDRANT_PLAN_SKILL_DRAFT=off`` opts out."""
    v = os.environ.get("QDRANT_PLAN_SKILL_DRAFT", "on")
    return str(v or "on").strip().lower() not in ("off", "0", "no", "false")


def plan_skill_draft_min_stages() -> int:
    """Minimum DONE-plan stage count worth drafting (default 3 — a
    trivial 1-2 stage plan is not a reusable recipe)."""
    try:
        v = int(os.environ.get("QDRANT_PLAN_SKILL_DRAFT_MIN_STAGES", "") or 0)
    except ValueError:
        v = 0
    return v if v > 0 else 3


def drafts_dir() -> Path:
    """Draft bundles land here — deliberately OUTSIDE ``skills_dir()``
    (skill_import refuses library-internal sources; a draft is BY
    DEFINITION pending review)."""
    env = os.environ.get("QDRANT_SKILL_DRAFTS_DIR", "").strip()
    d = Path(env) if env else _rag_home() / "skill_drafts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def draft_from_plan(rec: dict) -> dict:
    """Write one SKILL.md DRAFT bundle from a plan record — the
    successful-experience-fixation counterpart of the failure loop
    (recovery_hint→skill_search). Layout mirrors imports; provenance
    marks ``auto-drafted-from-plan`` + ``pending-confirm``. Never
    raises — returns an ``error`` dict instead."""
    try:
        if not isinstance(rec, dict):
            return {"error": "draft_from_plan: rec must be a dict"}
        plan_id = str(rec.get("id") or "").strip()[:12]
        if not plan_id:
            return {"error": "draft_from_plan: plan id missing"}
        stages = rec.get("stages") or []
        name_raw = str(rec.get("name") or "plan").strip() or "plan"
        slug = re.sub(r"[^\w\-]+", "-", name_raw)[:24].strip("-").lower()
        name = ("plan-" + slug + "-" + plan_id)[:_NAME_MAX]
        desc = str(rec.get("description") or "").strip()[:300]

        fm = ("---\nname: %s\ndescription: %s\n---\n"
              % (name, (desc or name).replace("\n", " ")[:120]))
        parts = [fm, "# %s\n" % name_raw,
                 "**auto-draft** from TACTI plan ``%s`` — review, then "
                 "import via ``skill_import(source=<folder>, "
                 "confirm=true)``.\n" % plan_id]
        if desc:
            parts.append("\n## Goal\n\n%s\n" % desc[:600])
        parts.append("\n## Steps\n")
        for i, st in enumerate(stages, start=1):
            title = str(st.get("title") or "step-%d" % i).replace("\n", " ")
            parts.append("\n### Step %d: %s\n\n" % (i, title))
            prompt = str(st.get("prompt") or "").strip()
            if prompt:
                parts.append("```\n%s\n```\n" % prompt[:2000])
            v = st.get("verify")
            if isinstance(v, dict) and v.get("path"):
                parts.append(
                    "- verify_artifact(path=%r)\n" % v["path"])
            if st.get("tools_allow"):
                parts.append("- tools_allow: %s\n"
                             % ", ".join(map(str, st.get("tools_allow"))))
        parts.append(
            "\n## Handoff\n\n每 step 的結果寫在 write sandbox "
            "``out/<stage_id>.json``；下游 step 先讀上游 handoff，"
            "不重做上游已完成的工作。\n\n---\n_drafted %s · plan=%s · "
            "stages=%d\n" % (datetime.now().isoformat(timespec="seconds"),
                              plan_id, len(stages)))
        body = "".join(parts)
        if len(body) > 16000:
            body = body[:16000] + "\n…[draft truncated]\n"

        d = drafts_dir() / name
        d.mkdir(parents=True, exist_ok=True)
        f = d / _SKILL_DOC
        tmp = d / ".draft.tmp"
        tmp.write_text(body, encoding="utf-8")
        tmp.replace(f)
        meta = {
            "name": name,
            "source": "auto-drafted-from-plan",
            "plan_id": plan_id,
            "status": "pending-confirm",
            "drafted_at": datetime.now().isoformat(timespec="seconds"),
            "stages": len(stages),
            "import_hint": ("skill_import(source='%s', confirm=true) — "
                            "review first; drafts are never auto-imported"
                            % d.as_posix()),
        }
        mf = d / _META_FILE
        mt = d / ".meta.tmp"
        mt.write_text(json.dumps(meta, ensure_ascii=False, indent=1),
                      encoding="utf-8")
        mt.replace(mf)
        return {
            "ok": True,
            "name": name,
            "path": f.as_posix(),
            "folder": d.as_posix(),
            "import_with": ("skill_import(source='%s', confirm=true) — "
                            "review first; drafts are never auto-imported"
                            % d.as_posix()),
            "stages": len(stages),
        }
    except Exception as e:  # noqa: BLE001 — a draft must never break a run
        logger.debug("[skill-library] draft_from_plan failed: %r", e)
        return {"error": "draft_from_plan failed: %r" % (e,)}
