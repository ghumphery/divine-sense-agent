"""Tests for the stock Qdrant snapshot store (v0.9.35.0+).

``StockStore`` mirrors LogStore over the dedicated
``QDRANT_STOCK_COLLECTION`` (default ``stock_docs``): one MCP query
result == one point, deterministic uuid5 ids, tool/symbol/ts_epoch
payload indexes. Mock-driven — the Qdrant client is a MagicMock, the
embedder a stub; no real network.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture()
def store_env(monkeypatch):
    from src.stock_store import StockStore

    monkeypatch.setenv("QDRANT_STOCK_COLLECTION", "stock_test")
    s = StockStore(url="http://127.0.0.1:6333", collection="stock_docs")
    c = MagicMock()
    c.collection_exists.return_value = False
    c.scroll.return_value = ([], None)
    c.query_points.return_value = MagicMock(points=[])
    c.count.return_value = MagicMock(count=0)
    s._client = c
    return s, c


QUOTE = {
    "mode": "mock", "symbol": "2330", "name": "Mock 台積電",
    "open": 500.0, "high": 510.0, "low": 495.0, "close": 505.0,
    "volume": 20000, "simulated": True,
}


class TestBuildSummary:
    def test_quote_summary_has_scalars(self):
        from src.stock_store import build_summary_text

        text = build_summary_text(
            "stock_get_quote", {"symbol": "2330"}, QUOTE,
        )
        assert "stock_get_quote" in text
        assert "symbol=2330" in text
        assert "close=505.0" in text

    def test_kline_summary_has_counts(self):
        from src.stock_store import build_summary_text

        result = {"candles": [{"o": 1, "c": 2}, {"o": 2, "c": 3}]}
        text = build_summary_text(
            "stock_get_kline", {"symbol": "2330", "interval": "day"},
            result,
        )
        assert "bars_n=2" in text
        assert "candles_first=" in text

    def test_capped_length(self):
        from src.stock_store import build_summary_text

        big = {"data": "x" * 5000}
        assert len(build_summary_text("t", {}, big)) <= 2000


class TestPointId:
    def test_deterministic(self):
        from src.stock_store import StockStore

        a = StockStore.point_id("stock_get_quote", "2330", 1726000000)
        b = StockStore.point_id("stock_get_quote", "2330", 1726000000)
        assert a == b

    def test_distinct_by_tool_or_ts(self):
        from src.stock_store import StockStore

        a = StockStore.point_id("stock_get_quote", "2330", 1726000000)
        b = StockStore.point_id("stock_get_quote", "2330", 1726000001)
        c = StockStore.point_id("stock_get_kline", "2330", 1726000000)
        assert a != b and a != c

    def test_is_valid_uuid(self):
        import uuid

        from src.stock_store import StockStore

        pid = StockStore.point_id("stock_get_quote", "", 1.0)
        assert str(uuid.UUID(pid)) == pid


class TestSaveAndSearch:
    def test_save_query_upserts_payload(self, store_env):
        from src.stock_store import StockStore

        s, c = store_env
        pid = s.save_query(
            tool="stock_get_quote",
            args={"symbol": "2330"},
            result=QUOTE,
            text="summary text",
            vector=[0.1] * 8,
            ts_epoch=1726000000,
        )
        assert pid == StockStore.point_id(
            "stock_get_quote", "2330", 1726000000,
        )
        pts = c.upsert.call_args.kwargs["points"]
        assert len(pts) == 1
        payload = pts[0].payload
        assert payload["tool"] == "stock_get_quote"
        assert payload["symbol"] == "2330"
        assert payload["text"] == "summary text"
        assert payload["ts_epoch"] == 1726000000

    def test_search_passes_filter_and_returns_hits(self, store_env):
        s, c = store_env
        hit = MagicMock(score=0.9)
        hit.payload = {"tool": "stock_get_quote", "symbol": "2330"}
        c.query_points.return_value = MagicMock(points=[hit])
        hits = s.search([0.2] * 8, top_k=3, tool="stock_get_quote")
        assert len(hits) == 1
        assert hits[0]["symbol"] == "2330"
        assert hits[0]["score"] == 0.9

    def test_recent_sorts_newest_first(self, store_env):
        s, c = store_env
        old, new = MagicMock(), MagicMock()
        old.payload = {"ts_epoch": 100.0, "tool": "t"}
        new.payload = {"ts_epoch": 200.0, "tool": "t"}
        c.scroll.return_value = ([old, new], None)
        rows = s.recent(limit=5)
        assert rows[0]["ts_epoch"] == 200.0
        assert rows[1]["ts_epoch"] == 100.0

    def test_count_delegates(self, store_env):
        s, c = store_env
        s.count_points(tool="stock_get_quote")
        assert c.count.called


class TestHostHook:
    """MegaStockLocalHost auto-save + history tools.

    All host tests PIN MEGA_STOCK_MODE=mock (the live .env values must
    never drive unit tests — the mock book needs no socket and NEVER
    touches the real MegaAPI).
    """

    def _host(self, monkeypatch):
        monkeypatch.setenv("MEGA_STOCK_MODE", "mock")
        monkeypatch.delenv("MEGA_STOCK_TRADING_ENABLED", raising=False)
        from src.mcp_client.host import MegaStockLocalHost

        return MegaStockLocalHost()

    def test_eight_tools_advertised(self, monkeypatch):
        h = self._host(monkeypatch)
        names = [t.name for t in h.list_tools()]
        assert "stock_search_history" in names
        assert "stock_list_history" in names

    def test_quote_saves_to_qdrant(self, monkeypatch):
        h = self._host(monkeypatch)
        h._save_query = MagicMock(return_value={"ok": True, "id": "p1"})
        out = h._quote({"symbol": "2330"})
        assert out["qdrant"]["ok"] is True

    def test_save_failure_never_breaks_query(self, monkeypatch):
        h = self._host(monkeypatch)
        h._save_query = MagicMock(return_value={"ok": False})
        out = h._quote({"symbol": "2330"})
        assert out["qdrant"] == {"ok": False}
        assert "symbol" in out

    def test_search_history_requires_query(self, monkeypatch):
        assert "error" in self._host(monkeypatch)._search_history({})

    def test_search_history_unavailable_store(self, monkeypatch):
        h = self._host(monkeypatch)
        h._stock_ensure = lambda: None
        assert "error" in h._search_history({"query": "x"})

    def test_search_history_ok(self, monkeypatch):
        h = self._host(monkeypatch)
        store = MagicMock()
        embedder = MagicMock()
        embedder.embed.return_value = [0.1]
        store.search.return_value = [{"symbol": "2330"}]
        h._stock_ensure = lambda: (store, embedder)
        out = h._search_history({"query": "台積電", "symbol": "2330"})
        assert out["count"] == 1
        store.search.assert_called_once()

    def test_list_history_strips_data(self, monkeypatch):
        h = self._host(monkeypatch)
        store = MagicMock()
        store.recent.return_value = [
            {"tool": "t", "ts_epoch": 1, "data": "BIG"},
        ]
        store.count_points.return_value = 1
        h._stock_ensure = lambda: (store, MagicMock())
        out = h._list_history({})
        assert "data" not in out["recent"][0]
        assert out["total"] == 1

    def test_stdio_server_has_history_dispatch(self):
        import io

        src = io.open(
            "src/mcp_server/stock_server.py", encoding="utf-8",
        ).read()
        assert "stock_search_history" in src
        assert "stock_list_history" in src

    def test_prompt_mentions_history_tools(self, monkeypatch, tmp_path):
        monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path))
        from src.agent.agent import Agent

        a = Agent()
        a._provider = "stub"
        a._model = "stub-model"
        a._mcp_manager = None
        prompt = a._system_prompt(tools_enabled=True)
        assert "stock_search_history" in prompt
        assert "stock_docs" in prompt