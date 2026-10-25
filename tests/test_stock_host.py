"""Tests for the 兆豐 MegaAPI stock tools (v0.9.24.0+).

SAFETY: every test runs in mock mode (or asserts the live path is
LOCKED). No test ever imports the 兆豐 megaspeedy component, opens a
socket, or places a real order — the live wiring is untested by
design until the broker integration is completed.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.mega_stock_tools import (
    DEFAULT_KLINE_BARS,
    MAX_KLINE_BARS,
    MODE_MOCK,
    MegaStockEngine,
)


@pytest.fixture(autouse=True)
def _clean_stock_env(monkeypatch):
    """Env isolation — stock knobs must never leak between tests."""
    monkeypatch.delenv("MEGA_STOCK_MODE", raising=False)
    monkeypatch.delenv("MEGA_STOCK_TRADING_ENABLED", raising=False)


class TestEngineMock:
    """Mock mode — the development/testing surface."""

    def test_default_mode_is_mock(self):
        engine = MegaStockEngine()
        assert engine.mode == MODE_MOCK
        assert engine.is_mock
        assert not engine.trading_enabled

    def test_quote_is_deterministic_and_simulated(self):
        engine = MegaStockEngine()
        a = engine.get_quote("2330")
        b = engine.get_quote("2330")
        assert a["price"] == b["price"]  # same symbol, same day
        assert a["simulated"] is True
        assert a["exchange"] == "TWSE"
        assert a["high"] >= a["low"]
        for k in ("open", "close" if False else "price",
                  "prev_close", "volume", "ts"):
            assert k in a

    def test_quote_requires_symbol(self):
        assert "error" in MegaStockEngine().get_quote("")

    def test_kline_bars_and_interval(self):
        engine = MegaStockEngine()
        out = engine.get_kline("2330", interval="day", bars=5)
        assert out["bars"] == 5
        assert len(out["candles"]) == 5
        assert out["simulated"] is True
        assert "error" in engine.get_kline("2330", interval="hour")
        assert "error" in engine.get_kline("")

    def test_kline_bars_clamped(self):
        engine = MegaStockEngine()
        assert engine.get_kline("2330", bars=9999)["bars"] == MAX_KLINE_BARS
        assert engine.get_kline("2330", bars="bad")["bars"] == \
            DEFAULT_KLINE_BARS

    def test_place_limit_order_stays_open(self):
        engine = MegaStockEngine()
        out = engine.place_order("2330", "buy", 1000, price=1000.0,
                                 confirm=True)
        assert out["simulated"] is True
        order = out["order"]
        assert order["order_id"].startswith("MOCK-")
        assert order["status"] == "open"
        assert order["symbol"] == "2330"
        assert order["side"] == "buy"
        assert order["price"] == 1000.0

    def test_place_market_order_fills_immediately(self):
        engine = MegaStockEngine()
        out = engine.place_order("2454", "buy", 200, order_type="market",
                                 confirm=True)
        order = out["order"]
        assert order["status"] == "filled"
        assert order["filled_qty"] == 200
        assert order["filled_price"] > 0

    def test_place_order_validation(self):
        engine = MegaStockEngine()
        assert "error" in engine.place_order("", "buy", 100, price=1,
                                             confirm=True)
        assert "error" in engine.place_order("2330", "hold", 100,
                                             price=1, confirm=True)
        assert "error" in engine.place_order("2330", "buy", 0,
                                             price=1, confirm=True)
        assert "error" in engine.place_order("2330", "buy", 100,
                                             order_type="weird",
                                             confirm=True)
        # limit without price
        assert "error" in engine.place_order("2330", "buy", 100,
                                             confirm=True)

    def test_place_order_confirm_gate(self):
        out = MegaStockEngine().place_order("2330", "buy", 1000,
                                            price=1000.0)
        assert "error" in out
        assert "confirm" in out["error"]

    def test_list_orders_filters_status(self):
        engine = MegaStockEngine()
        open_o = engine.place_order("2330", "buy", 1000, price=1000.0,
                                    confirm=True)["order"]
        filled_o = engine.place_order("2454", "buy", 200,
                                      order_type="market",
                                      confirm=True)["order"]
        all_rows = engine.list_orders(status="all")
        assert all_rows["total"] == 2
        assert engine.list_orders(status="open")["orders"][0][
            "order_id"] == open_o["order_id"]
        assert engine.list_orders(status="filled")["orders"][0][
            "order_id"] == filled_o["order_id"]
        assert "error" in engine.list_orders(status="nope")

    def test_cancel_order_roundtrip(self):
        engine = MegaStockEngine()
        oid = engine.place_order("2330", "buy", 1000, price=1000.0,
                                 confirm=True)["order"]["order_id"]
        out = engine.cancel_order(oid)
        assert out["order"]["status"] == "cancelled"
        assert "error" in engine.cancel_order(oid)  # already cancelled
        assert "error" in engine.cancel_order("MOCK-NOPE")
        assert "error" in engine.cancel_order("")

    def test_account_summary_mock(self):
        engine = MegaStockEngine()
        out = engine.get_account_summary()
        assert out["account"] == "MOCK-ACCOUNT"
        assert out["simulated"] is True
        assert out["holdings"]
        assert out["equity"] > 0


class TestEngineLiveLock:
    """live path stays locked — no real orders in dev/testing."""

    def test_live_orders_locked_without_trading_flag(self, monkeypatch):
        monkeypatch.setenv("MEGA_STOCK_MODE", "live")
        engine = MegaStockEngine()
        assert not engine.is_mock
        out = engine.place_order("2330", "buy", 1000, price=1000.0,
                                 confirm=True)
        assert "error" in out
        assert "MEGA_STOCK_TRADING_ENABLED" in out["error"]
        assert engine.cancel_order("x")["mode"] == "live"

    def test_live_orders_still_gated_with_flag(self, monkeypatch):
        """With MEGA_STOCK_TRADING_ENABLED=on but NO credentials, the
        live order path still refuses with a clear gate error (the
        v0.9.28.0 Telegram consent gate or the v0.9.26.0 creds gate —
        whichever fires first; both are documented gates)."""
        monkeypatch.setenv("MEGA_STOCK_MODE", "live")
        monkeypatch.setenv("MEGA_STOCK_TRADING_ENABLED", "on")
        monkeypatch.delenv("MEGA_STOCK_ID", raising=False)
        monkeypatch.setenv("MEGA_STOCK_ID", "")
        engine = MegaStockEngine()
        assert engine.trading_enabled
        out = engine.place_order("2330", "buy", 1000, price=1000.0,
                                 confirm=True)
        assert "error" in out
        assert ("MEGA_STOCK_ID" in out["error"]
                or "Telegram" in out["error"])

    def test_live_queries_unavailable(self, monkeypatch):
        """live WITHOUT credentials → clean error, never a socket."""
        monkeypatch.setenv("MEGA_STOCK_MODE", "live")
        monkeypatch.delenv("MEGA_STOCK_ID", raising=False)
        monkeypatch.setenv("MEGA_STOCK_ID", "")
        monkeypatch.delenv("MEGA_STOCK_PASSWORD", raising=False)
        monkeypatch.setenv("MEGA_STOCK_PASSWORD", "")
        engine = MegaStockEngine()
        for fn in (lambda: engine.get_quote("2330"),
                   lambda: engine.get_kline("2330"),
                   engine.get_account_summary):
            out = fn()
            assert "error" in out

    def test_invalid_mode_falls_back_to_mock(self, monkeypatch):
        monkeypatch.setenv("MEGA_STOCK_MODE", "yolo")
        engine = MegaStockEngine()
        assert engine.mode == MODE_MOCK

    def test_trading_flag_parsing(self, monkeypatch):
        monkeypatch.setenv("MEGA_STOCK_MODE", "live")
        for flag in ("on", "1", "true", "yes", "ON"):
            monkeypatch.setenv("MEGA_STOCK_TRADING_ENABLED", flag)
            assert MegaStockEngine().trading_enabled
        for flag in ("off", "0", "false", "", "no"):
            monkeypatch.setenv("MEGA_STOCK_TRADING_ENABLED", flag)
            assert not MegaStockEngine().trading_enabled


class TestHostSurface:
    """MegaStockLocalHost tool surface + handlers."""

    def test_six_tools_exposed(self):
        from src.mcp_client.host import MegaStockLocalHost

        host = MegaStockLocalHost()
        tools = host.list_tools()
        assert [t.name for t in tools] == [
            "stock_get_quote", "stock_get_quotes",
            "stock_get_kline",
            "stock_get_account_summary", "stock_list_orders",
            "stock_place_order", "stock_cancel_order",
            "stock_search_history", "stock_list_history",
        ]
        assert all(t.server_name == "stock-local" for t in tools)
        schema = tools[0].input_schema
        assert schema["required"] == ["symbol"]

    def test_place_order_schema_requires_confirm(self):
        from src.mcp_client.host import MegaStockLocalHost

        host = MegaStockLocalHost()
        place = next(t for t in host.list_tools()
                     if t.name == "stock_place_order")
        assert place.input_schema["required"] == [
            "symbol", "side", "qty", "confirm",
        ]

    def test_handlers_end_to_end(self, monkeypatch):
        from src.mcp_client.host import MegaStockLocalHost

        host = MegaStockLocalHost()
        host._save_query = MagicMock(return_value={"ok": False})
        quote = host._quote({"symbol": "0056"})
        assert quote["simulated"] is True
        placed = host._place({
            "symbol": "0056", "side": "sell", "qty": 100,
            "price": 50.0, "confirm": True,
        })
        oid = placed["order"]["order_id"]
        assert placed["order"]["status"] == "open"
        assert host._cancel({"order_id": oid})["order"][
            "status"] == "cancelled"
        assert host._orders({"status": "all"})["total"] == 1
        assert host._account({})["simulated"] is True
        assert len(host._kline({"symbol": "0056", "bars": 3})[
            "candles"]) == 3
        # None args tolerated (LocalTool coercion edge)
        assert "error" in host._quote(None)

    def test_batch_quotes_e2e(self, monkeypatch):
        """v0.9.85.0 — stock_get_quotes: ONE call covers multiple
        symbols with per-symbol containment (mock mode)."""
        from src.mcp_client.host import MegaStockLocalHost

        host = MegaStockLocalHost()
        host._save_query = MagicMock(return_value={"ok": False})
        out = host._quotes_batch(
            {"symbols": ["2330", "0056", "2454"]})
        assert out["count"] == 3 and out["ok_count"] == 3
        assert [q["symbol"] for q in out["quotes"]] == [
            "2330", "0056", "2454"]
        assert all(q["simulated"] for q in out["quotes"])
        # string tolerance + cap
        monkeypatch.setenv("QDRANT_STOCK_BATCH_MAX", "1")
        out = host._quotes_batch({"symbols": "2330"})
        assert out["count"] == 1
        out = host._quotes_batch({
            "symbols": [f"{i:04d}" for i in range(15)]})
        assert out["count"] == 1  # cap clamps to 1
        assert "error" in host._quotes_batch({})


class TestManagerWiring:
    """from_config registers stock-local (THIRTEENTH LocalHost)."""

    def test_from_config_registers_stock_host(self, tmp_path):
        from src.mcp_client.manager import McpManager

        cfg = tmp_path / "mcp.json"
        cfg.write_text('{"servers": []}', encoding="utf-8")
        mgr = McpManager.from_config(str(cfg))
        names = {t.name for t in mgr.list_tools()}
        assert "stock_get_quote" in names
        assert "stock_place_order" in names
        assert mgr._stock_local_host is not None
        assert mgr._stock_local_host.SERVER_NAME == "stock-local"
        mgr.close_all()

    def test_dispatch_reaches_engine(self, tmp_path):
        from src.mcp_client.manager import McpManager

        cfg = tmp_path / "mcp.json"
        cfg.write_text('{"servers": []}', encoding="utf-8")
        mgr = McpManager.from_config(str(cfg))
        out = mgr.call("stock_place_order", {
            "symbol": "2330", "side": "buy", "qty": 100,
            "price": 1000.0, "confirm": True,
        })
        assert not out.is_error
        assert "MOCK-" in out.text
        assert "未送出任何真實下單" in out.text
        mgr.close_all()


class TestStdioParity:
    """stdio server mirrors the in-process tool surface."""

    def test_specs_match_host(self):
        from src.mcp_client.host import MegaStockLocalHost
        from src.mcp_server.stock_server import StockMCPServer

        server = StockMCPServer()
        host_names = [t.name for t in MegaStockLocalHost().list_tools()]
        server_names = [t.name for t in server._tool_specs()]
        assert server_names == host_names

    def test_handler_map_complete(self):
        from src.mcp_server.stock_server import StockMCPServer

        server = StockMCPServer()
        # every spec has a handler path
        for spec in server._tool_specs():
            assert spec.name in {
                "stock_get_quote", "stock_get_quotes",
                "stock_get_kline",
                "stock_get_account_summary", "stock_list_orders",
                "stock_place_order", "stock_cancel_order",
                "stock_search_history", "stock_list_history",
            }


class TestSafeDecode:
    def test_safe_str_ascii_and_big5(self):
        from src.mega_stock_tools import _safe_str

        assert _safe_str(b"OK") == "OK"
        assert _safe_str("台積電".encode("big5")) == "台積電"

    def test_safe_str_utf8_chinese(self):
        # the live-observed crash: UTF-8 「未授權使用」 passed to a
        # Big5 decode — must NOT raise, must decode via the UTF-8
        # fallback
        from src.mega_stock_tools import _safe_str

        assert _safe_str("未授權使用".encode("utf-8")) == "未授權使用"

    def test_safe_str_garbage_never_raises(self):
        from src.mega_stock_tools import _safe_str

        assert isinstance(_safe_str(b"\xe6\x9c\xaa\xff\xff"), str)
        assert _safe_str(123) == "123"
        assert _safe_str(None) == "None"


class TestSystemPrompt:
    """Trigger block presence + legacy exclusion."""

    def _agent(self):
        from unittest.mock import MagicMock

        from src.agent.agent import Agent

        a = Agent()
        a._client = MagicMock()
        a._model = "mock-model"
        a._provider = "mock"
        return a

    def test_stock_block_present_with_tools(self):
        prompt = self._agent()._system_prompt(tools_enabled=True)
        assert "stock_get_quote" in prompt
        assert "stock_get_quotes" in prompt  # v0.9.85.0 batch tool
        assert "stock_place_order" in prompt
        assert "stock_cancel_order" in prompt
        assert "股價" in prompt
        assert "模擬" in prompt  # mock-data honesty rule
        assert "confirm" in prompt

    def test_stock_block_absent_legacy(self):
        prompt = self._agent()._system_prompt(tools_enabled=False)
        assert "stock_place_order" not in prompt