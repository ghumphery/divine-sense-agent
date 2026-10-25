# -*- coding: utf-8 -*-
"""v0.9.126.0 — optional HTTPS/TLS for the web UI (the PWA install
path): _tls_material resolution (env pair / AUTO self-signed / unset =
plain http byte-identical), the self-signed generator (SAN covers the
LAN IPs; reuse does not regenerate), _format_url/_urls_for scheme
plumbing, and a LIVE https round-trip on an ephemeral wrapped server
(Android Firefox: accept-the-risk once → install becomes possible)."""
from __future__ import annotations

import json
import ssl
import threading
from pathlib import Path

import pytest

import gui.web_server as web

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "gui" / "assets"


class TestTlsMaterial:
    def test_unset_stays_plain_http(self, monkeypatch):
        for var in ("QDRANT_WEB_TLS_CERT", "QDRANT_WEB_TLS_KEY",
                    "QDRANT_WEB_TLS_AUTO"):
            monkeypatch.delenv(var, raising=False)
        assert web._tls_material() is None

    def test_half_set_refused(self, monkeypatch):
        monkeypatch.setenv("QDRANT_WEB_TLS_CERT", "C:/x/a.crt")
        assert web._tls_material() is None

    def test_env_pair_missing_files_refused(self, monkeypatch):
        monkeypatch.setenv("QDRANT_WEB_TLS_CERT",
                           str(ROOT / "nope.crt"))
        monkeypatch.setenv("QDRANT_WEB_TLS_KEY", str(ROOT / "nope.key"))
        assert web._tls_material() is None

    def test_env_pair_real_files_accepted(self, monkeypatch, tmp_path):
        cert = tmp_path / "c.pem"
        key = tmp_path / "k.pem"
        cert.write_text("CERT", encoding="utf-8")
        key.write_text("KEY", encoding="utf-8")
        monkeypatch.setenv("QDRANT_WEB_TLS_CERT", str(cert))
        monkeypatch.setenv("QDRANT_WEB_TLS_KEY", str(key))
        got = web._tls_material()
        assert got == (str(cert), str(key))


class TestSelfSigned:
    def test_auto_generates_and_reuses(self, tmp_path, monkeypatch):
        monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path))
        monkeypatch.delenv("QDRANT_WEB_TLS_CERT", raising=False)
        monkeypatch.delenv("QDRANT_WEB_TLS_KEY", raising=False)
        monkeypatch.setenv("QDRANT_WEB_TLS_AUTO", "on")
        got = web._tls_material()
        assert got is not None
        cert_p, key_p = Path(got[0]), Path(got[1])
        assert cert_p.is_file() and key_p.is_file()
        assert "certs" in str(cert_p)
        pem = cert_p.read_text(encoding="utf-8")
        assert "BEGIN CERTIFICATE" in pem
        # SAN carries the LAN IPs (the device connects by IP)
        from cryptography import x509

        cert = x509.load_pem_x509_certificate(
            cert_p.read_bytes())
        sans = cert.extensions.get_extension_for_class(
            x509.SubjectAlternativeName).value.get_values_for_type(
                x509.IPAddress)
        ips = [str(v) for v in sans]
        assert "127.0.0.1" in ips
        lan = web._enumerate_lan_ips()
        assert all(ip in ips for ip in lan)
        # REUSE: a second call returns the same files (the Android
        # "accept the risk" trust decision must survive reboots)
        got2 = web._tls_material()
        assert Path(got2[0]) == cert_p and Path(got2[1]) == key_p
        # resolution through the env knob (truthy aliases)
        monkeypatch.setenv("QDRANT_WEB_TLS_AUTO", "1")
        assert web._tls_material() == got

    def test_scheme_plumbing(self):
        assert web._format_url(
            "127.0.0.1", 8334, "https") == "https://127.0.0.1:8334/"
        assert web._format_url(
            "::1", 8334, "https") == "https://[::1]:8334/"
        urls, browser = web._urls_for("0.0.0.0", 8334, "https")
        assert all(u.startswith("https://") for u in urls)
        assert browser == "https://127.0.0.1:8334/"


class _FakeApi:
    def get_status(self):  # pragma: no cover - not invoked
        return {"version": "test"}


class TestLiveHttps:
    def test_https_round_trip(self, tmp_path, monkeypatch):
        # AUTO pair in an isolated home, then a REAL wrapped server
        monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path))
        monkeypatch.delenv("QDRANT_WEB_TLS_CERT", raising=False)
        monkeypatch.delenv("QDRANT_WEB_TLS_KEY", raising=False)
        monkeypatch.setenv("QDRANT_WEB_TLS_AUTO", "on")
        pair = web._tls_material()
        assert pair is not None

        handler = web.make_handler(_FakeApi(), ASSETS)
        srv_cls = web.ThreadingHTTPServer
        server = srv_cls(("127.0.0.1", 0), handler)
        server.daemon_threads = True
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(pair[0], pair[1])
        server.socket = ctx.wrap_socket(
            server.socket, server_side=True)
        port = server.server_address[1]
        th = threading.Thread(
            target=server.serve_forever, daemon=True)
        th.start()
        try:
            import http.client
            import socket as sock_mod

            ctxc = ssl.create_default_context()
            ctxc.check_hostname = False
            ctxc.verify_mode = ssl.CERT_NONE   # self-signed acceptance
            raw = sock_mod.create_connection(
                ("127.0.0.1", port), timeout=15)
            tls_sock = ctxc.wrap_socket(
                raw, server_hostname="127.0.0.1")
            client = http.client.HTTPSConnection(
                "127.0.0.1", port, timeout=15)
            client.sock = tls_sock
            client.request("GET", "/manifest.webmanifest")
            r = client.getresponse()
            body = r.read()
            assert r.status == 200
            m = json.loads(body.decode("utf-8"))
            assert m["display"] == "standalone"
            client.close()
        finally:
            server.shutdown()
            server.server_close()