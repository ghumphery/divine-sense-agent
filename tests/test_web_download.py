"""v0.9.65.0 — chat file download tests (web + GUI bridges).

The reverse direction of v0.9.16.0 upload: the LLM generates files
inside the filesystem sandbox and the chat UI renders a ⬇ download
chip per detected sandbox path. Covers:

* ``Api._resolve_sandbox_file`` — absolute / relative (WRITE-root
  anchored) paths inside the import ∪ READ ∪ WRITE roots; outside /
  missing / directory / empty refusals (never an arbitrary read);
* ``Api.save_sandbox_file_as`` — GUI-mode copy with collision-safe
  ``-N`` suffix, dest_dir validation, dangerous-ext refusal;
* ``_download_max_bytes`` — env override / invalid fallback;
* ``_content_disposition`` — RFC 5987 ``filename*`` (CJK safe);
* live HTTP round-trips on an ephemeral port: 200 + streamed bytes +
  headers, CJK filename, relative uploads/ path, missing 404,
  outside-roots 403, dangerous-ext 403, directory 400, oversized 413;
* frontend source guards (app.js renderSandboxFiles /
  downloadSandboxFile / web-vs-GUI branch / i18n ×4; style.css chip;
  web_server /download wiring).
"""
from __future__ import annotations

import http.client
import io
import json
import logging
import threading
import urllib.parse
from pathlib import Path

import pytest

import gui.web_server as web
from gui.web_server import (
    _content_disposition,
    _download_ext_allowed,
    _download_max_bytes,
    make_handler,
)

ROOT = Path(__file__).resolve().parents[1]


# ─── fixtures ────────────────────────────────────────────────


@pytest.fixture
def roots(tmp_path, monkeypatch):
    """Filesystem sandboxes — WRITE + READ + import roots, all isolated
    in tmp. NOTE: the import root must be pinned too — the live .env
    sets QDRANT_IMPORT_ALLOWED_ROOT to the user's home directory and
    gui.api's import chain loads dotenv, so without this pin the
    outside-roots refusal is vacuous."""
    write = tmp_path / "sandbox"
    read = tmp_path / "readroot"
    imp = tmp_path / "importroot"
    for r in (write, read, imp):
        r.mkdir()
    monkeypatch.setenv("QDRANT_FILESYSTEM_WRITE_ROOT", str(write))
    monkeypatch.setenv("QDRANT_FILESYSTEM_READ_ROOT", str(read))
    monkeypatch.setenv("QDRANT_IMPORT_ALLOWED_ROOT", str(imp))
    monkeypatch.delenv("WATCH_DIR", raising=False)
    return {"write": write, "read": read, "import": imp}


@pytest.fixture
def api():
    """A bare Api (no __init__ / prewarm) — the download helpers only
    read the sandbox-root env vars via src.mcp_client.host."""
    from gui.api import Api

    return object.__new__(Api)


# ─── Api._resolve_sandbox_file ───────────────────────────────


class TestResolveSandboxFile:
    def test_absolute_path_inside_write_root(self, api, roots):
        f = roots["write"] / "out.csv"
        f.write_text("a,b\n1,2", encoding="utf-8")
        resolved, status, msg = api._resolve_sandbox_file(str(f))
        assert resolved is not None and status == 0
        assert resolved.name == "out.csv"

    def test_relative_path_anchored_at_write_root(self, api, roots):
        (roots["write"] / "uploads").mkdir()
        f = roots["write"] / "uploads" / "paste-1.png"
        f.write_bytes(b"\x89PNG fake")
        resolved, status, msg = api._resolve_sandbox_file(
            "uploads/paste-1.png")
        assert resolved is not None and status == 0
        assert resolved.name == "paste-1.png"

    def test_file_uri_stripped(self, api, roots):
        f = roots["write"] / "doc.md"
        f.write_text("hi", encoding="utf-8")
        resolved, status, _ = api._resolve_sandbox_file(
            "file:///" + str(f).replace("\\", "/"))
        assert resolved is not None and status == 0

    def test_outside_roots_refused(self, api, roots, tmp_path):
        outside = tmp_path / "evil.txt"
        outside.write_text("x", encoding="utf-8")
        resolved, status, msg = api._resolve_sandbox_file(str(outside))
        assert resolved is None and status == 403
        assert "outside" in msg

    def test_missing_refused(self, api, roots):
        resolved, status, msg = api._resolve_sandbox_file(
            str(roots["write"] / "nope.txt"))
        assert resolved is None and status == 404

    def test_directory_refused(self, api, roots):
        resolved, status, msg = api._resolve_sandbox_file(
            str(roots["write"]))
        assert resolved is None and status == 400
        assert "directory" in msg

    def test_empty_refused(self, api):
        resolved, status, msg = api._resolve_sandbox_file("")
        assert resolved is None and status == 400

    def test_traversal_relative_refused(self, api, roots):
        resolved, status, _ = api._resolve_sandbox_file("../evil.txt")
        assert resolved is None and status in (400, 403)


class TestDownloadExtAllowed:
    def test_dangerous_refused(self):
        for name in ("a.exe", "b.bat", "c.sh", "d.ps1"):
            ok, why = _download_ext_allowed(name)
            assert not ok and "dangerous" in why

    def test_normal_allowed(self):
        for name in ("a.txt", "b.pdf", "c.csv", "d.drawio",
                     "e.docx", "f"):
            ok, why = _download_ext_allowed(name)
            assert ok, (name, why)


# ─── Api.save_sandbox_file_as (GUI-mode copy) ────────────────


class TestSaveSandboxFileAs:
    def test_copy_round_trip(self, api, roots, tmp_path):
        src = roots["write"] / "report.pdf"
        src.write_bytes(b"%PDF-1.4 fake")
        dest = tmp_path / "downloads"
        dest.mkdir()
        r = api.save_sandbox_file_as(str(src), str(dest))
        assert r["ok"] is True
        copied = dest / "report.pdf"
        assert copied.read_bytes() == b"%PDF-1.4 fake"
        assert r["dest"] == copied.resolve().as_posix()
        assert r["size"] == len(b"%PDF-1.4 fake")

    def test_collision_suffix(self, api, roots, tmp_path):
        src = roots["write"] / "note.txt"
        src.write_text("v2", encoding="utf-8")
        dest = tmp_path / "dl"
        dest.mkdir()
        (dest / "note.txt").write_text("v1", encoding="utf-8")
        r = api.save_sandbox_file_as(str(src), str(dest))
        assert r["ok"] is True
        assert (dest / "note-1.txt").read_text(encoding="utf-8") == "v2"
        assert (dest / "note.txt").read_text(encoding="utf-8") == "v1"

    def test_missing_dest_dir(self, api, roots, tmp_path):
        src = roots["write"] / "a.txt"
        src.write_text("x", encoding="utf-8")
        r = api.save_sandbox_file_as(str(src), "")
        assert "error" in r and "dest_dir" in r["error"]
        r2 = api.save_sandbox_file_as(
            str(src), str(tmp_path / "not-there"))
        assert "error" in r2 and "not a directory" in r2["error"]

    def test_dangerous_ext_refused(self, api, roots, tmp_path):
        src = roots["write"] / "tool.exe"
        src.write_bytes(b"MZ")
        r = api.save_sandbox_file_as(str(src), str(tmp_path))
        assert "error" in r and "dangerous" in r["error"]

    def test_outside_roots_refused(self, api, roots, tmp_path):
        outside = tmp_path / "x.txt"
        outside.write_text("x", encoding="utf-8")
        r = api.save_sandbox_file_as(str(outside), str(tmp_path))
        assert "error" in r and "outside" in r["error"]


# ─── _download_max_bytes / _content_disposition ──────────────


class TestDownloadMaxBytes:
    def test_default(self, monkeypatch):
        monkeypatch.delenv("QDRANT_WEB_DOWNLOAD_MAX_MB", raising=False)
        assert _download_max_bytes() == 200 * 1024 * 1024

    def test_override(self, monkeypatch):
        monkeypatch.setenv("QDRANT_WEB_DOWNLOAD_MAX_MB", "10")
        assert _download_max_bytes() == 10 * 1024 * 1024

    def test_invalid_falls_back(self, monkeypatch):
        for bad in ("abc", "0", "-5", ""):
            monkeypatch.setenv("QDRANT_WEB_DOWNLOAD_MAX_MB", bad)
            assert _download_max_bytes() == 200 * 1024 * 1024


class TestContentDisposition:
    def test_ascii_name(self):
        h = _content_disposition("report.pdf")
        assert 'filename="report.pdf"' in h
        assert "filename*=UTF-8''report.pdf" in h

    def test_cjk_name_rfc5987(self):
        h = _content_disposition("報表.pdf")
        # ASCII fallback keeps only the extension; the RFC 5987 part
        # carries the CJK name URL-encoded.
        quoted = urllib.parse.quote("報表.pdf", safe="")
        assert f"filename*=UTF-8''{quoted}" in h
        assert ".pdf" in h

    def test_quotes_neutralised_in_fallback(self):
        h = _content_disposition('a"b.txt')
        assert 'filename="a' in h
        assert ".txt" in h

    def test_html_served_inline(self):
        """v0.9.133.5 — remote OpenUI previews: .html gets `inline` so
        a click on the chat link RENDERS the page (no save-as dance)."""
        h = _content_disposition("preview.html")
        assert h.startswith("inline;")

    def test_non_html_stays_attachment(self):
        assert _content_disposition(
            "report.pdf").startswith("attachment;")
        assert _content_disposition(
            "data.csv").startswith("attachment;")
        assert _content_disposition(
            "shot.png").startswith("attachment;")


class TestLiveHtmlInlineCsp:
    """The live handler response for an .html file carries the CSP
    sandbox header (opaque-origin isolation for LLM-made previews)."""

    def test_html_inline_csp_header(self, api, roots):
        f = roots["write"] / "preview.html"
        f.write_text("<html><body>hi</body></html>",
                     encoding="utf-8")
        with _HttpSrv(api, ROOT / "gui" / "assets") as srv:
            status, body, headers = srv.get(
                "/download?path=" + urllib.parse.quote(f.as_posix()))
        assert status == 200
        assert body.startswith(b"<html>")
        cd = headers.get("Content-Disposition", "")
        assert cd.startswith("inline;")
        csp = headers.get("Content-Security-Policy", "")
        assert "sandbox" in csp and "allow-scripts" in csp

    def test_non_html_no_csp(self, api, roots):
        f = roots["write"] / "data.txt"
        f.write_text("hello", encoding="utf-8")
        with _HttpSrv(api, ROOT / "gui" / "assets") as srv:
            status, body, headers = srv.get(
                "/download?path=" + urllib.parse.quote(f.as_posix()))
        assert status == 200
        assert headers.get("Content-Security-Policy", "") == ""


def _run_node_jsdom_inline(driver_js: str) -> str:
    """Minimal local jsdom runner (this file otherwise has no node
    infra): write the driver under the repo root so require('jsdom')
    resolves, run `node <file>`, tolerate the Node-24 benign teardown
    crash, return stdout. Skips the test when node/jsdom is missing."""
    import shutil as _sh
    import tempfile

    if not _sh.which("node"):
        pytest.skip("node not installed")
    fd, path = tempfile.mkstemp(suffix=".js", dir=str(ROOT))
    with __import__("os").fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(driver_js)
    import subprocess

    try:
        p = subprocess.run(
            ["node", path], cwd=str(ROOT), timeout=60,
            capture_output=True, text=True, encoding="utf-8",
            errors="replace")
        out = p.stdout or ""
        # Node 24 + jsdom teardown can crash AFTER the payload log
        # (0xC00000FD) — stderr-only failures with logged payload stay
        # acceptable; empty stdout is the real failure.
        if not out.strip():
            raise AssertionError("node stderr: " + (p.stderr or "")[-300:])
    finally:
        try:
            import os as _os

            _os.unlink(path)
        except OSError:
            pass
    return out


def _read_asset(name: str) -> str:
    import io as _io

    return _io.open(
        ROOT / "gui" / "assets" / name, encoding="utf-8",
        errors="replace").read()


class TestHtmlPreviewModal:
    """v0.9.133.6 — LLM-made `.html` previews open INSIDE the app as a
    sandboxed-iframe modal (the iPhone standalone Web App has no back
    button; `window.open` would strand the user outside the app)."""

    def test_source_guards(self):
        js = _read_asset("app.js")
        assert "function openHtmlPreview(" in js
        assert "function _htmlPreviewSrc(" in js
        assert "function _closeHtmlPreview(" in js
        # the iframe is sandboxed and never plain-navigates
        assert ("'allow-scripts allow-popups "
                "allow-popups-to-escape-sandbox'") in js
        assert "chat-html-preview-frame" in js
        assert "chat-html-preview-chip" in js
        i = js.index("function _htmlPreviewSrc(")
        seg = js[i:js.index("function openHtmlPreview(")]
        assert "encodeURIComponent(path)" in seg      # web → /download
        assert "file:///" in seg                       # GUI → local file
        css = _read_asset("style.css")
        assert ".chat-html-preview" in css
        assert ".chat-html-preview-frame" in css
        assert ".chat-html-preview-close" in css

    def test_i18n_four_locales(self):
        js = _read_asset("app.js")
        assert js.count("htmlPreview:") == 4
        assert js.count("htmlPreviewClose:") == 4

    def test_chip_dual_buttons_for_html(self):
        """An .html path chips BOTH a 👁 preview (in-app modal) and the
        legacy ⬇ download; other extensions keep ⬇ only."""
        js = _read_asset("app.js")
        i = js.index("function renderSandboxFiles(")
        seg = js[i: js.index("let _htmlPreviewEl")]
        assert "ext2 === 'html'" in seg
        assert "openHtmlPreview(p, name)" in seg
        assert "t('htmlPreview')" in seg
        assert "t('downloadFile')" in seg

    def test_jsdom_open_close(self):
        """Behaviour: openHtmlPreview mounts overlay + sandboxed iframe
        with a /download src (web stub) and ✕ removes it."""
        js = _read_asset("app.js")
        seg = js[js.index("let _htmlPreviewEl = null;"):
                 js.index("async function downloadSandboxFile(")]
        driver = (
            "const { JSDOM } = require('jsdom');\n"
            "const w = new JSDOM('<body></body>').window;\n"
            "global.document = w.document;\n"
            "global.window = w;\n"
            "global.getComputedStyle = w.getComputedStyle;\n"
            "var t = function (k) { return k; };\n"
            "var isWebMode = function () { return true; };\n"
            + seg + "\n" +
            "openHtmlPreview('C:/sandbox/preview.html',\n"
            "  'preview.html');\n"
            "const ov = document.querySelector('.chat-html-preview');\n"
            "console.log(JSON.stringify({"
            " mounted: !!ov,"
            " iframe: !!document.querySelector("
            "'.chat-html-preview-frame'),"
            " sandbox: (document.querySelector("
            "'.chat-html-preview-frame')||{}).getAttribute"
            "('sandbox') || '',"
            " src: (document.querySelector("
            "'.chat-html-preview-frame')||{}).src || ''"
            "}));\n"
            "document.querySelector('.chat-html-preview-close')\n"
            "  .dispatchEvent(new w.MouseEvent('click'));\n"
            "console.log(JSON.stringify({"
            " closed: !document.querySelector('.chat-html-preview')"
            "}));\n"
        )
        out = _run_node_jsdom_inline(driver)
        r = json.loads(out.splitlines()[0])
        assert r["mounted"] is True
        assert r["iframe"] is True
        assert "allow-scripts" in r["sandbox"]
        assert r["src"].startswith("/download?path=")
        r2 = json.loads(out.splitlines()[1])
        assert r2["closed"] is True


# ─── live HTTP round-trips ───────────────────────────────────


class _HttpSrv:
    def __init__(self, api, assets_dir: Path):
        handler = make_handler(api, assets_dir)
        from http.server import ThreadingHTTPServer

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True,
        )

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()

    def get(self, path: str) -> tuple[int, bytes, dict]:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        try:
            conn.request("GET", path)
            r = conn.getresponse()
            return r.status, r.read(), dict(r.getheaders())
        finally:
            conn.close()


class TestLiveDownload:
    def test_download_round_trip(self, api, roots):
        f = roots["write"] / "data.txt"
        payload = "編號,值\n1,中文".encode("utf-8")
        f.write_bytes(payload)
        with _HttpSrv(api, ROOT / "gui" / "assets") as srv:
            status, raw, headers = srv.get(
                "/download?path=" + urllib.parse.quote(str(f)))
        assert status == 200
        assert raw == payload
        assert headers.get("Content-Disposition", "").startswith(
            "attachment")
        assert "filename*=UTF-8''" in headers.get("Content-Disposition", "")
        # .csv maps to application/vnd.ms-excel on Windows — assert a
        # non-octet-stream guess instead of a vendor-specific type.
        assert headers.get("Content-Type", "")
        assert not headers.get("Content-Type", "").startswith(
            "application/octet-stream")

    def test_relative_uploads_path(self, api, roots):
        up = roots["write"] / "uploads"
        up.mkdir(exist_ok=True)
        f = up / "paste.txt"
        f.write_text("uploaded", encoding="utf-8")
        with _HttpSrv(api, ROOT / "gui" / "assets") as srv:
            status, raw, _ = srv.get(
                "/download?path=" + urllib.parse.quote("uploads/paste.txt"))
        assert status == 200
        assert raw == b"uploaded"

    def test_missing_404(self, api, roots):
        with _HttpSrv(api, ROOT / "gui" / "assets") as srv:
            status, raw, _ = srv.get(
                "/download?path=" + urllib.parse.quote(
                    str(roots["write"] / "nope.txt")))
        assert status == 404
        assert "not found" in json.loads(raw)["error"]

    def test_outside_roots_403(self, api, roots, tmp_path):
        outside = tmp_path / "secret.txt"
        outside.write_text("x", encoding="utf-8")
        with _HttpSrv(api, ROOT / "gui" / "assets") as srv:
            status, raw, _ = srv.get(
                "/download?path=" + urllib.parse.quote(str(outside)))
        assert status == 403

    def test_dangerous_ext_403(self, api, roots):
        f = roots["write"] / "tool.bat"
        f.write_text("@echo off", encoding="utf-8")
        with _HttpSrv(api, ROOT / "gui" / "assets") as srv:
            status, raw, _ = srv.get(
                "/download?path=" + urllib.parse.quote(str(f)))
        assert status == 403
        assert "dangerous" in json.loads(raw)["error"]

    def test_directory_400(self, api, roots):
        d = roots["write"] / "sub"
        d.mkdir()
        with _HttpSrv(api, ROOT / "gui" / "assets") as srv:
            status, raw, _ = srv.get(
                "/download?path=" + urllib.parse.quote(str(d)))
        assert status == 400
        assert "directory" in json.loads(raw)["error"]

    def test_missing_param_400(self, api, roots):
        with _HttpSrv(api, ROOT / "gui" / "assets") as srv:
            status, raw, _ = srv.get("/download")
        assert status == 400
        assert "required" in json.loads(raw)["error"]

    def test_oversized_413(self, api, roots, monkeypatch):
        monkeypatch.setenv("QDRANT_WEB_DOWNLOAD_MAX_MB", "1")
        f = roots["write"] / "big.bin"
        f.write_bytes(b"\0" * (1024 * 1024 + 1024))
        with _HttpSrv(api, ROOT / "gui" / "assets") as srv:
            status, raw, _ = srv.get(
                "/download?path=" + urllib.parse.quote(str(f)))
        assert status == 413
        assert "too large" in json.loads(raw)["error"]


# ─── frontend source guards ──────────────────────────────────


class _Assets:
    def _read(self, name: str) -> str:
        return io.open(
            ROOT / "gui" / "assets" / name, encoding="utf-8",
            errors="replace").read()


class TestFrontendGuards(_Assets):
    def test_app_js_functions_exist(self):
        js = _read_asset("app.js")
        assert "function renderSandboxFiles(" in js
        assert "async function downloadSandboxFile(" in js

    def test_renderchat_calls_pass(self):
        js = _read_asset("app.js")
        assert "renderSandboxFiles();" in js
        assert js.index("renderSandboxImages();") < js.index(
            "renderSandboxFiles();")

    def test_web_vs_gui_branch(self):
        js = _read_asset("app.js")
        assert "isWebMode()" in js
        assert (
            "window.open('/download?path=' + encodeURIComponent(path)"
            in js)
        assert "save_sandbox_file_as" in js
        assert "create_file_dialog" in js
        assert "pv.FileDialog.FOLDER" in js

    def test_chip_build_is_textcontent_only(self):
        js = _read_asset("app.js")
        i = js.index("function renderSandboxFiles(")
        seg = js[i:js.index("async function downloadSandboxFile(")]
        assert "innerHTML" not in seg
        assert "textContent = '⬇ '" in seg
        assert "chat-download-chip" in seg

    def test_image_exts_excluded(self):
        js = _read_asset("app.js")
        assert "_DL_IMG_EXTS" in js
        i = js.index("_DL_IMG_EXTS = [")
        seg = js[i:i + 120]
        assert "'png'" in seg and "'svg'" in seg

    def test_i18n_four_locales(self):
        js = _read_asset("app.js")
        assert js.count("downloadFile:") == 4
        assert js.count("fileSaved:") == 4

    def test_style_css_chip(self):
        css = _read_asset("style.css")
        assert ".chat-msg .chat-download-chip {" in css

    def test_web_server_download_wiring(self):
        py = io.open(
            ROOT / "gui" / "web_server.py", encoding="utf-8",
            errors="replace").read()
        assert 'path == "/download"' in py
        assert "_handle_download" in py
        assert "Content-Disposition" in py
        assert "filename*=UTF-8''" in py
        assert "QDRANT_WEB_DOWNLOAD_MAX_MB" in py

    def test_api_methods_wired(self):
        py = io.open(
            ROOT / "gui" / "api.py", encoding="utf-8",
            errors="replace").read()
        assert "_resolve_sandbox_file" in py
        assert "def save_sandbox_file_as" in py
        assert "_allowed_file_roots" in py