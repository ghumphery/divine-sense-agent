"""Archive tools — create / list / extract zip, 7z, tar, gzip (v0.9.8+).

Pure format logic: no MCP, no sandbox enforcement (that is the
caller's job — ``ArchiveLocalHost`` in ``src/mcp_client/host.py``
resolves every path against the filesystem read/write roots before
anything here touches disk).

Formats
-------

==========  =====================  ==================================
format      create                 extract
==========  =====================  ==================================
zip         stdlib zipfile         stdlib zipfile (streamed)
7z          py7zr (lazy import)    py7zr (lazy import)
tar         stdlib tarfile         stdlib tarfile (``filter='data'``)
tar.gz      stdlib tarfile w:gz    stdlib tarfile r:gz
tar.bz2     stdlib tarfile w:bz2   stdlib tarfile r:bz2
gz          stdlib gzip (1 file)   stdlib gzip (1 file)
==========  =====================  ==================================

``7z`` needs the optional ``py7zr`` package; :func:`sevenzip_available`
reports availability so hosts can degrade gracefully with a clear
"pip install py7zr" message instead of an ImportError traceback.

Security (enforced here, re-checked by the host):

* **Zip-Slip** — every extracted member is resolved and must stay
  inside the destination directory; ``..`` segments, absolute paths,
  and Windows drive prefixes are all rejected *before* any write.
* **Zip-bomb bounds** — the caller passes ``max_bytes`` /
  ``max_files``; :func:`extract_archive` aborts (partial extraction
  reported) the moment a member would exceed either.
* **Dangerous extensions** — members whose name ends with an
  executable-ish extension (``.exe`` / ``.bat`` / ``.sh`` / …, the
  same blocklist the filesystem MCP writes behind) are skipped and
  reported rather than written to disk.
"""
from __future__ import annotations

import gzip
import os
import shutil
import tarfile
import time
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Callable

__all__ = [
    "CREATE_FORMATS",
    "DEFAULT_MAX_BYTES",
    "DEFAULT_MAX_FILES",
    "extract_archive",
    "list_members",
    "sevenzip_available",
    "sniff_archive_format",
    "ArchiveError",
]

CREATE_FORMATS = ("zip", "7z", "tar", "tar.gz", "tar.bz2", "gz")

DEFAULT_MAX_BYTES = 512 * 1024 * 1024   # 512 MB total uncompressed
DEFAULT_MAX_FILES = 10000               # member-count cap

# Extensions this module refuses to *extract* by default. The
# archive layer only skips them (never executes anything); the
# blocklist mirrors the filesystem MCP's dangerous-write list so
# an archive can't be used to smuggle executables past the
# write-sandbox policy.
_DANGEROUS_EXTS: frozenset[str] = frozenset({
    ".exe", ".bat", ".cmd", ".com", ".scr", ".ps1", ".vbs", ".vbe",
    ".js", ".jse", ".wsf", ".wsh", ".msi", ".mst", ".msp", ".sh",
    ".bash", ".bashrc", ".profile", ".dll", ".so", ".dylib", ".reg",
})


class ArchiveError(Exception):
    """Raised for archive-level failures the LLM should see."""


def sevenzip_available() -> bool:
    """True iff the optional ``py7zr`` package can be imported."""
    try:
        import py7zr  # noqa: F401

        return True
    except Exception:
        return False


def sniff_archive_format(path: str | Path) -> str | None:
    """Map a filename to one of the supported archive formats.

    Extension-driven (content sniffing would mis-detect text tar
    members); ``None`` means "no supported format".
    """
    name = Path(path).name.lower()
    if name.endswith((".tar.gz", ".tgz")):
        return "tar.gz"
    if name.endswith((".tar.bz2", ".tbz2")):
        return "tar.bz2"
    if name.endswith(".tar"):
        return "tar"
    if name.endswith(".zip"):
        return "zip"
    if name.endswith(".7z"):
        return "7z"
    if name.endswith(".gz"):
        return "gz"
    return None


def _is_dangerous(name: str) -> bool:
    return Path(name).suffix.lower() in _DANGEROUS_EXTS


def _safe_dest(dest_root: Path, member_name: str) -> Path | None:
    """Resolve a member name against ``dest_root``.

    Returns the resolved target path, or ``None`` when the member
    escapes the destination root (``..`` traversal, absolute path,
    Windows drive prefix). Empty names and directory entries resolve
    to a directory path — the caller decides what to do.
    """
    p = PurePosixPath(member_name.replace("\\", "/"))
    if p.is_absolute() or (p.parts and p.parts[0].endswith(":")):
        return None
    target = (dest_root / p).resolve()
    root = dest_root.resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return None
    return target


# ─── create ──────────────────────────────────────────────────────

def _write_zip(files: list[tuple[Path, str]], out: Path) -> None:
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for f, arcname in files:
            zf.write(str(f), arcname=arcname)


def _write_tar(
    files: list[tuple[Path, str]], out: Path, mode: str,
) -> None:
    with tarfile.open(out, mode) as tf:
        for f, arcname in files:
            tf.add(str(f), arcname=arcname, recursive=False)


def _write_gz(src: Path, out: Path) -> None:
    with open(src, "rb") as fin, gzip.GzipFile(
        filename=src.name, mode="wb", fileobj=open(out, "wb"),
    ) as fout:
        shutil.copyfileobj(fin, fout)


def _write_7z(files: list[tuple[Path, str]], out: Path) -> None:
    try:
        import py7zr
    except Exception as e:  # pragma: no cover — guarded by caller
        raise ArchiveError(
            "7z support requires the optional 'py7zr' package "
            f"(pip install py7zr): {e!r}",
        ) from e
    with py7zr.SevenZipFile(str(out), "w") as z:
        for f, arcname in files:
            z.write(str(f), arcname=arcname)


def create_archive(
    files: list[tuple[Path, str]],
    out: Path,
    fmt: str,
) -> dict[str, Any]:
    """Write ``files`` ([(abs_path, arcname), …]) into ``out``.

    ``fmt`` must be one of :data:`CREATE_FORMATS`. Returns a small
    summary dict; raises :class:`ArchiveError` on failure.
    """
    if fmt not in CREATE_FORMATS:
        raise ArchiveError(
            f"unsupported format {fmt!r}; expected one of "
            f"{list(CREATE_FORMATS)}",
        )
    if fmt == "7z" and not sevenzip_available():
        raise ArchiveError(
            "7z support requires the optional 'py7zr' package "
            "(pip install py7zr).",
        )
    if fmt == "gz":
        if len(files) != 1:
            raise ArchiveError(
                "format 'gz' compresses exactly ONE file — use "
                "'tar.gz' to bundle multiple files.",
            )
        src, _ = files[0]
        if not src.is_file():
            raise ArchiveError("'gz' can only compress a regular file.")

    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        if fmt == "zip":
            _write_zip(files, out)
        elif fmt == "7z":
            _write_7z(files, out)
        elif fmt == "tar":
            _write_tar(files, out, "w")
        elif fmt == "tar.gz":
            _write_tar(files, out, "w:gz")
        elif fmt == "tar.bz2":
            _write_tar(files, out, "w:bz2")
        else:  # gz
            src, _ = files[0]
            _write_gz(src, out)
    except ArchiveError:
        raise
    except Exception as e:
        raise ArchiveError(f"archive creation failed: {e!r}") from e

    total_in = sum(
        f.stat().st_size for f, _ in files if f.is_file()
    )
    arch = out.stat().st_size
    return {
        "format": fmt,
        "out_path": out.resolve().as_posix(),
        "file_count": len(files),
        "input_bytes": total_in,
        "archive_bytes": arch,
        "compression_ratio": (
            round(total_in / arch, 2) if arch and total_in else None
        ),
    }


# ─── list ────────────────────────────────────────────────────────

def list_members(path: Path, fmt: str) -> list[dict[str, Any]]:
    """List archive members: name / size / compressed / is_dir / mtime."""
    out: list[dict[str, Any]] = []
    if fmt == "zip":
        with zipfile.ZipFile(path) as zf:
            for info in zf.infolist():
                out.append({
                    "name": info.filename,
                    "size": int(info.file_size),
                    "compressed": int(info.compress_size),
                    "is_dir": info.is_dir(),
                    "modified": (
                        "%04d-%02d-%02d %02d:%02d:%02d" % info.date_time
                    ) if info.date_time else "",
                })
    elif fmt == "7z":
        if not sevenzip_available():
            raise ArchiveError(
                "7z support requires the optional 'py7zr' package "
                "(pip install py7zr).",
            )
        import py7zr

        with py7zr.SevenZipFile(str(path), "r") as z:
            for info in z.list():
                out.append({
                    "name": info.filename,
                    "size": int(info.uncompressed or 0),
                    "compressed": int(info.compressed or 0),
                    "is_dir": bool(info.is_directory),
                    "modified": str(info.creationtime or ""),
                })
    elif fmt in ("tar", "tar.gz", "tar.bz2"):
        mode = (
            "r:gz" if fmt == "tar.gz"
            else "r:bz2" if fmt == "tar.bz2"
            else "r:"
        )
        with tarfile.open(str(path), mode) as tf:
            for m in tf:
                out.append({
                    "name": m.name,
                    "size": int(m.size or 0),
                    "compressed": 0,
                    "is_dir": m.isdir(),
                    "modified": time.strftime(
                        "%Y-%m-%d %H:%M:%S",
                        time.gmtime(m.mtime or 0),
                    ),
                })
    elif fmt == "gz":
        stem = Path(path).name[:-3] if str(path).lower().endswith(".gz") else "content"
        size = 0
        with gzip.open(str(path), "rb") as fh:
            while True:
                chunk = fh.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
        out.append({
            "name": stem,
            "size": size,
            "compressed": Path(path).stat().st_size,
            "is_dir": False,
            "modified": "",
        })
    else:
        raise ArchiveError(f"unsupported format {fmt!r}")
    return out


# ─── extract ─────────────────────────────────────────────────────

def extract_archive(
    path: Path,
    dest: Path,
    *,
    fmt: str | None = None,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_files: int = DEFAULT_MAX_FILES,
    skip_dangerous: bool = True,
    overwrite: bool = False,
    members: list[str] | None = None,
) -> dict[str, Any]:
    """Extract ``path`` into ``dest`` with full safety checks.

    * every member is resolved and must stay inside ``dest``
      (Zip-Slip / absolute-path / drive-prefix rejection);
    * cumulative uncompressed size is capped at ``max_bytes`` and the
      member count at ``max_files`` (zip-bomb bounds);
    * dangerous executable extensions are skipped and reported when
      ``skip_dangerous`` is set;
    * ``members`` (when given) restricts the extraction scope — names
      outside the list are silently ignored.

    Returns ``{target_dir, extracted, skipped, count, total_bytes}``.
    Raises :class:`ArchiveError` for structural problems (unknown
    format, corrupt archive, 7z without py7zr).
    """
    fmt = fmt or sniff_archive_format(path)
    if not fmt:
        raise ArchiveError(
            "cannot determine archive format from the file name — "
            "supported: .zip .7z .tar .tar.gz .tgz .tar.bz2 .gz",
        )
    if fmt == "7z" and not sevenzip_available():
        raise ArchiveError(
            "7z support requires the optional 'py7zr' package "
            "(pip install py7zr).",
        )

    dest.mkdir(parents=True, exist_ok=True)
    dest_r = dest.resolve()
    extracted: list[str] = []
    skipped: list[dict[str, Any]] = []
    state = {"total": 0}

    def _skip(name: str, reason: str) -> None:
        skipped.append({"name": name, "reason": reason})

    def _guard(name: str, size: int) -> Path | None:
        """Shared member guard → resolved target or None to skip."""
        if members is not None and name not in members:
            return None  # out of requested scope — not an error
        if size >= 0 and state["total"] + size > max_bytes:
            _skip(name, f"exceeds max_bytes budget ({max_bytes})")
            return None
        if len(extracted) >= max_files:
            _skip(name, f"exceeds max_files budget ({max_files})")
            return None
        if skip_dangerous and _is_dangerous(name):
            _skip(name, "dangerous executable extension")
            return None
        target = _safe_dest(dest_r, name)
        if target is None:
            _skip(name, "escapes the destination sandbox (zip-slip)")
            return None
        if target.exists() and not overwrite and target.is_file():
            _skip(name, "target exists (overwrite=False)")
            return None
        return target

    if fmt == "zip":
        with zipfile.ZipFile(path) as zf:
            for info in zf.infolist():
                name = info.filename
                if info.is_dir():
                    t = _safe_dest(dest_r, name)
                    if t is not None:
                        t.mkdir(parents=True, exist_ok=True)
                    continue
                target = _guard(name, int(info.file_size))
                if target is None:
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, open(target, "wb") as fout:
                    shutil.copyfileobj(src, fout)
                state["total"] += int(info.file_size)
                extracted.append(name)
    elif fmt in ("tar", "tar.gz", "tar.bz2"):
        mode = (
            "r:gz" if fmt == "tar.gz"
            else "r:bz2" if fmt == "tar.bz2"
            else "r:"
        )
        with tarfile.open(str(path), mode) as tf:
            for m in tf.getmembers():
                name = m.name
                if m.isdir():
                    t = _safe_dest(dest_r, name)
                    if t is not None:
                        t.mkdir(parents=True, exist_ok=True)
                    continue
                if not m.isfile():
                    _skip(name, "non-regular member (link / device)")
                    continue
                target = _guard(name, int(m.size or 0))
                if target is None:
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                src = tf.extractfile(m)
                if src is None:
                    _skip(name, "member has no data")
                    continue
                with src, open(target, "wb") as fout:
                    shutil.copyfileobj(src, fout)
                state["total"] += int(m.size or 0)
                extracted.append(name)
    elif fmt == "7z":
        import py7zr

        with py7zr.SevenZipFile(str(path), "r") as z:
            infos = z.list()
            selected: list[str] = []
            for info in infos:
                name = info.filename
                if info.is_directory:
                    t = _safe_dest(dest_r, name)
                    if t is not None:
                        t.mkdir(parents=True, exist_ok=True)
                    continue
                target = _guard(name, int(info.uncompressed or 0))
                if target is not None:
                    selected.append(name)
            if selected:
                z.reset()
                # py7zr 1.x dropped the ``overwrite`` kwarg — targets
                # that already exist are filtered by our own guard
                # above (overwrite=False skips existing files), so
                # the bare extract call keeps the same semantics.
                z.extract(
                    targets=selected,
                    path=str(dest_r),
                )
                extracted.extend(selected)
                state["total"] += sum(
                    int(i.uncompressed or 0) for i in infos
                    if i.filename in selected
                )
    else:  # gz — exactly one file, streamed
        stem = Path(path).name[:-3] if str(path).lower().endswith(".gz") else "content"
        stem = Path(stem).name or "content"
        target = _guard(stem, 0)   # size unknown until streamed
        if target is not None:
            target.parent.mkdir(parents=True, exist_ok=True)
            total = 0
            overflow = False
            with gzip.open(str(path), "rb") as src, open(target, "wb") as fout:
                while True:
                    chunk = src.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        overflow = True
                        break
                    fout.write(chunk)
            if overflow:
                try:
                    target.unlink()
                except OSError:
                    pass
                _skip(stem, "exceeds max_bytes budget while streaming")
            else:
                state["total"] = total
                extracted.append(stem)

    return {
        "target_dir": dest_r.as_posix(),
        "extracted": extracted,
        "skipped": skipped,
        "count": len(extracted),
        "total_bytes": state["total"],
    }