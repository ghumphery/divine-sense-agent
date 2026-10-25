"""HTTP bridge exposing the GUI as a plain web app (v0.9.15.0).

Launch options (in addition to the pywebview window):

    python -m gui.app --web          # documented selector (gui mode stays default)
    python gui/web_server.py         # direct entry
    divine-sense-agent-web.bat       # double-click launcher

Serves the SAME ``gui/assets/`` frontend as the pywebview GUI and
exposes the SAME ``gui.api.Api`` methods over ``POST /api/<method>``.
A small shim (``/pywebview-shim.js``) is injected into index.html's
``<head>`` so it runs BEFORE app.js and implements the
``window.pywebview`` surface (``api.<method>(...)`` returning
Promises, ``state``, ``FileDialog`` + ``create_file_dialog``) on top
of ``fetch`` — ``app.js`` / ``diagrams.js`` run UNMODIFIED in both
modes, and ``pywebviewready`` fires immediately (no boot race).

Security model:
    * binds ``127.0.0.1`` by default — the Api can drive desktop
      automation and filesystem tools, so it must never be exposed
      to the LAN; a loud WARNING is logged for any non-loopback host;
    * v0.9.15.1+: ANY local IP is bindable — ``0.0.0.0`` (all IPv4
      interfaces; the reachable LAN URLs are printed at startup and
      the browser is opened on a loopback URL), a specific LAN IPv4,
      or IPv6 (``::1`` / ``::`` dual-stack) via the AF_INET6 server;
    * only PUBLIC Api methods (no leading ``_``) are dispatchable;
    * host/port come from ``--host`` / ``--web-port`` flags or the
      ``QDRANT_WEB_HOST`` / ``QDRANT_WEB_PORT`` env vars (``.env`` is
      loaded BEFORE they are resolved — v0.9.15.1 fix: previously the
      env vars were read before ``Api()`` triggered src.config's
      load_dotenv, so ``.env`` values were silently ignored).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import mimetypes
import os
import re
import socket
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("gui.web")

DEFAULT_WEB_HOST = "127.0.0.1"
DEFAULT_WEB_PORT = 8334

# v0.9.15.1 — host classification for the "bind any local IP" support.
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
WILDCARD_HOSTS = frozenset({"0.0.0.0", "::"})

# v0.9.15.3 — app.js polls ``get_status`` every 30 s PER OPEN PAGE
# (``setInterval(refreshStatus, 30000)`` — the status-bar heartbeat)
# plus once per page boot. With several web-UI pages open (desktop
# tabs + phone) that is one INFO line every few seconds flooding
# app.log, and it keeps flowing during long chat turns. Heartbeat
# methods are therefore logged at DEBUG; every other method stays
# at INFO so real actions (chat_turn / ingest / list_sessions / …)
# remain visible.
HEARTBEAT_METHODS = frozenset({
    "get_status",
    # v0.9.82.4 — the ⏳ task panel auto-refreshes every 2 s while
    # open; a forgotten-open panel floods app.log with INFO lines
    # (same lesson as v0.9.15.3 get_status).
    "list_background_tasks",
    "get_readiness",
})


# ─── v0.9.16.0 — client file upload (web mode) ────────────────────
#
# The web shim's ``create_file_dialog`` opens a tkinter dialog on the
# SERVER's desktop — useless for phones / remote LAN clients. The
# ``POST /upload`` endpoint accepts ``multipart/form-data`` (built by
# the frontend from ``<input type=file>`` via FormData) and saves the
# parts into the filesystem WRITE sandbox under ``uploads/`` — the
# same sandbox ``FilesystemLocalHost`` writes to, so the LLM can
# read / process what was uploaded (and Api.ingest_file can ingest it
# by absolute path). Python 3.13+ removed the ``cgi`` module, so the
# multipart parsing here is a small pure-stdlib implementation.

UPLOAD_DIR_NAME = "uploads"
UPLOAD_MAX_FILES = 20          # per request
_UPLOAD_DEFAULT_MAX_MB = 200
# Early-reject paths (413 / bad content type) drain up to this many
# body bytes first so the client gets the JSON error response instead
# of a socket reset while it is still streaming the upload.
_UPLOAD_DRAIN_MAX = 8 * 1024 * 1024


def _upload_max_bytes() -> int:
    """Upload body budget in bytes from ``QDRANT_WEB_UPLOAD_MAX_MB``
    (default 200 MB, floor 1 MB). Invalid values fall back to the
    default with a warning — a typo must not take down the server."""
    raw = os.environ.get("QDRANT_WEB_UPLOAD_MAX_MB")
    if not raw:
        return _UPLOAD_DEFAULT_MAX_MB * 1024 * 1024
    try:
        mb = int(raw)
        if mb <= 0:
            raise ValueError
        return mb * 1024 * 1024
    except ValueError:
        logger.warning(
            "invalid QDRANT_WEB_UPLOAD_MAX_MB=%r — using default %d MB",
            raw, _UPLOAD_DEFAULT_MAX_MB,
        )
        return _UPLOAD_DEFAULT_MAX_MB * 1024 * 1024


def _sanitize_upload_name(name: str) -> str:
    """Reduce a client-supplied filename to a safe sandbox leaf:
    basename only (traversal), Windows-illegal chars stripped,
    whitespace collapsed, length capped, never empty."""
    name = os.path.basename((name or "").replace("\\", "/")).strip()
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    name = re.sub(r"\s+", " ", name).strip(". ")
    if not name:
        name = "upload.bin"
    return name[:120]


# ─── v0.9.65.0 — chat file download (web mode) ────────────────────
#
# The reverse direction of POST /upload: the LLM generates files
# inside the filesystem sandbox (write_file / exec_python / archive /
# describe_image snapshots / uploaded uploads/ files) and the chat
# UI renders a ⬇ download chip per detected sandbox path. GET
# /download?path=<p> resolves the path through the SAME sandbox
# resolution as Api.read_sandbox_image (file:/// strip, relative →
# WRITE root, import ∪ READ ∪ WRITE containment — never an arbitrary
# file read), refuses dangerous executables, caps the size
# (QDRANT_WEB_DOWNLOAD_MAX_MB, default 200) and streams the bytes
# with an RFC 5987 Content-Disposition (CJK filenames safe).

_DOWNLOAD_DEFAULT_MAX_MB = 200
_DOWNLOAD_CHUNK = 1024 * 1024


def _download_max_bytes() -> int:
    """Download size budget in bytes from
    ``QDRANT_WEB_DOWNLOAD_MAX_MB`` (default 200 MB, floor 1 MB).
    Invalid values fall back to the default with a warning — a typo
    must not take down the server."""
    raw = os.environ.get("QDRANT_WEB_DOWNLOAD_MAX_MB")
    if not raw:
        return _DOWNLOAD_DEFAULT_MAX_MB * 1024 * 1024
    try:
        mb = int(raw)
        if mb <= 0:
            raise ValueError
        return mb * 1024 * 1024
    except ValueError:
        logger.warning(
            "invalid QDRANT_WEB_DOWNLOAD_MAX_MB=%r — using default %d MB",
            raw, _DOWNLOAD_DEFAULT_MAX_MB,
        )
        return _DOWNLOAD_DEFAULT_MAX_MB * 1024 * 1024


def _content_disposition(filename: str) -> str:
    """RFC 6266/5987 header — an ASCII fallback plus a UTF-8 encoded
    ``filename*`` so CJK / accented names survive browsers.

    v0.9.133.5: ``.html`` files produced by the LLM (OpenUI
    browser-bundle previews, etc.) get ``inline`` + a CSP-sandbox
    response so a REMOTE user (phone / LAN / public https) can click
    the chat link and the page RENDERS in place instead of becoming a
    download. ``Content-Security-Policy: sandbox allow-scripts`` makes
    the document an OPAQUE origin — scripts run (incl. https CDN
    bundles) but the page cannot touch this origin's localStorage /
    cookies / DOM (the chat session stays isolated). The download cap
    and the dangerous-extension refusal are unchanged."""
    name = os.path.basename(filename or "")
    ext = os.path.splitext(name)[1].lower()
    if ext == ".html":
        fallback = (
            name.encode("ascii", "ignore").decode("ascii") or "preview"
        ).replace('"', "'")
        quoted = urllib.parse.quote(name, safe="")
        return (f"inline; filename=\"{fallback}\"; "
                f"filename*=UTF-8''{quoted}")
    fallback = (
        filename.encode("ascii", "ignore").decode("ascii") or "download"
    ).replace('"', "'")
    quoted = urllib.parse.quote(filename, safe="")
    return (
        f"attachment; filename=\"{fallback}\"; "
        f"filename*=UTF-8''{quoted}"
    )


def _download_ext_allowed(name: str) -> tuple[bool, str]:
    """Dangerous-executable refusal — the same blocklist the
    filesystem MCP write tools / ``POST /upload`` / telegram file
    tools use."""
    from src.mcp_client.host import _FS_DANGEROUS_WRITE_EXTS

    ext = os.path.splitext(name)[1].lower()
    if ext in _FS_DANGEROUS_WRITE_EXTS:
        return False, f"dangerous extension {ext!r} refused"
    return True, ""


def _parse_multipart(
    body: bytes,
    content_type: str,
    keep_fields: bool = False,
) -> list[dict[str, Any]]:
    """Parse one ``multipart/form-data`` body (pure stdlib; the
    ``cgi`` module is gone since Python 3.13). Returns the FILE parts
    (``filename`` present) as ``{"name", "filename", "data"}`` dicts;
    plain form fields are ignored — unless ``keep_fields=True``
    (v0.9.127.5, the share_target path), when they are appended as
    ``{"name", "value"}`` entries WITHOUT a ``filename`` key (that
    key's absence is what distinguishes them for the callers).
    Raises ``ValueError`` when the boundary is missing or the body
    carries no parts at all."""
    m = re.search(r'boundary="?([^";,]+)"?', content_type or "", re.I)
    if not m:
        raise ValueError("multipart boundary missing")
    boundary = m.group(1).encode("latin-1")
    delim = b"--" + boundary
    chunks = body.split(delim)
    parts: list[dict[str, Any]] = []
    # chunks[0] = preamble; an element starting with b"--" is the
    # closing marker's remainder. Everything else is one form part.
    for raw in chunks[1:]:
        if raw[:2] == b"--":
            break  # closing boundary reached
        if raw.startswith(b"\r\n"):
            raw = raw[2:]
        head, sep, data = raw.partition(b"\r\n\r\n")
        if not sep:
            continue
        if data.endswith(b"\r\n"):
            data = data[:-2]
        disp = ""
        for line in head.split(b"\r\n"):
            if line[:20].lower() == b"content-disposition:":
                disp = line.decode("utf-8", "replace")
                break
        if not disp:
            continue
        name_m = re.search(r'name="([^"]*)"', disp)
        file_m = re.search(r'filename="([^"]*)"', disp)
        if not file_m:
            if keep_fields:
                parts.append({
                    "name": name_m.group(1) if name_m else "",
                    "value": data.decode("utf-8", "replace"),
                })
            continue  # plain field (not a file) — ignored in legacy mode
        parts.append({
            "name": name_m.group(1) if name_m else "files",
            "filename": file_m.group(1),
            "data": data,
        })
    if not parts:
        raise ValueError("no file parts found")
    return parts


class _ThreadingHTTPServer6(ThreadingHTTPServer):
    """AF_INET6 variant so IPv6 hosts (``::1``, ``::``, LAN v6) can
    actually bind — the base ``ThreadingHTTPServer`` is IPv4-only and
    any IPv6 host raised ``OSError`` before v0.9.15.1. For the ``::``
    wildcard we ALSO clear ``IPV6_V6ONLY`` so one socket serves both
    stacks (Windows defaults to v6-only); failures are non-fatal."""

    address_family = socket.AF_INET6

    def server_bind(self) -> None:  # noqa: D102 (stdlib override)
        try:
            self.socket.setsockopt(
                socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0,
            )
        except OSError:
            pass  # platform without the sockopt — bind v6-only
        super().server_bind()


# ─── pywebview shim (served at /pywebview-shim.js) ────────────────

_SHIM_JS = """\
/* v0.9.15.0 - web-mode pywebview bridge shim (injected into <head>). */
(function () {
  'use strict';
  function callMethod(name, args) {
    return fetch('/api/' + encodeURIComponent(name), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(args || []),
    }).then(function (resp) {
      return resp.json().catch(function () { return {}; })
        .then(function (data) {
          if (!resp.ok || data.ok === false) {
            throw new Error(data.error || ('HTTP ' + resp.status));
          }
          return data.result;
        });
    });
  }
  const apiObj = new Proxy({}, {
    get: function (_t, prop) {
      if (typeof prop !== 'string') return undefined;
      return function () {
        return callMethod(prop, Array.prototype.slice.call(arguments));
      };
    },
  });
  const state = {
    _data: {},
    set: function (k, v) { this._data[k] = v; },
    get: function (k) { return this._data[k]; },
  };
  /* v0.9.16.0+ — app.js reads this to enable the client-upload UI
     (📎 / 📷 buttons + fetch('/upload')). The real pywebview bridge
     never sets it, so GUI mode hides the upload buttons. */
  window.__WEB_MODE__ = true;
  window.pywebview = {
    api: apiObj,
    state: state,
    FileDialog: { OPEN: 'open', FOLDER: 'folder' },
    create_file_dialog: function (dialogType) {
      return callMethod('__file_dialog', [
        dialogType === 'folder' ? 'folder' : 'open',
      ]);
    },
  };
  setTimeout(function () {
    try { window.dispatchEvent(new Event('pywebviewready')); } catch (_) {}
  }, 0);
})();
"""

_MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".map": "application/json; charset=utf-8",
    # v0.9.125.9 — PWA installability (standalone launch = no browser
    # banner on Android)
    ".webmanifest": "application/manifest+json",
}


def _inject_shim(html: str) -> str:
    """Insert the shim ``<script>`` right after ``<head>`` so it runs
    before every other script (app.js is loaded at the end of body)."""
    return re.sub(
        r"(<head[^>]*>)",
        lambda m: m.group(1) + '\n<script src="/pywebview-shim.js"></script>',
        html,
        count=1,
        flags=re.I,
    )


def _save_upload_parts(parts: list[dict[str, Any]]) -> dict[str, Any]:
    """Write the parsed upload parts into
    ``<filesystem write sandbox>/uploads/`` with a timestamp prefix
    (collision-safe ``-N`` suffix). Dangerous executable extensions
    are refused (same blocklist as the filesystem MCP write tools).
    Never raises — failures land in ``skipped``."""
    try:
        from src.mcp_client.host import (
            _FS_DANGEROUS_WRITE_EXTS,
            _resolve_filesystem_root,
        )

        write_root = _resolve_filesystem_root("QDRANT_FILESYSTEM_WRITE_ROOT")
    except Exception as e:  # sandbox unresolvable — refuse the upload
        return {"ok": False, "error": f"cannot resolve write sandbox: {e!r}"}
    uploads = write_root / UPLOAD_DIR_NAME
    try:
        uploads.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return {"ok": False, "error": f"cannot create uploads dir: {e}"}
    stamp = time.strftime("%Y%m%d-%H%M%S")
    saved: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for part in parts[:UPLOAD_MAX_FILES]:
        clean = _sanitize_upload_name(part["filename"])
        ext = os.path.splitext(clean)[1].lower()
        if ext in _FS_DANGEROUS_WRITE_EXTS:
            skipped.append({
                "filename": part["filename"],
                "reason": f"dangerous extension {ext!r} refused",
            })
            continue
        target = uploads / f"{stamp}-{clean}"
        n = 0
        while target.exists() and n < 1000:
            n += 1
            target = uploads / f"{stamp}-{n}-{clean}"
        try:
            target.write_bytes(part["data"])
        except OSError as e:
            skipped.append({
                "filename": part["filename"],
                "reason": f"write failed: {e}",
            })
            continue
        saved.append({
            "name": clean,
            "path": target.resolve().as_posix(),
            "relative_path": f"{UPLOAD_DIR_NAME}/{target.name}",
            "size": len(part["data"]),
        })
    for part in parts[UPLOAD_MAX_FILES:]:
        skipped.append({
            "filename": part["filename"],
            "reason": f"request carries more than {UPLOAD_MAX_FILES} files",
        })
    return {
        "ok": True,
        "files": saved,
        "skipped": skipped,
        "write_root": write_root.as_posix(),
        "dir": uploads.resolve().as_posix(),
    }


# ─── v0.9.127.5 — share_target text sidecar ───────────────────────
#
# When the OS share carries title/text/url instead of (or next to)
# the files, the text lands as a plain ``shared-<ts>.txt`` in the
# SAME uploads/ sandbox — the redirect marker flow then treats it
# like any other uploaded file (and the LLM can read it verbatim).

_SHARE_TEXT_MAX_CHARS = 200000


def _store_share_text_file(text: str) -> "str | None":
    """Save one text blob as ``<write sandbox>/uploads/shared-<ts>.txt``.
    Never raises — failures return None (the share still lands with
    the files it managed to save)."""
    try:
        from src.mcp_client.host import _resolve_filesystem_root

        write_root = _resolve_filesystem_root(
            "QDRANT_FILESYSTEM_WRITE_ROOT")
    except Exception:
        return None
    uploads = write_root / UPLOAD_DIR_NAME
    try:
        uploads.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = uploads / f"{stamp}-shared.txt"
    n = 0
    while target.exists() and n < 1000:
        n += 1
        target = uploads / f"{stamp}-{n}-shared.txt"
    try:
        target.write_text(text[:_SHARE_TEXT_MAX_CHARS],
                          encoding="utf-8")
    except OSError:
        return None
    return target.resolve().as_posix()


def _save_share_text(fields: dict[str, str]) -> "str | None":
    """Compose the share fields (share_title / share_url / share_text
    — the manifest ``params`` names) into a readable text sidecar and
    store it. None when nothing shareable arrived."""
    title = (fields.get("share_title") or "").strip()
    url = (fields.get("share_url") or "").strip()
    text = (fields.get("share_text") or "").strip()
    if not (title or url or text):
        return None
    blob = f"[title] {title}\n[url] {url}\n[text]\n{text}"
    return _store_share_text_file(blob)


def _native_file_dialog(mode: str) -> list[str]:
    """Native open-file / pick-folder dialog via tkinter (stdlib).

    Only sensible when the server runs on the user's own machine
    (the default 127.0.0.1 bind) — the dialog opens on the server's
    desktop, which IS the user's desktop in that setup.
    """
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    try:
        root.attributes("-topmost", True)
    except Exception:
        pass
    try:
        if mode == "folder":
            path = filedialog.askdirectory(parent=root)
        else:
            path = filedialog.askopenfilename(parent=root)
        return [path] if path else []
    finally:
        try:
            root.destroy()
        except Exception:
            pass


def call_api_method(api: Any, method: str, args: list[Any]) -> dict[str, Any]:
    """Dispatch one bridge call. Returns ``{"ok": ..., ...}`` — the
    HTTP layer always answers 200 with this envelope (the Api itself
    already returns ``{"error": ...}`` dicts per its own contract).

    Testable without the HTTP server: the tests call this directly.
    """
    if method == "__file_dialog":
        try:
            mode = str(args[0]) if args else "open"
            return {"ok": True, "result": _native_file_dialog(mode)}
        except Exception as e:  # no display / tkinter missing → degrade
            return {"ok": False, "error": f"no native dialog: {e!r}"}
    if method.startswith("_") or not hasattr(api, method):
        return {"ok": False, "error": f"unknown api method: {method}"}
    fn = getattr(api, method)
    if not callable(fn):
        return {"ok": False, "error": f"api.{method} is not callable"}
    try:
        result = fn(*args)
    except Exception as e:
        logger.error("api call %s FAILED: %r", method, e, exc_info=True)
        return {"ok": False, "error": repr(e)}
    # v0.9.15.0: a non-JSON-serialisable result (a stray object) must
    # degrade to an error envelope — never kill the request thread
    # without a response.
    try:
        json.dumps(result, ensure_ascii=False)
    except Exception as e:
        logger.error("api call %s non-JSON result: %r", method, e)
        return {"ok": False, "error": f"non-JSON-serialisable result: {e!r}"}
    return {"ok": True, "result": result}


# ─── HTTP layer ───────────────────────────────────────────────────


def make_handler(
    api: Any, assets_dir: Path,
) -> type[BaseHTTPRequestHandler]:
    """Build the request-handler class bound to one Api instance and
    one assets directory (keeps the handler testable — the tests
    spin a real ThreadingHTTPServer on an ephemeral port)."""

    index_bytes = _inject_shim(
        (assets_dir / "index.html").read_text(encoding="utf-8"),
    ).encode("utf-8")
    shim_bytes = _SHIM_JS.encode("utf-8")

    # v0.9.97.1 — cache-busting asset URLs. The user-reported splash
    # bug ("rows spin, never turn green, jump to main") is the exact
    # signature of a STALE app.js (the pre-v0.9.87.3 duplicate-finish
    # bug). ``Cache-Control: no-cache`` alone was not enough in the
    # field (no validator header + browser/WebView quirks incl.
    # bfcache). Version the hashed asset URLs so ANY cache layer keys
    # them per release; the do_GET path already strips the query.
    try:
        from gui.api import _app_version

        _asset_v = "?v=" + str(_app_version() or "dev")
    except Exception:
        _asset_v = "?v=dev"
    for _old, _new in (
        ('src="app.js"', f'src="app.js{_asset_v}"'),
        ('href="style.css"', f'href="style.css{_asset_v}"'),
        ('src="/pywebview-shim.js"',
         f'src="/pywebview-shim.js{_asset_v}"'),
        # v0.9.127.4 — the favicon + the header logo: favicon caches
        # are notoriously sticky, so the icon refs are re-keyed per
        # release exactly like app.js (do_GET strips the query).
        ('href="icon.svg"', f'href="icon.svg{_asset_v}"'),
        ('href="icon.ico"', f'href="icon.ico{_asset_v}"'),
        ('src="icon.svg"', f'src="icon.svg{_asset_v}"'),
    ):
        index_bytes = index_bytes.replace(
            _old.encode("utf-8"), _new.encode("utf-8"))

    class WebApiHandler(BaseHTTPRequestHandler):
        server_version = "divine-sense-agent"
        sys_version = ""

        # ── helpers ──
        def _send_json(self, obj: dict[str, Any], status: int = 200) -> None:
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(body)

        def _send_bytes(
            self, body: bytes, ctype: str, status: int = 200,
        ) -> None:
            # v0.9.97.1 — ETag validator + If-None-Match -> 304 (a
            # real revalidation anchor for the no-cache policy).
            etag = '"' + hashlib.sha1(body).hexdigest()[:16] + '"'
            if (status == 200 and
                    self.headers.get("If-None-Match") == etag):
                self.send_response(304)
                self.send_header("ETag", etag)
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                return
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.send_header("ETag", etag)
            self.end_headers()
            self.wfile.write(body)

        def _send_bytes_nostore(self, body: bytes, ctype: str) -> None:
            # v0.9.132.6 — the INDEX (and its pywebview-shim) must never
            # come from a browser cache: a stale index bakes STALE asset
            # URLs (every ?v=<old>), so even a hard reload re-fetched the
            # cached old app.js under the same old URL and every fix
            # looked lost (the user's /playpet dog ran the v132.3-era
            # cached copy for a day). no-cache + ETag only helps when
            # the BROWSER revalidates — a long-lived tab simply never
            # revalidates. no-store guarantees every NAVIGATION gets a
            # fresh index with fresh asset URLs.
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Pragma", "no-cache")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        # ── GET ──
        def do_GET(self) -> None:  # noqa: N802 (stdlib naming)
            path = self.path.split("?", 1)[0].split("#", 1)[0]
            if path in ("/", "/index.html"):
                self._send_bytes_nostore(index_bytes, _MIME[".html"])
                return
            if path == "/pywebview-shim.js":
                self._send_bytes_nostore(shim_bytes, _MIME[".js"])
                return
            if path == "/healthz":
                self._send_json({"ok": True})
                return
            if path == "/download":
                self._handle_download()
                return
            rel = path.lstrip("/")
            target = (assets_dir / rel).resolve()
            try:
                target.relative_to(assets_dir.resolve())
            except ValueError:
                self._send_json({"ok": False, "error": "forbidden"}, 403)
                return
            if not target.is_file():
                self._send_json({"ok": False, "error": "not found"}, 404)
                return
            ctype = _MIME.get(target.suffix.lower(), "application/octet-stream")
            try:
                self._send_bytes(target.read_bytes(), ctype)
            except OSError:
                self._send_json({"ok": False, "error": "read failed"}, 500)

        # ── GET /download (v0.9.65.0+ chat file download) ──
        def _handle_download(self) -> None:
            qs = urllib.parse.parse_qs(
                urllib.parse.urlsplit(self.path).query)
            p_str = (qs.get("path") or [""])[0].strip()
            if not p_str:
                self._send_json(
                    {"ok": False, "error": "path is required"}, 400)
                return
            if api is None or not hasattr(api, "_resolve_sandbox_file"):
                self._send_json(
                    {"ok": False, "error": "api unavailable"}, 503)
                return
            resolved, status, msg = api._resolve_sandbox_file(p_str)
            if resolved is None:
                self._send_json({"ok": False, "error": msg}, status)
                return
            allowed, why = _download_ext_allowed(resolved.name)
            if not allowed:
                self._send_json({"ok": False, "error": why}, 403)
                return
            size = resolved.stat().st_size
            cap = _download_max_bytes()
            if size > cap:
                self._send_json({
                    "ok": False,
                    "error": (f"file too large ({size // (1024 * 1024)} MB "
                              f"> {cap // (1024 * 1024)} MB cap)"),
                }, 413)
                return
            ctype = (mimetypes.guess_type(resolved.name)[0]
                     or "application/octet-stream")
            try:
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(size))
                self.send_header(
                    "Content-Disposition",
                    _content_disposition(resolved.name),
                )
                if (resolved.suffix.lower() == ".html"
                        and "inline" in _content_disposition(resolved.name)):
                    # v0.9.133.5 — remote OpenUI previews:
                    # sandbox the document into an opaque origin so an
                    # inline-rendered LLM-made .html cannot touch this
                    # origin's storage/cookies (scripts still load,
                    # e.g. the OpenUI CDN bundle).
                    self.send_header(
                        "Content-Security-Policy",
                        "sandbox allow-scripts allow-popups",
                    )
                self.send_header("Cache-Control", "no-cache")
                self.send_header(
                    "X-Content-Type-Options", "nosniff")
                self.end_headers()
                with resolved.open("rb") as fh:
                    sent = 0
                    while True:
                        chunk = fh.read(_DOWNLOAD_CHUNK)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        sent += len(chunk)
                logger.info(
                    "download: %s (%d bytes) via /download",
                    resolved.as_posix(), sent,
                )
            except (BrokenPipeError, ConnectionResetError):
                logger.warning(
                    "download: client gone (%s)", resolved.as_posix())
            except OSError as e:
                # headers may already be out — a JSON envelope is no
                # longer possible; log and close.
                logger.error("download: read failed: %r", e)

        # ── POST /upload (v0.9.16.0+ multipart, BEFORE JSON parse) ──
        def do_POST(self) -> None:  # noqa: N802 (stdlib naming)
            path = self.path.split("?", 1)[0]
            if path == "/upload":
                try:
                    self._handle_upload()
                except OSError:
                    # a reply to a client that already left mid-body
                    # (incl. ssl.SSLEOFError — NOT a ConnectionError,
                    # the live 2026-10-16 traceback): harmless, keep
                    # the worker log quiet
                    logger.debug(
                        "upload: connection closed mid-reply (client)")
                return
            # v0.9.127.5 — iOS 捷徑 (Shortcuts) / any HTTP client text
            # drop into the SAME uploads/ sandbox as /upload.
            if path == "/share-text":
                try:
                    self._handle_share_text()
                except OSError:
                    logger.debug(
                        "upload: connection closed mid-reply (client)")
                return
            if not path.startswith("/api/"):
                self._send_json({"ok": False, "error": "not found"}, 404)
                return
            method = path[len("/api/"):]

            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            raw = self.rfile.read(length) if length > 0 else b"[]"
            try:
                payload = json.loads(raw.decode("utf-8") or "[]")
            except Exception as e:
                self._send_json(
                    {"ok": False, "error": f"invalid JSON body: {e!r}"}, 200,
                )
                return
            if isinstance(payload, dict) and "args" in payload:
                args = payload["args"]
            elif isinstance(payload, list):
                args = payload
            else:
                args = [payload]
            if not isinstance(args, list):
                args = [args]

            # v0.9.15.3: heartbeat methods (get_status) -> DEBUG;
            # everything else stays at INFO.
            log = logger.debug if method in HEARTBEAT_METHODS else logger.info
            log("POST /api/%s (%d args)", method, len(args))
            # v0.9.16.1 — the client (phone browser fetch) can give up
            # before a long chat-turn answer lands; a broken pipe on
            # the response write is not a server fault. Log quietly.
            try:
                self._send_json(call_api_method(api, method, args))
            except ConnectionError as e:
                logger.debug("client gone before response: %r", e)

        # ── POST /upload (v0.9.16.0+ client file upload) ──────────
        def _drain(self, length: int) -> bool:
            """Read+discard up to ``_UPLOAD_DRAIN_MAX`` body bytes so
            the client receives the JSON error instead of a connection
            reset mid-upload. Returns True when fully drained."""
            remaining = length
            while remaining > 0:
                chunk = self.rfile.read(min(65536, remaining))
                if not chunk:
                    return True  # client stopped early
                remaining -= len(chunk)
                if length - remaining >= _UPLOAD_DRAIN_MAX:
                    return False  # too big to drain — close the socket
            return True

        def _handle_upload(self) -> None:
            ctype = self.headers.get("Content-Type") or ""
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            # v0.9.127.8 — the「主機端沒有 log」complaint: the handler
            # was silent until the body read COMPLETED (a multi-minute
            # read on a slow link looks like the server is down).
            # Log the ARRIVAL itself so receiving is always visible.
            logger.info(
                "upload: incoming POST, content-length=%d", length)
            if "multipart/form-data" not in ctype.lower():
                if length > 0 and not self._drain(length):
                    self.close_connection = True
                self._send_json(
                    {"ok": False, "error": "expected multipart/form-data"},
                    400,
                )
                return
            if length <= 0:
                self._send_json(
                    {"ok": False, "error": "empty upload body"}, 400,
                )
                return
            max_bytes = _upload_max_bytes()
            if length > max_bytes:
                # Drain what we reasonably can so the client gets a
                # clean 413 rather than a reset while still streaming.
                if not self._drain(length):
                    self.close_connection = True
                self._send_json({
                    "ok": False,
                    "error": (
                        f"payload too large: {length} bytes > "
                        f"{max_bytes} (QDRANT_WEB_UPLOAD_MAX_MB)"
                    ),
                }, 413)
                return
            buf = bytearray()
            remaining = length
            t0 = time.monotonic()
            while remaining > 0:
                chunk = self.rfile.read(min(65536, remaining))
                if not chunk:
                    break
                buf.extend(chunk)
                remaining -= len(chunk)
            # v0.9.127.6 — a body SHORTER than its own Content-Length
            # = the client went away mid-stream (the local 30 s abort
            # on a slow link, a phone OS killing the request, flaky
            # WAN). _parse_multipart on the TRUNCATED buffer silently
            # keeps only the parts fully received before the cut —
            # live 2026-10-16 14:30:37「選了十幾個、saved 3 skipped 0」
            # ＝靜默部分儲存把掉檔藏起來。Refuse the partial save.
            if remaining > 0:
                logger.warning(
                    "upload: truncated body — got %d of %d bytes; "
                    "refusing the partial save", len(buf), length,
                )
                try:
                    self._send_json({
                        "ok": False,
                        "error": (
                            "upload truncated: received %d of %d "
                            "bytes — connection dropped mid-upload; "
                            "none of the files were saved"
                            % (len(buf), length)),
                    }, 400)
                except OSError:
                    # ssl.SSLEOFError lives here too (the live
                    # 2026-10-16 traceback escaped the narrow
                    # ConnectionError catch — the client's TLS was
                    # simply gone mid-reply).
                    logger.debug(
                        "upload: client gone on truncated reply")
                return
            # v0.9.127.5 — the PWA share_target (manifest action
            # /upload?share=1) lands here as a top-level browser
            # navigation POST: plain form fields (title/text/url) ride
            # along with the files and the browser shows whatever the
            # response body is — a raw JSON dump reads terribly, so
            # share=1 replies with a 303 redirect back to the app
            # ("/?shared=…"): the app inserts the [已上傳] markers.
            # `share=json` keeps the JSON shape for HTTP clients (iOS
            # Shortcuts). The legacy non-share contract is unchanged.
            is_share = "share=" in (self.path or "")
            try:
                parts = _parse_multipart(
                    bytes(buf), ctype, keep_fields=is_share)
            except ValueError as e:
                self._send_json(
                    {"ok": False, "error": f"bad multipart body: {e}"}, 400,
                )
                return
            out = _save_upload_parts(
                [p for p in parts if "filename" in p])
            if out.get("ok"):
                # v0.9.127.9 — the save line carries the receive
                # RATE: on a cross-host slow link「跨主機速度慢」
                # becomes a MEASURED number, not a feeling.
                dt = max(0.001, time.monotonic() - t0)
                logger.info(
                    "upload: saved %d file(s), skipped %d "
                    "(%.1fs, avg %.0f KB/s) -> %s",
                    len(out["files"]), len(out["skipped"]),
                    dt, (length / dt) / 1024.0, out["dir"],
                )
            if not is_share:
                self._send_json(out)
                return
            fields = {
                (p.get("name") or ""): (p.get("value") or "").strip()
                for p in parts if "filename" not in p
            }
            if "share=json" in (self.path or ""):
                txt_path = _save_share_text(fields)
                out["share"] = {
                    "fields": {k: v for k, v in fields.items() if v},
                }
                if txt_path:
                    out["share"]["text_path"] = txt_path
                self._send_json(out)
                return
            self._send_share_redirect(out, fields)

        def _send_share_redirect(
            self, out: dict[str, Any], fields: dict[str, str],
        ) -> None:
            """303 the share navigation back into the app: the shared
            file paths (plus the text sidecar) ride the ?shared= query
            the landing handler consumes. Never raises — on total
            failure the redirect still lands, with ?shared_err=."""
            paths = [f.get("path", "") for f in out.get("files", [])
                     if f.get("path")]
            txt_path = _save_share_text(fields)
            if txt_path:
                paths.append(txt_path)
            if out.get("ok") is False and not paths:
                shared_err = str(out.get("error") or "share failed")[:120]
                loc = "/?shared_err=" + urllib.parse.quote(shared_err)
            else:
                q = ["shared=" + urllib.parse.quote("\n".join(paths))]
                sk = len(out.get("skipped", []))
                if sk:
                    q.append("skipped=" + str(sk))
                loc = "/?" + "&".join(q)
            try:
                self.send_response(303)
                self.send_header("Location", loc)
                self.send_header("Content-Length", "0")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
            except OSError:
                # the client vanished right after the share POST
                logger.debug("share: client gone on redirect reply")
                return
            logger.info("share: redirect %r", loc)

        def _handle_share_text(self) -> None:
            """POST /share-text (v0.9.127.5) — iOS 捷徑 / any HTTP
            client text drop into the SAME uploads/ sandbox: raw
            text/plain body, or a multipart form carrying
            share_text / text field. Replies {ok, path}."""
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            if length <= 0:
                self._send_json(
                    {"ok": False, "error": "empty share body"}, 400)
                return
            if length > _SHARE_TEXT_MAX_CHARS * 2:  # utf-8 worst ~3x
                self._send_json(
                    {"ok": False, "error": "share text too large"}, 413)
                return
            raw = self.rfile.read(length)
            # v0.9.127.6 — same truncated-body rule as /upload: a
            # shorter-than-declared body must not save a half text.
            if len(raw) < length:
                self._send_json(
                    {"ok": False, "error": "share text truncated"}, 400)
                return
            ctype = (self.headers.get("Content-Type") or "").lower()
            text = ""
            if "multipart/form-data" in ctype:
                try:
                    parts = _parse_multipart(
                        raw, ctype, keep_fields=True)
                except ValueError as e:
                    self._send_json(
                        {"ok": False,
                         "error": f"bad multipart body: {e}"}, 400)
                    return
                fields = {
                    (p.get("name") or ""): p.get("value") or ""
                    for p in parts if "filename" not in p
                }
                text = fields.get("share_text") \
                    or fields.get("text") or ""
            else:
                text = raw.decode("utf-8", "replace")
            text = (text or "").strip()
            if not text:
                self._send_json(
                    {"ok": False, "error": "empty share text"}, 400)
                return
            path = _store_share_text_file(text)
            if not path:
                self._send_json(
                    {"ok": False,
                     "error": "cannot resolve write sandbox"}, 500)
                return
            logger.info("share-text: saved %d chars -> %s",
                        len(text), path)
            self._send_json({"ok": True, "path": path})

        def log_message(self, fmt: str, *args: Any) -> None:
            logger.debug("http: " + fmt, *args)

    return WebApiHandler


def _get_assets_dir() -> Path:
    """Same resolution as gui.app (source + PyInstaller frozen)."""
    import sys
    if getattr(sys, "frozen", False):
        meipass = Path(getattr(sys, "_MEIPASS", "."))  # type: ignore[attr-defined]
        candidate = meipass / "gui" / "assets"
        if candidate.exists():
            return candidate
        return Path(sys.executable).parent / "gui" / "assets"
    return Path(__file__).resolve().parent / "assets"


# ─── v0.9.15.1 — bind-any-local-IP helpers ────────────────────────


WEB_TLS_CERT_ENV = "QDRANT_WEB_TLS_CERT"
WEB_TLS_KEY_ENV = "QDRANT_WEB_TLS_KEY"
WEB_TLS_AUTO_ENV = "QDRANT_WEB_TLS_AUTO"


def _tls_material() -> tuple[str, str] | None:
    """``(cert, key)`` for the HTTPS wrap, or ``None`` to stay plain
    http (the v0.9.15 default behaviour — an UNSET TLS config must
    change nothing).

    * ``QDRANT_WEB_TLS_CERT`` + ``QDRANT_WEB_TLS_KEY`` = an existing
      PEM pair (both must exist, otherwise honest refusal);
    * ``QDRANT_WEB_TLS_AUTO=on`` = self-signed pair auto-generated /
      reused under ``$QDRANT_RAG_HOME/certs/`` (SAN covers 127.0.0.1,
      ::1 and EVERY detected LAN IPv4 — Android's Firefox can then
      "accept the risk" once and INSTALL the PWA; plain http origins
      can never be installed).

    A missing ``cryptography`` install degrades honestly to http with
    a warning — never a crash.
    """
    cert = (os.environ.get(WEB_TLS_CERT_ENV) or "").strip()
    key = (os.environ.get(WEB_TLS_KEY_ENV) or "").strip()
    if cert and key:
        if os.path.isfile(cert) and os.path.isfile(key):
            return cert, key
        logger.warning(
            "TLS pair incomplete/missing — serving plain http: "
            "cert=%r key=%r (exists: %s/%s)",
            cert, key,
            os.path.isfile(cert), os.path.isfile(key),
        )
        return None
    if cert or key:
        logger.warning(
            "Set BOTH %s and %s to enable TLS — serving plain http",
            WEB_TLS_CERT_ENV, WEB_TLS_KEY_ENV,
        )
        return None

    auto = (os.environ.get(WEB_TLS_AUTO_ENV) or "").strip().lower()
    if auto not in ("on", "yes", "true", "1"):
        return None
    try:
        cp, kp = _self_signed_pair()
        return str(cp), str(kp)
    except Exception as e:  # noqa: BLE001 — TLS is optional armour
        logger.warning(
            "self-signed TLS generation failed (%r) — serving plain "
            "http", e,
        )
        return None


def _cert_dir() -> Path:
    home = (os.environ.get("QDRANT_RAG_HOME", "") or "").strip() \
        or os.path.expanduser("~/.qdrant-rag-agent")
    return Path(home) / "certs"


def _self_signed_pair() -> tuple[Path, Path]:
    """Generate (or reload) the auto self-signed PEM pair. Key stays
    on disk (0.600 perms best-effort) — regenerating every boot would
    invalidate the Android-side 'accept the risk' trust decision."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    import datetime
    import ipaddress

    d = _cert_dir()
    d.mkdir(parents=True, exist_ok=True)
    cert_p = d / "web-tls.crt"
    key_p = d / "web-tls.key"
    if cert_p.is_file() and key_p.is_file():
        return cert_p, key_p

    key = rsa.generate_private_key(
        public_exponent=65537, key_size=2048)
    names = [ipaddress.ip_address("127.0.0.1"),
             ipaddress.ip_address("::1")]
    for ip in _enumerate_lan_ips():
        try:
            names.append(ipaddress.ip_address(ip))
        except ValueError:
            continue
    subject = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "Divine Sense Agent"),
    ])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)          # self-signed
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.IPAddress(n) for n in names]),
            critical=False,
        )
        .add_extension(
            x509.BasicConstraints(ca=False, path_length=None),
            critical=True,
        )
        .sign(key, hashes.SHA256())
    )
    key_p.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ))
    cert_p.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    try:
        os.chmod(key_p, 0o600)
    except OSError:
        pass
    logger.info(
        "web UI TLS: self-signed pair written to %s (SAN: %s)",
        d, ", ".join(str(n) for n in names),
    )
    return cert_p, key_p


def _format_url(host: str, port: int, scheme: str = "http") -> str:
    """Browser URL for one bound host. IPv6 literals need brackets
    (``https://[::1]:8334/``) or the browser parses the colons as
    host:port separators and fails. v0.9.126.0 — the scheme follows
    the TLS config (https when a wrap is in place)."""
    if ":" in host:  # IPv6 literal
        return f"{scheme}://[{host}]:{port}/"
    return f"{scheme}://{host}:{port}/"


def _enumerate_lan_ips() -> list[str]:
    """Best-effort NON-loopback local IPv4 addresses, stdlib only.

    Three probes, dedup'd, order-stable:
      1. the UDP-connect trick — a UDP socket ``connect`` fills in the
         routing-table source address for the default route WITHOUT
         sending any packet; this yields the primary LAN IP.
      2. ``socket.getaddrinfo`` on the machine hostname.
      3. ``socket.gethostbyname_ex`` on the machine hostname (often
         the only probe that surfaces every NIC on Windows).

    Never raises — an unreachable probe just contributes nothing.
    Unroutable placeholders (``0.0.0.0``) and loopback are skipped.
    """

    def _keep(ip: str, ips: list[str]) -> None:
        if (
            ip
            and ip not in ("0.0.0.0", "255.255.255.255")
            and not ip.startswith("127.")
            and ip not in ips
        ):
            ips.append(ip)

    ips: list[str] = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))  # no packet sent (UDP connect)
            _keep(s.getsockname()[0], ips)
    except OSError:
        pass
    try:
        hostname = socket.gethostname()
    except OSError:
        hostname = ""
    if hostname:
        try:
            for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
                _keep(info[4][0], ips)
        except OSError:
            pass
        try:
            for ip in socket.gethostbyname_ex(hostname)[2]:
                _keep(ip, ips)
        except (OSError, AttributeError):
            pass
    return ips


def _urls_for(host: str, port: int,
              scheme: str = "http") -> tuple[list[str], str]:
    """``(display_urls, browser_url)`` for the bound host.

    * loopback / specific IP → the host itself.
    * ``0.0.0.0`` (all IPv4 interfaces) → the loopback URL (always
      works on the server machine) PLUS every detected LAN IP, since
      ``0.0.0.0`` itself is NOT a browsable address.
    * ``::`` → the ``::1`` loopback URL (dual-stack socket covers v4).

    The browser is ALWAYS opened on ``browser_url`` — a URL that
    actually resolves — never on the wildcard literal.
    """
    if host in WILDCARD_HOSTS:
        loop = "127.0.0.1" if host == "0.0.0.0" else "::1"
        browser_url = _format_url(loop, port, scheme)
        urls = [browser_url]
        if host == "0.0.0.0":
            urls += [_format_url(ip, port, scheme)
                     for ip in _enumerate_lan_ips()]
        return urls, browser_url
    url = _format_url(host, port, scheme)
    return [url], url


def run_web_server(
    host: str | None = None,
    port: int | None = None,
    open_browser: bool = True,
    api: Any = None,
) -> int:
    """Blocking web-mode entry. Returns a process exit code."""
    # v0.9.15.1: load .env BEFORE resolving QDRANT_WEB_HOST/PORT —
    # web mode reads the env before Api() (which is what triggers
    # src.config's load_dotenv), so .env values were previously
    # ignored. override=False keeps real env vars / CLI flags winning.
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    except ImportError:
        pass  # dotenv missing — fall back to the process env only

    host = (
        host
        or (os.environ.get("QDRANT_WEB_HOST", "") or "").strip()
        or DEFAULT_WEB_HOST
    )
    if port is None:
        raw = (os.environ.get("QDRANT_WEB_PORT", "") or "").strip()
        try:
            port = int(raw) if raw else DEFAULT_WEB_PORT
        except ValueError:
            logger.warning(
                "QDRANT_WEB_PORT=%r invalid — using %d",
                raw, DEFAULT_WEB_PORT,
            )
            port = DEFAULT_WEB_PORT

    if host not in LOOPBACK_HOSTS:
        if host in WILDCARD_HOSTS:
            logger.warning(
                "Web UI bound to ALL interfaces (%s) — the Api can "
                "drive desktop automation and filesystem tools; do "
                "NOT expose it to untrusted networks", host,
            )
        else:
            logger.warning(
                "Web UI binding NON-LOOPBACK host %r — the Api can "
                "drive desktop automation and filesystem tools; do "
                "NOT expose it to untrusted networks", host,
            )

    assets_dir = _get_assets_dir()
    if not (assets_dir / "index.html").exists():
        logger.error("index.html not found at %s", assets_dir)
        print(f"[error] missing UI assets: {assets_dir}")
        return 2

    if api is None:
        from gui.api import Api
        api = Api()

    handler = make_handler(api, assets_dir)
    server_cls = _ThreadingHTTPServer6 if ":" in host else ThreadingHTTPServer
    try:
        server = server_cls((host, port), handler)
    except OSError as e:
        logger.error("web server bind FAILED on %s:%d: %r", host, port, e)
        print(f"[error] cannot bind {host}:{port} — {e!r}")
        return 1
    server.daemon_threads = True

    # v0.9.126.0 — optional HTTPS wrap: PWA installation (Android) is
    # only offered on a secure context; plain http can never be
    # installed. Unset TLS config = plain http, byte-identical.
    tls_pair = _tls_material()
    scheme = "http"
    if tls_pair:
        import ssl
        if hasattr(server, "socket"):
            try:
                ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                ctx.load_cert_chain(tls_pair[0], tls_pair[1])
                server.socket = ctx.wrap_socket(
                    server.socket, server_side=True)
                scheme = "https"
                logger.info(
                    "web UI TLS: serving HTTPS (%s / %s)",
                    tls_pair[0], tls_pair[1],
                )
            except (OSError, ssl.SSLError) as te:
                logger.error(
                    "TLS wrap failed (%r) — refusing a half-open "
                    "server (fix the cert/key pair or unset the TLS "
                    "env vars)", te,
                )
                raise SystemExit(
                    f"[error] TLS wrap failed: {te!r} — fix the "
                    "cert/key pair or unset the TLS env vars")
        else:
            # a test double (fake_servers) has no socket — scheme
            # plumbing only; the REAL wrap is live-proven elsewhere
            scheme = "https"

    # v0.9.126.1 — the certified name the TLS pair covers (Android
    # installs must go through the NAME, not a bare IP: the cert SAN
    # lists tchtool.app, so this is the banner line users copy).
    tls_name = (os.environ.get("QDRANT_WEB_TLS_NAME", "") or "").strip()
    # v0.9.126.8 — with a certified name the NAME is the CANONICAL url:
    # the banner's main line and the auto-opened browser use
    # https://<NAME>:<port>/ instead of the loopback literal (the SAN
    # only covers the name — opening 127.0.0.1 would hit a cert
    # warning). Loopback / LAN IPs stay listed as secondary
    # "also reachable at" URLs.
    urls, browser_url = _urls_for(host, port, scheme)
    if scheme == "https" and tls_name:
        canonical = f"{scheme}://{tls_name}:{port}/"
        urls = [canonical] + [u for u in urls if u != canonical]
        browser_url = canonical
    logger.info(
        "web UI listening on %s (assets=%s)", urls[0], assets_dir,
    )
    # flush=True: the banner must land even when stdout is piped or
    # redirected (detached launches lose buffered output on kill).
    print(f"[web] Divine Sense Agent web UI: {urls[0]}  (Ctrl+C to stop)",
          flush=True)
    for extra in urls[1:]:
        print(f"[web]   also reachable at: {extra}", flush=True)
    if host in WILDCARD_HOSTS and len(urls) > 1:
        print(
            "[web]   (bound to ALL interfaces — use any of the above "
            "from LAN devices; keep off untrusted networks)", flush=True,
        )
    if scheme == "https" and tls_name:
        print(
            f"[web]   certified as: {scheme}://{tls_name}:{port}/  "
            "(Android PWA install — use THIS name, not an IP)",
            flush=True,
        )
    if open_browser:
        try:
            webbrowser.open(browser_url)
        except Exception as e:
            logger.warning("browser open failed: %r", e)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("web UI stopped by user (Ctrl+C)")
        print("\n[web] stopped")
    finally:
        try:
            server.server_close()
        except Exception:
            pass
    return 0


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        import sys
        argv = sys.argv[1:]
    from gui.logger import setup_logging
    log_path = setup_logging()
    logger.info("=" * 60)
    logger.info("divine-sense-agent WEB UI starting (log=%s)", log_path)
    logger.info("argv=%r", argv)

    p = argparse.ArgumentParser(
        prog="gui.web_server",
        description="Divine Sense Agent — web UI server",
    )
    p.add_argument("--host", default=None,
                   help="bind host (default 127.0.0.1; env "
                        "QDRANT_WEB_HOST). Any local IP works: "
                        "0.0.0.0 = all IPv4 interfaces (LAN URLs "
                        "printed), a specific LAN IP, or IPv6 "
                        "(::1 / :: dual-stack)")
    p.add_argument("--port", type=int, default=None,
                   help="bind port (default 8334; env QDRANT_WEB_PORT)")
    p.add_argument("--no-browser", action="store_true",
                   help="don't auto-open the system browser")
    args = p.parse_args(argv)
    return run_web_server(
        host=args.host, port=args.port,
        open_browser=not args.no_browser,
    )


if __name__ == "__main__":
    raise SystemExit(main())

