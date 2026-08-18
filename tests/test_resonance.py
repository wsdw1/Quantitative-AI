from __future__ import annotations

import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from pipeline.schemas import Candidate
from strategies.base import StrategyContext
from strategies.registry import get_strategy, list_strategies
from strategies.resonance.strategy import ResonanceStrategy


class _FakeSubStrategy:
    meta = type("Meta", (), {"default_config": {}})()

    def __init__(self, strategy_id: str) -> None:
        self.strategy_id = strategy_id

    def warmup_bars(self, _: dict) -> int:
        return 100

    def prepare_all(self, data: dict[str, pd.DataFrame], _: dict, __=None) -> dict[str, pd.DataFrame]:
        result = {}
        for code, frame in data.items():
            item = frame.copy()
            item["kdj_col"] = 1.0
            item["mom_col"] = 2.0
            result[code] = item
        return result

    def select_prepared(self, data: dict[str, pd.DataFrame], _: dict, context) -> list:
        return []


class ResonanceSkeletonTests(unittest.TestCase):
    def test_strategy_is_registered(self) -> None:
        strategy = get_strategy("resonance")
        self.assertIsInstance(strategy, ResonanceStrategy)
        self.assertIn("resonance", {item.id for item in list_strategies()})

    def test_default_config_has_required_keys(self) -> None:
        cfg = ResonanceStrategy()._cfg({})
        self.assertEqual(cfg["sub_strategies"], ["b1", "volume_new_high", "high_52w_momentum"])
        self.assertEqual(cfg["min_hits"], 2)
        self.assertEqual(cfg["risk_high_threshold"], 85.0)
        self.assertEqual(cfg["bottom_low_threshold"], 15.0)
        self.assertEqual(cfg["reversal_volume_ratio"], 1.2)
        self.assertEqual(cfg["high_position_action"], "downweight")
        self.assertEqual(cfg["downweight_factor"], 0.5)
        self.assertEqual(cfg["bottom_stock_pos_cap"], 30.0)

    def test_warmup_covers_longest_sub_strategy_and_252(self) -> None:
        strategy = ResonanceStrategy()
        warmup = strategy.warmup_bars({})
        self.assertGreaterEqual(warmup, 252 + 30)

    def test_prepare_all_merges_columns_from_sub_strategies(self) -> None:
        strategy = ResonanceStrategy(registry=lambda sid: _FakeSubStrategy(sid))
        frame = pd.DataFrame(
            {"close": [10.0, 11.0, 12.0], "volume": [100.0, 100.0, 100.0], "amount": [1000.0, 1000.0, 1000.0]},
            index=pd.bdate_range("2026-01-01", periods=3),
        )
        merged = strategy.prepare_all({"000001": frame}, strategy._cfg({}))
        self.assertIn("kdj_col", merged["000001"].columns)
        self.assertIn("mom_col", merged["000001"].columns)


def _candidate(code: str, score: float, strategy: str = "resonance") -> Candidate:
    return Candidate(
        code=code, name=code, date="2026-08-18", strategy=strategy,
        close=10.0, turnover_n=1000.0, score=score,
    )


class ResonanceMergeTests(unittest.TestCase):
    def _context(self) -> StrategyContext:
        return StrategyContext(
            pick_date=pd.Timestamp("2026-08-18"),
            names={"000001": "股票1", "000002": "股票2", "000003": "股票3"},
            pool={"000001", "000002", "000003"},
            progress_enabled=False,
        )

    def test_merge_requires_min_hits_and_sums_percentile_ranks(self) -> None:
        strategy = ResonanceStrategy(registry=lambda sid: _FakeSubStrategy(sid))
        cfg = strategy._cfg({})
        sub_results = [
            [_candidate("000001", 1.0, strategy="s1"), _candidate("000002", 2.0, strategy="s1")],
            [_candidate("000002", 1.0, strategy="s2"), _candidate("000003", 2.0, strategy="s2")],
        ]
        merged = strategy._merge(sub_results, cfg)
        codes = {item.code for item in merged}
        self.assertEqual(codes, {"000002"})
        hit = next(item for item in merged if item.code == "000002")
        self.assertEqual(hit.extra["hit_count"], 2)
        self.assertEqual(hit.strategy, "resonance")
        self.assertIn("hits", hit.extra)

    def test_risk_regime_downweights_high_position_stocks(self) -> None:
        strategy = ResonanceStrategy(registry=lambda sid: _FakeSubStrategy(sid))
        candidates = [_candidate("000001", 1.0), _candidate("000002", 1.0)]
        dates = pd.bdate_range("2026-07-20", periods=300)
        data = {
            "000001": pd.DataFrame({"close": np.linspace(10, 20, 300), "pos252": 90.0}, index=dates),
            "000002": pd.DataFrame({"close": np.linspace(10, 20, 300), "pos252": 20.0}, index=dates),
        }
        fake_regime = {
            "regime": "risk", "available": True,
            "market": [{"code": "000001.SH", "position": 90.0}],
            "boards": {},
        }
        with patch("market_analysis.positions.market_regime", return_value=fake_regime), \
             patch("market_analysis.positions.stock_positions", return_value={}):
            result = strategy._apply_regime(candidates, strategy._cfg({}), self._context(), data)
        by_code = {item.code: item for item in result}
        self.assertEqual(by_code["000001"].extra["regime"], "risk")
        self.assertEqual(by_code["000001"].score, 0.5)
        self.assertEqual(by_code["000002"].score, 1.0)

    def test_bottom_regime_adds_pool_candidates(self) -> None:
        strategy = ResonanceStrategy(registry=lambda sid: _FakeSubStrategy(sid))
        frame = pd.DataFrame(
            {"close": [10.0] * 20 + [10.5], "pct_chg": [0.0] * 20 + [5.0],
             "volume": [1000.0] * 21, "amount": [10000.0] * 21, "pos252": 5.0},
            index=pd.bdate_range("2026-07-20", periods=21),
        )
        frame.iloc[-1, frame.columns.get_loc("volume")] = 1400.0
        data = {"000003": frame}
        context = StrategyContext(
            pick_date=frame.index[-1],
            names={"000003": "股票3"},
            pool={"000003"},
            progress_enabled=False,
        )
        fake_regime = {"regime": "bottom", "available": True, "market": [], "boards": {}}
        with patch("market_analysis.positions.market_regime", return_value=fake_regime), \
             patch("market_analysis.positions.stock_positions", return_value={}):
            result = strategy._apply_regime([], strategy._cfg({}), context, data)
        self.assertEqual([item.code for item in result], ["000003"])
        self.assertTrue(result[0].extra["bottom_signal"])

    def test_bottom_pool_skips_frame_without_volume_column(self) -> None:
        strategy = ResonanceStrategy(registry=lambda sid: _FakeSubStrategy(sid))
        frame = pd.DataFrame(
            {"close": [10.0] * 21, "turnover_n": [10000.0] * 21, "pos252": 5.0},
            index=pd.bdate_range("2026-07-20", periods=21),
        )
        context = StrategyContext(
            pick_date=frame.index[-1],
            names={"000003": "股票3"},
            pool={"000003"},
            progress_enabled=False,
        )
        fake_regime = {"regime": "bottom", "available": True, "market": [], "boards": {}}
        with patch("market_analysis.positions.market_regime", return_value=fake_regime), \
             patch("market_analysis.positions.stock_positions", return_value={}):
            result = strategy._apply_regime([], strategy._cfg({}), context, {"000003": frame})
        self.assertEqual(result, [])

    def test_prepare_all_adds_pos252_column(self) -> None:
        strategy = ResonanceStrategy(registry=lambda sid: _FakeSubStrategy(sid))
        closes = list(range(1, 301))
        frame = pd.DataFrame(
            {"close": closes, "volume": [1000.0] * 300, "amount": [10000.0] * 300},
            index=pd.bdate_range("2025-01-01", periods=300),
        )
        merged = strategy.prepare_all({"000001": frame}, strategy._cfg({}))
        self.assertIn("pos252", merged["000001"].columns)
        self.assertEqual(float(merged["000001"]["pos252"].iloc[-1]), 100.0)


if __name__ == "__main__":
    unittest.main()
