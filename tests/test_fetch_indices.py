from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

import storage.database as database
import pipeline.fetch_indices as fetch_indices


class FakePro:
    def index_daily(self, ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        dates = pd.bdate_range("2026-01-05", periods=3).strftime("%Y%m%d")
        return pd.DataFrame({
            "ts_code": [ts_code] * 3,
            "trade_date": dates,
            "close": [10.0, 10.5, 11.0],
            "open": [9.9, 10.4, 10.9],
            "high": [10.1, 10.6, 11.1],
            "low": [9.8, 10.3, 10.8],
            "pre_close": [9.9, 10.0, 10.5],
            "change": [0.1, 0.5, 0.5],
            "pct_chg": [1.01, 5.0, 4.76],
            "vol": [1000.0, 1200.0, 900.0],
            "amount": [10000.0, 12000.0, 9000.0],
        })

    def stock_basic(self, **kwargs) -> pd.DataFrame:
        return pd.DataFrame({
            "ts_code": ["000001.SZ"],
            "name": ["平安银行"],
            "industry": ["银行"],
            "market": ["主板"],
        })


class FetchIndicesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_path = database.DB_PATH
        self.tmp = tempfile.TemporaryDirectory()
        database.DB_PATH = Path(self.tmp.name) / "fetch.db"
        database.init_db()

    def tearDown(self) -> None:
        database.DB_PATH = self.original_path
        self.tmp.cleanup()

    def test_normalize_index_frame(self) -> None:
        raw = FakePro().index_daily("000001.SH", "20260101", "20260131")
        frame = fetch_indices.normalize_index_frame(raw)
        self.assertIsInstance(frame.index, pd.DatetimeIndex)
        self.assertEqual(list(frame.columns), ["open", "high", "low", "close", "vol", "amount", "pct_chg"])
        self.assertEqual(len(frame), 3)

    def test_run_fetches_and_persists(self) -> None:
        result = fetch_indices.run(
            pro=FakePro(),
            codes=["000001.SH"],
            start_date="20260101",
            end_date="20260131",
            sync_industries=False,
        )
        self.assertEqual(result["rows"], 3)
        self.assertEqual(database.index_price_codes(), {"000001.SH"})

    def test_incremental_starts_after_latest_date(self) -> None:
        fetch_indices.run(pro=FakePro(), codes=["000001.SH"], start_date="20260101", end_date="20260131", sync_industries=False)
        with patch.object(fetch_indices, "fetch_index_bars", wraps=fetch_indices.fetch_index_bars) as mocked:
            fetch_indices.run(pro=FakePro(), codes=["000001.SH"], start_date="20260101", end_date="20260131", sync_industries=False)
            called = [call.args for call in mocked.call_args_list]
            self.assertTrue(all(start >= "20260107" for _, _, start, _ in called))

    def test_sync_industries_persists_mapping(self) -> None:
        database.upsert_stocks(pd.DataFrame(
            [{"代码": "000001", "名称": "平安银行", "ts_code": "000001.SZ", "market": "主板", "list_date": "19910403"}]
        ))
        fetch_indices.run(pro=FakePro(), codes=[], start_date="20260101", end_date="20260131", sync_industries=True)
        self.assertEqual(database.load_stock_industries(), {"000001": "银行"})


if __name__ == "__main__":
    unittest.main()
