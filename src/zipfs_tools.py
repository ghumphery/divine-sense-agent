"""Zip/Tar-as-filesystem — operate archive members in place (v0.9.133.13).

Pure format logic; no MCP, no sandbox enforcement (the caller —
``ZipFsLocalHost`` in ``src/mcp_client/host.py`` — resolves every
archive path against the filesystem read/write roots before anything
here touches disk, exactly like ``ArchiveLocalHost`` does for the
create/extract tools).

Use case (user request v0.9.133.13): the user uploads ONE archive
containing their whole working directory; the LLM lists / reads /
adds / edits / renames / deletes members **without extracting the
whole thing**; the modified archive stays a single file the user can
download again.

Formats & capability matrix
---------------------------

=========  ============  =================================
format     read          write (in-place member surgery)
=========  ============  =================================
zip        stdlib        yes  (full rebuild via copy-on-write)
tar        stdlib        yes  (full rebuild via copy-on-write)
tar.gz     stdlib        yes
tar.bz2    stdlib        yes
7z         py7zr         NO  (read-only: list / read / extract_one)
=========  ============  =================================

Every mutating operation is a **copy-on-write atomic rebuild**: the
old archive is scanned member-by-member into a temp file in the same
directory (deletions skipped, renames re-written, additions appended
on top), the temp is integrity-checked, then ``os.replace``d over the
original — a crash mid-write can never lose the caller's upload.

Security (same discipline as ``archive_tools``):

* **member names** are normalised and MUST stay root-relative:
  ``..`` segments, absolute paths, and Windows drive prefixes are
  rejected *before* any read/write;
* **budgets** — the rebuilt archive honours the shared
  member-count / uncompressed-size caps (zip-bomb bounds);
* **writes** refuse dangerous extensions (mirrors the filesystem
  write-sandbox blocklist); **extract-to-disk** refuses them by
  default too.
"""
from __future__ import annotations

import io as _io
import os
import tarfile
import tempfile
import time
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from src.archive_tools import (
    DEFAULT_MAX_BYTES,   # 512 MB uncompressed budget
    DEFAULT_MAX_FILES,   # 10k member budget
    _DANGEROUS_EXTS,
    ArchiveError,        # noqa: F401 — re-exported alias
    list_members,
    sniff_archive_format,
)

__all__ = [
    "RW_FORMATS",
    "RO_FORMATS",
    "ZipFsError",
    "archive_info",
    "delete_members",
    "extract_member",
    "list_tree",
    "read_member",
    "rename_members",
    "write_member",
]

RW_FORMATS = ("zip", "tar", "tar.gz", "tar.bz2")
RO_FORMATS = ("7z",)

# Per-entry cap for extract-to-disk (a member bigger than this cannot
# be pulled out; the whole-archive budgets still apply on rebuilds).
DEFAULT_MEMBER_BYTES = DEFAULT_MAX_BYTES


class ZipFsError(Exception):
    """Raised for zipfs-level failures the LLM should see."""


# ─── member-name normalisation ──────────────────────────────────

def _norm_member(name: str) -> str:
    """Normalise an archive member name to a safe root-relative
    posix path. Raises ZipFsError on traversal / absolute / drive
    forms. Returns the canonical ``a/b/c`` form (``..`` segments,
    absolute paths and Windows drive prefixes all rejected)."""
    raw0 = str(name or "")
    if raw0.startswith(("/", "\\")):
        raise ZipFsError(
            f"member name {raw0!r} is absolute — archive members must "
            "be archive-relative."
        )
    raw = raw0.replace("\\", "/").strip().lstrip("/")
    if not raw:
        raise ZipFsError("member name is empty.")
    p = PurePosixPath(raw)
    if p.is_absolute() or (p.parts and p.parts[0].endswith(":")):
        raise ZipFsError(
            f"member name {raw!r} is absolute / drive-prefixed — "
            "archive members must be archive-relative."
        )
    if any(part == ".." for part in p.parts):
        raise ZipFsError(
            f"member name {raw!r} escapes the archive root (.. segment)."
        )
    out = "/".join(p.parts)
    if raw.endswith("/"):
        out += "/"
    return out


def _member_variants(name: str) -> tuple[str, ...]:
    """Lookup variants for an archive member lookup: the canonical
    form plus the common ``./``-prefixed / trailing-slash shapes tar
    archives love to store."""
    n = _norm_member(name)
    variants = [n]
    if not n.endswith("/"):
        variants.append(n + "/")
        variants.append("./" + n)
    else:
        variants.append("." + n)
    seen: list[str] = []
    for v in variants:
        if v not in seen:
            seen.append(v)
    return tuple(seen)


def _is_dangerous_member(name: str) -> bool:
    return PurePosixPath(name).suffix.lower() in _DANGEROUS_EXTS \
        or Path(name).suffix.lower() in _DANGEROUS_EXTS


def _P_suffix(name: str) -> str:
    return Path(name).suffix.lower()


# ─── format capability gating ───────────────────────────────────

def _sniff(path: Path) -> str:
    fmt = sniff_archive_format(path)
    if not fmt:
        raise ZipFsError(
            "not a recognised archive extension — zipfs supports "
            ".zip .tar .tar.gz .tgz .tar.bz2 (read-write) and "
            ".7z (read-only)."
        )
    if fmt == "gz" or fmt not in RW_FORMATS + RO_FORMATS:
        raise ZipFsError(
            f"format {fmt!r} is not an editable bundle — the zipfs "
            "member-surgery tools support .zip .tar .tar.gz .tgz "
            ".tar.bz2 (read-write) and .7z (read-only); use the "
            "archive_create/archive_extract tools for .gz streams."
        )
    return fmt


def _tar_mode(fmt: str) -> str:
    return {"tar": "r:", "tar.gz": "r:gz", "tar.bz2": "r:bz2"}[fmt]


def _tar_write_mode(fmt: str) -> str:
    return {"tar": "w:", "tar.gz": "w:gz", "tar.bz2": "w:bz2"}[fmt]


def _sevenzip_available() -> bool:
    try:
        import py7zr  # noqa: F401

        return True
    except Exception:
        return False


# ─── read one member (all supported formats incl 7z read-only) ──

def read_member(
    path: Path,
    member: str,
    *,
    max_bytes: int = 1_000_000,
) -> dict[str, Any]:
    """Read ONE member's bytes without extraction. Text members come
    back decoded (utf-8); binary members come back base64 (``encoding
    = 'binary'``) so the LLM never receives mojibake. Budget:
    ``max_bytes`` per call."""
    fmt = _sniff(path)
    name = _norm_member(member)
    members_index = [
        {"name": m.get("name")} for m in _members(path, fmt)
    ]

    data: bytes | None = None
    if fmt == "zip":
        with zipfile.ZipFile(path) as zf:
            zl = zf.NameToInfo
            hit = None
            for v in _member_variants(name):
                if v in zl:
                    hit = v
                    break
            if hit is None:
                raise ZipFsError(f"member {name!r} not found.")
            data = zf.read(hit)[:max_bytes]
    elif fmt in ("tar", "tar.gz", "tar.bz2"):
        with tarfile.open(str(path), _tar_mode(fmt)) as tf:
            hit = _has_member(members_index, name)
            if hit is None:
                raise ZipFsError(f"member {name!r} not found.")
            fo = tf.extractfile(hit["name"])
            if fo is None:
                raise ZipFsError(
                    f"member {name!r} is not a regular file (directory "
                    "or special entry — nothing to read)."
                )
            data = fo.read(max_bytes)
    else:  # 7z (read-only)
        if not _sevenzip_available():
            raise ZipFsError(
                "7z read requires the optional 'py7zr' package "
                "(pip install py7zr)."
            )
        import py7zr

        with py7zr.SevenZipFile(str(path), "r") as z:
            reads = z.read([name])
            data = (reads.get(name) or b"")[:max_bytes]

    if data is None:
        raise ZipFsError(f"member {name!r} could not be read.")

    try:
        text = data.decode("utf-8")
        return {
            "ok": True, "path": Path(path).as_posix(),
            "member": name, "format": fmt,
            "size": len(data), "truncated": len(data) >= max_bytes,
            "encoding": "utf-8", "content": text,
        }
    except UnicodeDecodeError:
        import base64

        return {
            "ok": True, "path": Path(path).as_posix(),
            "member": name, "format": fmt,
            "size": len(data), "truncated": len(data) >= max_bytes,
            "encoding": "base64",
            "content": base64.b64encode(data).decode("ascii"),
            "hint": "binary member — pass encoding='base64' to "
                    "zipfs_write to modify it",
        }


def _has_member(
    members: list[dict[str, Any]], name: str,
) -> dict[str, Any] | None:
    for v in _member_variants(name):
        for m in members:
            if m.get("name") == v:
                return m
    return None


# ─── members snapshot ────────────────────────────────────────────

def _members(path: Path, fmt: str) -> list[dict[str, Any]]:
    return list_members(path, fmt)


# ─── copy-on-write atomic rebuild (the mutation engine) ─────────

def rebuild_archive(
    path: Path,
    *,
    fmt: str,
    delete: set[str] | None = None,
    rename: dict[str, str] | None = None,
    writes: dict[str, bytes] | None = None,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_files: int = DEFAULT_MAX_FILES,
) -> dict[str, Any]:
    """Copy-on-write rebuild: read every member of ``path``, skip the
    ``delete`` set, re-write ``rename`` pairs under their new names,
    and append the ``writes`` entries into a temp file in the SAME
    directory; integrity-check the temp, then ``os.replace`` it over
    the original. Crash-safe: the caller's upload can never be lost."""
    path = Path(path)
    names_in = [str(m.get("name") or "") for m in _members(path, fmt)]
    delete_n: set[str] = set()
    for d in (delete or []):
        dn = _norm_member(d)
        if dn.endswith("/"):
            # subtree delete: every member under the prefix (and the
            # dir entry itself, in either storage shape)
            for nm in names_in:
                if nm.startswith(dn) or nm == dn[:-1]:
                    delete_n.add(nm)
        else:
            delete_n.add(dn)
    rename_n: dict[str, str] = {}
    for k, v in (rename or {}).items():
        kk = _norm_member(k)
        vv = _norm_member(v)
        if kk.endswith("/"):
            # subtree rename: rename each member under the prefix
            for nm in names_in:
                if nm.startswith(kk):
                    rename_n[nm] = vv + nm[len(kk):]
                elif nm == kk[:-1]:
                    rename_n[nm] = vv[:-1] or vv
        elif kk != vv:
            rename_n[kk] = vv
    writes_n: dict[str, bytes] = {}
    for k, v in (writes or {}).items():
        nm = _norm_member(k)
        if not isinstance(v, bytes):
            raise ZipFsError("write payloads must be bytes.")
        if len(v) > max_bytes:
            raise ZipFsError(
                f"write to {nm!r} is {len(v)} bytes — over the "
                f"{max_bytes} archive budget; split the content."
            )
        if _is_dangerous_member(nm):
            raise ZipFsError(
                f"refusing to write member {nm!r}: the "
                f"{_P_suffix(nm)} extension is on the dangerous-write "
                "blocklist (the filesystem write policy refuses the "
                "same extensions)."
            )
        writes_n[nm] = v

    tmp_fd, tmp_name = tempfile.mkstemp(
        prefix=path.stem + "_rebuild_", suffix=path.suffix,
        dir=str(path.parent),
    )
    os.close(tmp_fd)
    tmp = Path(tmp_name)
    added = removed = renamed = 0
    rebuild_bytes = 0
    try:
        if fmt == "zip":
            with zipfile.ZipFile(path, "r") as zin, \
                    zipfile.ZipFile(
                        tmp, "w", compression=zipfile.ZIP_DEFLATED,
                    ) as zout:
                for zinfo in zin.infolist():
                    nm = zinfo.filename
                    if nm in delete_n:
                        removed += 1
                        continue
                    if nm in writes_n:
                        # overwrite semantics — the new bytes are
                        # appended below; do NOT keep the old copy
                        # (a duplicate entry would bloat the archive)
                        continue
                    data = zin.read(zinfo)
                    rebuild_bytes += len(data)
                    if nm in rename_n:
                        nm = rename_n[nm]
                        renamed += 1
                    zinfo2 = zipfile.ZipInfo(
                        nm, date_time=time.localtime()[0:6],
                    )
                    zinfo2.compress_type = zinfo.compress_type
                    zinfo2.external_attr = zinfo.external_attr
                    zout.writestr(zinfo2, data)
                for nm, data in writes_n.items():
                    zout.writestr(
                        zipfile.ZipInfo(
                            nm, date_time=time.localtime()[0:6],
                        ),
                        data,
                    )
                    added += 1
                    rebuild_bytes += len(data)
        else:  # tar family — same walk shape
            with tarfile.open(str(path), _tar_mode(fmt)) as tin, \
                    tarfile.open(str(tmp), _tar_write_mode(fmt)) as tout:
                for ti in tin:
                    nm = ti.name
                    if nm in delete_n:
                        removed += 1
                        continue
                    if nm in writes_n and not ti.isdir():
                        # overwrite semantics (see the zip branch)
                        continue
                    if ti.isdir():
                        if nm in rename_n:
                            ti.name = rename_n[nm]
                            renamed += 1
                        tout.addfile(ti)
                        continue
                    fo = tin.extractfile(ti)
                    data = fo.read() if fo else b""
                    rebuild_bytes += len(data)
                    if nm in rename_n:
                        ti.name = rename_n[nm]
                        renamed += 1
                    tout.addfile(ti, _io.BytesIO(data))
                for nm, data in writes_n.items():
                    ti = tarfile.TarInfo(name=nm)
                    ti.size = len(data)
                    ti.mtime = int(time.time())
                    ti.mode = 0o644
                    tout.addfile(ti, _io.BytesIO(data))
                    added += 1
                    rebuild_bytes += len(data)

        members_out = _members(tmp, fmt)
        if len(members_out) > max_files:
            raise ZipFsError(
                f"rebuild would hold {len(members_out)} members — over "
                f"the {max_files} budget; delete members first."
            )
        total = sum(m.get("size") or 0 for m in members_out)
        if total > max_bytes:
            raise ZipFsError(
                f"rebuild would hold {total} uncompressed bytes — over "
                f"the {max_bytes} budget."
            )
        # integrity probe BEFORE replacing the user's upload
        if fmt == "zip":
            with zipfile.ZipFile(tmp) as zf:
                if zf.testzip() is not None:
                    raise ZipFsError("rebuilt zip failed the CRC probe.")
        else:
            with tarfile.open(str(tmp), _tar_mode(fmt)) as tf:
                _ = tf.getmembers()
        os.replace(tmp, path)
        tmp = None  # replaced — nothing to clean up
        return {
            "ok": True,
            "path": path.as_posix(), "format": fmt,
            "added": added, "removed": removed, "renamed": renamed,
            "members": len(members_out), "total_bytes": total,
            "rebuild_bytes": rebuild_bytes,
        }
    finally:
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def write_member(
    path: Path,
    member: str,
    data: bytes,
    *,
    fmt: str,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_files: int = DEFAULT_MAX_FILES,
) -> dict[str, Any]:
    """Add / overwrite ONE member and atomically rebuild."""
    return rebuild_archive(
        path, fmt=fmt, writes={member: data},
        max_bytes=max_bytes, max_files=max_files,
    )


def delete_members(
    path: Path,
    names: list[str],
    *,
    fmt: str,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_files: int = DEFAULT_MAX_FILES,
) -> dict[str, Any]:
    """Delete members (the CALLER expands directory prefixes) and
    atomically rebuild."""
    return rebuild_archive(
        path, fmt=fmt, delete=set(names),
        max_bytes=max_bytes, max_files=max_files,
    )


def rename_members(
    path: Path,
    renames: dict[str, str],
    *,
    fmt: str,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_files: int = DEFAULT_MAX_FILES,
) -> dict[str, Any]:
    """Rename members (the CALLER expands directory prefixes)."""
    return rebuild_archive(
        path, fmt=fmt, rename=renames,
        max_bytes=max_bytes, max_files=max_files,
    )


# ─── extract one member to disk ──────────────────────────────────

def extract_member(
    path: Path,
    member: str,
    dest: Path,
    *,
    allow_dangerous: bool = False,
    max_member_bytes: int = DEFAULT_MEMBER_BYTES,
) -> dict[str, Any]:
    """Pull ONE member out to ``dest`` (the host has already anchored
    it inside the write sandbox). Dangerous executables are refused
    unless ``allow_dangerous`` is explicitly set."""
    fmt = _sniff(path)
    name = _norm_member(member)
    if not allow_dangerous and _is_dangerous_member(name):
        raise ZipFsError(
            f"member {name!r} ends with a dangerous executable "
            "extension — extraction refused (pass allow_dangerous="
            "true only if you really mean it)."
        )
    data = None
    if fmt == "zip":
        with zipfile.ZipFile(path) as zf:
            for v in _member_variants(name):
                if v in zf.NameToInfo:
                    data = zf.read(v)
                    break
    elif fmt in ("tar", "tar.gz", "tar.bz2"):
        with tarfile.open(str(path), _tar_mode(fmt)) as tf:
            try:
                fo = tf.extractfile(name)
            except KeyError:
                fo = tf.extractfile("./" + name)
            data = fo.read() if fo else None
    else:  # 7z read-only
        if not _sevenzip_available():
            raise ZipFsError(
                "7z read requires the optional 'py7zr' package "
                "(pip install py7zr)."
            )
        import py7zr

        with py7zr.SevenZipFile(str(path), "r") as z:
            reads = z.read([name])
            data = reads.get(name)
    if data is None:
        raise ZipFsError(f"member {name!r} not found / not a regular file.")
    if len(data) > max_member_bytes:
        raise ZipFsError(
            f"member {name!r} is {len(data)} bytes — over the "
            f"{max_member_bytes} per-member extract cap."
        )
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return {
        "ok": True, "member": name, "dest": dest.as_posix(),
        "size": len(data),
    }


# ─── info / tree listing ─────────────────────────────────────────

def archive_info(path: Path) -> dict[str, Any]:
    """One-shot metadata: format, writability, budgets, sizes."""
    fmt = _sniff(path)
    members = _members(path, fmt)
    total = sum(m.get("size") or 0 for m in members)
    compressed = sum(m.get("compressed") or 0 for m in members)
    return {
        "ok": True,
        "path": Path(path).as_posix(),
        "format": fmt,
        "writable": fmt in RW_FORMATS,
        "members": len(members),
        "dirs": sum(1 for m in members if m.get("is_dir")),
        "files": sum(1 for m in members if not m.get("is_dir")),
        "total_uncompressed_bytes": total,
        "archive_bytes": Path(path).stat().st_size,
        "compressed_bytes": compressed or None,
        "budget": {
            "max_members": DEFAULT_MAX_FILES,
            "max_uncompressed_bytes": DEFAULT_MAX_BYTES,
        },
    }


def list_tree(
    path: Path,
    *,
    subdir: str | None = None,
    depth: int = 2,
    max_entries: int = 600,
) -> dict[str, Any]:
    """Depth-limited tree over the archive members (optionally rooted
    at a subdirectory). Deep entries beyond ``depth`` are collapsed
    into a single ``dir/`` row with ``children: '+'``."""
    fmt = _sniff(path)
    members = _members(path, fmt)
    prefix = ""
    if subdir:
        prefix = _norm_member(subdir)
        if not prefix.endswith("/"):
            prefix += "/"
    rows: list[dict[str, Any]] = []
    truncated = False
    seen_dirs: set[str] = set()
    for m in members:
        nm = str(m.get("name") or "")
        nm = nm[2:] if nm.startswith("./") else nm
        if prefix:
            if not (nm.startswith(prefix) or (prefix[:-1] + "/") == nm):
                continue
        rel = nm[len(prefix):] if prefix else nm
        if not rel or rel.endswith(prefix[:-1] + "/"):
            continue
        parts = [p for p in rel.split("/") if p]
        is_dir = bool(m.get("is_dir")) or rel.endswith("/")
        if len(parts) > depth and not is_dir:
            d = "/".join(parts[:depth])
            dirkey = (prefix + d) if prefix else d
            if dirkey not in seen_dirs:
                seen_dirs.add(dirkey)
                rows.append({
                    "name": dirkey + "/", "is_dir": True,
                    "children": "+", "size": None,
                })
            truncated = True
        else:
            rows.append({
                "name": nm, "is_dir": is_dir,
                "size": m.get("size"), "modified": m.get("modified", ""),
                "compressed": m.get("compressed"),
            })
        if len(rows) >= max_entries:
            truncated = True
            break
    return {
        "ok": True, "path": Path(path).as_posix(), "format": fmt,
        "subdir": prefix or None, "depth": depth,
        "count": len(rows), "truncated": truncated,
        "entries": rows,
    }