# -*- coding: utf-8 -*-
"""v0.9.127.5 — PWA share_target / share plumbing tests.

Covers: the manifest share_target contract (POST multipart + files
accept list), the /upload?share=1 303-redirect shape (→ /?shared=…),
the `share=json` HTTP-client variant (iOS Shortcuts), the legacy
non-share /upload contract staying intact, the /share-text text-drop
endpoint, the share text sidecar helpers, and the app.js source
guards (per-bubble share-out button + handleSharedLanding).
"""
from __future__ import annotations

import http.client
import json
import threading
import urllib.parse
from pathlib import Path

import pytest

import gui.web_server as web

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "gui" / "assets"


def _multipart(fields=None, files=None):
    """Build a minimal multipart/form-data body + its Content-Type."""
    b = "sharetestboundary123"
    chunks = []
    for name, value in (fields or {}).items():
        chunks.append((
            f"--{b}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n'
            f"\r\n"
            f"{value}\r\n"
        ).encode("utf-8"))
    for name, filename, blob in (files or []):
        chunks.append((
            f"--{b}\r\n"
            f'Content-Disposition: form-data; name="{name}"; '
            f'filename="{filename}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode("ascii") + blob + b"\r\n")
    body = b"".join(chunks) + f"--{b}--\r\n".encode("ascii")
    return body, f"multipart/form-data; boundary={b}"


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    root = tmp_path / "sandbox"
    monkeypatch.setenv("QDRANT_FILESYSTEM_WRITE_ROOT", str(root))
    monkeypatch.delenv("WATCH_DIR", raising=False)
    return root


class _Srv:
    """Live server on an ephemeral loopback port (api unused by the
    upload/share routes — a bare stub is fine)."""

    def __init__(self):
        handler = web.make_handler(type("StubApi", (), {})(), ASSETS)
        from http.server import ThreadingHTTPServer

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()

    def post(self, path: str, body, ctype: str):
        if isinstance(body, str):
            body = body.encode("utf-8")
        conn = http.client.HTTPConnection(
            "127.0.0.1", self.port, timeout=15)
        try:
            conn.request("POST", path, body=body,
                         headers={"Content-Type": ctype})
            r = conn.getresponse()
            return r.status, r.headers, r.read()
        finally:
            conn.close()


class TestManifestContract:
    @staticmethod
    def _manifest() -> dict:
        return json.loads((ASSETS / "manifest.webmanifest")
                          .read_text(encoding="utf-8"))

    def test_share_target_shape(self):
        st = self._manifest()["share_target"]
        assert st["action"] == "/upload?share=1"
        assert st["method"] == "POST"
        assert st["enctype"] == "multipart/form-data"
        p = st["params"]
        assert p["title"] == "share_title"
        assert p["text"] == "share_text"
        assert p["url"] == "share_url"
        files = p["files"][0]
        assert files["name"] == "files"
        assert "image/png" in files["accept"]
        assert ".png" in files["accept"]

    def test_share_target_action_in_scope(self):
        m = self._manifest()
        assert m["share_target"]["action"].startswith("/")
        assert m["scope"] == "/"
class TestMultipartKeepFields:
    def test_legacy_mode_field_only_raises(self):
        body, ct = _multipart(fields={"share_text": "x"})
        with pytest.raises(ValueError):
            web._parse_multipart(body, ct)

    def test_keep_fields_returns_fields(self):
        body, ct = _multipart(fields={"share_text": "hello 世界"})
        parts = web._parse_multipart(body, ct, keep_fields=True)
        assert parts == [{"name": "share_text",
                          "value": "hello 世界"}]

    def test_mixed_files_and_fields(self):
        body, ct = _multipart(
            fields={"share_title": "T"},
            files=[("files", "a.png", b"PNGDATA")])
        parts = web._parse_multipart(body, ct, keep_fields=True)
        files = [p for p in parts if "filename" in p]
        fields = [p for p in parts if "filename" not in p]
        assert len(files) == 1 and files[0]["data"] == b"PNGDATA"
        assert fields[0] == {"name": "share_title", "value": "T"}

    def test_legacy_file_part_unchanged(self):
        body, ct = _multipart(files=[("files", "a.png", b"PNGDATA")])
        parts = web._parse_multipart(body, ct)
        assert parts == [{"name": "files", "filename": "a.png",
                          "data": b"PNGDATA"}]
class TestShareTextSidecar:
    def test_store_share_text_file(self, sandbox):
        p = web._store_share_text_file("hello\nworld")
        assert p is not None
        f = Path(p)
        assert f.exists()
        assert f.read_text(encoding="utf-8") == "hello\nworld"
        assert f.parent == sandbox / "uploads"

    def test_store_failures_return_none(self, tmp_path, monkeypatch):
        blocker = tmp_path / "blocker.txt"
        blocker.write_text("x", encoding="utf-8")
        monkeypatch.setenv("QDRANT_FILESYSTEM_WRITE_ROOT", str(blocker))
        assert web._store_share_text_file("x") is None

    def test_save_share_text_composes_fields(self, sandbox):
        p = web._save_share_text({
            "share_title": "Doc title",
            "share_url": "https://example.com/a",
            "share_text": "body text",
        })
        blob = Path(p).read_text(encoding="utf-8")
        assert "[title] Doc title" in blob
        assert "[url] https://example.com/a" in blob
        assert "[text]\nbody text" in blob

    def test_save_share_text_nothing(self, sandbox):
        assert web._save_share_text({}) is None
        assert web._save_share_text({"share_text": "   "}) is None


class TestLiveShareUpload:
    def test_share1_redirects_with_paths(self, sandbox):
        body, ct = _multipart(
            fields={"share_title": "T", "share_text": "some text"},
            files=[("files", "pic.png", b"\x89PNG-fake")])
        with _Srv() as srv:
            status, headers, payload = srv.post(
                "/upload?share=1", body, ct)
        assert status == 303
        loc = headers.get("Location") or ""
        assert loc.startswith("/?shared=")
        ups = sandbox / "uploads"
        saved = list(ups.glob("*-pic.png"))
        assert saved and saved[0].read_bytes() == b"\x89PNG-fake"
        txts = list(ups.glob("*-shared*.txt"))
        assert txts and "some text" in txts[0].read_text("utf-8")
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(loc).query)
        paths = q["shared"][0].split("\n")
        assert len(paths) == 2  # one file + one text sidecar

    def test_share_json_mode_for_shortcuts(self, sandbox):
        body, ct = _multipart(
            fields={"share_text": "hi"},
            files=[("files", "a.txt", b"data")])
        with _Srv() as srv:
            status, headers, payload = srv.post(
                "/upload?share=json", body, ct)
        assert status == 200
        data = json.loads(payload.decode("utf-8"))
        assert data["ok"] is True
        assert data["share"]["fields"] == {"share_text": "hi"}
        assert data["share"]["text_path"]
        assert data["files"][0]["path"].endswith("a.txt")

    def test_legacy_upload_contract_unchanged(self, sandbox):
        body, ct = _multipart(
            files=[("files", "a.png", b"\x89PNG")],
            fields={"share_text": "ignored"})
        with _Srv() as srv:
            status, headers, payload = srv.post("/upload", body, ct)
        assert status == 200
        data = json.loads(payload.decode("utf-8"))
        assert data["ok"] is True and len(data["files"]) == 1
        assert "share" not in data

    def test_share1_reports_skipped(self, sandbox):
        body, ct = _multipart(
            files=[("files", "evil.exe", b"MZ")])
        with _Srv() as srv:
            status, headers, payload = srv.post(
                "/upload?share=1", body, ct)
        assert status == 303
        assert "skipped=1" in (headers.get("Location") or "")

    def test_share_err_redirect_when_sandbox_dead(
            self, tmp_path, monkeypatch):
        blocker = tmp_path / "blocker.txt"
        blocker.write_text("x", encoding="utf-8")
        monkeypatch.setenv("QDRANT_FILESYSTEM_WRITE_ROOT", str(blocker))
        body, ct = _multipart(files=[("files", "a.png", b"P")])
        with _Srv() as srv:
            status, headers, payload = srv.post(
                "/upload?share=1", body, ct)
        assert status == 303
        assert "shared_err=" in (headers.get("Location") or "")
class TestLiveShareText:
    def test_raw_text_body(self, sandbox):
        with _Srv() as srv:
            status, headers, payload = srv.post(
                "/share-text", "快取的文字內容",
                "text/plain; charset=utf-8")
        assert status == 200
        data = json.loads(payload.decode("utf-8"))
        assert data["ok"] is True
        f = Path(data["path"])
        assert f.exists()
        assert "快取的文字內容" in f.read_text(encoding="utf-8")
        assert f.parent == (sandbox / "uploads")

    def test_multipart_field(self, sandbox):
        body, ct = _multipart(fields={"share_text": "from-form"})
        with _Srv() as srv:
            status, headers, payload = srv.post(
                "/share-text", body, ct)
        assert status == 200
        data = json.loads(payload.decode("utf-8"))
        assert status == 200 and data["ok"] is True
        assert "from-form" in Path(data["path"]).read_text("utf-8")

    def test_empty_body_400(self, sandbox):
        with _Srv() as srv:
            status, headers, payload = srv.post(
                "/share-text", b"", "text/plain")
        assert status == 400

    def test_whitespace_text_400(self, sandbox):
        with _Srv() as srv:
            status, headers, payload = srv.post(
                "/share-text", "   \r\n ", "text/plain")
        assert status == 400


class TestAppJsGuards:
    @staticmethod
    def _js() -> str:
        return (ASSETS / "app.js").read_text(encoding="utf-8")

    def test_share_out_button_wired(self):
        js = self._js()
        assert "btn-msg-share" in js
        assert "_shareChatText(stext)" in js
        assert "t('shareReply')" in js

    def test_share_out_fallback_chain(self):
        js = self._js()
        assert "navigator.share" in js
        assert "AbortError" in js
        assert "navigator.clipboard.writeText(text)" in js
        assert "shareCopied" in js

    def test_landing_handler_exists(self):
        js = self._js()
        assert "function handleSharedLanding()" in js
        assert "handleSharedLanding._done" in js
        # consumes the redirect contract sent by _send_share_redirect
        assert "q.get('shared')" in js
        assert "q.get('shared_err')" in js
        assert "q.get('skipped')" in js
        assert "history.replaceState" in js
        # markers use the protocol token (never the locale key)
        assert "UPLOAD_MARK + ' ' + p" in js
        assert "sharedIn" in js

    def test_landing_bound_from_bind_events(self):
        js = self._js()
        seg = js[js.index("function bindEvents()"):]
        # v0.9.133.0 — 900 → 1300: the clipboard + bubble-style binder
        # lines grew the head (window drift, guard intent unchanged).
        assert "handleSharedLanding();" in seg[:1300]

    def test_share_css_button(self):
        css = (ASSETS / "style.css").read_text(encoding="utf-8")
        assert ".chat-msg .btn-msg-share {" in css


class TestDeadClientShield:
    """v0.9.127.7 — the live 2026-10-16 14:49 traceback: the
    truncated-refusal reply was written into a DEAD TLS socket
    (ssl.SSLEOFError is an OSError but NOT a ConnectionError), so
    the narrow catch escaped and socketserver printed a traceback."""

    def test_refusal_reply_uses_oserror_guard(self):
        src = (ROOT / "gui" / "web_server.py").read_text(
            encoding="utf-8")
        # the narrow family is gone everywhere in this module
        assert "except (ConnectionError, BrokenPipeError):" not in src
        assert "except OSError:" in src
        assert "client gone on truncated reply" in src
        # both /upload and /share-text routes are shielded
        assert src.count(
            "upload: connection closed mid-reply (client)") == 2
        assert "client gone on redirect reply" in src


class TestBatchedUpload:
    def test_batching_source_guards(self):
        js = self._js()
        assert "const UPLOAD_BATCH_SIZE = 5;" in js
        assert ("function _uploadOneBatch(slice, total, start, slot)"
                in js)
        assert "files.slice(i, i + UPLOAD_BATCH_SIZE)" in js
        assert "saved before the failure" in js
        # pending buffer + markers ride EVERY completed batch
        assert js.count("__PENDING_UPLOAD_PATHS__") >= 2
        assert js.count("UPLOAD_MARK + ' ' + f.path") >= 1

    def test_batch_deadline_source(self):
        js = self._js()
        # v0.9.127.8 — 60 s base + 60 s / file, 10-minute ceiling
        # (the flat 30 s of 125.3 died mid-stream on slow links).
        assert "60000 + slice.length * 60000" in js
        assert "Math.min(600000," in js
        # the old flat abort is gone
        assert "30000 + files.length * 20000" not in js
        # v0.9.127.9 — size-aware batches (12 MB cap) + the while
        # loop that advances by the TRIMMED slice length.
        assert "const UPLOAD_BATCH_BYTES = 12 * 1024 * 1024;" in js
        assert "while (i < files.length)" in js

    def test_parallel_pool_source(self):
        """v0.9.127.10 — PARALLEL batch uploads: a worker pool runs
        up to UPLOAD_CONCURRENCY batch POSTs at once; a failure stops
        new batches but lets in-flight ones finish."""
        js = self._js()
        assert "const UPLOAD_CONCURRENCY = 3;" in js
        assert "while (!stopFlag)" in js
        assert "await Promise.all(workers);" in js
        assert "_upSlots" in js
        # one summary toast after the pool settles
        assert "saved before the failure" in js

    @staticmethod
    def _js() -> str:
        return (ASSETS / "app.js").read_text(encoding="utf-8")


class TestTruncatedUpload:
    def test_partial_body_refused_no_partial_save(self, sandbox):
        """Client vanished mid-stream: the parsed prefix must NOT be
        saved silently — live 2026-10-16 14:30 (十幾個 files picked,
        'saved 3, skipped 0' from the truncated prefix) made the loss
        invisible; v0.9.127.6 makes it a loud 400 with the byte math.
        (raw socket: half a body, then an orderly half-close)"""
        import socket as _socket

        body, ct = _multipart(files=[
            ("files", f"n{i}.png", b"X" * (80 * 1024))
            for i in range(1, 13)])
        with _Srv() as srv:
            s = _socket.create_connection(
                ("127.0.0.1", srv.port), timeout=15)
            try:
                head = (
                    "POST /upload HTTP/1.1\r\n"
                    "Host: local\r\n"
                    f"Content-Type: {ct}\r\n"
                    f"Content-Length: {len(body)}\r\n"
                    "\r\n").encode("ascii")
                s.sendall(head + body[:60 * 1024])
                s.shutdown(_socket.SHUT_WR)
                raw = b""
                while b"\r\n\r\n" not in raw:
                    raw += s.recv(65536)
                head_txt, _, rest = raw.partition(b"\r\n\r\n")
                clen = 0
                for hb in head_txt.lower().split(b"\r\n"):
                    if hb.startswith(b"content-length:"):
                        clen = int(hb.split(b":")[1])
                while len(rest) < clen:
                    nxt = s.recv(65536)
                    if not nxt:
                        break
                    rest += nxt
            finally:
                s.close()
        assert b"400" in raw.split(b"\r\n", 1)[0]
        assert b"truncated" in rest
        # NO partial save: the uploads dir was never even created
        assert not (sandbox / "uploads").exists()

    def test_share_text_truncation_rule_source(self):
        src = (ROOT / "gui" / "web_server.py").read_text(
            encoding="utf-8")
        # /upload and /share-text both refuse shorter-than-declared
        assert src.count("truncated body — got %d of %d") == 1
        assert "share text truncated" in src
        assert src.count('if len(raw) < length:') == 1