"""v0.9.86.0 — Agentic-RAG slice ①: the query-rewrite gate.

ONE jev_noul decides whether the question needs a retrieval-optimised
rewrite (shadow = compute+log only); on + p ≥ prob runs ONE cheap LLM
rewrite. Every failure keeps the ORIGINAL query (fail-open).
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest


class TestResolvers:
    def test_mode_default_and_invalid(self, monkeypatch):
        from src.agent.query_rewrite import rewrite_mode

        monkeypatch.delenv("QDRANT_REWRITE", raising=False)
        assert rewrite_mode() == "off"
        monkeypatch.setenv("QDRANT_REWRITE", "shadow")
        assert rewrite_mode() == "shadow"
        monkeypatch.setenv("QDRANT_REWRITE", "on")
        assert rewrite_mode() == "on"
        monkeypatch.setenv("QDRANT_REWRITE", "wat")
        assert rewrite_mode() == "off"

    def test_prob_default_and_clamp(self, monkeypatch):
        from src.agent.query_rewrite import _rewrite_prob

        monkeypatch.delenv("QDRANT_REWRITE_PROB", raising=False)
        assert _rewrite_prob() == 0.6
        monkeypatch.setenv("QDRANT_REWRITE_PROB", "0.9")
        assert _rewrite_prob() == 0.9
        monkeypatch.setenv("QDRANT_REWRITE_PROB", "7")
        assert _rewrite_prob() == 0.6
        monkeypatch.setenv("QDRANT_REWRITE_PROB", "abc")
        assert _rewrite_prob() == 0.6

    def test_model_default_and_override(self, monkeypatch):
        from src.agent.query_rewrite import _rewrite_model

        monkeypatch.delenv("QDRANT_REWRITE_MODEL", raising=False)
        assert _rewrite_model() == "openai/gpt-4o-mini"
        monkeypatch.setenv("QDRANT_REWRITE_MODEL", "openai/gpt-4o")
        assert _rewrite_model() == "openai/gpt-4o"


class TestGate:
    @staticmethod
    def _rw():
        from src.agent.query_rewrite import maybe_rewrite

        return maybe_rewrite

    def test_off_skips_jev(self, monkeypatch):
        from src.agent.query_rewrite import maybe_rewrite

        monkeypatch.delenv("QDRANT_REWRITE", raising=False)
        monkeypatch.setattr(
            "src.agent.rerank.jev_noul",
            lambda *a, **k: (_ for _ in ()).throw(
                AssertionError("off must not call jev")))
        q, meta = maybe_rewrite("這是一段很口語的長問題嗎？請幫我檢查")
        assert q == "這是一段很口語的長問題嗎？請幫我檢查"
        assert meta["skipped"]

    def test_short_query_skips_jev(self, monkeypatch):
        monkeypatch.setenv("QDRANT_REWRITE", "on")
        monkeypatch.setattr(
            "src.agent.rerank.jev_noul",
            lambda *a, **k: (_ for _ in ()).throw(
                AssertionError("short queries skip the gate")))
        q, meta = self._rw()("2330")
        assert q == "2330"
        assert meta["skipped"]

    def test_shadow_computes_but_keeps_original(self, monkeypatch):
        monkeypatch.setenv("QDRANT_REWRITE", "shadow")
        monkeypatch.setattr("src.agent.rerank.jev_noul",
                            lambda *a, **k: (0.9, {"elapsed_s": 0.2}))
        monkeypatch.setattr(
            "src.agent.query_rewrite._llm_rewrite",
            lambda q: "中華電信 董事長")
        q, meta = self._rw()("請問一下現在的中華電信董事長是誰啊？")
        assert q == "請問一下現在的中華電信董事長是誰啊？"  # NOT applied
        assert meta["rewritten"] == "中華電信 董事長"
        assert meta["mode"] == "shadow"

    def test_on_applies_rewrite(self, monkeypatch):
        monkeypatch.setenv("QDRANT_REWRITE", "on")
        monkeypatch.setattr("src.agent.rerank.jev_noul",
                            lambda *a, **k: (0.9, {"elapsed_s": 0.2}))
        monkeypatch.setattr("src.agent.query_rewrite._llm_rewrite",
                            lambda q: "中華電信 董事長")
        q, meta = self._rw()("請問一下現在的中華電信董事長是誰啊？")
        assert q == "中華電信 董事長"
        assert meta["fired"] is True

    def test_jev_low_confidence_keeps_original(self, monkeypatch):
        monkeypatch.setenv("QDRANT_REWRITE", "on")
        monkeypatch.setattr("src.agent.rerank.jev_noul",
                            lambda *a, **k: (0.3, {"elapsed_s": 0.2}))

        def _boom(*a, **k):
            raise AssertionError("LLM must not run when p < prob")

        monkeypatch.setattr("src.agent.query_rewrite._llm_rewrite", _boom)
        q, meta = self._rw()("這是一段很口語的長問題嗎？請幫我檢查看看")
        assert "請問" not in meta and q.startswith("這是一段")

    def test_jev_failure_fails_open(self, monkeypatch):
        monkeypatch.setenv("QDRANT_REWRITE", "on")
        monkeypatch.setattr("src.agent.rerank.jev_noul",
                            lambda *a, **k: (None, {"error": "402"}))
        q, meta = self._rw()("這是一段很口語的長問題嗎？請幫我檢查看看")
        assert meta.get("error") == "jev-failed"
        assert q.startswith("這是一段")

    def test_llm_rewrite_failure_keeps_original(self, monkeypatch):
        monkeypatch.setenv("QDRANT_REWRITE", "on")
        monkeypatch.setattr("src.agent.rerank.jev_noul",
                            lambda *a, **k: (0.9, {"elapsed_s": 0.2}))
        monkeypatch.setattr("src.agent.query_rewrite._llm_rewrite",
                            lambda q: "")
        q, meta = self._rw()("這是一段很口語的長問題嗎？請幫我檢查看看")
        assert not meta["fired"]
        assert q.startswith("這是一段")


class TestRetrieverHook:
    def test_retriever_calls_gate(self, monkeypatch):
        from unittest.mock import patch

        from src.vector_store.retriever import Retriever

        r = Retriever()
        # stub the store so no Qdrant is touched
        r.store = MagicMock()
        r.store.search.return_value = []
        monkeypatch.setattr(
            "src.agent.query_rewrite.maybe_rewrite",
            lambda q: ("rewritten-query", {"mode": "on", "fired": True}))
        embed_calls: list[str] = []

        with patch("src.ingest.embedder.Embedder") as _Emb:
            _Emb.return_value.embed.side_effect = (
                lambda q: embed_calls.append(q) or [0.0] * 8)
            r.search("任何問題")
        assert getattr(r, "last_rewrite_meta", {}).get("fired") is True
        assert embed_calls == ["rewritten-query"]  # embed got the NEW q