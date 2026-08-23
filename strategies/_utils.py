"""Shared helpers for strategy scoring and cross-section ranking."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def to_scalar(value: Any) -> float | None:
    """Unwrap a pandas scalar/Series and return a finite float, or None."""
    try:
        if isinstance(value, pd.Series):
            value = value.iloc[-1]
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    return result if np.isfinite(result) else None


def safe_float(value: Any, default: float = 0.0) -> float:
    """Coerce to a finite float, returning ``default`` for NaN/±inf or bad input."""
    result = to_scalar(value)
    return result if result is not None else default


def safe_bool(value: Any) -> bool:
    """Coerce to bool, treating any non-finite scalar (NaN/inf) as False."""
    result = to_scalar(value)
    return result is not None and bool(result)


def extract_cross_section(
    data: dict[str, pd.DataFrame],
    pick_date: pd.Timestamp,
    column: str,
    pool: set[str] | None = None,
) -> dict[str, float]:
    """Build ``{code: finite_value}`` for one indicator column at ``pick_date``.

    Codes outside ``pool``, frames that do not contain ``pick_date`` or lack the
    ``column`` are skipped, and non-finite values are dropped so callers can rank
    without per-row NaN/Series handling.
    """
    out: dict[str, float] = {}
    for code, frame in data.items():
        if pool is not None and code not in pool:
            continue
        if pick_date not in frame.index or column not in frame.columns:
            continue
        value = to_scalar(frame.loc[pick_date, column])
        if value is not None:
            out[code] = value
    return out


def apply_cross_section_rank(
    data: dict[str, pd.DataFrame],
    pick_date: pd.Timestamp,
    source_column: str,
    target_column: str,
    pool: set[str] | None = None,
) -> None:
    """Rank ``source_column`` across the universe and store pct-rank per code."""
    values = extract_cross_section(data, pick_date, source_column, pool=pool)
    if not values:
        return
    ranks = pd.Series(values).rank(pct=True, method="average")
    for code, rank_value in ranks.items():
        data[code].loc[pick_date, target_column] = float(rank_value)
