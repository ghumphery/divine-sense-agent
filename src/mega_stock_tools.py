"""Mega Securities (兆豐證券) stock query + order tools — MCP core.

參考文件: https://www.emega.com.tw/emega/upload/megaapi/teach0.html
(MegaAPI — Python `spdOrderAPI` 交易 / `spdQuoteAPI` 行情, 事件驅動
 socket API, Windows 元件 + 電子憑證 .pfx + 帳號登入).

雙模式設計（安全第一）:

    MEGA_STOCK_MODE=mock  預設。開發/測試用：完全模擬 — 不 import
                          megaSpeedy/pySpeedy 元件、不開 socket、
                          絕不送出真實委託。報價/K線/下單/刪單全部
                          在記憶體模擬（確定性生成，同一 symbol
                          重複查詢得一致價格）。
    MEGA_STOCK_MODE=live  真實模式。需要 Windows + 兆豐 megaspeedy
                          元件 + 電子憑證 .pfx + 交易主機參數；本
                          build 的 live 端僅含連線/登入骨架（依
                          teach2/teach5 樣板）與明確的「聯調未完
                          成」錯誤 — 未經真實聯調的下單路徑一律
                          鎖住。

下單的三道閘（全部通過才會碰到 live API；mock 模式無風險）:
    1. MEGA_STOCK_MODE=live
    2. MEGA_STOCK_TRADING_ENABLED=on
    3. 下單參數 confirm=True
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from typing import Any

__all__ = [
    "MegaStockEngine", "MODE_MOCK", "MODE_LIVE", "DEFAULT_MODE",
    "VALID_SIDES", "VALID_ORDER_TYPES", "VALID_INTERVALS",
    "VALID_ORDER_STATUSES",
]

MODE_MOCK = "mock"
MODE_LIVE = "live"
DEFAULT_MODE = MODE_MOCK

VALID_SIDES = ("buy", "sell")
VALID_ORDER_TYPES = ("limit", "market")
VALID_INTERVALS = ("day", "week", "month")
VALID_ORDER_STATUSES = ("all", "open", "filled", "cancelled")

DEFAULT_KLINE_BARS = 10
MAX_KLINE_BARS = 200
DEFAULT_ORDER_LIST_LIMIT = 20
MAX_ORDER_LIST_LIMIT = 100

# ── Live MegaAPI connection settings (v0.9.26.0) ──
# Defaults from the 兆豐 API permission email (115/09/09):
#   證期交易主機  spapi.emega.com.tw : 56789
#   證期即時行情  119.31.179.86 : 34567
DEFAULT_ORDER_HOST = "spapi.emega.com.tw"
DEFAULT_ORDER_PORT = 56789
DEFAULT_QUOTE_HOST = "119.31.179.86"
DEFAULT_QUOTE_PORT = 34567

_LIVE_FIRE_WAIT_S = 10.0   # order reply event wait
_LIVE_CONN_WAIT_S = 15.0   # connect+logon event wait
_LIVE_QUOTE_WAIT_S = 3.0   # orderbook snapshot collect window


def _clamp_int(value: Any, default: int, lo: int, hi: int) -> int:
    try:
        v = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


def _stable_price(symbol: str) -> float:
    """Deterministic pseudo-price from the symbol — the same symbol
    always resolves to the same mock base price (reproducible tests)."""
    digest = hashlib.sha256(symbol.encode("utf-8")).hexdigest()
    return 40.0 + int(digest[:6], 16) % 96000 / 10.0  # 40.0 ~ 9,640.0


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class MegaStockEngine:
    """Mock-first engine for 兆豐 MegaAPI stock query / orders.

    `mode` comes from ``MEGA_STOCK_MODE`` (mock | live; default
    mock). `trading_enabled` mirrors ``MEGA_STOCK_TRADING_ENABLED``
    (on|off; default off) — even in live mode, orders stay locked
    until this is explicitly turned on AND the caller passes
    confirm=True.
    """

    def __init__(self, mode: str | None = None,
                 trading_enabled: str | None = None):
        self.mode = (mode or os.environ.get("MEGA_STOCK_MODE", "")
                     or DEFAULT_MODE).strip().lower()
        if self.mode not in (MODE_MOCK, MODE_LIVE):
            self.mode = DEFAULT_MODE
        flag = (trading_enabled if trading_enabled is not None
                else os.environ.get("MEGA_STOCK_TRADING_ENABLED", ""))
        self.trading_enabled = str(flag).strip().lower() in (
            "on", "1", "true", "yes",
        )
        # in-memory mock order book: order_id -> order dict
        self._orders: dict[str, dict[str, Any]] = {}
        self._mock_seq = 0
        # live SDK handle (lazy; NEVER imported in mock mode)
        self._live_session: Any = None

    # ── mode / safety ──────────────────────────────────────────

    @property
    def is_mock(self) -> bool:
        return self.mode == MODE_MOCK

    def live_session(self) -> "MegaLiveSession":
        """Lazy live connection wrapper (never built in mock mode)."""
        if self.is_mock:
            raise RuntimeError("live session unavailable in mock mode")
        if self._live_session is None:
            self._live_session = MegaLiveSession(self._live_creds())
        return self._live_session

    def _live_call(self, what: str, fn) -> dict[str, Any]:
        """Run a live call; any failure becomes a clear error dict."""
        try:
            creds_err = self._live_creds_error(what)
            if creds_err:
                return creds_err
            return fn()
        except ImportError as e:
            return {"error": str(e), "mode": self.mode}
        except Exception as e:
            return {
                "error": f"{what}: live 查詢失敗 — {e!r}",
                "mode": self.mode,
            }

    def _live_creds(self) -> dict[str, Any]:
        """Live credentials + hosts from the environment."""
        return {
            "order_host": os.environ.get(
                "MEGA_STOCK_ORDER_HOST", DEFAULT_ORDER_HOST,
            ),
            "order_port": _clamp_int(
                os.environ.get("MEGA_STOCK_ORDER_PORT", ""),
                DEFAULT_ORDER_PORT, 1, 65535,
            ),
            "quote_host": os.environ.get(
                "MEGA_STOCK_QUOTE_HOST", DEFAULT_QUOTE_HOST,
            ),
            "quote_port": _clamp_int(
                os.environ.get("MEGA_STOCK_QUOTE_PORT", ""),
                DEFAULT_QUOTE_PORT, 1, 65535,
            ),
            "uid": os.environ.get("MEGA_STOCK_ID", ""),
            "password": os.environ.get("MEGA_STOCK_PASSWORD", ""),
            "account": os.environ.get("MEGA_STOCK_ACCOUNT", ""),
            "branch": os.environ.get("MEGA_STOCK_BRANCH", ""),
            "market": os.environ.get("MEGA_STOCK_MARKET", "tse"),
            "cert_path": os.environ.get("MEGA_STOCK_CERT_PATH", ""),
            "cert_password": os.environ.get("MEGA_STOCK_CERT_PASSWORD", ""),
        }

    def _live_creds_error(self, what: str) -> dict[str, Any]:
        c = self._live_creds()
        missing = [k for k in ("uid", "password", "account", "branch")
                   if not c[k]]
        if missing:
            return {
                "error": (
                    f"{what}: live 模式缺少登入資訊 — 請於 .env 設定 "
                    "MEGA_STOCK_ID（身分證字號大寫）、MEGA_STOCK_PASSWORD"
                    "（電子登入密碼）、MEGA_STOCK_ACCOUNT（證券帳號 7 碼）、"
                    f"MEGA_STOCK_BRANCH（分公司 4 碼）；缺：{missing}"
                ),
                "mode": self.mode,
            }
        return {}

    def _live_unavailable(self, what: str) -> dict[str, Any]:
        return {
            "error": (
                f"{what}: live 模式需要兆豐 megaspeedy 元件（Windows）"
                "+ 電子憑證 .pfx + 交易主機參數，且 live 聯調尚未完成。"
                "目前請使用 mock 模式（MEGA_STOCK_MODE=mock）。"
            ),
            "mode": self.mode,
        }

    # ── mock data generators (deterministic) ───────────────────

    def _mock_quote(self, symbol: str) -> dict[str, Any]:
        base = _stable_price(symbol)
        digest = hashlib.sha256(
            (symbol + _now_iso()[:10]).encode("utf-8"),
        ).hexdigest()
        drift = (int(digest[:4], 16) % 400 - 200) / 100  # ±2%
        price = round(base * (1 + drift / 100), 2)
        open_ = round(base * 0.998, 2)
        return {
            "symbol": symbol,
            "exchange": "TWSE",
            "name": f"Mock {symbol}",
            "price": price,
            "open": open_,
            "high": round(max(price, open_) * 1.005, 2),
            "low": round(min(price, open_) * 0.995, 2),
            "prev_close": base,
            "change": round(price - base, 2),
            "change_pct": round((price - base) / base * 100, 2),
            "volume": int(digest[4:8], 16) % 50000 + 100,
            "ts": _now_iso(),
            "simulated": True,
        }

    def _mock_kline(self, symbol: str, interval: str,
                    bars: int) -> list[dict[str, Any]]:
        base = _stable_price(symbol)
        out = []
        digest = hashlib.sha256(symbol.encode("utf-8")).hexdigest()
        for i in range(bars):
            # cycle the digest (64 hex chars) so bars > 16 stay valid
            chunk = digest[(i * 4) % 60:(i * 4) % 60 + 4]
            drift = (int(chunk, 16) % 300 - 150) / 100
            close = round(base * (1 + drift / 100), 2)
            open_ = round(base * (1 - drift / 200), 2)
            vchunk = digest[(i * 2) % 60:(i * 2) % 60 + 4]
            out.append({
                "date": f"mock-{interval}-{bars - i:02d}",
                "open": open_,
                "high": round(max(open_, close) * 1.01, 2),
                "low": round(min(open_, close) * 0.99, 2),
                "close": close,
                "volume": int(vchunk, 16) % 40000 + 50,
            })
        return out

    def _mock_order_id(self) -> str:
        self._mock_seq += 1
        stamp = _now_iso()[:10].replace("-", "")
        return f"MOCK-{stamp}-{self._mock_seq:04d}"

    # ── public tool operations ─────────────────────────────────

    def get_quote(self, symbol: str) -> dict[str, Any]:
        symbol = str(symbol or "").strip()
        if not symbol:
            return {"error": "symbol is required"}
        if not self.is_mock:
            return self._live_call(
                "stock_get_quote",
                lambda: self._live_quote(symbol),
            )
        quote = self._mock_quote(symbol)
        return {"mode": self.mode, **quote}

    def _live_quote(self, symbol: str) -> dict[str, Any]:
        snap = dict(self.live_session().snapshot(symbol) or {})
        out: dict[str, Any] = {"mode": self.mode, **snap}
        if not snap.get("bid_price") and not snap.get("last_price"):
            out["note"] = (
                "live 即時快照無資料（盤外或訂閱後無成交）— "
                "可改用 stock_get_kline 取得開高低收"
            )
        out["simulated"] = False
        return out

    def get_kline(self, symbol: str, interval: str = "day",
                  bars: int = DEFAULT_KLINE_BARS) -> dict[str, Any]:
        symbol = str(symbol or "").strip()
        interval = str(interval or "day").strip().lower()
        if interval not in VALID_INTERVALS:
            return {"error": f"interval must be one of {VALID_INTERVALS}"}
        bars = _clamp_int(bars, DEFAULT_KLINE_BARS, 1, MAX_KLINE_BARS)
        if not symbol:
            return {"error": "symbol is required"}
        if not self.is_mock:
            data = self._live_call(
                "stock_get_kline",
                lambda: {
                    "mode": self.mode, "symbol": symbol,
                    "interval": interval, "simulated": False,
                    **self.live_session().kline(symbol),
                },
            )
            # v0.9.98.1 — compact + truncate the live rows. The raw
            # GetDKChartData payload is the FULL price history
            # (hundreds of wide rows -> 15-20k-char tool results that
            # sit just under the offload threshold and get re-carried
            # by EVERY tool-loop iteration — the 138k/258k-token
            # turns). Keep the latest ``bars`` candles in the compact
            # {date,open,high,low,close,volume} shape, write the FULL
            # raw rows to the offload file (read_file-able), and note
            # the omission in the result.
            return self._compact_live_kline(data, bars, symbol)
        return {
            "mode": self.mode, "symbol": symbol,
            "interval": interval, "bars": bars,
            "candles": self._mock_kline(symbol, interval, bars),
            "simulated": True,
        }

    # ── live kline compaction (v0.9.98.1 — result-size guard) ──────
    _KLINE_ROW_KEYS = (
        # GetDKChartData row layout (v0.9.28.3):
        # [date, symbol, name, open, high, low, close, volume, ...]
        ("date", 0), ("open", 3), ("high", 4), ("low", 5),
        ("close", 6), ("volume", 7),
    )

    @staticmethod
    def _offload_dir() -> str:
        """The read-sandbox offload dir (same chain as the context
        compressor): ``QDRANT_OFFLOAD_DIR`` →
        ``QDRANT_FILESYSTEM_READ_ROOT/chat_offloads`` → ``./cache``."""
        d = (os.environ.get("QDRANT_OFFLOAD_DIR", "") or "").strip()
        if d:
            return d
        read_root = (os.environ.get("QDRANT_FILESYSTEM_READ_ROOT", "")
                     or "").strip()
        if read_root:
            return os.path.join(read_root, "chat_offloads")
        return "chat_offloads"

    def _compact_live_kline(
        self, data: dict[str, Any], bars: int, symbol: str,
    ) -> dict[str, Any]:
        """Trim the live GetDKChartData payload to the latest ``bars``
        candles in the compact {date,open,high,low,close,volume} shape
        (same keys as the mock candles), write the FULL raw rows to
        the offload file when anything is omitted, and note both in
        the result. ANY failure → return the original data unchanged
        (fail-open — a fat result beats a broken one)."""
        try:
            rows = data.get("rows") or []
            if not rows:
                return data
            total = len(rows)
            if bars >= total:
                return data  # nothing to omit
            # v0.9.98.3 — GetDKChartData returns rows NEWEST-FIRST
            # (live-verified via the offload files: rows[0] =
            # 20260923, rows[-1] = 20220811). The v0.9.98.1
            # ``rows[-bars:]`` slice therefore kept the OLDEST rows —
            # the "kline 一直回 2022 資料" bug. Slice the HEAD, and
            # fall back to the tail when a payload happens to be
            # ascending (defensive: compare the first/last dates).
            def _date_of(r):
                if isinstance(r, (list, tuple)) and r:
                    return str(r[0])
                return ""
            newest_first = _date_of(rows[0]) >= _date_of(rows[-1])
            keep = rows[:bars] if newest_first else rows[-bars:]
            candles = []
            for row in keep:
                candle: dict[str, Any] = {}
                if isinstance(row, (list, tuple)):
                    for key, idx in self._KLINE_ROW_KEYS:
                        try:
                            candle[key] = row[idx]
                        except (IndexError, TypeError):
                            pass
                else:
                    candle = {"raw": row}
                if candle:
                    candles.append(candle)
            full_path = None
            if bars < total:
                try:
                    out_dir = self._offload_dir()
                    os.makedirs(out_dir, exist_ok=True)
                    self._offload_seq = getattr(
                        self, "_offload_seq", 0) + 1
                    name = (
                        f"kline-{symbol}-{datetime.now():%Y%m%d-%H%M%S}"
                        f"-{self._offload_seq}.json"
                    )
                    path = os.path.join(out_dir, name)
                    tmp = path + ".tmp"
                    with open(tmp, "w", encoding="utf-8") as f:
                        json.dump(
                            {"symbol": symbol, "interval":
                             data.get("interval"), "title":
                             data.get("title"), "rows": rows}, f,
                            ensure_ascii=False,
                        )
                    os.replace(tmp, path)
                    full_path = os.path.abspath(path)
                except Exception:  # noqa: BLE001 — degrade inline
                    full_path = None
            out = {
                "mode": data.get("mode", self.mode),
                "symbol": symbol,
                "interval": data.get("interval"),
                "simulated": False,
                "requested_bars": bars,
                "total_available": total,
                "omitted": total - len(keep),
                "candles": candles,
                "title": data.get("title"),
            }
            if full_path:
                out["full_file"] = full_path
                out["note"] = (
                    f"已截斷至最近 {len(keep)} 筆（共 {total} 筆）— "
                    f"完整原始資料已存 {full_path}，需要更多歷史時可 "
                    f"read_file 讀取，勿重複呼叫本工具。"
                )
            return out
        except Exception:  # noqa: BLE001 — fail-open
            return data

    def get_account_summary(self) -> dict[str, Any]:
        if not self.is_mock:
            c = self._live_creds()
            return self._live_call(
                "stock_get_account_summary",
                lambda: {
                    "mode": self.mode, "simulated": False,
                    "account": c["account"], "branch": c["branch"],
                    "account_inquiry": self.live_session().query(
                        "makeStockAccountInquriy",
                        self._query_param(),
                    ),
                },
            )
        holdings = [
            {"symbol": "2330", "name": "Mock 台積電", "qty": 1000,
             "avg_cost": _stable_price("2330"), "simulated": True},
            {"symbol": "2454", "name": "Mock 聯發科", "qty": 200,
             "avg_cost": _stable_price("2454"), "simulated": True},
        ]
        equity = sum(h["avg_cost"] * h["qty"] for h in holdings)
        return {
            "mode": self.mode, "account": "MOCK-ACCOUNT",
            "equity": round(equity, 2), "holdings": holdings,
            "open_orders": len([
                o for o in self._orders.values()
                if o["status"] == "open"
            ]),
            "simulated": True,
        }

    def _query_param(self) -> dict[str, str]:
        c = self._live_creds()
        return {
            "branch_id": c["branch"], "cust_id": c["account"],
            "apcode": "0", "market": "0", "qry_type": "0",
            "nocsint": "N",
        }

    # ── Telegram consent gate (v0.9.28.0 — mandatory for live orders) ──

    def _place_with_consent(self, symbol: str, price: Any, side: str,
                            qty: int, order_type: str,
                            c: dict) -> dict:
        """SendNewOrderEx behind the Telegram consent gate: the
        approval chat (default: the first TELEGRAM_ALLOWED_CHAT_IDS
        entry, override with MEGA_STOCK_ORDER_APPROVAL_CHAT_ID) MUST
        reply 「同意 <STK-code>」 before the order is sent."""
        flag = (os.environ.get("MEGA_STOCK_ORDER_APPROVAL", "on")
                or "on").strip().lower()
        approval_on = flag not in ("off", "0", "no", "false")
        if approval_on:
            consent_err = self._telegram_approval(
                symbol, price, side, qty, order_type,
            )
            if consent_err:
                return consent_err
        out = self.live_session().place(
            symbol, float(price or 0), side, qty,
            order_type, "R", "N", "A",
            c["cert_path"], c["cert_password"],
        )
        if approval_on:
            out["consent"] = "approved via Telegram"
        return out

    def _telegram_approval(self, symbol: str, price: Any, side: str,
                           qty: int, order_type: str) -> dict[str, Any]:
        """Blocking Telegram consent gate. Returns {} on approval,
        an error dict otherwise."""
        from src.agent.telegram_bot import request_order_approval

        chat_id = _clamp_int(
            os.environ.get("MEGA_STOCK_ORDER_APPROVAL_CHAT_ID", ""),
            0, 0, 10**12,
        )
        if not chat_id:
            allowlist = [
                int(t.strip()) for t in os.environ.get(
                    "TELEGRAM_ALLOWED_CHAT_IDS", "",
                ).split(",") if t.strip().lstrip("-").isdigit()
            ]
            chat_id = allowlist[0] if allowlist else <YOUR_TELEGRAM_CHAT_ID>
        timeout = _clamp_int(
            os.environ.get("MEGA_STOCK_ORDER_APPROVAL_TIMEOUT", ""),
            180, 10, 900,
        )
        summary = (
            f"市場: {self._live_creds().get('market', 'tse')} | "
            f"股票: {symbol} | 方向: "
            f"{'買(B)' if side == 'buy' else '賣(S)'} | "
            f"數量: {qty} | 價格: {price}（{order_type}）\n"
            f"帳號: {self._live_creds()['account']}（分公司 "
            f"{self._live_creds()['branch']}）"
        )
        outcome = request_order_approval(
            chat_id, summary, timeout_s=timeout,
        )
        if outcome != "approved":
            return {
                "error": (
                    f"live 下單未取得 Telegram 同意（outcome={outcome}）"
                    "— 已取消送單。需由確認人於 Telegram 回覆同意。"
                ),
                "mode": self.mode,
                "consent": outcome,
                "approval_chat_id": chat_id,
            }
        return {}

    def list_orders(self, status: str = "all",
                    limit: int = DEFAULT_ORDER_LIST_LIMIT) -> dict:
        status = str(status or "all").strip().lower()
        if status not in VALID_ORDER_STATUSES:
            return {"error": f"status must be one of {VALID_ORDER_STATUSES}"}
        limit = _clamp_int(limit, DEFAULT_ORDER_LIST_LIMIT,
                           1, MAX_ORDER_LIST_LIMIT)
        if not self.is_mock:
            return self._live_call(
                "stock_list_orders",
                lambda: self._live_orders(status, limit),
            )
        rows = [
            {**o, "simulated": True}
            for o in sorted(self._orders.values(),
                            key=lambda x: x["placed_at"], reverse=True)
            if status == "all" or o["status"] == status
        ]
        return {
            "mode": self.mode, "status": status,
            "total": len(rows), "orders": rows[:limit],
            "simulated": True,
        }

    def _live_orders(self, status: str, limit: int) -> dict:
        """Live 委託/成交查詢 — queryStkOrder + queryStkMatch."""
        out: dict[str, Any] = {
            "mode": self.mode, "status": status,
            "simulated": False, "limit": limit,
        }
        qp = self._query_param()
        out["orders"] = self.live_session().query("queryStkOrder", qp)
        if status in ("all", "filled"):
            out["fills"] = self.live_session().query(
                "queryStkMatch", qp,
            )
        return out

    def _live_cancel(self, order_id: str) -> dict:
        """Live 刪單 — 先從 queryStkOrder 找到該單的
        symbol / price / side（SendCancelOrderEx 必填），再送刪單。"""
        qp = self._query_param()
        rows = self.live_session().query("queryStkOrder", qp)
        found = _find_order_row(rows, order_id)
        if found is None:
            return {
                "ok": False,
                "error": f"live 委託查詢找不到單號 {order_id!r} "
                         "（可能已成交/已刪除）",
            }
        sym, price, side = found
        return {
            "mode": self.mode, "simulated": False,
            **self.live_session().cancel(
                sym, float(price or 0), str(side).lower(), order_id, "N",
            ),
        }

    def place_order(self, symbol: str, side: str, qty: int,
                    price: float | None = None,
                    order_type: str = "limit",
                    confirm: bool = False) -> dict[str, Any]:
        symbol = str(symbol or "").strip()
        side = str(side or "").strip().lower()
        order_type = str(order_type or "limit").strip().lower()
        if not symbol:
            return {"error": "symbol is required"}
        if side not in VALID_SIDES:
            return {"error": f"side must be one of {VALID_SIDES}"}
        if qty is None or int(qty) <= 0:
            return {"error": "qty must be a positive integer"}
        qty = _clamp_int(qty, 1, 1, 9999)
        if order_type not in VALID_ORDER_TYPES:
            return {"error": f"order_type must be one of {VALID_ORDER_TYPES}"}
        if order_type == "limit" and not price:
            return {"error": "price is required for limit orders"}
        if not confirm:
            return {"error": (
                "confirm=True required — 下單需明確確認（防呆閘）"
            )}

        if not self.is_mock:
            # live: the gates must ALL pass before touching the real
            # MegaAPI. The real wiring (SendNewOrderEx) is live as of
            # v0.9.26.0 — gates: mode=live + trading_enabled=on +
            # per-call confirm=True + (v0.9.28.0) the Telegram consent
            # gate + the certificate gate.
            if not self.trading_enabled:
                return {"error": (
                    "live 下單未啟用：需 MEGA_STOCK_TRADING_ENABLED=on，"
                    "且下單前需設定憑證（MEGA_STOCK_CERT_PATH/"
                    "MEGA_STOCK_CERT_PASSWORD）。開發/測試請保持 mock。"
                ), "mode": self.mode}
            c = self._live_creds()
            return self._live_call(
                "stock_place_order(live)",
                lambda: {
                    "mode": self.mode, "simulated": False,
                    **self._place_with_consent(symbol, price, side,
                                               qty, order_type, c),
                },
            )

        oid = self._mock_order_id()
        order = {
            "order_id": oid,
            "symbol": symbol,
            "side": side,
            "qty": qty,
            "price": round(float(price), 2) if price else None,
            "order_type": order_type,
            "status": "open",
            "placed_at": _now_iso(),
            "account": "MOCK-ACCOUNT",
        }
        # mock 模擬成交（市價單立即以模擬價成交；限價單留在 open
        # 供刪單/查詢演練）
        if order_type == "market":
            order["status"] = "filled"
            order["filled_price"] = self._mock_quote(symbol)["price"]
            order["filled_qty"] = qty
        self._orders[oid] = order
        return {
            "mode": self.mode, "simulated": True,
            "order": {**order, "simulated": True},
            "note": (
                "mock 模式 — 委託僅存在記憶體，未送出任何真實下單。"
                "live 下單需 MEGA_STOCK_MODE=live + "
                "MEGA_STOCK_TRADING_ENABLED=on + 完成兆豐 API 聯調。"
            ),
        }

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        if not self.is_mock:
            if not self.trading_enabled:
                return {"error": (
                    "live 刪單未啟用：需 MEGA_STOCK_TRADING_ENABLED=on"
                ), "mode": self.mode}
            return self._live_call(
                "stock_cancel_order(live)",
                lambda: self._live_cancel(order_id),
            )
        order = self._orders.get(order_id)
        if order is None:
            return {"error": f"order {order_id!r} not found (mock book)"}
        if order["status"] != "open":
            return {"error": (
                f"order {order_id!r} is {order['status']} — "
                "only open orders can be cancelled"
            )}
        order["status"] = "cancelled"
        return {
            "mode": self.mode, "simulated": True,
            "order": {**order, "simulated": True},
        }


# ─── Live MegaAPI session (v0.9.26.0) ─────────────────────────
#
# Real 兆豐 MegaAPI wiring (megaspeedy component). The wrapper py
# is event-driven (callbacks from a DLL thread) — these helpers
# wrap it into synchronous request/response with threading.Event
# waits. NEVER imported in mock mode; stdout is redirected during
# the import because the SDK banner prints would corrupt the
# stdio MCP JSON-RPC channel.


def _import_live_sdk():
    """Import the megaspeedy SDK with stdout silenced."""
    import contextlib
    import io

    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            from megaSpeedy.spdOrderAPI import spdOrderAPI
            from megaSpeedy.spdQuoteAPI import spdQuoteAPI

        return spdOrderAPI, spdQuoteAPI
    except Exception as e:
        raise ImportError(
            "megaspeedy 元件載入失敗 — 請確認專案根目錄有 megaSpeedy/ "
            "資料夾（megaSpeedyAPI_64.dll + spdOrderAPI.py + "
            "spdQuoteAPI.py + Temp/ + speedyAPI_config.json）。"
            f"原始錯誤：{e!r}"
        ) from e


def _safe_str(b: Any) -> str:
    """Defensive decode for the SDK's c_char_p strings. The parent
    CB wrappers hard-code ``decode('Big5')`` which CRASHES on UTF-8
    Chinese (e.g. 「未授權使用」) — 'Exception ignored on calling
    ctypes callback' — and our OnLogonResponse override then never
    fires. Live-observed 2026-09-11 on the quote host."""
    if isinstance(b, (bytes, bytearray)):
        try:
            return b.decode("big5")
        except UnicodeDecodeError:
            try:
                return b.decode("utf-8")
            except UnicodeDecodeError:
                return b.decode("big5", errors="replace")
    return str(b)


def _order_api_class():
    """Build the spdOrderAPI subclass with event capture."""
    spdOrderAPI, _ = _import_live_sdk()
    import threading

    class _OrderApi(spdOrderAPI):
        """Event-collecting wrapper: OnConnected triggers the logon
        (official testPyOrderAPI pattern); replies/rejects land in
        events for synchronous waits."""

        def __init__(self):
            super().__init__()
            self.FIsLogon = False
            self._logon_ok: bool | None = None
            self._logon_event = threading.Event()
            self._logon_reply = ""
            self._reply_event = threading.Event()
            self._last_reply: dict[str, Any] = {}

        def set_creds(self, uid, pwd, account, branch):
            self._uid, self._pwd = uid, pwd
            self._account, self._branch = account, branch

        def OnConnected(self):  # DLL thread — logon right here
            try:
                ok = self.LogonProxy(self._uid, self._pwd, self._account)
                if not ok:
                    self._logon_ok = False
                    self._logon_reply = self.GetLastErrorMsg()
                    self._logon_event.set()
            except Exception as e:
                self._logon_ok = False
                self._logon_reply = repr(e)
                self._logon_event.set()

        def OnDisconnected(self):
            self.FIsLogon = False

        def OnLogonResponse(self, IsSucceed, ReplyString):
            self.FIsLogon = bool(IsSucceed)
            self._logon_ok = bool(IsSucceed)
            self._logon_reply = str(ReplyString)
            if IsSucceed:
                # 證券設定下單帳號（SetAccount per official sample）
                try:
                    self.SetAccount("TWSE", self._branch, self._account)
                except Exception:
                    pass
            self._logon_event.set()

        def OnReplyNewOrder(self, NID, UDD, Symbol, Price, Side,
                            OrderQty, OrderType, TimeInForce, OrderID):
            self._last_reply = {
                "kind": "new", "order_id": str(OrderID),
                "symbol": str(Symbol), "price": Price,
                "side": str(Side), "qty": OrderQty,
            }
            self._reply_event.set()

        def OnReplyCancelOrder(self, NID, UDD, Symbol, Price, Side,
                               OrderID):
            self._last_reply = {
                "kind": "cancel", "order_id": str(OrderID),
            }
            self._reply_event.set()

        def OnRejectOrder(self, NID, UDD, ActionFrom, ErrCode, ErrMsg):
            self._last_reply = {
                "kind": "reject", "err_code": str(ErrCode),
                "err_msg": str(ErrMsg),
            }
            self._reply_event.set()

        def OnFill(self, NID, UDD, OrderID, ReportSequence, FillPrice,
                   FillQty, FillTime):
            self._last_reply = {
                "kind": "fill", "order_id": str(OrderID),
                "fill_price": FillPrice, "fill_qty": FillQty,
            }
            self._reply_event.set()

        # ── defensive-decode CB overrides (v0.9.28.1) ──
        # The parent CB wrappers hard-code decode('Big5') and CRASH
        # on UTF-8 strings (「未授權使用」 etc.) — our event hooks
        # then never fire. Re-decode defensively ourselves.

        def OnLogonResponseCB(self, _, IsSucceed, ReplyString):
            self.OnLogonResponse(IsSucceed, _safe_str(ReplyString))

        def OnReplyNewOrderCB(self, _, NID, UDD, Symbol, Price, Side,
                              OrderQty, OrderType, TimeInForce, OrderID):
            self.OnReplyNewOrder(
                NID, UDD, _safe_str(Symbol), Price, _safe_str(Side),
                OrderQty, _safe_str(OrderType), _safe_str(TimeInForce),
                _safe_str(OrderID),
            )

        def OnReplyCancelOrderCB(self, _, NID, UDD, Symbol, Price,
                                 Side, OrderID):
            self.OnReplyCancelOrder(
                NID, UDD, _safe_str(Symbol), Price, _safe_str(Side),
                _safe_str(OrderID),
            )

        def OnRejectOrderCB(self, _, NID, UDD, ActionFrom, ErrCode,
                            ErrMsg):
            self.OnRejectOrder(
                NID, UDD, _safe_str(ActionFrom), _safe_str(ErrCode),
                _safe_str(ErrMsg),
            )

        def OnFillCB(self, _, NID, UDD, OrderID, ReportSequence,
                     FillPrice, FillQty, FillTime):
            self.OnFill(
                NID, UDD, _safe_str(OrderID), ReportSequence,
                FillPrice, FillQty, _safe_str(FillTime),
            )

    return _OrderApi


def _quote_api_class():
    spdOrderAPI, spdQuoteAPI = _import_live_sdk()
    import threading

    class _QuoteApi(spdQuoteAPI):
        """Collects the last orderbook/trade snapshot per symbol."""

        def __init__(self):
            super().__init__()
            self._snapshots: dict[str, dict[str, Any]] = {}
            self._contracts_done = threading.Event()

        def OnOrderBook(self, Exchange, Symbol, MsgTime, Msg):
            try:
                self._snapshots[str(Symbol)] = {
                    "symbol": str(Symbol),
                    "exchange": str(Exchange),
                    "bid_price": float(Msg.BidPrice1),
                    "bid_qty": int(Msg.BidQty1),
                    "ask_price": float(Msg.AskPrice1),
                    "ask_qty": int(Msg.AskQty1),
                    "bid2_price": float(Msg.BidPrice2),
                    "ask2_price": float(Msg.AskPrice2),
                    "derived_bid": float(
                        getattr(Msg, "DerivedBidPrice", 0),
                    ),
                    "derived_ask": float(
                        getattr(Msg, "DerivedAskPrice", 0),
                    ),
                    "msg_time": str(MsgTime),
                    "simulated": False,
                }
            except Exception:
                pass

        def OnTrade(self, Exchange, Symbol, MatchTime, MatchPrice,
                    MatchQty, IsTestMatch):
            snap = self._snapshots.setdefault(str(Symbol), {
                "exchange": str(Exchange), "simulated": False,
            })
            snap["last_price"] = float(MatchPrice)
            snap["last_qty"] = int(MatchQty)
            snap["last_time"] = str(MatchTime)

        def OnContractDownloadComplete(self):
            self._contracts_done.set()

        def OnLogonResponseCB(self, _, IsSucceed, ReplyString):
            # v0.9.28.1: defensive decode — the parent wrapper crashes
            # on UTF-8 replies and our hook never fires.
            self.OnLogonResponse(IsSucceed, _safe_str(ReplyString))

    return _QuoteApi


class MegaLiveSession:
    """Long-lived 兆豐 MegaAPI connection wrapper for one engine
    instance. Connects lazily on the first live call; thread-safe."""

    def __init__(self, creds: dict[str, Any]):
        self.c = creds
        self._order: Any = None
        self._quote: Any = None
        import threading

        self._lock = threading.Lock()

    def ensure_order(self) -> Any:
        with self._lock:
            if (
                self._order is not None
                and getattr(self._order, "FIsLogon", False)
            ):
                return self._order
            _OrderApi = _order_api_class()
            api = _OrderApi()
            api.set_creds(
                self.c["uid"], self.c["password"],
                self.c["account"], self.c["branch"],
            )
            # v0.9.28.2: the TRADING login itself requires the
            # signature certificate — EnableMEGACA BEFORE Connect
            # (official sample order: EnableMEGACA → Connect →
            # OnConnected → LogonProxy). Live-observed 2026-09-11:
            # without it LogonProxy fails 「簽章元件尚未安裝或無有效的
            # 憑證」. QUOTES don't need a cert (quote host logon OK).
            if self.c.get("cert_path"):
                if not api.EnableMEGACA(
                    self.c["cert_path"], self.c["uid"],
                    self.c["cert_password"],
                ):
                    raise RuntimeError(
                        "憑證載入失敗（EnableMEGACA）："
                        f"{api.GetLastErrorMsg()} — 請確認 "
                        "MEGA_STOCK_CERT_PATH / MEGA_STOCK_CERT_PASSWORD"
                    )
            # v0.9.28.1: Connect() is ASYNC — it may return False
            # while the handshake continues (observed live: Connect
            # False → OnConnected + LogonProxy fired right after).
            # NEVER treat a falsy return as fatal; wait for the
            # logon event instead (set by OnLogonResponse, or by the
            # OnConnected handler on an immediate LogonProxy failure).
            api.Connect(
                self.c["order_host"], self.c["order_port"], 10,
            )
            if not api._logon_event.wait(_LIVE_CONN_WAIT_S):
                raise RuntimeError(
                    f"登入逾時（{_LIVE_CONN_WAIT_S:.0f}s，"
                    f"OnLogonResponse 未回）— "
                    f"connected={api.IsConnected()} last_error="
                    f"{api.GetLastErrorMsg()!r} "
                    f"({self.c['order_host']}:{self.c['order_port']})"
                )
            if not api.FIsLogon:
                reply = api._logon_reply
                hint = ""
                if ("簽章" in reply or "憑證" in reply) and not \
                        self.c.get("cert_path"):
                    hint = (
                        " — live 交易登入需要電子憑證：請先設定 "
                        "MEGA_STOCK_CERT_PATH / MEGA_STOCK_CERT_PASSWORD"
                        "（EnableMEGACA 於連線前呼叫；查詢行情主機不需"
                        "憑證）"
                    )
                raise RuntimeError(f"登入失敗：{reply}{hint}")
            self._order = api
            return api

    def query(self, fn: str, query_param: dict) -> Any:
        api = self.ensure_order()
        raw = getattr(api, fn)(query_param)
        try:
            return json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, json.JSONDecodeError):
            return {"raw": raw}

    def kline(self, symbol: str) -> Any:
        api = self.ensure_order()
        for fn in ("GetAdjustedDKChartData", "GetDKChartData"):
            try:
                raw = getattr(api, fn)(symbol)
                if raw:
                    # v0.9.28.3: parse the JSON string into a
                    # structured payload ({"Title": [...], "Data":
                    # [[date, symbol, name, open, high, low, close,
                    #   volume, ...], ...]})
                    parsed = None
                    if isinstance(raw, str):
                        try:
                            parsed = json.loads(raw)
                        except json.JSONDecodeError:
                            parsed = {"raw": raw}
                    else:
                        parsed = raw
                    if isinstance(parsed, dict) and "Title" in parsed:
                        return {
                            "fn": fn, "title": parsed.get("Title"),
                            "rows": parsed.get("Data") or [],
                        }
                    return {"fn": fn, "data": parsed}
            except Exception:
                continue
        raise RuntimeError(f"K線查詢失敗（{symbol}）")

    def place(self, symbol, price, side, qty, order_type, tif,
              ts, pe, to, cert_path, cert_pwd) -> dict:
        api = self.ensure_order()
        if cert_path:
            api.EnableMEGACA(cert_path, self.c["uid"], cert_pwd)
        nid = api.SendNewOrderEx(
            self.c["market"], "divine-sense-agent", symbol, float(price),
            "B" if str(side) == "buy" else "S", int(qty),
            "L" if order_type == "limit" else "M", tif, ts, pe, "0",
        )
        if nid == 0:
            return {"ok": False, "error": api.GetLastErrorMsg()}
        if not api._reply_event.wait(_LIVE_FIRE_WAIT_S):
            return {
                "ok": False,
                "error": "委託送出但未收到回報（逾時）；請以委託查詢確認",
            }
        api._reply_event.clear()
        r = dict(api._last_reply)
        if r.get("kind") == "reject":
            return {"ok": False, "error": (
                f"委託被拒（{r.get('err_code')} {r.get('err_msg')}）"
            )}
        return {"ok": True, "reply": r, "nid": nid}

    def cancel(self, symbol, price, side, order_id, ts) -> dict:
        api = self.ensure_order()
        nid = api.SendCancelOrderEx(
            self.c["market"], "divine-sense-agent", symbol, float(price),
            "B" if str(side) == "buy" else "S", str(order_id), ts, "0",
        )
        if nid == 0:
            return {"ok": False, "error": api.GetLastErrorMsg()}
        if api._reply_event.wait(_LIVE_FIRE_WAIT_S):
            api._reply_event.clear()
            return {"ok": True, "reply": dict(api._last_reply)}
        return {"ok": True, "note": "刪單已送出（未收到回報，請查詢確認）"}

    def snapshot(self, symbol: str) -> dict:
        with self._lock:
            if self._quote is None:
                _QuoteApi = _quote_api_class()
                q = _QuoteApi()
                if not q.Logon(
                    self.c["quote_host"], self.c["quote_port"],
                    self.c["uid"], self.c["password"], True,
                ):
                    raise RuntimeError(
                        f"行情登入失敗：{q.GetLastErrorMsg()}"
                    )
                self._quote = q
            q = self._quote
        # v0.9.28.1: the first Logon triggers a 64k-contract download
        # (~9 s live-observed) — wait for it or Stocks lookup misses.
        if not q._contracts_done.wait(20):
            logger.warning(
                "telegram/quote: 商品資料下載未在 20s 內完成 — "
                "改用 Subscribe(Exchange, Symbol) 直接訂閱",
            )
        contract = (getattr(q, "Stocks", {}) or {}).get(symbol)
        if contract is not None:
            q.SubscribeContract(contract)
        else:
            q.Subscribe("TWSE", symbol)
        import time as _t

        deadline = _t.time() + _LIVE_QUOTE_WAIT_S
        while _t.time() < deadline:
            snap = q._snapshots.get(symbol)
            if snap and ("bid_price" in snap or "last_price" in snap):
                q.UnsubscribeAll()
                return snap
            _t.sleep(0.2)
        q.UnsubscribeAll()
        return {}

    def close(self) -> None:
        for obj in (self._order, self._quote):
            try:
                if obj is not None and hasattr(obj, "Disconnect"):
                    obj.Disconnect()
            except Exception:
                pass
        self._order = self._quote = None


def _find_order_row(rows: Any, order_id: str):
    """Recursively search the queryStkOrder payload for the row whose
    order id matches ``order_id``; return (symbol, price, side).
    The real row shape is only known after the first live query —
    the search is defensive (common MegaAPI field names)."""
    keys_id = ("OrderID", "OrderNo", "OrdNo", "ordno", "order_id",
               "Order_ID")
    keys_sym = ("Symbol", "StockSymbol", "ProdCode", "symbol",
                "StockID")
    keys_px = ("Price", "OrderPrice", "price", "LimitPrice")
    keys_side = ("Side", "BS", "BuySell", "side")

    def _norm_id(v: Any) -> str:
        return str(v).strip() if v is not None else ""

    def _walk(node: Any) -> Any:
        if isinstance(node, dict):
            ids = [_norm_id(node.get(k)) for k in keys_id
                   if node.get(k) is not None]
            if any(i == order_id for i in ids):
                sym = next((node[k] for k in keys_sym
                            if node.get(k) is not None), None)
                px = next((node[k] for k in keys_px
                           if node.get(k) is not None), 0)
                sd = next((node[k] for k in keys_side
                           if node.get(k) is not None), None)
                return (sym, px, "buy" if str(sd).upper() in
                        ("B", "BUY", "1") else "sell")
            for k, v in node.items():
                r = _walk(v)
                if r is not None:
                    return r
        elif isinstance(node, list):
            for v in node:
                r = _walk(v)
                if r is not None:
                    return r
        return None

    return _walk(rows)