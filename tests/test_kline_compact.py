"""v0.9.98.1 — live kline compaction + offload threshold tuning.

* MegaStockEngine.get_kline (live) now honours ``bars``: the raw
  GetDKChartData payload (FULL price history, hundreds of wide rows)
  is trimmed to the latest ``bars`` candles in the compact
  {date,open,high,low,close,volume} shape (same keys as mock
  candles); the FULL raw rows are written to the offload file
  (read-sandbox, read_file-able) whenever anything is omitted.
* DEFAULT_KLINE_BARS 20 -> 10.
* (config-only) CHAT_OFFLOAD_THRESHOLD_CHARS=6000 in live .env —
  fat tool results offload at 6k chars instead of 20k.
"""
import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

from src.mega_stock_tools import (  # noqa: E402
    DEFAULT_KLINE_BARS, MegaStockEngine,
)


def _engine(tmp_home, monkeypatch=None):
    """Mock-mode engine (no network); compact path is mode-agnostic."""
    monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_home))
    import importlib

    import src.mega_stock_tools as M

    importlib.reload(M)
    eng = M.MegaStockEngine()
    return eng, M


def _raw_rows(n):
    """Rows in the REAL API order: NEWEST-FIRST (live-verified —
    rows[0] = 20260922, rows[-1] = 20220810)."""
    return [
        [f"20260923{i:03d}", "2344", "華邦電", 100.0 + i, 101.0 + i,
         99.0 + i, 100.5 + i, 40000 + i * 10, "extra1", "extra2"]
        for i in range(n, 0, -1)  # newest (highest i) first
    ]


def _live_payload(n, interval="day"):
    return {
        "mode": "live", "interval": interval, "simulated": False,
        "fn": "GetDKChartData", "title": ["日期", "open"],
        "rows": _raw_rows(n),
    }


class TestLiveKlineCompaction(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path

        self._tmp = Path(tempfile.mkdtemp(prefix="kline-"))
        self._old = {
            k: os.environ.get(k) for k in (
                "QDRANT_RAG_HOME", "MEGA_STOCK_MODE",
                "QDRANT_OFFLOAD_DIR", "QDRANT_FILESYSTEM_READ_ROOT",
            )
        }
        os.environ["QDRANT_RAG_HOME"] = str(self._tmp)
        os.environ["MEGA_STOCK_MODE"] = "mock"
        os.environ.pop("QDRANT_OFFLOAD_DIR", None)
        os.environ.pop("QDRANT_FILESYSTEM_READ_ROOT", None)
        import importlib

        import src.mega_stock_tools as M

        importlib.reload(M)
        self.M = M
        self.eng = M.MegaStockEngine()

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        import importlib

        importlib.reload(self.M)

    def test_default_bars_is_10(self):
        self.assertEqual(DEFAULT_KLINE_BARS, 10)

    def test_live_rows_truncated_and_compacted(self, ):
        data = _live_payload(300)
        out = self.eng._compact_live_kline(data, 10, "2344")
        self.assertEqual(out["requested_bars"], 10)
        self.assertEqual(out["total_available"], 300)
        self.assertEqual(out["omitted"], 290)
        self.assertEqual(len(out["candles"]), 10)
        first = out["candles"][0]
        # NEWEST-first slice: the newest row (highest i) must be kept —
        # the v0.9.98.1 tail-slice returned the OLDEST rows (2022 bug)
        self.assertEqual(first["date"], "20260923300")
        self.assertEqual(first["open"], 100.0 + 300)
        self.assertEqual(first["close"], 100.5 + 300)
        self.assertEqual(first["volume"], 40000 + 300 * 10)
        self.assertNotIn("rows", out)
        self.assertIn("full_file", out)
        with open(out["full_file"], encoding="utf-8") as f:
            full = json.load(f)
        self.assertEqual(len(full["rows"]), 300)
        self.assertIn("note", out)

    def test_short_history_untouched_no_file(self):
        data = _live_payload(5)
        out = self.eng._compact_live_kline(data, 10, "2344")
        self.assertIs(out, data)  # nothing to omit → original payload
        # no kline files written
        offload = os.path.join(self._tmp, "chat_offloads")
        self.assertFalse(os.path.isdir(offload))

    def test_offload_dir_env_wins(self):
        import tempfile

        d = tempfile.mkdtemp(prefix="kline-off-")
        os.environ["QDRANT_OFFLOAD_DIR"] = d
        try:
            out = self.eng._compact_live_kline(_live_payload(30), 5, "2344")
            self.assertTrue(os.path.isfile(out["full_file"]))
            self.assertTrue(
                os.path.dirname(out["full_file"]).lower().startswith(
                    d.lower())
            )
        finally:
            os.environ.pop("QDRANT_OFFLOAD_DIR", None)

    def test_ascending_payload_falls_back_to_tail(self):
        """Defensive: an ASCENDING payload (oldest first) must still
        yield the NEWEST bars — take the tail."""
        data = _live_payload(300)
        data["rows"] = list(reversed(data["rows"]))  # oldest first
        out = self.eng._compact_live_kline(data, 10, "2344")
        self.assertEqual(len(out["candles"]), 10)
        # tail slice → newest row (highest i) is last
        self.assertEqual(out["candles"][0]["date"], "20260923291")
        self.assertEqual(out["candles"][9]["date"], "20260923300")

    def test_fail_open_on_bad_payload(self):
        out = self.eng._compact_live_kline({"mode": "live"}, 10, "2344")
        self.assertEqual(out, {"mode": "live"})  # no rows → unchanged
        # rows of unexpected shape: _date_of() = "" for both ends →
        # newest_first ties TRUE → head slice keeps the FIRST row
        data = {"rows": ["not-a-list", {"k": 1}], "title": None}
        out = self.eng._compact_live_kline(data, 1, "2344")
        self.assertEqual(out["candles"][0], {"raw": "not-a-list"})

    def test_mock_kline_unchanged(self):
        out = self.eng.get_kline("2344")
        self.assertTrue(out["simulated"])
        self.assertEqual(len(out["candles"]), 10)  # default now 10
        self.assertEqual(
            set(out["candles"][0]),
            {"date", "open", "high", "low", "close", "volume"},
        )


if __name__ == "__main__":
    unittest.main()