from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from strategies._utils import safe_bool, safe_float, to_scalar


class StrategyUtilsTests(unittest.TestCase):
    def test_safe_float_keeps_finite(self) -> None:
        self.assertEqual(safe_float(3.5), 3.5)
        self.assertEqual(safe_float(np.float64(2.0)), 2.0)

    def test_safe_float_uses_default_for_non_finite(self) -> None:
        self.assertEqual(safe_float(float("nan")), 0.0)
        self.assertEqual(safe_float(float("inf")), 0.0)
        self.assertEqual(safe_float(float("-inf"), default=9.0), 9.0)

    def test_safe_float_uses_default_for_bad_input(self) -> None:
        self.assertEqual(safe_float("abc"), 0.0)

    def test_safe_bool_treats_non_finite_as_false(self) -> None:
        self.assertTrue(safe_bool(1))
        self.assertFalse(safe_bool(0))
        self.assertFalse(safe_bool(float("nan")))
        self.assertFalse(safe_bool(None))

    def test_to_scalar_unwraps_series_last_value(self) -> None:
        series = pd.Series([1.0, 2.0])
        self.assertEqual(to_scalar(series), 2.0)


if __name__ == "__main__":
    unittest.main()
