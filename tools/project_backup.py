# -*- coding: utf-8 -*-
"""project_backup — one-zip project + uploads + Qdrant snapshot
backup / restore (v0.9.133.0).

Self-contained ops helper: bundle the project + sandbox uploads + a
Qdrant collection snapshot into ONE zip, with a matching restore
command.  Everything here is stdlib-only so it can run on a machine
that cannot start the app.

Usage:
  python tools/project_backup.py backup [--out DIR] [--include-qdrant]
  python tools/project_backup.py restore ZIP [--mode all|sandbox|qdrant] [--dry-run]
  python tools/project_backup.py list [--out DIR]

Zip layout:
  project/…      repo snapshot (excludes the heavy volatile dirs)
  uploads/…      sandbox upload/import root (only if it exists)
  qdrant/…       snapshot dir + manifest.json (only if --include-qdrant)
  MANIFEST.json  {version, created, source_dir, counts, sha256, git}

Restore modes:
  project/ → fresh dir (never over the running tree)
  uploads/ → filesystem WRITE root
  qdrant/  → upload snapshot via /snapshots/upload, then restore
  --dry-run prints what WOULD happen and touches nothing.

The Qdrant snapshot needs the server REST API (QDRANT_URL /
QDRANT_API_KEY env or --qdrant-url / --qdrant-api-key).  Everything
else is local files only.

Self-contained: stdlib only.
"""

import argparse
import datetime
import fnmatch
import hashlib
import json
import os
import shutil
import sys
import subprocess
import urllib.error  # noqa: F401 — re-exported for callers/tests
import urllib.request
import zipfile
from pathlib import Path

TOOL_VERSION = "133.0"

REPO = Path(__file__).resolve().parent.parent

# v0.9.133.0 — same volatile dirs the zip payload never carries.
DEFAULT_EXCLUDE_DIRS = {
    ".git", ".venv", "venv", "env", "__pycache__", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", "node_modules", "dist", "build",
    "logs", "sessions", "schedules", "state", "backups", "tmp",
    # v0.9.133.0 round-trip fixes: these ate 300+ MB of non-project
    # payload (android SDK, temp junk, caches) and would blow the zip.
    ".android-sdk", "Temp", "cache", ".cache",
}
DEFAULT_EXCLUDE_PAT = ("*.pyc", "*.pyo", "*.log", "*.tmp", "~$*",
                       "._*", ".DS_Store", "Thumbs.db")


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _excluded(rel: str, name: str, exclude_dirs=None, exclude_pat=None) -> bool:
    """rel is a POSIX-style path relative to the zip payload root
    (never absolute)."""
    ex_dirs = DEFAULT_EXCLUDE_DIRS if exclude_dirs is None else exclude_dirs
    ex_pat = DEFAULT_EXCLUDE_PAT if exclude_pat is None else exclude_pat
    parts = rel.replace("\\", "/").split("/")
    if any(seg in ex_dirs for seg in parts):
        return True
    for pat in ex_pat:  # basename match — one pattern at a time
        if fnmatch.fnmatch(name, pat):
            return True
    return False


def zip_tree(z: zipfile.ZipFile, src: Path = REPO,
             exclude_paths=None) -> dict:
    """Zip the repo snapshot under ``project/`` (z must be open in
    write mode). exclude_paths: resolved paths that must never be
    walked (the backup output dir itself — a backup zip inside its own
    source tree would otherwise grow on every run)."""
    src = src.resolve()
    excl = []
    for p in exclude_paths or []:
        try:
            excl.append(Path(p).resolve())
        except Exception:  # noqa: BLE001
            pass
    count = 0

    def _under_out(p: Path) -> bool:
        for e in excl:
            try:
                p.relative_to(e)
                return True
            except ValueError:
                continue
        return False

    for root, dirs, files in os.walk(src):
        keep = []
        for d in dirs:
            if d in DEFAULT_EXCLUDE_DIRS:
                continue
            if _under_out(Path(root) / d):
                continue
            keep.append(d)
        dirs[:] = keep
        for fn in files:
            fp = Path(root) / fn
            rel = fp.relative_to(src).as_posix()
            if _excluded(rel, fn):
                continue
            if _under_out(fp):
                continue
            z.write(fp, "project/" + rel)
            count += 1
    return {"files": count}


def _upload_root():
    """The sandbox upload/import root — first candidate that exists."""
    env = os.environ.get("QDRANT_SANDBOX_UPLOAD_DIR")
    cands = [Path(env)] if env else []
    cands += [REPO / "uploads", REPO / "state" / "uploads",
              REPO / "sandbox", REPO / "gui" / "uploads"]
    for c in cands:
        if c.is_dir():
            return c
    return None


def collect_upload_files(z: zipfile.ZipFile, uploads_root) -> dict:
    """Add the sandbox upload/import root under ``uploads/``."""
    uploads_root = Path(uploads_root).resolve()
    if not uploads_root.is_dir():
        return {"files": 0, "skipped": "no uploads root"}
    count = 0
    for root, dirs, files in os.walk(uploads_root):
        for fn in files:
            fp = Path(root) / fn
            rel = fp.relative_to(uploads_root).as_posix()
            if _excluded(rel, fn):
                continue
            z.write(fp, "uploads/" + rel)
            count += 1
    return {"files": count}


def qdrant_url() -> str:
    return (os.environ.get("QDRANT_URL") or "http://localhost:6333").rstrip("/")


def qdrant_snapshot(base_url: str, api_key, out_dir, timeout: int = 120) -> dict:
    """Create + download a snapshot for every collection via the REST
    API (HTTP only; no qdrant-client dependency)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(base_url.rstrip("/") + "/collections")
    if api_key:
        req.add_header("api-key", api_key)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        cols = (json.load(r).get("result") or {}).get("collections") or []
    made = []
    for c in cols:
        name = c.get("name") or ""
        if not name or name.startswith("."):
            continue
        req = urllib.request.Request(
            base_url.rstrip("/") + "/collections/" + name + "/snapshots",
            method="POST")
        if api_key:
            req.add_header("api-key", api_key)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            snap_name = (json.load(r).get("result") or {}).get("name")
        if not snap_name:
            continue
        dl = base_url.rstrip("/") + "/collections/" + name + \
            "/snapshots/" + snap_name
        req = urllib.request.Request(dl)
        if api_key:
            req.add_header("api-key", api_key)
        dest = out_dir / (name + ".snapshot")
        with urllib.request.urlopen(req, timeout=timeout) as r, \
                dest.open("wb") as f:
            shutil.copyfileobj(r, f)
        try:  # delete the server-side snapshot (best effort)
            urllib.request.urlopen(urllib.request.Request(
                dl, method="DELETE"), timeout=timeout)
        except Exception:  # noqa: BLE001
            pass
        made.append({"collection": name, "file": dest.name,
                     "bytes": dest.stat().st_size})
    manifest = {"version": TOOL_VERSION, "base_url": base_url,
                "snapshots": made}
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8")
    return {"collections": len(made), "dir": str(out_dir)}


def _git_commit() -> str:
    """One-line git head for the manifest; 'none' when not a repo."""
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%h %s"],
            cwd=str(REPO), capture_output=True, text=True, timeout=10)
        s = (out.stdout or "").strip()
        return s if out.returncode == 0 and s else "none"
    except Exception:  # noqa: BLE001 — git is optional
        return "none"


def _multipart(files: list) -> tuple:
    """files: [(field, filename, bytes)] → (content_type, body)."""
    boundary = "----qdrantrestore" + hashlib.sha256(
        json.dumps([f[1] for f in files]).encode()).hexdigest()[:16]
    parts = []
    for field, filename, data in files:
        head = (
            "--" + boundary + "\r\n"
            "Content-Disposition: form-data; name=\"" + field + "\"; "
            "filename=\"" + filename + "\"\r\n"
            "Content-Type: application/octet-stream\r\n\r\n").encode()
        parts.append(head + data + b"\r\n")
    parts.append(("--" + boundary + "--\r\n").encode())
    body = b"".join(parts)
    return "multipart/form-data; boundary=" + boundary, body


def qdrant_restore_snapshot(base_url: str, api_key, snap_dir,
                            timeout: int = 300) -> dict:
    """Upload every ``*.snapshot`` in snap_dir and trigger its
    recovery. Returns {ok, restored, errors}."""
    snap_dir = Path(snap_dir)
    manifest = {}
    mf = snap_dir / "manifest.json"
    if mf.is_file():
        manifest = json.loads(mf.read_text(encoding="utf-8"))
    restored, errors = [], []
    for ent in manifest.get("snapshots", []) or []:
        name = ent.get("collection") or ""
        fp = snap_dir / ent.get("file", "")
        if not name or not fp.is_file():
            errors.append({"collection": name, "error": "snapshot missing"})
            continue
        ct, body = _multipart([("snapshot", fp.name, fp.read_bytes())])
        base = base_url.rstrip("/")
        req = urllib.request.Request(
            base + "/collections/" + name +
            "/snapshots/upload?priority=snapshot",
            data=body, method="POST")
        req.add_header("Content-Type", ct)
        if api_key:
            req.add_header("api-key", api_key)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                res = json.load(r)
            snap_name = (res.get("result") or {}).get("name") or ""
            if not snap_name:
                errors.append({"collection": name,
                               "error": "no snapshot name"})
                continue
            rec = urllib.request.Request(
                base + "/collections/" + name +
                "/snapshots/" + snap_name + "/recover", method="POST")
            if api_key:
                rec.add_header("api-key", api_key)
            urllib.request.urlopen(rec, timeout=timeout)
            restored.append(name)
        except Exception as e:  # noqa: BLE001
            errors.append({"collection": name, "error": repr(e)})
    return {"ok": not errors, "restored": restored, "errors": errors}


# --- do_backup / do_restore / do_list / main --------------------------------


def do_backup(out_dir, include_qdrant=False, qdrant_base=None,
              qdrant_api_key=None) -> dict:
    """Build the backup zip. Returns {zip, manifest}."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    zpath = out_dir / f"qrag-backup-{stamp}.zip"
    manifest = {"tool_version": TOOL_VERSION, "created": stamp,
                "source_dir": str(REPO), "git": _git_commit(),
                "sections": {}}
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        manifest["sections"]["project"] = zip_tree(
            z, exclude_paths=[out_dir])
        uploads_root = _upload_root()
        if uploads_root:
            manifest["sections"]["uploads"] = collect_upload_files(
                z, uploads_root)
        if include_qdrant:
            base = qdrant_base or qdrant_url()
            snap_dir = out_dir / f"qdrant-{stamp}"
            manifest["sections"]["qdrant"] = qdrant_snapshot(
                base, qdrant_api_key, snap_dir)
            for fp in sorted(snap_dir.iterdir()):
                z.write(fp, "qdrant/" + fp.name)
        z.writestr("MANIFEST.json", json.dumps(manifest, indent=2))
    return {"zip": str(zpath), "manifest": manifest}


def do_restore(zip_path, out_dir, mode="all", dry_run=False) -> dict:
    """Extract a backup zip into out_dir. mode = all | project |
    uploads | qdrant. Returns {ok, extracted, dry_run}."""
    zpath = Path(zip_path)
    if not zpath.is_file():
        return {"ok": False, "error": f"zip not found: {zip_path}"}
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    # v0.9.133.2 — an unfinished/invalid zip is an honest error,
    # never a raw traceback (a killed mid-write backup leaves a
    # tail-less archive that zipfile detects at namelist()).
    try:
        members = zipfile.ZipFile(zpath).namelist()
    except (zipfile.BadZipFile, OSError) as e:
        return {"ok": False, "error": f"zip is invalid or unfinished: {e}"}
    selected = [m for m in members if not m.endswith("/")]
    if mode != "all":
        selected = [m for m in selected if m.startswith(mode + "/")]
    extracted = []
    with zipfile.ZipFile(zpath) as z:
        for m in selected:
            # zip-slip guard: every member must stay inside out_dir
            dest = (out_dir / m).resolve()
            if not str(dest).startswith(str(out_dir.resolve())):
                return {"ok": False,
                        "error": f"zip-slip blocked: {m}"}
            if dry_run:
                extracted.append(m)
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            with z.open(m) as src, dest.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            extracted.append(m)
    return {"ok": True, "dry_run": dry_run, "extracted": extracted,
            "count": len(extracted)}


def do_list(out_dir) -> list:
    """List backup zips (newest first) with their section counts."""
    out_dir = Path(out_dir)
    if not out_dir.is_dir():
        return []
    rows = []
    for zpath in sorted(out_dir.glob("qrag-backup-*.zip"),
                        reverse=True):
        sections = {}
        try:
            with zipfile.ZipFile(zpath) as z:
                mf = json.loads(z.read("MANIFEST.json"))
                sections = mf.get("sections", {})
        except Exception:  # noqa: BLE001 — corrupt manifest still lists
            pass
        rows.append({"zip": str(zpath), "bytes": zpath.stat().st_size,
                     "sections": sections})
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="project_backup",
        description="project_backup — one-zip project + uploads + Qdrant "
                    f"snapshot backup/restore (v0.9.{TOOL_VERSION})")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("backup", help="build the backup zip")
    p.add_argument("--out", default=str(REPO / "backups"))
    p.add_argument("--include-qdrant", action="store_true",
                   help="also snapshot Qdrant collections (REST API)")
    p.add_argument("--qdrant-url", default=None)
    p.add_argument("--qdrant-api-key", default=None)

    p = sub.add_parser("restore", help="extract a backup zip")
    p.add_argument("zip")
    p.add_argument("--out", default=str(REPO / "restore-out"))
    p.add_argument("--mode", choices=["all", "project", "uploads",
                                      "qdrant"], default="all")
    p.add_argument("--dry-run", action="store_true")

    p = sub.add_parser("list", help="list backup zips")
    p.add_argument("--out", default=str(REPO / "backups"))

    args = ap.parse_args(argv)
    if args.cmd == "backup":
        r = do_backup(args.out, include_qdrant=args.include_qdrant,
                      qdrant_base=args.qdrant_url,
                      qdrant_api_key=args.qdrant_api_key)
        print(json.dumps(r["manifest"]["sections"], indent=2))
        print("zip:", r["zip"])
    elif args.cmd == "restore":
        r = do_restore(args.zip, args.out, mode=args.mode,
                       dry_run=args.dry_run)
        print(json.dumps(r, indent=2, default=str))
        if not r.get("ok"):
            return 1
    else:
        for row in do_list(args.out):
            print(f"{row['zip']}  {row['bytes']:>12,} bytes  "
                  f"{json.dumps(row['sections'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
