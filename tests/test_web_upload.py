"""v0.9.16.0 — Web UI client file upload tests.

``POST /upload`` (multipart/form-data, pure stdlib parser — the
``cgi`` module is gone since Python 3.13) saves client-side files
into the filesystem WRITE sandbox under ``uploads/`` so the LLM can
read them (and Api.ingest_file can ingest them by path). Covers:

* ``_parse_multipart`` — single/multiple files, plain fields ignored,
  quoted boundary, CRLF stripping, no-file body;
* ``_sanitize_upload_name`` — traversal / Windows-illegal chars /
  empty / long names;
* ``_save_upload_parts`` — timestamp prefix, collision suffix,
  dangerous-ext refusal, >20-file cap;
* live HTTP round-trips on an ephemeral port (real multipart body →
  file on disk; bad content type; oversized body 413);
* shim exposes ``window.__WEB_MODE__``;
* frontend source guards (index.html inputs/buttons, app.js
  uploadFiles/bindUpload/GUI-hide/i18n ×4, style.css desktop rule).
"""
from __future__ import annotations

import http.client
import json
import logging
import threading
from pathlib import Path

import pytest

import gui.web_server as web
from gui.web_server import (
    _parse_multipart,
    _sanitize_upload_name,
    _save_upload_parts,
    _upload_max_bytes,
    make_handler,
)

ROOT = Path(__file__).resolve().parents[1]


# ─── helpers ─────────────────────────────────────────────────


def _multipart_body(
    files: list[tuple[str, bytes]], boundary: str = "testbound123",
) -> bytes:
    out = bytearray()
    for fname, data in files:
        out += (
            f"--{boundary}\r\n"
            f"Content-Disposition: form-data; name=\"files\"; "
            f"filename=\"{fname}\"\r\n"
            f"Content-Type: application/octet-stream\r\n\r\n"
        ).encode("utf-8")
        out += data + b"\r\n"
    out += f"--{boundary}--\r\n".encode("utf-8")
    return bytes(out)


def _ctype(boundary: str = "testbound123") -> str:
    return f"multipart/form-data; boundary={boundary}"


@pytest.fixture
def write_root(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "sandbox"
    root.mkdir()
    monkeypatch.setenv("QDRANT_FILESYSTEM_WRITE_ROOT", str(root))
    return root


# ─── _parse_multipart ────────────────────────────────────────


class TestParseMultipart:
    def test_single_file(self):
        body = _multipart_body([("a.txt", b"hello")])
        parts = _parse_multipart(body, _ctype())
        assert len(parts) == 1
        assert parts[0]["filename"] == "a.txt"
        assert parts[0]["data"] == b"hello"

    def test_multiple_files_and_binary(self):
        body = _multipart_body([
            ("a.bin", bytes(range(256))),
            ("b.txt", "中文內容".encode("utf-8")),
        ])
        parts = _parse_multipart(body, _ctype())
        assert [p["filename"] for p in parts] == ["a.bin", "b.txt"]
        assert parts[0]["data"] == bytes(range(256))
        assert parts[1]["data"] == "中文內容".encode("utf-8")

    def test_plain_field_without_filename_ignored(self):
        body = (
            b"--testbound123\r\n"
            b"Content-Disposition: form-data; name=\"note\"\r\n\r\n"
            b"just a field\r\n"
            b"--testbound123\r\n"
            b"Content-Disposition: form-data; name=\"files\"; "
            b"filename=\"x.txt\"\r\n\r\n"
            b"data\r\n"
            b"--testbound123--\r\n"
        )
        parts = _parse_multipart(body, _ctype())
        assert [p["filename"] for p in parts] == ["x.txt"]

    def test_quoted_boundary(self):
        body = _multipart_body([("a.txt", b"x")])
        parts = _parse_multipart(
            body, 'multipart/form-data; boundary="testbound123"')
        assert parts[0]["data"] == b"x"

    def test_missing_boundary_raises(self):
        with pytest.raises(ValueError):
            _parse_multipart(b"--x\r\n\r\n", "application/json")

    def test_no_file_parts_raises(self):
        body = (
            b"--testbound123\r\n"
            b"Content-Disposition: form-data; name=\"note\"\r\n\r\n"
            b"hi\r\n"
            b"--testbound123--\r\n"
        )
        with pytest.raises(ValueError):
            _parse_multipart(body, _ctype())


# ─── _sanitize_upload_name ───────────────────────────────────


class TestSanitizeUploadName:
    def test_traversal_stripped(self):
        assert _sanitize_upload_name("../../etc/passwd") == "passwd"
        assert _sanitize_upload_name("C:\\Windows\\evil.exe") == "evil.exe"
        assert _sanitize_upload_name("..\\..\\boot.ini") == "boot.ini"

    def test_windows_illegal_chars(self):
        cleaned = _sanitize_upload_name('a<b>c"d|e?f*g.txt')
        assert "<" not in cleaned and ">" not in cleaned
        assert "|" not in cleaned and "?" not in cleaned and "*" not in cleaned
        assert cleaned.endswith(".txt")

    def test_empty_falls_back(self):
        assert _sanitize_upload_name("") == "upload.bin"
        assert _sanitize_upload_name("...") == "upload.bin"

    def test_long_name_capped(self):
        assert len(_sanitize_upload_name("x" * 500 + ".txt")) <= 120


# ─── _save_upload_parts ──────────────────────────────────────


class TestSaveUploadParts:
    def test_saves_into_uploads_dir(self, write_root):
        out = _save_upload_parts([
            {"filename": "note.md", "data": b"# hi"},
        ])
        assert out["ok"] is True
        assert len(out["files"]) == 1
        f = out["files"][0]
        assert f["relative_path"].startswith("uploads/")
        p = Path(f["path"])
        assert p.exists()
        assert p.read_bytes() == b"# hi"
        assert p.parent == write_root / "uploads"
        # timestamp prefix YYYYmmdd-HHMMSS-
        assert p.name[8] == "-" and p.name[15] == "-"

    def test_collision_gets_numeric_suffix(self, write_root):
        part = {"filename": "same.txt", "data": b"x"}
        out1 = _save_upload_parts([dict(part)])
        out2 = _save_upload_parts([dict(part)])
        p1 = Path(out1["files"][0]["path"])
        p2 = Path(out2["files"][0]["path"])
        assert p1 != p2 and p1.exists() and p2.exists()

    def test_dangerous_ext_refused(self, write_root):
        out = _save_upload_parts([
            {"filename": "evil.exe", "data": b"MZ"},
            {"filename": "ok.txt", "data": b"fine"},
        ])
        assert len(out["files"]) == 1
        assert out["files"][0]["name"] == "ok.txt"
        assert len(out["skipped"]) == 1
        assert "dangerous" in out["skipped"][0]["reason"]
        assert not list((write_root / "uploads").glob("*.exe"))

    def test_more_than_20_files_capped(self, write_root):
        parts = [{"filename": f"f{i}.txt", "data": b"x"} for i in range(25)]
        out = _save_upload_parts(parts)
        assert len(out["files"]) == 20
        assert len(out["skipped"]) == 5


class TestUploadMaxBytes:
    def test_default(self, monkeypatch):
        monkeypatch.delenv("QDRANT_WEB_UPLOAD_MAX_MB", raising=False)
        assert _upload_max_bytes() == 200 * 1024 * 1024

    def test_custom(self, monkeypatch):
        monkeypatch.setenv("QDRANT_WEB_UPLOAD_MAX_MB", "5")
        assert _upload_max_bytes() == 5 * 1024 * 1024

    def test_invalid_falls_back(self, monkeypatch):
        monkeypatch.setenv("QDRANT_WEB_UPLOAD_MAX_MB", "banana")
        assert _upload_max_bytes() == 200 * 1024 * 1024


# ─── live HTTP round-trips ───────────────────────────────────


class _HttpSrv:
    """Real ThreadingHTTPServer on an ephemeral loopback port,
    with a raw-POST helper for multipart bodies."""

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

    def _request(self, method: str, path: str, body: bytes,
                 content_type: str) -> tuple[int, bytes]:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        try:
            conn.request(method, path, body=body,
                         headers={"Content-Type": content_type})
            r = conn.getresponse()
            return r.status, r.read()
        finally:
            conn.close()

    def post_raw(self, path: str, body: bytes, content_type: str):
        return self._request("POST", path, body, content_type)


class _FakeApi:
    def get_status(self):  # pragma: no cover - not called
        return {"version": "test"}


@pytest.fixture
def real_assets() -> Path:
    return ROOT / "gui" / "assets"


class TestLiveUpload:
    def test_upload_round_trip_saves_file(
        self, real_assets, write_root, monkeypatch,
    ):
        monkeypatch.setenv("QDRANT_WEB_UPLOAD_MAX_MB", "10")
        body = _multipart_body([("hello.txt", "哈囉".encode("utf-8"))])
        with _HttpSrv(_FakeApi(), real_assets) as srv:
            status, raw = srv.post_raw("/upload", body, _ctype())
        assert status == 200
        out = json.loads(raw)
        assert out["ok"] is True
        assert len(out["files"]) == 1
        f = out["files"][0]
        assert f["name"] == "hello.txt"
        assert Path(f["path"]).read_bytes() == "哈囉".encode("utf-8")
        assert f["relative_path"].startswith("uploads/")

    def test_bad_content_type_rejected(self, real_assets, write_root):
        with _HttpSrv(_FakeApi(), real_assets) as srv:
            status, raw = srv.post_raw("/upload", b"{}", "application/json")
        assert status == 400
        assert "multipart" in json.loads(raw)["error"]

    def test_oversized_body_rejected(
        self, real_assets, write_root, monkeypatch,
    ):
        monkeypatch.setenv("QDRANT_WEB_UPLOAD_MAX_MB", "1")
        big = _multipart_body([("big.bin", b"\0" * (1024 * 1024 + 1024))])
        with _HttpSrv(_FakeApi(), real_assets) as srv:
            status, raw = srv.post_raw("/upload", big, _ctype())
        assert status == 413
        assert "too large" in json.loads(raw)["error"]

    def test_dangerous_ext_skipped_in_live_round_trip(
        self, real_assets, write_root,
    ):
        body = _multipart_body([
            ("evil.bat", b"@echo off"),
            ("doc.pdf", b"%PDF-1.4 fake"),
        ])
        with _HttpSrv(_FakeApi(), real_assets) as srv:
            status, raw = srv.post_raw("/upload", body, _ctype())
        assert status == 200
        out = json.loads(raw)
        assert len(out["files"]) == 1
        assert out["files"][0]["name"] == "doc.pdf"
        assert len(out["skipped"]) == 1
        assert not list((write_root / "uploads").glob("*.bat"))

    def test_upload_logged_at_info(self, real_assets, write_root, caplog):
        body = _multipart_body([("n.txt", b"x")])
        with _HttpSrv(_FakeApi(), real_assets) as srv, \
                caplog.at_level(logging.INFO, logger="gui.web"):
            srv.post_raw("/upload", body, _ctype())
        assert any(
            r.name == "gui.web" and r.levelno == logging.INFO
            and "upload:" in r.getMessage()
            for r in caplog.records
        )


# ─── shim + frontend source guards ───────────────────────────


class TestShimWebModeFlag:
    def test_shim_sets_web_mode_flag(self):
        assert "window.__WEB_MODE__ = true" in web._SHIM_JS


class TestFrontendSourceGuards:
    def _read(self, rel: str) -> str:
        return (ROOT / "gui" / "assets" / rel).read_text(encoding="utf-8")

    def test_index_has_upload_inputs_and_buttons(self):
        html = self._read("index.html")
        for el in ("chat-upload-input", "chat-media-input",
                   "chat-upload-btn", "chat-media-btn"):
            assert f'id="{el}"' in html
        assert 'accept="image/*,video/*"' in html

    def test_app_js_upload_wiring(self):
        js = self._read("app.js")
        assert "function isWebMode()" in js
        assert "window.__WEB_MODE__ === true" in js
        assert "async function uploadFiles(" in js
        assert "fetch('/upload'" in js
        assert "function bindUpload()" in js
        assert "bindUpload();" in js  # wired inside bindEvents
        assert "fileIn.value = ''" in js  # re-pick same file allowed

    def test_app_js_hides_upload_ui_in_gui_mode(self):
        js = self._read("app.js")
        # the GUI-hide branch must reference all four element ids
        assert "'chat-upload-btn', 'chat-media-btn'" in js
        assert "'chat-upload-input', 'chat-media-input'" in js

    @pytest.mark.parametrize("locale,mark", [
        ("zh-TW", "[已上傳]"),
        ("zh-CN", "[已上传]"),
        ("en", "[uploaded]"),
        ("ja", "[アップロード済]"),
    ])
    def test_i18n_keys_per_locale(self, locale, mark):
        js = self._read("app.js")
        assert f"uploadMark: '{mark}'" in js
        assert "uploadOk:" in js and "uploadFail:" in js

    def test_upload_inserts_mark_jsdom(self):
        """v0.9.125.3b — the REAL uploadFiles from app.js, replayed in
        jsdom against a server-shaped /upload JSON: the [已上傳] path
        MUST land in #chat-input (this was the user-visible bug)."""
        js = self._read("app.js")
        i = js.find("const UPLOAD_MARK")
        seg = js[i:js.find("\n  function bindUpload", i)]
        driver = """
const { JSDOM } = require('jsdom');
const w = new JSDOM('<textarea id="chat-input"></textarea>').window;
const calls = { fetchUrl: '' };
w.fetch = async function (url, opts) {
  calls.fetchUrl = url;
  return {
    ok: true,
    json: async function () {
      return { ok: true, files: [
        { name: 'probe.xlsx', path: 'C:/Users/x/uploads/p.xlsx',
          relative_path: 'uploads/p.xlsx', size: 21 }],
        skipped: [] };
    },
  };
};
const t = function (k, o) {
  // v0.9.125.5 — language-NEUTRAL stub: if the insertion chain ever
  // reads t('uploadMark') again the inserted line betrays itself as
  // "[L:uploadMark]" and the assertion below fails.
  if (k === 'uploadOk') return '已上傳 {ok} 個檔案';
  if (k === 'uploadSkip') return '（略過 {skip} 個）';
  return '[L:' + k + ']';
};
const toast = function () {};
const isWebMode = function () { return true; };
// v0.9.127.8 — app.js prefers XHR for upload progress; the jsdom
// XHR would really try to request '/upload'. Delete it so the
// fetch-fallback path runs against the stub above (same contract).
delete w.XMLHttpRequest;
const FormData = w.FormData;
global.document = w.document;
global.window = w;
global.FormData = w.FormData;
global.fetch = w.fetch;
const fetch = w.fetch;
async function main() {
__SEG__
        const fake = new w.File(['x'], 'probe.xlsx',
          { type: 'application/vnd.ms-excel' });
        await uploadFiles([fake]);
  const inp = w.document.getElementById('chat-input');
  console.log('RESULT:' + JSON.stringify({
    value: inp.value, fetchUrl: calls.fetchUrl,
  }));
}
main().catch(function (e) {
  console.log('RESULT:' + JSON.stringify({ error: String(e) }));
}).finally(function () { setTimeout(function () {}, 30); });
setTimeout(function () { process.exit(0); }, 2500);
""".replace("__SEG__", seg)
        out = self._node_jsdom(driver)
        assert '"value":"[已上傳] C:/Users/x/uploads/p.xlsx\\n"' in out, \
            out[-400:]
        assert '"fetchUrl":"/upload"' in out, out[-200:]

    def _node_jsdom(self, driver: str) -> str:
        import os
        import subprocess
        import tempfile

        project = str(ROOT)
        with tempfile.NamedTemporaryFile(
            "w", suffix=".js", delete=False,
            dir=project, encoding="utf-8",
        ) as fh:
            fh.write(driver)
            path = fh.name
        try:
            p = subprocess.run(
                ["node", path], cwd=project, timeout=45,
                capture_output=True, text=True, encoding="utf-8",
                errors="replace",
            )
            out = (p.stdout or "")
            err = (p.stderr or "")
            if not out.strip() and err.strip():
                raise AssertionError("node driver stderr: " + err[-400:])
            return out
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    def test_upload_feedback_guard(self):
        """v0.9.125.3 —「按了上傳沒有反應」fix: (a) IMMEDIATE feedback
        (an uploading toast with the file names), (b) a hard
        AbortController deadline so a slow/half-connected fetch can
        never hang silently (v0.9.127.8: 60 s + 60 s/file, 10-min
        ceiling + a REAL progress panel via XHR onprogress), (c) an
        offline pre-check, (d) the upload/mic/paste/drop group binds
        FIRST in bindEvents (a dead toolbar element used to abort the
        chain before bindUpload ever ran), (e) all four binders are
        idempotent (a double call no longer double-binds = double
        POST)."""
        js = self._read("app.js")
        # immediate feedback + timeout + offline pre-check
        assert "t('uploading')" in js
        assert "AbortController" in js
        assert "60000 + slice.length * 60000" in js
        assert "navigator.onLine === false" in js
        # early binding + idempotence
        js_events = js[js.find("function bindEvents()"):]
        head = js_events[:1200]
        assert "bindUpload();" in head
        assert "bindMic();" in head and "bindPaste();" in head
        for fn in ("bindUpload", "bindMic", "bindPaste", "bindDrop"):
            assert f"{fn}._done" in js, fn

    def test_upload_mark_is_protocol_token_v0125_5(self):
        """v0.9.125.5 — the [已上傳] insert mark must be a FIXED
        protocol token, never t('uploadMark'): a remote device running
        the zh-CN/en UI used to insert "[已上传]"/"[uploaded]" so the
        user searching for 已上傳 saw \"nothing\" (the root cause of
        the remote-vs-local difference)."""
        js = self._read("app.js")
        assert "const UPLOAD_MARK = '[已上傳]'" in js
        # BOTH insert sites use the constant, not the locale key
        assert "t('uploadMark') + ' '" not in js
        for needle in (
            "UPLOAD_MARK + ' ' + f.path",
            "UPLOAD_MARK + ' ' + path",
        ):
            assert needle in js, needle
        # the i18n key stays (4 locales, rendering contracts intact)
        assert js.count("uploadMark:") == 4

    def test_uploading_i18n_four_locales(self):
        js = self._read("app.js")
        assert js.count("uploading:") == 4

    def test_style_css_hides_media_button_on_desktop(self):
        css = self._read("style.css")
        assert "@media (min-width: 821px)" in css
        assert "#chat-media-btn { display: none; }" in css

    # ── v0.9.127.7/127.9 batch-loop behaviour（jsdom 臨時驗證常駐化）──

    @staticmethod
    def _batch_driver(seg: str, files_expr: str) -> str:
        """Run the REAL uploadFiles against a jsdom file list and
        report every POST's file count + the final marker count.
        jsdom's File/Blob cannot materialize a multi-MB part (both
        typed-array and string parts hang the engine for minutes), so
        File/FormData are light fakes — the batch loop under test only
        reads f.size/f.name."""
        return """
const { JSDOM } = require('jsdom');
const w = new JSDOM('<textarea id="chat-input"></textarea>').window;
function FakeFile(name, size) {
  return { name: name, size: size, type: 'image/png' };
}
function FakeFormData() { this.parts = []; }
FakeFormData.prototype.append = function (k, v, n) {
  this.parts.push({ k: k, v: v, n: n }); };
FakeFormData.prototype.getAll = function (k) {
  const out = [];
  for (let i = 0; i < this.parts.length; i++) {
    if (this.parts[i].k === k) out.push(this.parts[i].v);
  }
  return out;
};
const posts = [];
let open = 0;
let maxc = 0;
w.fetch = async function (url, opts) {
  const parts = (opts && opts.body && opts.body.getAll)
    ? opts.body.getAll('files') : [];
  posts.push(parts.length);
  // v0.9.127.10 — concurrency watermark: three parallel posts must
  // overlap (30 ms artificial latency makes the overlap observable).
  open += 1;
  if (open > maxc) maxc = open;
  await new Promise(function (r2) { setTimeout(r2, 30); });
  open -= 1;
  const files = parts.map(function (f) {
    return { name: f.name,
             path: 'C:/Users/x/uploads/' + f.name,
             relative_path: 'uploads/' + f.name,
             size: f.size };
  });
  return { ok: true, json: async function () {
    return { ok: true, files: files, skipped: [] }; } };
};
// v0.9.127.8 — no XHR in the driver: the fetch fallback runs.
delete w.XMLHttpRequest;
const t = function (k, o) {
  if (k === 'uploadOk') return '已上傳 {ok} 個檔案';
  return '[L:' + k + ']';
};
const toast = function () {};
const isWebMode = function () { return true; };
global.document = w.document;
global.window = w;
global.FormData = FakeFormData;
global.fetch = w.fetch;
const fetch = w.fetch;
async function main() {
__SEG__
        const FAKE_FILES = __FILES__;
        await uploadFiles(FAKE_FILES);
  const inp = w.document.getElementById('chat-input');
  const marks = (inp.value.match(/\\[已上傳\\]/g) || []).length;
  console.log('RESULT:' + JSON.stringify({ posts: posts,
    marks: marks, maxc: maxc }));
}
main().catch(function (e) {
  console.log('RESULT:' + JSON.stringify({ error: String(e) }));
}).finally(function () { setTimeout(function () {}, 30); });
setTimeout(function () { process.exit(0); }, 2500);
""".replace("__SEG__", seg).replace("__FILES__", files_expr)

    def _real_batch_seg(self) -> str:
        js = self._read("app.js")
        i = js.index("const UPLOAD_MARK")
        return js[i:js.index("\n  function bindUpload", i)]

    def test_twelve_files_three_posts_jsdom(self):
        """the v0.9.127.7 throwaway verification, now permanent:
        12 files → exactly 3 POSTs [5,5,2] and 12 [已上傳] markers —
        the batch loop never loses a file across posts."""
        files_expr = ("(function () { const fs = []; "
                      "for (let k = 0; k < 12; k++) "
                      "fs.push(FakeFile('f' + k + '.png', 1));"
                      " return fs; })()")
        out = self._node_jsdom(
            self._batch_driver(self._real_batch_seg(), files_expr))
        assert '"posts":[5,5,2]' in out, out[-300:]
        assert '"marks":12' in out, out[-300:]
        # v0.9.127.10 — three batches run OVERLAPPING (parallel)
        assert '"maxc":3' in out, out[-300:]

    def test_big_file_bytes_trim_jsdom(self):
        """v0.9.127.9 — 4×5 MB files: the 12 MB batch cap trims the
        second file off (5+5=10 ≤ 12 OK, 10+5 > 12 cut) → posts
        [2,2] + 4 markers; a mega-file pick never rides one POST."""
        files_expr = ("(function () { const fs = []; "
                      "for (let k = 0; k < 4; k++) "
                      "fs.push(FakeFile('big' + k + '.png',"
                      " 5 * 1024 * 1024)); return fs; })()")
        out = self._node_jsdom(
            self._batch_driver(self._real_batch_seg(), files_expr))
        assert '"posts":[2,2]' in out, out[-300:]
        assert '"marks":4' in out, out[-300:]
        # two batches in flight at once
        assert '"maxc":2' in out, out[-300:]


class TestUploadProgressPanelV0127_8:
    """v0.9.127.8 — the「3 分鐘無反應」experience fix: a batch POST
    streams for MINUTES with zero visible feedback (the ⏳ toast
    lives 2.5 s; the server logs nothing until the body read
    completes — live 15:07/15:10 batches proved every gap).
    Fix: XHR upload.onprogress REAL byte progress + a persistent
    panel (pct / MB / stalled seconds) + a cancel button + the
    server logging the ARRIVAL itself."""

    @staticmethod
    def _read(name: str) -> str:
        return (ROOT / "gui" / "assets" / name).read_text(
            encoding="utf-8")

    def test_app_js_uses_xhr_progress_panel(self):
        js = self._read("app.js")
        assert "new XMLHttpRequest()" in js
        assert "xhr.upload.onprogress" in js
        assert "_showProgressPanel(" in js
        assert "_uppRepaint(" in js
        assert "_closeProgressPanel()" in js
        # v0.9.127.10 — the cancel button aborts EVERY slot
        assert "typeof sl.abort === 'function'" in js
        assert "slot.abort = function () {" in js
        # the fetch fallback survives (same deadline, same shape)
        assert "typeof XMLHttpRequest === 'function'" in js
        assert "files.slice(i, i + UPLOAD_BATCH_SIZE)" in js
        # progress must update the panel inside the callback
        assert "upload-progress-panel" in js
        # v0.9.127.9 — the bar tracks the WHOLE selection (never
        # resets to 0 % at each batch) + size-aware batch caps
        assert "_upAllTotal > 0" in js
        assert "_upAllDoneBytes + inflight" in js
        assert "_upBatchTotal = batches.length;" in js
        assert "while (!stopFlag)" in js
        assert "await Promise.all(workers);" in js

    def test_panel_css_exists(self):
        css = self._read("style.css")
        assert "#upload-progress-panel" in css
        assert ".upp-track" in css and ".upp-fill" in css
        assert ".upp-cancel" in css
        assert ".upp-meta" in css and ".upp-title" in css

    def test_panel_i18n_four_locales(self):
        js = self._read("app.js")
        for key in ("uploadCancel:", "uploadStalled:",
                    "uploadFlowing:", "uploadNetErr:",
                    "uploadBatch:"):
            assert js.count(key) == 4, key

    def test_server_logs_incoming_post(self):
        src = (ROOT / "gui" / "web_server.py").read_text(
            encoding="utf-8")
        assert "upload: incoming POST, content-length=" in src
        # v0.9.127.9 — the save line carries the measured rate
        assert "avg %.0f KB/s" in src
        assert "t0 = time.monotonic()" in src