"""Fetch market/industry index bars and stock industry mapping from TUShare."""
from __future__ import annotations

import argparse
import logging
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from storage.database import (  # noqa: E402
    index_price_codes,
    load_index_prices,
    upsert_index_prices,
    upsert_stock_industries,
)

logger = logging.getLogger(__name__)

MARKET_INDEX_CODES = [
    "000001.SH", "399001.SZ", "399006.SZ", "000300.SH",
    "000905.SH", "000688.SH", "899050.BJ",
]
INDUSTRY_INDEX_CODES = [
    "801010.SI", "801030.SI", "801040.SI", "801050.SI", "801080.SI", "801110.SI",
    "801120.SI", "801130.SI", "801140.SI", "801150.SI", "801160.SI", "801170.SI",
    "801180.SI", "801200.SI", "801210.SI", "801230.SI", "801710.SI", "801720.SI",
    "801730.SI", "801740.SI", "801750.SI", "801760.SI", "801770.SI", "801780.SI",
    "801790.SI", "801880.SI", "801890.SI", "801950.SI", "801960.SI", "801970.SI",
    "801980.SI",
]
DEFAULT_START = "20230101"


def load_token(env_path: Path | None = None) -> str:
    env_path = env_path or (_ROOT / ".env.local")
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("TUSHARE_TOKEN="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError("TUSHARE_TOKEN 未在 .env.local 中找到")


def normalize_index_frame(df: pd.DataFrame | None) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["open", "high", "low", "close", "vol", "amount", "pct_chg"])
    frame = df.copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce")
    frame = frame.dropna(subset=["trade_date", "close"])
    for column in ("open", "high", "low", "close", "vol", "amount", "pct_chg"):
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    keep = [column for column in ("open", "high", "low", "close", "vol", "amount", "pct_chg") if column in frame.columns]
    return frame.set_index("trade_date").sort_index()[keep]


def fetch_index_bars(pro: Any, code: str, start_date: str, end_date: str) -> pd.DataFrame:
    raw = pro.index_daily(ts_code=code, start_date=start_date, end_date=end_date)
    return normalize_index_frame(raw)


def fetch_stock_industries(pro: Any) -> dict[str, str]:
    raw = pro.stock_basic(exchange="", list_status="L", fields="ts_code,name,industry,market")
    if raw is None or raw.empty or "industry" not in raw.columns:
        return {}
    mapping: dict[str, str] = {}
    for _, row in raw.iterrows():
        code = str(row["ts_code"]).split(".")[0].zfill(6)
        industry = str(row.get("industry") or "").strip()
        if industry:
            mapping[code] = industry
    return mapping


def run(
    pro: Any | None = None,
    codes: list[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    sync_industries: bool = True,
    use_cache_only: bool = False,
    force_refresh: bool = False,
    stop_event: threading.Event | None = None,
) -> dict:
    if pro is None:
        if use_cache_only:
            return {"rows": 0, "codes": [], "industries": 0, "cached_only": True}
        import tushare as ts

        ts.set_token(load_token())
        pro = ts.pro_api()
    end_date = end_date or datetime.now().strftime("%Y%m%d")
    start_date = start_date or DEFAULT_START
    latest_by_code: dict[str, str] = {}
    if not force_refresh:
        for code, frame in load_index_prices().items():
            if not frame.empty:
                latest_by_code[code] = frame.index[-1].strftime("%Y%m%d")

    targets = codes or (MARKET_INDEX_CODES + INDUSTRY_INDEX_CODES)
    rows = 0
    persisted: list[str] = []
    for code in targets:
        if stop_event and stop_event.is_set():
            break
        fetch_start = start_date
        if code in latest_by_code and not force_refresh:
            fetch_start = (pd.Timestamp(latest_by_code[code]) + pd.Timedelta(days=1)).strftime("%Y%m%d")
            if fetch_start > end_date:
                continue
        frame = fetch_index_bars(pro, code, fetch_start, end_date)
        if frame.empty:
            continue
        rows += upsert_index_prices({code: frame})
        persisted.append(code)
    industries: dict[str, str] = {}
    if sync_industries and not use_cache_only:
        industries = fetch_stock_industries(pro)
        upsert_stock_industries(industries)
    logger.info("fetch_indices done: %d rows, %d codes, %d industries", rows, len(persisted), len(industries))
    return {"rows": rows, "codes": persisted, "industries": len(industries), "cached_only": use_cache_only}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    parser = argparse.ArgumentParser(description="拉取市场/行业指数日线与申万行业映射")
    parser.add_argument("--codes", choices=["market", "industry", "all"], default="all")
    parser.add_argument("--start-date", default=None, help="YYYYMMDD，默认 20230101")
    parser.add_argument("--force-refresh", action="store_true")
    parser.add_argument("--use-cache-only", action="store_true")
    parser.add_argument("--skip-industries", action="store_true")
    args = parser.parse_args()
    code_set = {
        "market": MARKET_INDEX_CODES,
        "industry": INDUSTRY_INDEX_CODES,
        "all": MARKET_INDEX_CODES + INDUSTRY_INDEX_CODES,
    }[args.codes]
    run(
        codes=code_set,
        start_date=args.start_date,
        sync_industries=not args.skip_industries,
        use_cache_only=args.use_cache_only,
        force_refresh=args.force_refresh,
    )
