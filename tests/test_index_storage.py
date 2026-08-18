from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

import storage.database as database


def _index_frame(closes: list[float], code: str = "000001.SH") -> pd.DataFrame:
    dates = pd.bdate_range("2026-01-01", periods=len(closes))
    frame = pd.DataFrame(index=dates)
    frame["open"] = closes
    frame["high"] = [v + 1 for v in closes]
    frame["low"] = [v - 1 for v in closes]
    frame["close"] = closes
    frame["vol"] = 1000.0
    frame["amount"] = [v * 1000 for v in closes]
    frame["pct_chg"] = 0.5
    return frame


class IndexStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_path = database.DB_PATH
        self.tmp = tempfile.TemporaryDirectory()
        database.DB_PATH = Path(self.tmp.name) / "index.db"

    def tearDown(self) -> None:
        database.DB_PATH = self.original_path
        self.tmp.cleanup()

    def test_upsert_and_load_round_trip(self) -> None:
        frame = _index_frame([10.0, 11.0, 12.0])
        rows = database.upsert_index_prices({"000001.SH": frame})
        self.assertEqual(rows, 3)
        loaded = database.load_index_prices(codes=["000001.SH"])
        self.assertEqual(list(loaded), ["000001.SH"])
        self.assertEqual(loaded["000001.SH"].index[0].strftime("%Y-%m-%d"), "2026-01-01")
        self.assertEqual(float(loaded["000001.SH"].loc[loaded["000001.SH"].index[0], "close"]), 10.0)

    def test_upsert_overwrites_same_key(self) -> None:
        frame = _index_frame([10.0, 11.0, 12.0])
        database.upsert_index_prices({"000001.SH": frame})
        frame2 = frame.copy()
        frame2.iloc[-1, frame2.columns.get_loc("close")] = 99.0
        database.upsert_index_prices({"000001.SH": frame2})
        loaded = database.load_index_prices()["000001.SH"]
        self.assertEqual(float(loaded["close"].iloc[-1]), 99.0)
        self.assertEqual(len(loaded), 3)

    def test_signature_and_codes(self) -> None:
        self.assertEqual(database.index_price_codes(), set())
        self.assertIsNone(database.index_price_signature()[1])
        database.upsert_index_prices({"399006.SZ": _index_frame([1.0, 2.0])})
        self.assertEqual(database.index_price_codes(), {"399006.SZ"})
        self.assertEqual(database.index_price_signature()[0], 2)


if __name__ == "__main__":
    unittest.main()
