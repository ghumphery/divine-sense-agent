"""Tests for the Jev 分類器 MCP core extension (v0.9.93.0+).

Jev = the typesafe/jev decision model (same backend as rerank);
mock-first: JEV_MODE=mock default = deterministic hash pick with NO
network; live mode calls src.agent.rerank.jev_choice.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path))
    monkeypatch.delenv("JEV_MODE", raising=False)
    monkeypatch.delenv("QDRANT_JEV_CLASSIFY_BATCH_MAX", raising=False)
    monkeypatch.delenv("QDRANT_JEV_CLASSIFY_WORKERS", raising=False)
    monkeypatch.delenv("QDRANT_JEV_CLASSIFY_TIMEOUT_S", raising=False)
    monkeypatch.delenv("QDRANT_FILESYSTEM_WRITE_ROOT", raising=False)
    monkeypatch.delenv("QDRANT_FILESYSTEM_READ_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)
    yield


_REQ = {
    "classes": ["帳單", "會議", "待辦"],
    "items": [
        {"id": "a1", "text": "中華電信 8 月帳單 899 元"},
        {"id": "a2", "text": "週三下午與設計師開會"},
    ],
}


class TestEngineMock:
    def _engine(self, monkeypatch=None):
        if monkeypatch is not None:
            monkeypatch.setenv("JEV_MODE", "mock")
        from src.jev_tools import JevClassifierEngine

        return JevClassifierEngine()

    def test_default_mode_is_mock(self):
        from src.jev_tools import MODE_MOCK, JevClassifierEngine

        e = JevClassifierEngine()
        assert e.mode == MODE_MOCK and e.is_mock

    def test_batch_classify(self, monkeypatch):
        from src.jev_tools import MODE_MOCK

        e = self._engine(monkeypatch)
        out = e.classify_request(_REQ)
        assert out["ok"] is True and out["mode"] == MODE_MOCK
        assert out["total"] == 2 and out["errors"] == 0
        assert [r["id"] for r in out["results"]] == ["a1", "a2"]
        assert all(r["choice"] in ("帳單", "會議", "待辦")
                   for r in out["results"])
        assert sum(out["counts"].values()) == 2

    def test_invalid_requests(self, monkeypatch):
        e = self._engine(monkeypatch)
        r = e.classify_request({"classes": ["a", "a"],
                                "items": [{"text": "x"}]})
        assert "重複" in r["error"]
        r = e.classify_request({"classes": ["a", "b"],
                                "items": [{"text": "x"}],
                                "default_class": "z"})
        assert "default_class" in r["error"]
        r = e.classify_request({"classes": ["a"],
                                "items": [{"text": "x"}]})
        assert "至少需要 2 個類別" in r["error"]
        r = e.classify_request({"classes": ["a", "b"],
                                "items": [{"id": "x"}]})
        assert "缺少 text" in r["error"]
        big = dict(_REQ, items=[{"text": f"n{i}"} for i in range(60)])
        assert "批次上限" in e.classify_request(big)["error"]

    def test_parse_payload(self):
        from src.jev_tools import JevClassifierEngine

        assert "不可同時為空" in JevClassifierEngine.parse_payload(
            "")["error"]
        assert "JSON 解析失敗" in JevClassifierEngine.parse_payload(
            "{bad")["error"]
        assert "必須是物件" in JevClassifierEngine.parse_payload("[1]")["error"]
        d = JevClassifierEngine.parse_payload(json.dumps(_REQ))
        assert d["classes"] == _REQ["classes"]


class TestFileMode:
    def test_classify_file_mode(self, monkeypatch, tmp_path):
        (tmp_path / "sandbox").mkdir()
        monkeypatch.setenv("QDRANT_FILESYSTEM_WRITE_ROOT",
                           str(tmp_path / "sandbox"))
        req = tmp_path / "sandbox" / "classify_req.json"
        req.write_text(json.dumps(
            {"classes": ["帳單", "會議", "待辦"],
             "items": [{"id": "f1", "text": "繳費通知"},
                       {"id": "f2", "text": "週會議程"}]}),
            encoding="utf-8")
        from src.mcp_client.host import JevLocalHost

        host = JevLocalHost()
        out = host._classify({"path": "classify_req.json",
                              "write_result": True})
        assert out["ok"] is True and out["total"] == 2
        assert "result_path" in out and "jev/" in out["result_path"]

    def test_classify_file_not_found(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        from src.mcp_client.host import JevLocalHost

        out = JevLocalHost()._classify({"path": "missing.json"})
        assert "error" in out and "找不到" in out["error"]


class TestLiveMode:
    def _live(self, monkeypatch):
        monkeypatch.setenv("JEV_MODE", "live")
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
        from src.jev_tools import JevClassifierEngine

        return JevClassifierEngine()

    def test_live_uses_jev_choice(self, monkeypatch):
        import src.agent.rerank as rerank_mod

        e = self._live(monkeypatch)
        calls = []

        def fake_choice(ins, state, options, timeout_s=None):
            calls.append(options)
            return ("會議" if "開會" in str(state) else "帳單"), 0.9, {
                "elapsed_s": 0.1, "error": ""}

        monkeypatch.setattr(rerank_mod, "jev_choice", fake_choice)
        import src.jev_tools as jev_mod
        monkeypatch.setattr(jev_mod, "jev_choice", fake_choice)
        out = e.classify_request(_REQ)
        assert out["mode"] == "live"
        assert out["results"][0]["choice"] == "帳單"
        assert out["results"][1]["choice"] == "會議"
        assert out["errors"] == 0
        assert len(calls) == 2

    def test_live_error_contained_per_item(self, monkeypatch):
        import src.agent.rerank as rerank_mod
        import src.jev_tools as jev_mod

        e = self._live(monkeypatch)

        def boom(ins, state, options, timeout_s=None):
            return None, None, {"error": "HTTP 402", "elapsed_s": 0.1}

        monkeypatch.setattr(rerank_mod, "jev_choice", boom)
        monkeypatch.setattr(jev_mod, "jev_choice", boom)
        out = e.classify_request(_REQ)
        assert out["errors"] == 2
        assert all(r.get("error") for r in out["results"])


class TestHost:
    def _host(self):
        from src.mcp_client.host import JevLocalHost

        return JevLocalHost()

    def test_three_tools_exposed(self):
        host = self._host()
        tools = host.list_tools()
        assert [t.name for t in tools] == [
            "jev_classify", "jev_schema", "jev_classify_status",
        ]
        assert all(t.server_name == "jev-classifier-local"
                   for t in tools)

    def test_handlers_e2e(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "sandbox").mkdir()
        monkeypatch.setenv("QDRANT_FILESYSTEM_WRITE_ROOT",
                           str(tmp_path / "sandbox"))
        host = self._host()
        out = host._classify({"payload_json": json.dumps(
            {"classes": ["帳單", "會議", "待辦"],
             "items": [{"id": "a1", "text": "繳費通知"},
                       {"id": "a2", "text": "週會議程"}]})})
        assert out["ok"] is True and out["total"] == 2
        sch = host._schema({})
        assert "classes" in sch["format"]
        st = host._status({})
        assert st["mode"] == "mock" and st["batch_max"] == 50

    def test_write_result_writes_sandbox(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "sandbox").mkdir()
        monkeypatch.setenv("QDRANT_FILESYSTEM_WRITE_ROOT",
                           str(tmp_path / "sandbox"))
        host = self._host()
        out = host._classify({"payload_json": json.dumps(
            {"classes": ["帳單", "會議"],
             "items": [{"text": "繳費通知"}]}),
            "write_result": True})
        assert "result_path" in out
        written = json.load(open(out["result_path"], encoding="utf-8"))
        assert written["ok"] is True


class TestManagerWiring:
    def test_from_config_registers_jev_host(self, tmp_path):
        from src.mcp_client.manager import McpManager

        cfg = tmp_path / "mcp.json"
        cfg.write_text('{"servers": []}', encoding="utf-8")
        mgr = McpManager.from_config(str(cfg))
        names = [t.name for t in mgr.list_tools()
                 if t.name.startswith("jev_")]
        assert names == ["jev_classify", "jev_schema",
                         "jev_classify_status"]
        assert mgr._jev_local_host.SERVER_NAME == "jev-classifier-local"


class TestStdioParity:
    def test_specs_match_host(self):
        from src.mcp_client.host import JevLocalHost
        from src.mcp_server.jev_server import JevMCPServer

        server = JevMCPServer()
        host_names = [t.name for t in JevLocalHost().list_tools()]
        server_names = [t.name for t in server._tool_specs()]
        assert server_names == host_names

    def test_handler_map_complete(self):
        from src.mcp_server.jev_server import JevMCPServer

        specs = {t.name for t in JevMCPServer()._tool_specs()}
        assert specs == {"jev_classify", "jev_schema",
                         "jev_classify_status"}


class TestSystemPrompt:
    def _agent(self):
        from src.agent.agent import Agent

        a = Agent()
        a._client = MagicMock()
        a._model = "stub-model"
        a._provider = "mock"
        return a

    def test_jev_block_present_with_tools(self):
        prompt = self._agent()._system_prompt(tools_enabled=True)
        assert "jev_classify" in prompt
        assert "分類器" in prompt

    def test_jev_block_absent_legacy(self):
        prompt = self._agent()._system_prompt(tools_enabled=False)
        assert "jev_classify" not in prompt