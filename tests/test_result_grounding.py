"""Tests for the result grounding guard (v0.9.94.0).

防 fake calling 終線：每次成功派發的工具結果以 uuid 存證；結論時
確定性快篩（答案含存證特徵詞即通過，零 Jev 成本）→ 可疑才一次 Jev
驗證 → 未 grounding 就無工具重寫（僅依存證內容）。
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path))
    for k in ("QDRANT_RESULT_GROUNDING", "QDRANT_RESULT_GROUNDING_PROB",
              "QDRANT_ROUTE", "QDRANT_TIME_ROUTE", "QDRANT_REWRITE",
              "QDRANT_SUPPORT_CHECK", "QDRANT_CALENDAR_GUARD"):
        monkeypatch.setenv(k, "off")
    yield


def _agent(monkeypatch, mode="on"):
    monkeypatch.setenv("QDRANT_RESULT_GROUNDING", mode)
    from src.agent.agent import Agent

    a = Agent()
    a._client = MagicMock()
    a._model = "stub-model"
    a._provider = "mock"
    a._mcp_manager = MagicMock()
    a.last_tool_dispatches = []
    a._last_turn_question = ""
    a._calendar_guard_ran = False
    a._turn_result_refs = {}
    a._last_memory_hits = []
    return a


class TestRegistry:
    def test_successful_dispatch_stamps_uuid(self, monkeypatch):
        from src.agent.agent import Agent
        from src.mcp_client.base import McpCallResult

        a = _agent(monkeypatch)
        mgr = MagicMock()
        mgr.call.return_value = McpCallResult(
            text='{"symbol":"2330","price":1055.0}', is_error=False)
        a._mcp_manager = mgr
        call = MagicMock()
        call.function.name = "stock_get_quote"
        call.function.arguments = json.dumps({"symbol": "2330"})
        call.id = "c1"
        a._run_tool_call(call)
        assert len(a._turn_result_refs) == 1
        ref = next(iter(a._turn_result_refs))
        assert a._turn_result_refs[ref]["tool"] == "stock_get_quote"
        assert "1055.0" in a._turn_result_refs[ref]["tokens"]

    def test_error_result_not_stamped(self, monkeypatch):
        from src.agent.agent import Agent
        from src.mcp_client.base import McpCallResult

        a = _agent(monkeypatch)
        mgr = MagicMock()
        mgr.call.return_value = McpCallResult(
            text="boom", is_error=True)
        a._mcp_manager = mgr
        call = MagicMock()
        call.function.name = "x"
        call.function.arguments = "{}"
        call.id = "c1"
        a._run_tool_call(call)
        assert a._turn_result_refs == {}


class TestGroundingGuard:
    def _refs(self):
        return {
            "abcd1234": {
                "tool": "stock_get_quote",
                "args": {"symbol": "2330"},
                "text": '{"symbol":"2330","price":1055.0}',
                "tokens": ["1055.0", "2026-09-23", "2330"],
            },
        }

    def test_fast_path_answer_carries_token(self, monkeypatch):
        import src.agent.rerank as rerank_mod

        a = _agent(monkeypatch)
        a._turn_result_refs = self._refs()
        calls = []
        monkeypatch.setattr(
            rerank_mod, "jev_noul",
            lambda *a2, **k: calls.append(1) or (0.1, {}))
        out = a._result_grounding_guard(
            "台積電現在 1055.0 元", [], 0.3)
        assert out == "台積電現在 1055.0 元"
        assert not calls  # deterministic fast path, no Jev

    def test_mode_off_skips(self, monkeypatch):
        import src.agent.rerank as rerank_mod

        a = _agent(monkeypatch, mode="off")
        a._turn_result_refs = self._refs()
        monkeypatch.setattr(
            rerank_mod, "jev_noul",
            lambda *a2, **k: (_ for _ in ()).throw(
                AssertionError("off must not call Jev")))
        out = a._result_grounding_guard("我記得是 1000 元", [], 0.3)
        assert out == "我記得是 1000 元"

    def test_shadow_runs_jev_no_rewrite(self, monkeypatch):
        import src.agent.rerank as rerank_mod

        a = _agent(monkeypatch, mode="shadow")
        a._turn_result_refs = self._refs()
        monkeypatch.setattr(
            rerank_mod, "jev_noul", lambda *a2, **k: (0.2, {}))
        out = a._result_grounding_guard("我記得是 1000 元", [], 0.3)
        assert out == "我記得是 1000 元"  # shadow = log only

    def test_ungrounded_triggers_rewrite(self, monkeypatch):
        import src.agent.rerank as rerank_mod

        a = _agent(monkeypatch)
        a._turn_result_refs = self._refs()
        monkeypatch.setattr(rerank_mod, "jev_noul",
                            lambda *a2, **k: (0.2, {}))
        resp = MagicMock()
        resp.choices = [MagicMock(message=MagicMock(
            content="依工具結果：2330 現價 1055.0 元"))]
        a._create_validated = MagicMock(return_value=(resp, True))
        out = a._result_grounding_guard("我記得是 1000 元", [], 0.3)
        assert "1055.0" in out
        a._create_validated.assert_called_once()

    def test_grounded_jev_pass_keeps_answer(self, monkeypatch):
        import src.agent.rerank as rerank_mod

        a = _agent(monkeypatch)
        a._turn_result_refs = self._refs()
        monkeypatch.setattr(rerank_mod, "jev_noul",
                            lambda *a2, **k: (0.9, {}))
        out = a._result_grounding_guard("看起來不錯", [], 0.3)
        assert out == "看起來不錯"

    def test_jev_failure_keeps_answer(self, monkeypatch):
        import src.agent.rerank as rerank_mod

        a = _agent(monkeypatch)
        a._turn_result_refs = self._refs()
        monkeypatch.setattr(
            rerank_mod, "jev_noul",
            lambda *a2, **k: (_ for _ in ()).throw(RuntimeError("x")))
        out = a._result_grounding_guard("我記得是 1000 元", [], 0.3)
        assert out == "我記得是 1000 元"

    def test_no_refs_skips(self, monkeypatch):
        import src.agent.rerank as rerank_mod

        a = _agent(monkeypatch)
        monkeypatch.setattr(
            rerank_mod, "jev_noul",
            lambda *a2, **k: (_ for _ in ()).throw(
                AssertionError("no refs must not call Jev")))
        out = a._result_grounding_guard("我記得是 1000 元", [], 0.3)
        assert out == "我記得是 1000 元"

    def test_resets_at_both_entries(self):
        from pathlib import Path

        src = Path("src/agent/agent.py").read_text(encoding="utf-8")
        assert src.count("self._turn_result_refs = {}") >= 3