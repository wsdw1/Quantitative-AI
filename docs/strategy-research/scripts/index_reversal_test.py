"""Index-level low-position + reversal confirmation test (resonance design).

Usage:
    python index_reversal_test.py <cache_dir>

Reads index CSVs cached by position_winrate_study.py and compares forward
returns of low-position days with and without reversal confirmation
(up day and volume ratio >= 1.2).
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

cache_dir = Path(sys.argv[1])
WINDOW = 252


def load(code):
    df = pd.read_csv(cache_dir / f"{code.replace('.', '_')}.csv", parse_dates=["trade_date"])
    df = df.sort_values("trade_date").reset_index(drop=True)
    close = df["close"].to_numpy(dtype=np.float64)
    pos = np.full(len(df), np.nan)
    for i in range(WINDOW - 1, len(df)):
        w = close[i - WINDOW + 1 : i + 1]
        pos[i] = (w <= close[i]).sum() / WINDOW * 100.0
    df["pos"] = pos
    df["vol_ma20"] = df["vol"].rolling(20, min_periods=5).mean()
    df["vol_ratio"] = df["vol"] / df["vol_ma20"].replace(0, np.nan)
    df["reversal"] = (df["pct_chg"] > 0) & (df["vol_ratio"] >= 1.2)
    for h in (5, 10, 20):
        df[f"fwd{h}"] = df["close"].shift(-h) / df["close"] - 1.0
    return df


MARKET_CODES = ["000001.SH", "399006.SZ", "000300.SH", "000905.SH", "000688.SH", "899050.BJ"]
INDUSTRY_CODES = [
    "801010.SI", "801030.SI", "801040.SI", "801050.SI", "801080.SI", "801110.SI",
    "801120.SI", "801130.SI", "801140.SI", "801150.SI", "801160.SI", "801170.SI",
    "801180.SI", "801200.SI", "801210.SI", "801230.SI", "801710.SI", "801720.SI",
    "801730.SI", "801740.SI", "801750.SI", "801760.SI", "801770.SI", "801780.SI",
    "801790.SI", "801880.SI", "801890.SI", "801950.SI", "801960.SI", "801970.SI",
    "801980.SI",
]


def test(df, label, cap):
    sub = df.dropna(subset=["pos", "fwd10"]).copy()
    sub = sub[sub["pos"] <= cap]
    if sub.empty:
        print(f"{label} cap={cap}: no samples")
        return
    base = sub
    w20 = base["fwd20"].dropna()
    print(
        f"{label} cap={cap}: all n={len(base)} win10={(base['fwd10'] > 0).mean() * 100:.1f}% "
        f"mean10={base['fwd10'].mean() * 100:.2f}% win20={(w20 > 0).mean() * 100:.1f}% "
        f"mean20={w20.mean() * 100:.2f}%"
    )
    for flag in (True, False):
        part = sub[sub["reversal"] == flag]
        if len(part):
            w20p = part["fwd20"].dropna()
            print(
                f"  reversal={int(flag)}: n={len(part)} win10={(part['fwd10'] > 0).mean() * 100:.1f}% "
                f"mean10={part['fwd10'].mean() * 100:.2f}% win20={(w20p > 0).mean() * 100:.1f}% "
                f"mean20={w20p.mean() * 100:.2f}%"
            )


print("===== MARKET INDICES =====")
pool = pd.concat([load(c) for c in MARKET_CODES], ignore_index=True)
for cap in (15, 30):
    test(pool, "market", cap)

print("\n===== INDUSTRY INDICES =====")
ind = pd.concat([load(c) for c in INDUSTRY_CODES], ignore_index=True)
for cap in (15, 30):
    test(ind, "industry", cap)

print("\n===== SHANGHAI COMPOSITE =====")
sh = load("000001.SH")
for cap in (15, 30):
    test(sh, "sh000001", cap)

print("\n===== CHINEXT =====")
cyb = load("399006.SZ")
for cap in (15, 30):
    test(cyb, "cyb399006", cap)

for name, df in [("market", pool), ("industry", ind)]:
    sub = df.dropna(subset=["pos"])
    freq = pd.cut(sub["pos"], bins=np.arange(0, 101, 10), include_lowest=True).value_counts(normalize=True).sort_index() * 100
    print(f"\n{name} pos distribution %:", {f"{int(b.left)}-{int(b.right)}": round(v, 1) for b, v in freq.items()})
