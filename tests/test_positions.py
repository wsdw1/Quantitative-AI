from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

import storage.database as database
from market_analysis.positions import (
    board_positions,
    classify,
    clear_positions_cache,
    compute_position,
    industry_positions,
    market_positions,
    market_regime,
    reversal_confirmed,
    stock_positions,
)


def _index_frame(closes: list[float], pct_chg: float = 0.5, vol: float = 1000.0, code: str = "000001.SH") -> pd.DataFrame:
    dates = pd.bdate_range("2025-01-01", periods=len(closes))
    frame = pd.DataFrame(index=dates)
    frame["open"] = closes
    frame["high"] = [v + 1 for v in closes]
    frame["low"] = [v - 1 for v in closes]
    frame["close"] = closes
    frame["vol"] = vol
    frame["amount"] = [v * vol for v in closes]
    frame["pct_chg"] = pct_chg
    return frame


def _stock_frame(closes: np.ndarray) -> pd.DataFrame:
    dates = pd.bdate_range("2025-01-01", periods=len(closes))
    close = pd.Series(closes, index=dates, dtype=float)
    frame = pd.DataFrame(index=dates)
    frame["open"] = close * 0.995
    frame["high"] = close * 1.01
    frame["low"] = close * 0.99
    frame["close"] = close
    frame["volume"] = 1_000_000.0
    frame["amount"] = close * 1_000_000
    frame["pct_chg"] = close.pct_change().fillna(0) * 100
    frame["turnover_n"] = frame["amount"].rolling(20, min_periods=1).sum()
    return frame


class PositionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_path = database.DB_PATH
        self.tmp = tempfile.TemporaryDirectory()
        database.DB_PATH = Path(self.tmp.name) / "positions.db"
        clear_positions_cache()

    def tearDown(self) -> None:
        database.DB_PATH = self.original_path
        clear_positions_cache()
        self.tmp.cleanup()

    def test_compute_position_monotonic_series(self) -> None:
        close = pd.Series(np.linspace(10, 20, 300))
        pos = compute_position(close, window=252)
        self.assertEqual(float(pos.iloc[-1]), 100.0)
        self.assertTrue(np.isnan(pos.iloc[250]))
        self.assertFalse(np.isnan(pos.iloc[251]))

    def test_compute_position_flat_series_is_high(self) -> None:
        close = pd.Series(np.full(300, 15.0))
        pos = compute_position(close, window=252)
        self.assertEqual(float(pos.iloc[-1]), 100.0)

    def test_reversal_confirmed_boundary(self) -> None:
        frame = _index_frame([10.0] * 20 + [10.5], pct_chg=1.0, vol=1000.0)
        frame.iloc[-1, frame.columns.get_loc("vol")] = 1300.0
        self.assertTrue(reversal_confirmed(frame, frame.index[-1]))
        frame.iloc[-1, frame.columns.get_loc("vol")] = 1100.0
        self.assertFalse(reversal_confirmed(frame, frame.index[-1]))

    def test_classify_precedence_and_boundaries(self) -> None:
        self.assertEqual(classify(90.0, reversal=False), "risk")
        self.assertEqual(classify(85.0, reversal=False), "risk")
        self.assertEqual(classify(84.9, reversal=False), "neutral")
        self.assertEqual(classify(15.0, reversal=True), "bottom")
        self.assertEqual(classify(15.0, reversal=False), "neutral")
        self.assertEqual(classify(None, reversal=True), "neutral")
        self.assertEqual(classify(0.0, reversal=False), "neutral")

    def test_market_positions_and_regime_with_db(self) -> None:
        database.upsert_index_prices({"000001.SH": _index_frame(np.linspace(10, 20, 280).tolist(), code="000001.SH")})
        payload = market_positions(as_of="2025-12-31")
        self.assertTrue(payload["available"])
        self.assertEqual(payload["market"][0]["code"], "000001.SH")
        self.assertGreaterEqual(payload["market"][0]["position"], 90.0)
        self.assertEqual(market_regime(as_of="2025-12-31")["regime"], "risk")

    def test_no_data_degrades_to_neutral(self) -> None:
        payload = market_positions()
        self.assertFalse(payload["available"])
        self.assertEqual(payload["regime"], "neutral")
        self.assertEqual(board_positions()["available"], False)
        self.assertEqual(industry_positions()["available"], False)
        self.assertEqual(market_regime()["regime"], "neutral")
        self.assertEqual(stock_positions(["000001"]), {})


if __name__ == "__main__":
    unittest.main()
