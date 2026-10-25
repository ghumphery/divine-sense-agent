# -*- coding: utf-8 -*-
"""v0.9.25.1 — loopback-name normalisation for config.QDRANT_URL.

Regression lock for the ~2s-per-request Qdrant latency: `localhost`
resolves to ::1 first on Windows and a TCP connect to ::1 takes ~2 s
to be refused, while qdrant-client re-opens its HTTP connection per
request — so every search/stats call paid that delay. config.py now
rewrites the bare `localhost` loopback name to the 127.0.0.1 literal
(Qdrant binds 0.0.0.0 — same server). Measured: 2073 ms -> 29 ms.
"""
import importlib
import os

import pytest

from src.config import _normalize_loopback_url


class TestNormalizeLoopbackUrl:
    def test_http_localhost_rewritten(self):
        assert _normalize_loopback_url(
            "http://localhost:6333"
        ) == "http://127.0.0.1:6333"

    def test_https_localhost_rewritten(self):
        assert _normalize_loopback_url(
            "https://localhost:6443"
        ) == "https://127.0.0.1:6443"

    def test_ipv4_literal_untouched(self):
        assert _normalize_loopback_url(
            "http://127.0.0.1:6333"
        ) == "http://127.0.0.1:6333"

    def test_lan_ip_and_hostname_untouched(self):
        assert _normalize_loopback_url(
            "http://192.168.50.154:6333"
        ) == "http://192.168.50.154:6333"
        assert _normalize_loopback_url(
            "https://qdrant.internal.lan:6443"
        ) == "https://qdrant.internal.lan:6443"

    def test_explicit_ipv6_loopback_untouched(self):
        # [::1] is a deliberate choice — the helper must not touch it.
        assert _normalize_loopback_url(
            "http://[::1]:6333"
        ) == "http://[::1]:6333"

    def test_empty_and_none_safe(self):
        assert _normalize_loopback_url("") == ""
        assert _normalize_loopback_url(None) == ""


class TestConfigIntegration:
    """The Config singleton applies the helper to the loaded value."""

    def test_live_env_value_is_ipv4_literal(self):
        # The shipped .env pins the IPv4 literal (v0.9.25.1); whatever
        # the local .env carries, a bare `localhost` name must never
        # survive into config.QDRANT_URL.
        import src.config as cfg_mod

        url = cfg_mod.config.QDRANT_URL
        assert url and "localhost" not in url, (
            f"config.QDRANT_URL still carries the localhost loopback "
            f"name: {url!r} (pays ~2 s per request on this box)"
        )

    def test_env_var_localhost_is_normalised_on_reload(self, monkeypatch):
        # Simulate a .env / env var carrying the legacy localhost name;
        # a fresh import must come out with the IPv4 literal.
        monkeypatch.setenv("QDRANT_URL", "http://localhost:6333")
        src = importlib.import_module("src.config")
        importlib.reload(src)
        try:
            assert src.config.QDRANT_URL == "http://127.0.0.1:6333"
        finally:
            monkeypatch.delenv("QDRANT_URL", raising=False)
            importlib.reload(src)   # restore the .env-driven state

    def test_env_var_lan_ip_survives_reload(self, monkeypatch):
        # Non-loopback URLs pass through untouched.
        monkeypatch.setenv("QDRANT_URL", "https://qdrant.internal.lan:6443")
        src = importlib.import_module("src.config")
        importlib.reload(src)
        try:
            assert src.config.QDRANT_URL == "https://qdrant.internal.lan:6443"
        finally:
            monkeypatch.delenv("QDRANT_URL", raising=False)
            importlib.reload(src)   # restore the .env-driven state

    def test_default_when_unset_is_ipv4_literal(self, monkeypatch):
        monkeypatch.delenv("QDRANT_URL", raising=False)
        src = importlib.import_module("src.config")
        importlib.reload(src)
        try:
            assert src.config.QDRANT_URL == "http://127.0.0.1:6333"
        finally:
            importlib.reload(src)   # restore the .env-driven state


class TestEmbedderUrlNormalization:
    """v0.9.25.3 — the Embedder applies the same helper to
    ``LLAMACPP_EMBED_URL`` (second victim of the ::1 trap: every
    requests.post re-opens its HTTP connection, so a bare `localhost`
    value costs ~2 s per embed / probe call)."""

    @staticmethod
    def _embedder(monkeypatch, url):
        monkeypatch.setenv("EMBEDDING_CACHE", "off")   # no SQLite side effects
        if url is None:
            monkeypatch.delenv("LLAMACPP_EMBED_URL", raising=False)
        else:
            monkeypatch.setenv("LLAMACPP_EMBED_URL", url)
        from src.ingest.embedder import Embedder
        return Embedder()

    def test_bare_localhost_rewritten(self, monkeypatch):
        emb = self._embedder(
            monkeypatch, "http://localhost:8080/v1/embeddings"
        )
        assert emb.llamacpp_url == "http://127.0.0.1:8080/v1/embeddings"

    def test_ipv4_literal_untouched(self, monkeypatch):
        emb = self._embedder(
            monkeypatch, "http://127.0.0.1:8080/v1/embeddings"
        )
        assert emb.llamacpp_url == "http://127.0.0.1:8080/v1/embeddings"

    def test_lan_hostname_and_ipv6_untouched(self, monkeypatch):
        emb = self._embedder(
            monkeypatch, "http://llamacpp.internal.lan:8080/v1/embeddings"
        )
        assert (
            emb.llamacpp_url
            == "http://llamacpp.internal.lan:8080/v1/embeddings"
        )
        emb2 = self._embedder(
            monkeypatch, "http://[::1]:8080/v1/embeddings"
        )
        assert emb2.llamacpp_url == "http://[::1]:8080/v1/embeddings"