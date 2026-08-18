"""Run calibration/validation backtests for resonance vs baselines.

Usage:
    python scripts/run_resonance_verification.py                       # all windows, all runs
    python scripts/run_resonance_verification.py --windows validation  # one window only
    python scripts/run_resonance_verification.py --runs baseline-b1,resonance-full

Requires: local stock history >= 1500 trading days (Task 0) and index data
(Task 3: python pipeline/fetch_indices.py).
"""
from __future__ import annotations

import sys
import argparse
import json
from pathlib import Path

import pandas as pd

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from backtest.schemas import BacktestRequest  # noqa: E402
from backtest.service import run_backtest  # noqa: E402

HOLDING_PERIODS = [5, 10, 20]
RUNS = [
    ("b1", "baseline-b1"),
    ("volume_new_high", "baseline-volume_new_high"),
    ("high_52w_momentum", "baseline-high_52w_momentum"),
    ("resonance", "resonance-no-regime"),
    ("resonance", "resonance-full"),
]
WINDOWS = [
    ("2023-01-03", "2024-06-28", "calibration-1"),
    ("2024-07-01", "2025-06-30", "calibration-2"),
    ("2025-07-01", "2026-08-18", "validation"),
]


def _config(strategy_id: str, with_regime: bool) -> dict:
    resonance = {
        "enabled": True,
        "sub_strategies": ["b1", "volume_new_high", "high_52w_momentum"],
        "min_hits": 2,
        "max_candidates": 30,
        "risk_high_threshold": 85,
        "bottom_low_threshold": 15,
        "reversal_volume_ratio": 1.2,
        "high_position_action": "downweight",
        "downweight_factor": 0.5,
        "bottom_fishing_enabled": with_regime,
        "bottom_stock_pos_cap": 30,
    }
    return {
        "global": {"adjust": "qfq", "top_m": 3000, "n_turnover_days": 43, "markets": ["main", "gem", "star", "bse"]},
        "strategies": {strategy_id: resonance if strategy_id == "resonance" else {}},
    }


def main(only_windows: list[str] | None = None, only_runs: list[str] | None = None) -> None:
    rows: list[dict] = []
    out_dir = _ROOT / "data" / "resonance_verification"
    out_dir.mkdir(parents=True, exist_ok=True)
    windows = [item for item in WINDOWS if only_windows is None or item[2] in only_windows]
    runs = [item for item in RUNS if only_runs is None or item[1] in only_runs]
    for start, end, phase in windows:
        for strategy_id, label in runs:
            try:
                request = BacktestRequest(
                    strategy_id=strategy_id,
                    start_date=start,
                    end_date=end,
                    holding_periods=HOLDING_PERIODS,
                    config=_config(strategy_id, with_regime="full" in label),
                )
                result = run_backtest(f"verify-{phase}-{label}", request)
                metrics = result.metrics
                rows.append({
                    "phase": phase,
                    "label": label,
                    "strategy_id": strategy_id,
                    "signal_count": metrics["signal_count"],
                    "win_rate_pct": metrics["win_rate_pct"],
                    "average_return_pct": metrics["average_return_pct"],
                    "profit_factor": metrics["profit_factor"],
                    "signal_day_count": metrics["signal_day_count"],
                    "error": None,
                })
                _append_row(out_dir, rows[-1])
                print(phase, label, metrics["signal_count"], metrics["win_rate_pct"], metrics["average_return_pct"])
            except Exception as exc:  # noqa: BLE001
                row = {
                    "phase": phase, "label": label, "strategy_id": strategy_id,
                    "signal_count": None, "win_rate_pct": None, "average_return_pct": None,
                    "profit_factor": None, "signal_day_count": None, "error": str(exc)[:300],
                }
                rows.append(row)
                _append_row(out_dir, row)
                print(phase, label, "ERROR", str(exc)[:200])
    pd.DataFrame(rows).to_csv(out_dir / "summary.csv", index=False, encoding="utf-8-sig")
    print("saved:", out_dir / "summary.csv")


def _append_row(out_dir: Path, row: dict) -> None:
    path = out_dir / "runs.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="resonance 校准/验证回测")
    parser.add_argument("--windows", nargs="*", choices=["calibration-1", "calibration-2", "validation"], default=None)
    parser.add_argument("--runs", nargs="*", default=None, help="标签子集，如 baseline-b1 resonance-full")
    args = parser.parse_args()
    main(only_windows=args.windows, only_runs=args.runs)
