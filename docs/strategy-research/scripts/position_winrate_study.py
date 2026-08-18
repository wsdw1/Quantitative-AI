"""Position vs forward-return win-rate study backing the resonance design.

Usage:
    python position_winrate_study.py <oversell.db> <env.local> <cache_dir>

- fetches index daily bars via Tushare (cached under cache_dir)
- loads stock daily prices from the local SQLite store
- computes 252-day close position percentiles and forward 5/10/20d returns
- prints decile win-rate tables, market median-position regime table, and a
  low-position + reversal confirmation test
"""

import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import tushare as ts

db = sys.argv[1]
env_path = Path(sys.argv[2])
cache_dir = Path(sys.argv[3])
cache_dir.mkdir(parents=True, exist_ok=True)

token = None
for line in env_path.read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if line.startswith("TUSHARE_TOKEN="):
        token = line.split("=", 1)[1].strip()
        break
ts.set_token(token)
pro = ts.pro_api()

MARKET_CODES = ["000001.SH", "399006.SZ", "000300.SH", "000905.SH", "000688.SH", "899050.BJ"]
INDUSTRY_CODES = [
    "801010.SI", "801030.SI", "801040.SI", "801050.SI", "801080.SI", "801110.SI",
    "801120.SI", "801130.SI", "801140.SI", "801150.SI", "801160.SI", "801170.SI",
    "801180.SI", "801200.SI", "801210.SI", "801230.SI", "801710.SI", "801720.SI",
    "801730.SI", "801740.SI", "801750.SI", "801760.SI", "801770.SI", "801780.SI",
    "801790.SI", "801880.SI", "801890.SI", "801950.SI", "801960.SI", "801970.SI",
    "801980.SI",
]
WINDOW = 252
END_DATE = datetime.now().strftime("%Y%m%d")


def load_index(code):
    cache_file = cache_dir / f"{code.replace('.', '_')}.csv"
    if cache_file.exists():
        df = pd.read_csv(cache_file, parse_dates=["trade_date"])
    else:
        df = pro.index_daily(ts_code=code, start_date="20230101", end_date=END_DATE)
        df.to_csv(cache_file, index=False)
    df = df.sort_values("trade_date").reset_index(drop=True)
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    return df


def add_position(df):
    close = df["close"].to_numpy(dtype=np.float64)
    pos = np.full(len(df), np.nan)
    for i in range(WINDOW - 1, len(df)):
        w = close[i - WINDOW + 1 : i + 1]
        pos[i] = (w <= close[i]).sum() / WINDOW * 100.0
    df["pos"] = pos
    for h in (5, 10, 20):
        df[f"fwd{h}"] = df["close"].shift(-h) / df["close"] - 1.0
    return df


def bucket_stats(df, label, horizons=(5, 10, 20)):
    print(f"\n=== {label} (n={len(df)}) ===")
    df = df.dropna(subset=["pos"])
    bins = np.arange(0, 101, 10)
    df["bucket"] = pd.cut(
        df["pos"], bins=bins, include_lowest=True,
        labels=[f"{i}-{i+10}" for i in range(0, 100, 10)],
    )
    for h in horizons:
        sub = df.dropna(subset=[f"fwd{h}"])
        g = sub.groupby("bucket", observed=False)[f"fwd{h}"]
        table = pd.DataFrame(
            {
                "n": g.count(),
                "win%": g.apply(lambda s: (s > 0).mean() * 100),
                "mean%": g.mean() * 100,
                "med%": g.median() * 100,
            }
        ).round(2)
        print(f"--- fwd {h}d ---")
        print(table.to_string())


def reversal_test(df, label, position_cap=30.0):
    print(f"\n=== Reversal test @ position <= {position_cap}: {label} ===")
    sub = df.dropna(subset=["pos", "fwd10"]).copy()
    sub = sub[sub["pos"] <= position_cap]
    if sub.empty:
        print("no samples")
        return
    print(f"all low-pos: n={len(sub)} win10%={(sub['fwd10'] > 0).mean() * 100:.2f} mean10%={sub['fwd10'].mean() * 100:.2f}")
    if "reversal" in sub.columns:
        for flag in (True, False):
            part = sub[sub["reversal"] == flag]
            if len(part):
                print(
                    f"reversal={int(flag)}: n={len(part)} "
                    f"win10%={(part['fwd10'] > 0).mean() * 100:.2f} "
                    f"mean10%={part['fwd10'].mean() * 100:.2f} "
                    f"med10%={part['fwd10'].median() * 100:.2f}"
                )


print("###### MARKET INDICES ######")
index_positions = {}
for code in MARKET_CODES:
    df = add_position(load_index(code))
    index_positions[code] = df
    latest = df.dropna(subset=["pos"]).iloc[-1]
    print(f"{code}: latest pos={latest['pos']:.1f} close={latest['close']:.1f} date={latest['trade_date'].date()}")

pooled = pd.concat([add_position(load_index(c)) for c in MARKET_CODES], ignore_index=True)
bucket_stats(pooled, "Pooled market indices")

print("\n###### INDUSTRY INDICES ######")
ind_pooled = pd.concat([add_position(load_index(c)) for c in INDUSTRY_CODES], ignore_index=True)
bucket_stats(ind_pooled, "Pooled SW industry indices")
latest_by_ind = ind_pooled.dropna(subset=["pos"]).groupby("ts_code").tail(1)
print("\ncurrent industry positions:")
print(latest_by_ind[["ts_code", "pos"]].round(1).sort_values("pos").to_string(index=False))

print("\n###### STOCKS ######")
con = sqlite3.connect(db)
prices = pd.read_sql_query(
    "select code, trade_date, close, volume, pct_chg from daily_prices where adjust='qfq'",
    con,
)
con.close()
prices["trade_date"] = pd.to_datetime(prices["trade_date"])
prices = prices.sort_values(["code", "trade_date"]).reset_index(drop=True)
prices = prices[prices.groupby("code")["close"].transform("count") >= 300].copy()
print("stocks with >=300 bars:", prices["code"].nunique(), "rows:", len(prices))


def add_stock_position_and_reversal(group):
    close = group["close"].to_numpy(dtype=np.float64)
    n = len(group)
    pos = np.full(n, np.nan)
    for i in range(WINDOW - 1, n):
        w = close[i - WINDOW + 1 : i + 1]
        pos[i] = (w <= close[i]).sum() / WINDOW * 100.0
    group = group.assign(pos=pos)
    group["vol_ma20"] = group["volume"].rolling(20, min_periods=5).mean()
    group["vol_ratio"] = group["volume"] / group["vol_ma20"].replace(0, np.nan)
    group["reversal"] = (group["pct_chg"] > 0) & (group["vol_ratio"] >= 1.2)
    for h in (5, 10, 20):
        group[f"fwd{h}"] = group["close"].shift(-h) / group["close"] - 1.0
    return group


stocks = prices.groupby("code", group_keys=False).apply(add_stock_position_and_reversal)
stocks = stocks.dropna(subset=["pos"]).reset_index(drop=True)
print("stock samples with pos:", len(stocks))

sample = stocks.sample(frac=0.35, random_state=42)
bucket_stats(sample, "Stock sample (35%)")
reversal_test(sample, "stocks", position_cap=30.0)

med_pos = stocks.groupby("trade_date")["pos"].median().rename("market_med_pos")
med_win = stocks.groupby("trade_date")["fwd10"].apply(lambda s: (s > 0).mean()).rename("median_stock_win10")
med_ret = stocks.groupby("trade_date")["fwd10"].mean().rename("median_stock_ret10")
regime = pd.concat([med_pos, med_win, med_ret], axis=1).dropna()
regime["bucket"] = pd.cut(
    regime["market_med_pos"], bins=np.arange(0, 101, 10), include_lowest=True,
    labels=[f"{i}-{i+10}" for i in range(0, 100, 10)],
)
g = regime.groupby("bucket", observed=False)
print("\n=== market median position (daily) -> next-10d avg stock win/ret ===")
print(
    pd.DataFrame(
        {
            "days": g.size(),
            "avg_win10%": g["median_stock_win10"].mean() * 100,
            "avg_ret10%": g["median_stock_ret10"].mean() * 100,
        }
    ).round(2).to_string()
)
print("\nlatest market median position:", regime["market_med_pos"].iloc[-1].round(1), regime.index[-1].date())
