# -*- coding: utf-8 -*-
"""Tests: hash-pollution audit + re-embed (v0.9.112.0).

Covers the three surfaces of the fix:
  1. ``Embedder.last_backends`` / ``embed_fresh`` /
     ``hash_fallback_vector`` (tagging + cache-bypass machinery),
  2. ``pipeline`` payload tagging + ``QDRANT_EMBED_FAIL_CLOSED``,
  3. ``src/ingest/reembed.py`` audit + repair over a FAKE Qdrant store
     (the exact-match heuristic must work for points tagged AND
     untagged — the tag is only a label, the recomputation is the
     truth).
Everything mock-driven — no real Qdrant, no real embedding POST.
"""
from __future__ import annotations

import types
from unittest import mock

import pytest

import src.ingest.reembed as rr


# ── helpers ──────────────────────────────────────────────────────────

def _resp(payload, status=200):
    r = types.SimpleNamespace(json=lambda: payload)
    r.status_code = status

    def _raise():
        if status >= 300:
            raise RuntimeError(f"http {status}")
    r.raise_for_status = _raise
    return r


class _FakeStore:
    """Duck-typed QdrantStore: .collection + .client.scroll + .upsert."""

    def __init__(self, points, collection="file_docs"):
        self.collection = collection
        self._points = points
        self.upserted: list = []
        client = types.SimpleNamespace()
        client.scroll = self._scroll
        self.client = client

    def _scroll(self, collection_name=None, limit=256, with_payload=True,
                with_vectors=False, offset=None):
        pts = self._points[:limit]
        return pts, None

    def upsert(self, points):
        if isinstance(points, dict):
            points = [points]
        self.upserted.extend(points)


class _E:
    """Minimal embedder stand-in for ``_scan`` — dimension +
    ``hash_fallback_vector`` only (audit must NOT need the real
    Embedder or a network)."""

    dimension = 4

    def hash_fallback_vector(self, text):
        import hashlib
        h = hashlib.md5(text.encode("utf-8")).digest()[:4]
        return [1.0, 0.0, 0.0, 0.0] if h[0] % 2 else [0.0, 1.0, 0.0, 0.0]


def _pt(pid, text, vec, payload=None):
    payload = dict(payload or {})
    payload.setdefault("text", text)
    payload.setdefault("filename", "f.txt")
    payload.setdefault("source", "C:/f.txt")
    return types.SimpleNamespace(id=pid, payload=payload, vector=vec)


def _points_for_audit():
    e4 = _E()
    return [
        # tagged hash — flagged WITHOUT recomputation
        _pt(1, "tagged hash text",
            e4.hash_fallback_vector("tagged hash text"),
            {"embedding_backend": "hash"}),
        # UNTAGGED but exactly the hash vector of its text (legacy
        # point, pre-tag) — detected by the exact-match heuristic
        _pt(2, "legacy hash text",
            e4.hash_fallback_vector("legacy hash text")),
        # untagged, REAL vector — clean
        _pt(3, "good text", [0.6, 0.8, 0.0, 0.0]),
        # untagged but a DIFFERENT vector of the same text → NOT flagged
        _pt(4, "good text", [0.0, 0.0, 1.0, 0.0]),
        # dim mismatch → skipped, not flagged
        _pt(5, "other text", [0.5, 0.5]),
    ]


def _pin_factory(monkeypatch):
    class _Emb(_E):
        _last_backend = "local"

        def embed_fresh(self, text):
            return [1.0, 0.0, 0.0, 0.0]
    monkeypatch.setattr(rr, "_embedder_factory", lambda: _Emb())


def _emb(monkeypatch, backend="local", env_dim=4):
    monkeypatch.setenv("EMBEDDING_DIM", str(env_dim))
    monkeypatch.setenv("EMBEDDING_BACKEND", backend)
    monkeypatch.setenv("EMBEDDING_CACHE", "off")
    monkeypatch.delenv("EMBEDDER_BACKEND_COOLDOWN", raising=False)
    from src.ingest.embedder import Embedder
    e = Embedder()
    e._fail_until.clear()
    return e
# ── 1. Embedder surface ──────────────────────────────────────────────

class TestEmbedderBackendTags:

    def test_batch_local_labels_all_local(self, monkeypatch):
        emb = _emb(monkeypatch, backend="local")
        payload = {"data": [{"embedding": [1.0, 0.0, 0.0, 0.0]},
                            {"embedding": [0.0, 1.0, 0.0, 0.0]}]}
        with mock.patch("src.ingest.embedder.requests.post",
                        return_value=_resp(payload)):
            out = emb.embed_batch(["hello", "world"])
        assert len(out) == 2
        assert emb.last_backends == {0: "local", 1: "local"}

    def test_fallback_overrides_with_hash(self, monkeypatch):
        emb = _emb(monkeypatch, backend="local")
        with mock.patch("src.ingest.embedder.requests.post",
                        side_effect=ConnectionError("down")):
            out = emb.embed_batch(["a", "b"])
        assert len(out) == 2
        assert emb.last_backends == {0: "hash", 1: "hash"}

    def test_labels_same_length_as_batch(self, monkeypatch):
        emb = _emb(monkeypatch, backend="local")
        with mock.patch("src.ingest.embedder.requests.post",
                        side_effect=ConnectionError("down")):
            emb.embed_batch(["x", "y", "z"])
        assert len(emb.last_backends) == 3

    def test_embed_fresh_bypasses_cache(self, monkeypatch):
        emb = _emb(monkeypatch)
        with mock.patch(
            "src.ingest.embedder.requests.post",
            side_effect=ConnectionError("down"),
        ):
            v1 = emb.embed("poisoned text")   # hash; disk-cache row
        # healthy again: embed() must serve the OLD hash row from the
        # cache (that IS the poisoning) while embed_fresh() re-embeds.
        emb._fail_until.clear()
        emb.openai_api_key = None
        with mock.patch.object(type(emb), "_select_backend",
                               return_value="local"), \
             mock.patch.object(type(emb), "_openrouter_available",
                               return_value=False), \
             mock.patch("src.ingest.embedder.requests.post",
                        return_value=_resp(
                            {"data": [{"embedding": [1.0] * 4}]})):
            v_recached = emb.embed("poisoned text")
            v_fresh = emb.embed_fresh("poisoned text")
        assert v_recached == v1      # cache returned the poisoned row
        assert v_fresh != v1         # embed_fresh bypassed both tiers
        assert emb._last_backend == "local"

    def test_hash_fallback_vector_is_deterministic(self, monkeypatch):
        emb = _emb(monkeypatch)
        assert (emb.hash_fallback_vector("同一句話")
                == emb.hash_fallback_vector("同一句話"))
        assert len(emb.hash_fallback_vector("t")) == emb.dimension


# ── 2. Pipeline tagging + fail-closed ────────────────────────────────

def _pipeline(labels):
    from src.ingest.pipeline import IngestPipeline
    p = IngestPipeline.__new__(IngestPipeline)
    p.embedder = types.SimpleNamespace(
        embed_batch=lambda texts: [[0.1] * 4 for _ in texts],
        last_backends=labels,
    )
    return p


class TestPipelineTagging:
    def _run(self, p, texts):
        from pathlib import Path
        chunks = [types.SimpleNamespace(text=t, metadata={})
                  for t in texts]
        fake = _FakeStore([])
        calls = []
        fake.upsert = lambda pts: calls.extend(pts)
        p.store = fake
        n = p._embed_and_upsert(
            Path("x/f.txt"), chunks, ["h"] * len(texts), "C:/x/f.txt")
        return n, calls

    def test_payload_carries_backend_tag(self, monkeypatch):
        p = _pipeline({0: "local", 1: "openrouter"})
        n, calls = self._run(p, ["a", "b"])
        assert n == 2
        assert calls[0]["payload"]["embedding_backend"] == "local"
        assert calls[1]["payload"]["embedding_backend"] == "openrouter"

    def test_default_tag_when_no_labels(self, monkeypatch):
        p = _pipeline({})
        n, calls = self._run(p, ["a"])
        assert n == 1
        assert calls[0]["payload"]["embedding_backend"] == ""

    def test_fail_closed_on_hash_writes_nothing(self, monkeypatch):
        monkeypatch.setenv("QDRANT_EMBED_FAIL_CLOSED", "on")
        p = _pipeline({0: "hash", 1: "hash"})
        with pytest.raises(RuntimeError) as ei:
            self._run(p, ["a", "b"])
        assert "hash" in str(ei.value) and "FAIL_CLOSED" in str(ei.value)

    def test_fail_closed_off_tags_and_continues(self, monkeypatch):
        monkeypatch.delenv("QDRANT_EMBED_FAIL_CLOSED", raising=False)
        p = _pipeline({0: "hash", 1: "hash"})
        n, calls = self._run(p, ["a", "b"])
        assert n == 2
        assert calls[0]["payload"]["embedding_backend"] == "hash"

    def test_fail_closed_bool_parser(self, monkeypatch):
        from src.ingest.pipeline import _embed_fail_closed
        monkeypatch.delenv("QDRANT_EMBED_FAIL_CLOSED", raising=False)
        assert _embed_fail_closed() is False
        for v in ("on", "1", "true", "ON", "Yes"):
            monkeypatch.setenv("QDRANT_EMBED_FAIL_CLOSED", v)
            assert _embed_fail_closed() is True
        for v in ("off", "0", "no", "", "garbage"):
            monkeypatch.setenv("QDRANT_EMBED_FAIL_CLOSED", v)
            assert _embed_fail_closed() is False# ── 3. reembed audit + repair (fake store) ───────────────────────────

class TestAudit:

    def test_flags_tagged_and_untagged_hash(self, monkeypatch):
        _pin_factory(monkeypatch)
        store = _FakeStore(_points_for_audit())
        report = rr.audit_collection(store)
        assert report["ok"] is True
        assert report["scanned"] == 5
        assert report["tagged_hash"] == 1
        assert report["matched_hash"] == 1
        assert report["skipped_dim_mismatch"] == 1
        assert report["hash_points"] == 2
        assert [f["id"] for f in report["flagged"]] == ["1", "2"]

    def test_audit_never_mutates(self, monkeypatch):
        _pin_factory(monkeypatch)
        store = _FakeStore(_points_for_audit())
        rr.audit_collection(store)
        assert store.upserted == []

    def test_scan_failure_is_envelope(self):
        class _Broken:
            collection = "x"
            client = types.SimpleNamespace(
                scroll=lambda *a, **k: (_ for _ in ()).throw(
                    RuntimeError("boom")))
        report = rr.audit_collection(_Broken())
        assert report.get("ok") is False and "error" in report


class TestReembed:

    def test_reembeds_flagged_and_updates_tag(self, monkeypatch):
        _pin_factory(monkeypatch)
        store = _FakeStore(_points_for_audit())
        report = rr.reembed_flagged(store)
        assert report["ok"] is True
        assert report["reembedded"] == 2
        assert len(store.upserted) == 2
        assert {p["id"] for p in store.upserted} == {"1", "2"}
        for p in store.upserted:
            assert p["vector"] == [1.0, 0.0, 0.0, 0.0]
            assert p["payload"]["embedding_backend"] == "local"
            assert p["payload"]["text"]  # payload preserved
        assert report["backends_used"] == {"local": 2}

    def test_still_hash_points_left_untouched(self, monkeypatch):
        class _Emb(_E):
            _last_backend = "hash"

            def embed_fresh(self, text):
                return [0.5, 0.5, 0.0, 0.0]
        monkeypatch.setattr(rr, "_embedder_factory", lambda: _Emb())
        store = _FakeStore(_points_for_audit())
        report = rr.reembed_flagged(store)
        assert report["reembedded"] == 0
        assert len(report["still_hash"]) == 2
        assert store.upserted == []   # the repair cannot lie

    def test_limit_caps_per_run(self, monkeypatch):
        _pin_factory(monkeypatch)
        store = _FakeStore(_points_for_audit())
        report = rr.reembed_flagged(store, limit=1)
        assert report["reembedded"] == 1
        assert len(store.upserted) == 1

    def test_per_point_embed_failure_contained(self, monkeypatch):
        _pin_factory(monkeypatch)

        class _Emb(_E):
            _last_backend = "local"

            def embed_fresh(self, text):
                if "legacy" in text:
                    raise RuntimeError("backend hiccup")
                return [1.0, 0.0, 0.0, 0.0]
        monkeypatch.setattr(rr, "_embedder_factory", lambda: _Emb())
        store = _FakeStore(_points_for_audit())
        report = rr.reembed_flagged(store)
        assert report["reembedded"] == 1
        assert len(report["failed"]) == 1
        assert "hiccup" in report["failed"][0]["error"]

    def test_upsert_batching(self, monkeypatch):
        _pin_factory(monkeypatch)
        pts = [_pt(i, f"hash {i}", _E().hash_fallback_vector(f"hash {i}"))
               for i in range(1, 8)]
        store = _FakeStore(pts)
        flushes = []
        orig = store.upsert

        def _spy(points):
            flushes.append(len(points))
            orig(points)
        store.upsert = _spy
        rr.reembed_flagged(store, upsert_batch=3)
        assert sum(flushes) == 7   # every flagged point upserted
        assert max(flushes) <= 3   # batch cap honoured
        assert flushes == [3, 3, 1]


# ── 4. gui/api.py wrappers ───────────────────────────────────────────

class TestGuiApiWrappers:

    def test_audit_wrapper_returns_report(self, monkeypatch):
        import gui.api as gam
        api = gam.Api.__new__(gam.Api)
        monkeypatch.setattr(gam.Api, "_get_store",
                            lambda self: _FakeStore(_points_for_audit()),
                            raising=False)
        report = api.audit_embedding_backends()
        assert report["ok"] is True and report["scanned"] == 5

    def test_audit_wrapper_error_envelope(self, monkeypatch):
        import gui.api as gam
        api = gam.Api.__new__(gam.Api)

        def _boom(self):
            raise RuntimeError("no store")
        monkeypatch.setattr(gam.Api, "_get_store", _boom, raising=False)
        report = api.audit_embedding_backends()
        assert "error" in report

    def test_reembed_wrapper_returns_report(self, monkeypatch):
        import gui.api as gam
        api = gam.Api.__new__(gam.Api)
        _pin_factory(monkeypatch)
        monkeypatch.setattr(gam.Api, "_get_store",
                            lambda self: _FakeStore(_points_for_audit()),
                            raising=False)
        report = api.reembed_hash_points()
        assert report["ok"] is True and report["reembedded"] == 2


# ── 5. cli.py + gui surface wiring (source guards) ───────────────────

class TestWiring:

    def test_cli_parsers_registered(self):
        src = open("cli.py", encoding="utf-8").read()
        assert '"audit-backends"' in src
        assert '"reembed-hash"' in src
        assert "_handle_audit_backends" in src
        assert "_handle_reembed_hash" in src
        assert src.count('args.command == "audit-backends"') == 1
        assert src.count('args.command == "reembed-hash"') == 1

    def test_gui_api_surface_names(self):
        import gui.api as gam
        for name in ("audit_embedding_backends", "reembed_hash_points",
                     "embedding_watchdog_status", "_start_embed_watchdog"):
            assert callable(getattr(gam.Api, name, None)), name

    def test_index_html_has_buttons(self):
        src = open("gui/assets/index.html", encoding="utf-8").read()
        assert "admin-audit-backends" in src
        assert "admin-reembed-hash" in src

    def test_app_js_has_handlers_and_i18n(self):
        src = open("gui/assets/app.js", encoding="utf-8").read()
        assert "adminAuditBackends" in src
        assert "adminReembedHash" in src
        assert "audit_embedding_backends" in src
        assert "reembed_hash_points" in src
        # four locales carry the new keys
        assert src.count("auditBackends:") == 4
        assert src.count("reembedHash:") == 4
        assert src.count("confirmReembed:") == 4


# ── 6. module-level helpers ──────────────────────────────────────────

class TestHelpers:

    def test_same_vector_tolerance(self):
        a = [0.1] * 64
        b = [x + 1e-9 for x in a]
        assert rr._same_vector(a, b) is True
        assert rr._same_vector(
            list(range(64)), list(range(32))) is False

    def test_same_vector_rejects_shape_mismatch(self):
        assert rr._same_vector([1.0], [1.0, 2.0]) is False
        assert rr._same_vector(None, [1.0]) is False

    def test_point_to_dict_named_vectors(self):
        p = types.SimpleNamespace(
            id=7, payload={"text": "t"},
            vector={"": [0.1, 0.2],
                    "bm25": {"indices": [0], "values": [1.0]}})
        d = rr._point_to_dict(p)
        assert d["id"] == "7" and d["vector"] == [0.1, 0.2]

    def test_point_to_dict_sparse_only_leg_skipped(self):
        p = types.SimpleNamespace(
            id=8, payload={"text": "t"},
            vector={"bm25": {"indices": [0], "values": [1.0]}})
        assert rr._point_to_dict(p)["vector"] is None

    def test_require_store_guard(self):
        assert rr._require_store(None)["ok"] is False
        assert rr._require_store(_FakeStore([])) == {}
        bad = _FakeStore([])
        bad.client = None
        assert rr._require_store(bad)["ok"] is False

    def test_audit_or_reembed_dispatch(self, monkeypatch):
        _pin_factory(monkeypatch)
        store = _FakeStore(_points_for_audit())
        audit = rr.audit_or_reembed(store)
        assert "reembedded" not in audit  # audit never writes
        assert audit["ok"] is True
        repair = rr.audit_or_reembed(store, reembed=True, limit=1)
        assert repair["reembedded"] == 1

    def test_no_store_envelope(self, monkeypatch):
        store = _FakeStore([])
        store.client = None
        assert rr.audit_or_reembed(store).get("ok") is False


if __name__ == "__main__":
    import unittest
    unittest.main()