"""v0.9.87.0 — hybrid search (dense + BM25 sparse RRF fusion) tests.

All Qdrant interaction is mock-driven (no live server): the tokenizer,
the sparse-vector derivation, the collection-schema gate, the fused
query_points body, the ingest vector dict, and the backfill walk.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    import src.config  # noqa: F401 — load_dotenv re-injection guard
    for k in ("QDRANT_HYBRID",):
        monkeypatch.delenv(k, raising=False)
    yield


class TestTokenizer:
    def test_latin_words_lowercased(self):
        from src.vector_store.sparse import tokenize

        toks = tokenize("Hello World 123")
        assert toks == {"hello": 1, "world": 1, "123": 1}

    def test_cjk_bigrams(self):
        from src.vector_store.sparse import tokenize

        toks = tokenize("台積電")
        assert toks["台積"] == 1 and toks["積電"] == 1
        assert toks["台"] == 1 and toks["積"] == 1 and toks["電"] == 1

    def test_deterministic_and_tf(self):
        from src.vector_store.sparse import tokenize

        a = tokenize("華邦電 2344 華邦電")
        b = tokenize("華邦電 2344 華邦電")
        assert a == b
        assert a["華邦"] == 2 and a["2344"] == 1

    def test_empty_and_punctuation(self):
        from src.vector_store.sparse import tokenize

        assert tokenize("") == {}
        assert tokenize("！？。，") == {}  # CJK punctuation ignored


class TestSparseVector:
    def test_indices_in_24bit_space_and_tf_weights(self):
        from src.vector_store.sparse import sparse_vector

        idx, vals = sparse_vector("華邦電 2344")
        assert all(0 <= i < 2 ** 24 for i in idx)
        assert len(idx) == len(vals)
        # tokens: 3 CJK singles + 2 bigrams + 1 latin → 6, all tf=1
        assert len(idx) == 6
        assert sorted(vals) == [1.0] * 6

    def test_deterministic(self):
        from src.vector_store.sparse import sparse_vector

        assert sparse_vector("中纖 1718") == sparse_vector("中纖 1718")

    def test_from_payload_prefers_text_keys(self):
        from src.vector_store.sparse import (
            sparse_from_payload, sparse_vector,
        )

        assert sparse_from_payload(
            {"symbol": "2344", "text": "華邦電"}) == sparse_vector("華邦電")
        assert sparse_from_payload({"message": "你好世界"}) == \
            sparse_vector("你好世界")
        assert sparse_from_payload({"symbol": "2344"}) == ([], [])

    def test_make_sparse_config_uses_idf(self):
        from qdrant_client.http import models as qmodels

        from src.vector_store.sparse import (
            SPARSE_FIELD, make_sparse_config,
        )

        cfg = make_sparse_config()
        assert isinstance(cfg, qmodels.SparseVectorParams)
        assert cfg.modifier == qmodels.Modifier.IDF
        assert SPARSE_FIELD == "bm25"


class TestSchemaGate:
    def test_collection_has_sparse_true(self):
        """The REAL qdrant-client shape: CollectionInfo.config.params.
        sparse_vectors (verified live 2026-09-22 — the first version
        checked config.sparse_vectors_config and always returned
        False, silently disabling fusion)."""
        from src.vector_store.sparse import collection_has_sparse

        info = MagicMock()
        info.config.params.sparse_vectors = {"bm25": MagicMock()}
        client = MagicMock()
        client.get_collection.return_value = info
        assert collection_has_sparse(client, "file_docs") is True

    def test_collection_has_sparse_old_shape_fallback(self):
        from src.vector_store.sparse import collection_has_sparse

        info = MagicMock(spec=[])  # attrs via getattr only
        cfg = MagicMock(spec=["sparse_vectors_config"])
        cfg.sparse_vectors_config = {"bm25": MagicMock()}
        info.config = cfg
        # params missing → getattr returns None → fallback path
        info.config = cfg
        client = MagicMock()
        client.get_collection.return_value = info
        assert collection_has_sparse(client, "file_docs") is True

    def test_collection_has_sparse_false(self):
        from src.vector_store.sparse import collection_has_sparse

        info = MagicMock()
        info.config.params.sparse_vectors = {}
        client = MagicMock()
        client.get_collection.return_value = info
        assert collection_has_sparse(client, "file_docs") is False
        client.get_collection.side_effect = RuntimeError("down")
        assert collection_has_sparse(client, "x") is False


class TestStoreWiring:
    def _store(self, monkeypatch, hybrid, has_sparse=True):
        import os

        if hybrid:
            os.environ["QDRANT_HYBRID"] = "on"
        else:
            os.environ.pop("QDRANT_HYBRID", None)
        from src.vector_store import qdrant_client as qc

        monkeypatch.setattr(qc, "_hybrid_enabled", lambda: hybrid)
        monkeypatch.setattr(
            "src.vector_store.sparse.collection_has_sparse",
            lambda *a, **k: has_sparse)
        s = qc.QdrantStore.__new__(qc.QdrantStore)
        s.collection = "file_docs"
        s.dim = 8
        s._client = MagicMock()
        s._client.query_points.return_value = MagicMock(points=[])
        s._list_cache_value = None
        s._stats_cache_value = None
        return s

    def test_search_fuses_when_hybrid_and_schema(self, monkeypatch):
        s = self._store(monkeypatch, hybrid=True, has_sparse=True)
        s.search([0.0] * 8, top_k=3, query_text="華邦電 2344 收盤價")
        kw = s._client.query_points.call_args.kwargs
        assert kw.get("prefetch") and len(kw["prefetch"]) == 2
        assert kw["prefetch"][0].using == ""
        assert kw["prefetch"][1].using == "bm25"
        assert kw["query"].fusion is not None  # RRF fusion query
        assert kw["limit"] == 3

    def test_search_plain_when_hybrid_off(self, monkeypatch):
        from src.vector_store import qdrant_client as qc

        s = self._store(monkeypatch, hybrid=False, has_sparse=True)
        s.search([0.0] * 8, top_k=3, query_text="華邦電")
        kw = s._client.query_points.call_args.kwargs
        assert "prefetch" not in kw  # legacy dense path
        assert kw["query"] == [0.0] * 8

    def test_search_falls_back_when_schema_lacks_sparse(
        self, monkeypatch,
    ):
        s = self._store(monkeypatch, hybrid=True, has_sparse=False)
        s.search([0.0] * 8, top_k=3, query_text="華邦電")
        kw = s._client.query_points.call_args.kwargs
        assert "prefetch" not in kw  # dense-only (old schema)

    def test_search_fusion_failure_falls_back(self, monkeypatch):
        s = self._store(monkeypatch, hybrid=True, has_sparse=True)

        def _qp(**kw):
            if kw.get("prefetch") is not None:
                raise RuntimeError("fusion 500")
            return MagicMock(points=[])

        s._client.query_points.side_effect = _qp
        out = s.search([0.0] * 8, top_k=3, query_text="華邦電")
        assert out == []  # fused attempt failed → dense fallback ran

    def test_upsert_hybrid_vector_dict(self, monkeypatch):
        s = self._store(monkeypatch, hybrid=True, has_sparse=True)
        s.upsert([{"id": "p1", "vector": [0.1] * 8,
                   "payload": {"text": "華邦電 2344 即時報價"}}])
        pts = s._client.upsert.call_args.kwargs["points"]
        vec = pts[0].vector
        assert isinstance(vec, dict)
        assert list(vec.keys()) == ["", "bm25"]
        assert vec["bm25"].indices

    def test_upsert_dense_only_when_off(self, monkeypatch):
        s = self._store(monkeypatch, hybrid=False, has_sparse=True)
        s.upsert([{"id": "p1", "vector": [0.1] * 8,
                   "payload": {"text": "華邦電"}}])
        vec = s._client.upsert.call_args.kwargs["points"][0].vector
        assert not isinstance(vec, dict)  # legacy list


class TestBackfill:
    def test_requires_sparse_schema(self, monkeypatch):
        from src.vector_store.sparse import backfill

        client = MagicMock()
        client.get_collection.return_value = MagicMock(
            config=MagicMock(sparse_vectors_config={}))
        with pytest.raises(RuntimeError):
            backfill(client, "file_docs")

    def test_backfill_walks_and_updates(self, monkeypatch):
        from src.vector_store.sparse import backfill

        info = MagicMock()
        info.config.params.sparse_vectors = {"bm25": MagicMock()}
        client = MagicMock()
        client.get_collection.return_value = info
        pt1, pt2 = MagicMock(id="a"), MagicMock(id="b")
        pt1.payload = {"text": "華邦電 即時報價"}
        pt2.payload = {"text": "南亞科 財報"}
        client.scroll.side_effect = [([pt1, pt2], "off1"), ([], None)]
        n = backfill(client, "file_docs", batch=10, progress_every=0)
        assert n == 2
        vecs = client.update_vectors.call_args.kwargs["points"]
        assert all(isinstance(v.vector, dict) and "bm25" in v.vector
                   for v in vecs)

    def test_backfill_skips_textless_points(self, monkeypatch):
        from src.vector_store.sparse import backfill

        info = MagicMock()
        info.config.params.sparse_vectors = {"bm25": MagicMock()}
        client = MagicMock()
        client.get_collection.return_value = info
        pt = MagicMock()
        pt.payload = {"symbol": "2344"}  # no text key
        client.scroll.side_effect = [([pt], None)]
        n = backfill(client, "file_docs", progress_every=0)
        assert n == 0
        assert not client.update_vectors.called


class TestCreateSchema:
    def test_recreate_includes_sparse_when_hybrid(self, monkeypatch):
        import os

        os.environ["QDRANT_HYBRID"] = "on"
        from src.vector_store import qdrant_client as qc

        s = qc.QdrantStore.__new__(qc.QdrantStore)
        s.collection = "hybrid_test"
        s.dim = 8
        s._client = MagicMock()
        s._client.collection_exists.return_value = False
        qc.QdrantStore._ensure_collection(s)
        body = s._client.create_collection.call_args.kwargs
        assert "bm25" in (body.get("sparse_vectors_config") or {})

    def test_create_dense_only_when_off(self, monkeypatch):
        import os

        os.environ.pop("QDRANT_HYBRID", None)
        from src.vector_store import qdrant_client as qc

        s = qc.QdrantStore.__new__(qc.QdrantStore)
        s.collection = "x"
        s.dim = 8
        s._client = MagicMock()
        s._client.collection_exists.return_value = False
        qc.QdrantStore._ensure_collection(s)
        body = s._client.create_collection.call_args.kwargs
        assert not (body.get("sparse_vectors_config") or {})