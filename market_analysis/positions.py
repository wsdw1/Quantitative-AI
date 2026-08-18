"""Index/board/industry position percentiles and risk/bottom regime detection."""
from __future__ import annotations

import threading
from typing import Any

import numpy as np
import pandas as pd

from storage.database import (
    index_price_signature,
    load_daily_prices,
    load_index_prices,
)

WINDOW = 252
RISK_HIGH_THRESHOLD = 85.0
BOTTOM_LOW_THRESHOLD = 15.0
REVERSAL_VOLUME_RATIO = 1.2

MARKET_INDEX_CODES = [
    "000001.SH", "399001.SZ", "399006.SZ", "000300.SH",
    "000905.SH", "000688.SH", "899050.BJ",
]
BOARD_INDEX_CODES: dict[str, list[str]] = {
    "main": ["000001.SH", "399001.SZ"],
    "gem": ["399006.SZ"],
    "star": ["000688.SH"],
    "bse": ["899050.BJ"],
}
INDUSTRY_INDEX_CODES = [
    "801010.SI", "801030.SI", "801040.SI", "801050.SI", "801080.SI", "801110.SI",
    "801120.SI", "801130.SI", "801140.SI", "801150.SI", "801160.SI", "801170.SI",
    "801180.SI", "801200.SI", "801210.SI", "801230.SI", "801710.SI", "801720.SI",
    "801730.SI", "801740.SI", "801750.SI", "801760.SI", "801770.SI", "801780.SI",
    "801790.SI", "801880.SI", "801890.SI", "801950.SI", "801960.SI", "801970.SI",
    "801980.SI",
]

_CACHE_LOCK = threading.Lock()
_CACHE: dict[tuple[Any, ...], dict[str, Any]] = {}


def clear_positions_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


def compute_position(close: pd.Series, window: int = WINDOW) -> pd.Series:
    """Percentile of each close within its trailing `window` trading days (0-100)."""
    arr = pd.to_numeric(close, errors="coerce").to_numpy(dtype="float64")
    out = np.full(len(arr), np.nan)
    if len(arr) < window:
        return pd.Series(out, index=close.index)
    windows = np.lib.stride_tricks.sliding_window_view(arr, window)
    counts = (windows <= windows[:, -1:]).sum(axis=1)
    out[window - 1 :] = counts / window * 100.0
    return pd.Series(out, index=close.index)


def _vol_ma(frame: pd.DataFrame, window: int = 20) -> pd.Series:
    column = "vol" if "vol" in frame.columns else "volume"
    return pd.to_numeric(frame[column], errors="coerce").rolling(window, min_periods=5).mean()


def reversal_confirmed(frame: pd.DataFrame, date: pd.Timestamp, volume_ratio: float = REVERSAL_VOLUME_RATIO) -> bool:
    if date not in frame.index:
        return False
    row = frame.loc[date]
    if isinstance(row, pd.DataFrame):
        row = row.iloc[-1]
    pct_chg = row.get("pct_chg")
    if pct_chg is None or not (float(pct_chg) > 0):
        return False
    column = "vol" if "vol" in frame.columns else "volume"
    volume = row.get(column)
    ma = _vol_ma(frame)
    if date not in ma.index or not (float(ma.loc[date]) > 0) or volume is None:
        return False
    return float(volume) / float(ma.loc[date]) >= volume_ratio


def classify(
    position: float | None,
    reversal: bool,
    risk_threshold: float = RISK_HIGH_THRESHOLD,
    bottom_threshold: float = BOTTOM_LOW_THRESHOLD,
) -> str:
    if position is None:
        return "neutral"
    if float(position) >= risk_threshold:
        return "risk"
    if float(position) <= bottom_threshold and reversal:
        return "bottom"
    return "neutral"


def _latest_rows(frames: dict[str, pd.DataFrame], as_of: str | None) -> dict[str, pd.Series]:
    result: dict[str, pd.Series] = {}
    for code, frame in frames.items():
        if frame.empty:
            continue
        if as_of is not None:
            frame = frame.loc[frame.index <= pd.Timestamp(as_of)]
        if frame.empty:
            continue
        result[code] = frame.iloc[-1]
    return result


def _positions_payload(as_of: str | None) -> dict[str, Any]:
    signature = index_price_signature()
    cache_key = (as_of, *signature)
    with _CACHE_LOCK:
        cached = _CACHE.get(cache_key)
    if cached is not None:
        return cached
    frames = load_index_prices()
    if not frames:
        payload: dict[str, Any] = {
            "available": False, "as_of": as_of, "regime": "neutral",
            "market": [], "boards": {}, "industries": [],
        }
        with _CACHE_LOCK:
            _CACHE.clear()
            _CACHE[cache_key] = payload
        return payload
    latest = _latest_rows(frames, as_of)
    market: list[dict[str, Any]] = []
    for code in MARKET_INDEX_CODES:
        if code not in latest:
            continue
        frame = frames[code]
        if as_of is not None:
            frame = frame.loc[frame.index <= pd.Timestamp(as_of)]
        row = latest[code]
        position = float(compute_position(frame["close"], WINDOW).iloc[-1])
        if not np.isfinite(position):
            continue
        market.append({
            "code": code,
            "position": round(position, 1),
            "close": float(row.get("close") or np.nan),
            "reversal": reversal_confirmed(frame, frame.index[-1]),
            "trade_date": str(frame.index[-1].date()),
        })
    boards: dict[str, dict[str, Any]] = {}
    for board, codes in BOARD_INDEX_CODES.items():
        entries = [item for item in market if item["code"] in codes]
        if not entries:
            continue
        boards[board] = {
            "position_risk": max(item["position"] for item in entries),
            "position_bottom": min(item["position"] for item in entries),
            "reversal": any(item["reversal"] for item in entries),
            "codes": codes,
        }
    industries: list[dict[str, Any]] = []
    for code in INDUSTRY_INDEX_CODES:
        if code not in latest:
            continue
        frame = frames[code]
        if as_of is not None:
            frame = frame.loc[frame.index <= pd.Timestamp(as_of)]
        position = float(compute_position(frame["close"], WINDOW).iloc[-1])
        if not np.isfinite(position):
            continue
        industries.append({
            "code": code,
            "position": round(position, 1),
            "reversal": reversal_confirmed(frame, frame.index[-1]),
            "trade_date": str(frame.index[-1].date()),
        })
    risk = any(item["position"] >= RISK_HIGH_THRESHOLD for item in market) or any(
        board["position_risk"] >= RISK_HIGH_THRESHOLD for board in boards.values()
    )
    bottom = any(item["position"] <= BOTTOM_LOW_THRESHOLD and item["reversal"] for item in market) or any(
        board["reversal"] and board["position_bottom"] <= BOTTOM_LOW_THRESHOLD for board in boards.values()
    )
    regime = "risk" if risk else ("bottom" if bottom else "neutral")
    payload = {
        "available": True, "as_of": as_of, "regime": regime,
        "market": market, "boards": boards, "industries": industries,
    }
    with _CACHE_LOCK:
        _CACHE.clear()
        _CACHE[cache_key] = payload
    return payload


def market_positions(as_of: str | None = None) -> dict[str, Any]:
    return _positions_payload(as_of)


def board_positions(as_of: str | None = None) -> dict[str, Any]:
    payload = market_positions(as_of)
    return {"available": payload["available"], "boards": payload["boards"]}


def industry_positions(as_of: str | None = None) -> dict[str, Any]:
    payload = market_positions(as_of)
    return {"available": payload["available"], "industries": payload["industries"]}


def stock_positions(codes: list[str], as_of: str | None = None, adjust: str = "qfq") -> dict[str, float | None]:
    frames = load_daily_prices(adjust, 43, symbols=codes, end_date=as_of)
    result: dict[str, float | None] = {}
    for code, frame in frames.items():
        position = compute_position(frame["close"], WINDOW)
        result[str(code).zfill(6)] = float(position.iloc[-1]) if len(position) else None
    return result


def market_regime(
    as_of: str | None = None,
    risk_threshold: float = RISK_HIGH_THRESHOLD,
    bottom_threshold: float = BOTTOM_LOW_THRESHOLD,
    reversal_volume_ratio: float = REVERSAL_VOLUME_RATIO,
) -> dict[str, Any]:
    payload = _positions_payload(as_of)
    if not payload["available"]:
        return {"regime": "neutral", "available": False, "market": [], "boards": {}}
    risk = any(item["position"] >= risk_threshold for item in payload["market"]) or any(
        board["position_risk"] >= risk_threshold for board in payload["boards"].values()
    )
    bottom = any(
        item["position"] <= bottom_threshold and item.get("reversal", False)
        for item in payload["market"]
    ) or any(
        board["reversal"] and board["position_bottom"] <= bottom_threshold
        for board in payload["boards"].values()
    )
    return {
        "regime": "risk" if risk else ("bottom" if bottom else "neutral"),
        "available": True,
        "market": payload["market"],
        "boards": payload["boards"],
    }
