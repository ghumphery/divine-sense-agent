"""Jev rerank tests (v0.9.83.0) — RerankClient contract + Retriever
widen/rerank/slice wiring. All HTTP is stubbed; no network in CI."""
import json
import urllib.error

import pytest

import src.agent.rerank as rr

_ENV_VARS = ("QDRANT_RERANK", "QDRANT_RERANK_BACKEND",
             "QDRANT_RERANK_CANDIDATES", "QDRANT_RERANK_TIMEOUT_S",
             "QDRANT_RERANK_CHUNK_CHARS", "JEV_MODEL",
             "QDRANT_RERANK_MIN_PROB", "OPENROUTER_API_KEY")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """The live .env (loaded at src.config import) sets QDRANT_RERANK=on
    — wipe the whole rerank namespace so each test starts neutral.

    src.config MUST be imported HERE, before the delenv loop: the first
    ``import src.vector_store.retriever`` mid-test would trigger the
    config→load_dotenv injection (v0.9.81.1 lesson) and the OFF test
    would see the live ``QDRANT_RERANK=on``."""
    import src.config  # noqa: F401 — .env loads BEFORE the wipe
    for name in _ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def _chunks(n):
    return [{"id": "c%d" % i, "text": "chunk %d body" % i,
             "score": 1.0 - i / (n + 1)} for i in range(n)]


def _fake_urlopen(monkeypatch, payload=None, exc=None):
    class _Resp:
        def read(self):
            return json.dumps(payload or {}).encode("utf-8")

    class _Ctx:
        def __enter__(self):
            return _Resp()

        def __exit__(self, *a):
            return False

    seen = {}

    def fake(req, timeout=None):
        seen["url"] = req.full_url
        seen["headers"] = dict(req.header_items())
        seen["body"] = json.loads(req.data.decode("utf-8"))
        seen["timeout"] = timeout
        if exc is not None:
            raise exc
        return _Ctx()

    monkeypatch.setattr(rr.urllib.request, "urlopen", fake)
    return seen


class TestResolvers:
    def test_off_by_default(self):
        assert rr.rerank_enabled() is False
        assert rr.RerankClient().enabled() is False

    @pytest.mark.parametrize("v", ["on", "TRUE", "1", "Yes"])
    def test_on_values(self, monkeypatch, v):
        monkeypatch.setenv("QDRANT_RERANK", v)
        assert rr.rerank_enabled() is True

    def test_backend_default_jev(self, monkeypatch):
        monkeypatch.setenv("QDRANT_RERANK", "on")
        c = rr.RerankClient()
        assert c.backend() == "jev" and c.enabled() is True

    def test_nimble_rejected(self, monkeypatch):
        monkeypatch.setenv("QDRANT_RERANK", "on")
        monkeypatch.setenv("QDRANT_RERANK_BACKEND", "nimble")
        assert rr.RerankClient().enabled() is False

    def test_candidates_default_and_clamp(self, monkeypatch):
        c = rr.RerankClient()
        assert c.candidates() == 20
        monkeypatch.setenv("QDRANT_RERANK_CANDIDATES", "7")
        assert c.candidates() == 7
        monkeypatch.setenv("QDRANT_RERANK_CANDIDATES", "abc")
        assert c.candidates() == 20
        monkeypatch.setenv("QDRANT_RERANK_CANDIDATES", "1")
        assert c.candidates() == 20  # below floor 2
        monkeypatch.setenv("QDRANT_RERANK_CANDIDATES", "999")
        assert c.candidates() == 20  # above ceiling 50
        monkeypatch.setenv("QDRANT_RERANK_CANDIDATES", "50")
        assert c.candidates() == 50

    def test_timeout_chunk_chars_model(self, monkeypatch):
        c = rr.RerankClient()
        assert c.timeout_s() == 10.0 and c.chunk_chars() == 600
        assert c.model() == "typesafe/jev-1.13"
        monkeypatch.setenv("QDRANT_RERANK_TIMEOUT_S", "3")
        monkeypatch.setenv("QDRANT_RERANK_CHUNK_CHARS", "250")
        monkeypatch.setenv("JEV_MODEL", "jev-1.13.0")
        assert c.timeout_s() == 3.0 and c.chunk_chars() == 250
        assert c.model() == "jev-1.13.0"
        monkeypatch.setenv("QDRANT_RERANK_TIMEOUT_S", "999")
        assert c.timeout_s() == 10.0

    def test_min_prob_resolver(self, monkeypatch):
        c = rr.RerankClient()
        assert c.min_prob() == 0.3          # default gate threshold
        monkeypatch.setenv("QDRANT_RERANK_MIN_PROB", "0.5")
        assert c.min_prob() == 0.5
        monkeypatch.setenv("QDRANT_RERANK_MIN_PROB", "0")
        assert c.min_prob() == 0.0          # gate disabled
        monkeypatch.setenv("QDRANT_RERANK_MIN_PROB", "abc")
        assert c.min_prob() == 0.3
        monkeypatch.setenv("QDRANT_RERANK_MIN_PROB", "-1")
        assert c.min_prob() == 0.3          # out of range → default
        monkeypatch.setenv("QDRANT_RERANK_MIN_PROB", "2.0")
        assert c.min_prob() == 0.3
        monkeypatch.setenv("QDRANT_RERANK_MIN_PROB", "1")
        assert c.min_prob() == 1.0


class TestRequestShape:
    def test_body_url_headers(self, monkeypatch):
        monkeypatch.setenv("QDRANT_RERANK", "on")
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-x")
        payload = {"answers": {"chunk_1": {"type": "noul", "noul": 0.9},
                               "chunk_2": {"type": "noul", "noul": 0.1},
                               "chunk_3": {"type": "noul", "noul": 0.0}}}
        seen = _fake_urlopen(monkeypatch, payload)
        out, meta = rr.RerankClient().rerank("房屋折舊", _chunks(3))
        assert "openrouter.ai/api/alpha/decisions" in seen["url"]
        hdrs = {k.lower(): v for k, v in seen["headers"].items()}
        assert hdrs.get("authorization") == "Bearer sk-or-x"
        body = seen["body"]
        assert body["model"] == "typesafe/jev-1.13"
        assert body["state"]["query"] == "房屋折舊"
        assert body["state"]["chunks"][0].startswith("1. chunk 0")
        assert len(body["questions"]) == 3
        assert body["questions"]["chunk_2"]["type"] == "noul"
        assert "編號 2" in body["questions"]["chunk_2"]["instructions"]
        # sorted by noul desc: c0 (0.9) → c1 (0.1) → c2 (0.0)
        assert [o["id"] for o in out] == ["c0", "c1", "c2"]
        assert out[0]["rerank_score"] == 0.9
        assert meta["reranked"] is True

    def test_chunk_truncation(self, monkeypatch):
        monkeypatch.setenv("QDRANT_RERANK", "on")
        monkeypatch.setenv("OPENROUTER_API_KEY", "k")
        monkeypatch.setenv("QDRANT_RERANK_CHUNK_CHARS", "150")
        seen = _fake_urlopen(monkeypatch, {"answers": {}})
        rr.RerankClient().rerank("q", [{"text": "x" * 500}, {"text": "b"}])
        assert len(seen["body"]["state"]["chunks"][0]) <= 153  # "1. " + 150

    def test_single_candidate_passthrough(self, monkeypatch):
        monkeypatch.setenv("QDRANT_RERANK", "on")
        monkeypatch.setenv("OPENROUTER_API_KEY", "k")
        seen = _fake_urlopen(monkeypatch)
        chunks = _chunks(1)
        out, meta = rr.RerankClient().rerank("q", chunks)
        assert seen == {} and meta["reranked"] is False
        assert out == chunks

    def test_missing_answer_counts_zero(self, monkeypatch):
        monkeypatch.setenv("QDRANT_RERANK", "on")
        monkeypatch.setenv("OPENROUTER_API_KEY", "k")
        payload = {"answers": {"chunk_2": {"type": "noul", "noul": 0.8}}}
        seen = _fake_urlopen(monkeypatch, payload)
        out, meta = rr.RerankClient().rerank("q", _chunks(3))
        assert [o["id"] for o in out] == ["c1", "c0", "c2"]
        assert out[2]["rerank_score"] == 0.0
        assert meta["reranked"] is True


class TestFailureFallback:
    def test_http_error_fallback(self, monkeypatch):
        monkeypatch.setenv("QDRANT_RERANK", "on")
        monkeypatch.setenv("OPENROUTER_API_KEY", "k")
        import io
        exc = urllib.error.HTTPError(
            rr._DECISIONS_URL, 402, "payment required", {},
            io.BytesIO(b""))
        seen = _fake_urlopen(monkeypatch, exc=exc)
        chunks = _chunks(3)
        out, meta = rr.RerankClient().rerank("q", chunks)
        assert meta["reranked"] is False
        assert "HTTP 402" in meta["error"]
        assert [o["id"] for o in out] == [c["id"] for c in chunks]

    def test_timeout_fallback(self, monkeypatch):
        monkeypatch.setenv("QDRANT_RERANK", "on")
        monkeypatch.setenv("OPENROUTER_API_KEY", "k")
        seen = _fake_urlopen(monkeypatch, exc=TimeoutError("timed out"))
        out, meta = rr.RerankClient().rerank("q", _chunks(3))
        assert meta["reranked"] is False and meta["error"]
        assert [o["id"] for o in out] == ["c0", "c1", "c2"]

    def test_missing_key_fallback(self, monkeypatch):
        monkeypatch.setenv("QDRANT_RERANK", "on")
        _fake_urlopen(monkeypatch)
        chunks = _chunks(3)
        out, meta = rr.RerankClient().rerank("q", chunks)
        assert meta["error"] == "OPENROUTER_API_KEY missing"
        assert meta["reranked"] is False and out == chunks

    def test_feature_off_passthrough(self, monkeypatch):
        _fake_urlopen(monkeypatch)  # any HTTP call would be a bug
        chunks = _chunks(3)
        out, meta = rr.RerankClient().rerank("q", chunks)
        assert meta["backend"] == "none" and out == chunks

    def test_probability_clamped(self):
        ans = {"chunk_1": {"noul": 1.7}, "chunk_2": {"noul": -0.5}}
        assert rr._noul(ans, "chunk_1") == 1.0
        assert rr._noul(ans, "chunk_2") == 0.0
        assert rr._noul({}, "chunk_9") == 0.0


class TestRetrieverWiring:
    """Retriever.search × RerankClient — widen → rerank → slice."""

    @pytest.fixture(autouse=True)
    def _stub_store_and_embedder(self, monkeypatch):
        import src.vector_store.qdrant_client as qc
        import src.ingest.embedder as emb

        captured = {}
        fake_results = []

        def fake_store_init(self, *a, **k):
            pass

        def fake_store_search(self, query_vector=None, top_k=5,
                              filename_filter=None, score_threshold=None, query_text=None):
            captured["top_k"] = top_k
            return [dict(c) for c in fake_results][:top_k]

        def fake_embed(self, query):
            return [0.1] * 8

        monkeypatch.setattr(qc.QdrantStore, "__init__", fake_store_init)
        monkeypatch.setattr(qc.QdrantStore, "search", fake_store_search)
        monkeypatch.setattr(emb.Embedder, "embed", fake_embed)
        self.captured = captured
        self.results_ref = fake_results

    def _seed(self, n):
        self.results_ref.clear()
        self.results_ref.extend(_chunks(n))

    def test_rerank_off_unchanged(self, monkeypatch):
        import src.vector_store.retriever as rt

        self._seed(5)
        out = rt.Retriever().search("q", top_k=5)
        assert self.captured["top_k"] == 5
        assert [o["id"] for o in out] == ["c0", "c1", "c2", "c3", "c4"]
        assert all("rerank_score" not in o for o in out)

    def test_rerank_on_widens_reranks_slices(self, monkeypatch):
        import src.vector_store.retriever as rt

        monkeypatch.setenv("QDRANT_RERANK", "on")
        monkeypatch.setenv("QDRANT_RERANK_CANDIDATES", "20")
        # this test exercises widen/rerank/slice, not the v0.9.83.1 gate
        # — the stubbed scores are all < 0.3, so disable the gate here
        monkeypatch.setenv("QDRANT_RERANK_MIN_PROB", "0")
        self._seed(20)

        def fake_rerank(self, query, chunks):
            ordered = [dict(c, rerank_score=c["score"])
                       for c in reversed(list(chunks))]
            return ordered, {"reranked": True}

        monkeypatch.setattr(rr.RerankClient, "rerank", fake_rerank)
        out = rt.Retriever().search("q", top_k=5)
        assert self.captured["top_k"] == 20        # widened pool
        assert [o["id"] for o in out] == ["c19", "c18", "c17", "c16",
                                          "c15"]  # reversed + sliced
        assert all("rerank_score" in o for o in out)

    def test_rerank_error_keeps_cosine_order(self, monkeypatch):
        import src.vector_store.retriever as rt

        monkeypatch.setenv("QDRANT_RERANK", "on")
        self._seed(6)

        def boom(self, query, chunks):
            raise RuntimeError("jev down")

        monkeypatch.setattr(rr.RerankClient, "rerank", boom)
        out = rt.Retriever().search("q", top_k=5)
        assert [o["id"] for o in out] == ["c0", "c1", "c2", "c3", "c4"]

    def test_rerank_unsupported_backend_no_widen(self, monkeypatch):
        import src.vector_store.retriever as rt

        monkeypatch.setenv("QDRANT_RERANK", "on")
        monkeypatch.setenv("QDRANT_RERANK_BACKEND", "nimble")
        self._seed(5)
        out = rt.Retriever().search("q", top_k=5)
        assert self.captured["top_k"] == 5  # no widen, no rerank call
        assert [o["id"] for o in out] == ["c0", "c1", "c2", "c3", "c4"]


class TestLowConfidenceGate:
    """v0.9.83.1 — best noul < min_prob → search returns nothing."""

    @pytest.fixture(autouse=True)
    def _stub_store_and_embedder(self, monkeypatch):
        import src.vector_store.qdrant_client as qc
        import src.ingest.embedder as emb

        captured = {}
        fake_results = []

        def fake_store_init(self, *a, **k):
            pass

        def fake_store_search(self, query_vector=None, top_k=5,
                              filename_filter=None, score_threshold=None, query_text=None):
            captured["top_k"] = top_k
            return [dict(c) for c in fake_results][:top_k]

        def fake_embed(self, query):
            return [0.1] * 8

        monkeypatch.setattr(qc.QdrantStore, "__init__", fake_store_init)
        monkeypatch.setattr(qc.QdrantStore, "search", fake_store_search)
        monkeypatch.setattr(emb.Embedder, "embed", fake_embed)
        self.captured = captured
        self.results_ref = fake_results

    def _seed(self, n):
        self.results_ref.clear()
        self.results_ref.extend(_chunks(n))

    def _patch_rerank(self, monkeypatch, scores, reranked=True):
        def fake_rerank(self, query, chunks):
            ordered = [dict(c, rerank_score=s)
                       for c, s in sorted(zip(chunks, scores),
                                          key=lambda p: -p[1])]
            return ordered, {"reranked": reranked, "backend": "jev"}

        monkeypatch.setattr(rr.RerankClient, "rerank", fake_rerank)

    def test_gate_empties_below_threshold(self, monkeypatch):
        import src.vector_store.retriever as rt

        monkeypatch.setenv("QDRANT_RERANK", "on")
        self._seed(5)
        # best = 0.29 < 0.3 → gate trips
        self._patch_rerank(
            monkeypatch, [0.29 - i * 0.05 for i in range(5)])
        retr = rt.Retriever()
        out = retr.search("q", top_k=5)
        assert out == []
        assert retr.last_rerank_meta["gate"] == "low-confidence-empty"
        assert retr.last_rerank_meta["best_score"] == pytest.approx(0.29)

    def test_gate_disabled_with_zero(self, monkeypatch):
        import src.vector_store.retriever as rt

        monkeypatch.setenv("QDRANT_RERANK", "on")
        monkeypatch.setenv("QDRANT_RERANK_MIN_PROB", "0")
        self._seed(5)
        self._patch_rerank(
            monkeypatch, [0.29 - 0.05 * i for i in range(5)])
        retr = rt.Retriever()
        out = retr.search("q", top_k=5)
        assert len(out) == 5 and out[0]["rerank_score"] == pytest.approx(
            0.29)
        assert not retr.last_rerank_meta.get("gate")

    def test_gate_passes_when_top_above_threshold(self, monkeypatch):
        import src.vector_store.retriever as rt

        monkeypatch.setenv("QDRANT_RERANK", "on")
        self._seed(20)
        scores = [0.9] + [0.2] * 19
        self._patch_rerank(monkeypatch, scores)
        retr = rt.Retriever()
        out = retr.search("q", top_k=5)
        assert len(out) == 5 and out[0]["rerank_score"] == pytest.approx(
            0.9)
        assert retr.last_rerank_meta.get("gate") is None

    def test_gate_not_applied_when_rerank_failed(self, monkeypatch):
        import src.vector_store.retriever as rt

        monkeypatch.setenv("QDRANT_RERANK", "on")
        self._seed(5)

        def failed_rerank(self, query, chunks):
            return list(chunks), {"reranked": False, "error": "HTTP 402"}

        monkeypatch.setattr(rr.RerankClient, "rerank", failed_rerank)
        retr = rt.Retriever()
        out = retr.search("q", top_k=5)
        assert [o["id"] for o in out] == ["c0", "c1", "c2", "c3", "c4"]
        assert retr.last_rerank_meta.get("reranked") is False

    def test_gate_kept_off_feature(self):
        import src.vector_store.retriever as rt

        self._seed(5)
        retr = rt.Retriever()
        out = retr.search("q", top_k=5)
        assert out and retr.last_rerank_meta is None


class TestEnvExample:
    def test_env_example_block(self):
        txt = open(".env.example", encoding="utf-8").read()
        for token in ("QDRANT_RERANK=on", "QDRANT_RERANK_CANDIDATES=20",
                      "QDRANT_RERANK_TIMEOUT_S=10",
                      "QDRANT_RERANK_CHUNK_CHARS=600",
                      "JEV_MODEL=typesafe/jev-1.13",
                      "QDRANT_RERANK_MIN_PROB=0.3",
                      "v0.9.83.0"):
            assert token in txt, token