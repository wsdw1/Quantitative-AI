from __future__ import annotations

import unittest

import pandas as pd

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


if __name__ == "__main__":
    unittest.main()
