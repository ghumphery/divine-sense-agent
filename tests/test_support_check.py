"""v0.9.86.0 — Agentic-RAG slice ②: the answer-support check.

For answers that used RAG context this turn (memory hits OR a
search_documents dispatch): ONE jev_noul asks whether the answer
contains claims the retrieved content does NOT support; on + p ≥ prob
runs ONE no-tools corrective create that rewrites the answer strictly
from the retrieved context. Shadow logs only; failures keep the
original answer (fail-open).
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest


@pytest.fixture
def agent(tmp_path, monkeypatch):
    monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path))
    from src.agent.agent import Agent

    a = Agent()
    a._provider = "stub"
    a._model = "stub-model"
    a._mcp_manager = MagicMock()
    a.last_tool_dispatches = ["search_documents"]
    a._last_memory_hits = []
    a.last_usage = None
    return a


def _mk_msgs():
    return [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "[檢索內容...] 問題"},
    ]


class TestResolvers:
    def test_mode_default_and_invalid(self, monkeypatch):
        from src.agent.agent import _support_check_mode

        monkeypatch.delenv("QDRANT_SUPPORT_CHECK", raising=False)
        assert _support_check_mode() == "off"
        monkeypatch.setenv("QDRANT_SUPPORT_CHECK", "shadow")
        assert _support_check_mode() == "shadow"
        monkeypatch.setenv("QDRANT_SUPPORT_CHECK", "on")
        assert _support_check_mode() == "on"
        monkeypatch.setenv("QDRANT_SUPPORT_CHECK", "wat")
        assert _support_check_mode() == "off"

    def test_prob_default_and_clamp(self, monkeypatch):
        from src.agent.agent import _support_check_prob

        monkeypatch.delenv("QDRANT_SUPPORT_CHECK_PROB", raising=False)
        assert _support_check_prob() == 0.7
        monkeypatch.setenv("QDRANT_SUPPORT_CHECK_PROB", "0.85")
        assert _support_check_prob() == 0.85
        monkeypatch.setenv("QDRANT_SUPPORT_CHECK_PROB", "5")
        assert _support_check_prob() == 0.7
        monkeypatch.setenv("QDRANT_SUPPORT_CHECK_PROB", "abc")
        assert _support_check_prob() == 0.7


class TestGate:
    def test_off_never_asks_jev(self, agent, monkeypatch):
        monkeypatch.delenv("QDRANT_SUPPORT_CHECK", raising=False)
        monkeypatch.setattr(
            "src.agent.rerank.jev_noul",
            lambda *a, **k: (_ for _ in ()).throw(
                AssertionError("off must not call jev")))
        out = agent._check_answer_support("q", "答案", _mk_msgs())
        assert out == "答案"

    def test_no_rag_context_skips(self, agent, monkeypatch):
        monkeypatch.setenv("QDRANT_SUPPORT_CHECK", "on")
        agent.last_tool_dispatches = ["web_search"]  # no RAG this turn
        agent._last_memory_hits = []
        monkeypatch.setattr(
            "src.agent.rerank.jev_noul",
            lambda *a, **k: (_ for _ in ()).throw(
                AssertionError("no RAG context → no gate")))
        out = agent._check_answer_support("q", "答案", _mk_msgs())
        assert out == "答案"

    def test_memory_hits_also_count_as_rag(self, agent, monkeypatch):
        monkeypatch.setenv("QDRANT_SUPPORT_CHECK", "on")
        agent.last_tool_dispatches = []           # no search_documents
        agent._last_memory_hits = [{"id": "m1"}]  # but memory was used
        monkeypatch.setattr("src.agent.rerank.jev_noul",
                            lambda *a, **k: (0.2, {"elapsed_s": 0.2}))
        fake = MagicMock()
        fake.chat.completions.create.side_effect = AssertionError(
            "support OK → no corrective")
        agent._client = fake
        out = agent._check_answer_support(
            "q", "依記憶：偏好台股 ETF", _mk_msgs())
        assert out == "依記憶：偏好台股 ETF"

    def test_shadow_logs_without_corrective(self, agent, monkeypatch):
        monkeypatch.setenv("QDRANT_SUPPORT_CHECK", "shadow")
        monkeypatch.setattr("src.agent.rerank.jev_noul",
                            lambda *a, **k: (0.95, {"elapsed_s": 0.2}))
        fake = MagicMock()
        fake.chat.completions.create.side_effect = AssertionError(
            "shadow must not correct")
        agent._client = fake
        out = agent._check_answer_support("q", "答案可能混入舊資料",
                                          _mk_msgs())
        assert out == "答案可能混入舊資料"

    def test_on_runs_corrective(self, agent, monkeypatch):
        monkeypatch.setenv("QDRANT_SUPPORT_CHECK", "on")
        monkeypatch.setattr("src.agent.rerank.jev_noul",
                            lambda *a, **k: (0.95, {"elapsed_s": 0.2}))
        agent._create_validated = MagicMock(return_value=(
            MagicMock(choices=[MagicMock(message=MagicMock(
                content="僅依檢索內容的重寫答案"))]), True))
        out = agent._check_answer_support("q", "含未受支持斷言的答案",
                                          _mk_msgs())
        assert out == "僅依檢索內容的重寫答案"
        # the corrective create ran with the NO-TOOLS note appended
        corr_msgs = agent._create_validated.call_args.kwargs["messages"]
        assert any("僅依據上述檢索內容" in str(m.get("content") or "")
                   for m in corr_msgs)
        assert agent._create_validated.call_args.kwargs.get(
            "tools") is None

    def test_on_low_confidence_no_corrective(self, agent, monkeypatch):
        monkeypatch.setenv("QDRANT_SUPPORT_CHECK", "on")
        monkeypatch.setattr("src.agent.rerank.jev_noul",
                            lambda *a, **k: (0.3, {"elapsed_s": 0.2}))
        agent._create_validated = MagicMock(
            side_effect=AssertionError("must not correct"))
        out = agent._check_answer_support("q", "一致的答案", _mk_msgs())
        assert out == "一致的答案"

    def test_jev_failure_fails_open(self, agent, monkeypatch):
        monkeypatch.setenv("QDRANT_SUPPORT_CHECK", "on")
        monkeypatch.setattr("src.agent.rerank.jev_noul",
                            lambda *a, **k: (None, {"error": "402"}))
        agent._create_validated = MagicMock(
            side_effect=AssertionError("must not correct"))
        out = agent._check_answer_support("q", "答案", _mk_msgs())
        assert out == "答案"

    def test_corrective_failure_keeps_original(self, agent, monkeypatch):
        monkeypatch.setenv("QDRANT_SUPPORT_CHECK", "on")
        monkeypatch.setattr("src.agent.rerank.jev_noul",
                            lambda *a, **k: (0.95, {"elapsed_s": 0.2}))
        agent._create_validated = MagicMock(side_effect=RuntimeError("down"))
        original = "原始答案"
        out = agent._check_answer_support("q", original, _mk_msgs())
        assert out == original

    def test_corrective_empty_keeps_original(self, agent, monkeypatch):
        monkeypatch.setenv("QDRANT_SUPPORT_CHECK", "on")
        monkeypatch.setattr("src.agent.rerank.jev_noul",
                            lambda *a, **k: (0.95, {"elapsed_s": 0.2}))
        agent._create_validated = MagicMock(
            return_value=(MagicMock(choices=[MagicMock(
                message=MagicMock(content=""))]), True))
        out = agent._check_answer_support("q", "原始答案", _mk_msgs())
        assert out == "原始答案"