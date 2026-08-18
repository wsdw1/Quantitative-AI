"""Resonance meta-strategy: sub-strategy consensus + position regime overlay."""
from __future__ import annotations

import logging
from typing import Callable

import pandas as pd

from pipeline.schemas import Candidate
from strategies.base import StrategyContext, StrategyMeta

logger = logging.getLogger(__name__)


DEFAULT_CONFIG = {
    "enabled": True,
    "sub_strategies": ["b1", "volume_new_high", "high_52w_momentum"],
    "min_hits": 2,
    "max_candidates": 30,
    "risk_high_threshold": 85.0,
    "bottom_low_threshold": 15.0,
    "reversal_volume_ratio": 1.2,
    "high_position_action": "downweight",
    "downweight_factor": 0.5,
    "bottom_fishing_enabled": True,
    "bottom_stock_pos_cap": 30.0,
}


class ResonanceStrategy:
    meta = StrategyMeta(
        id="resonance",
        name="多策略共振",
        description="并行运行子策略并按命中次数共振合并，叠加市场/板块/行业位置风控（高位降权、低位反转抄底）。",
        default_config=DEFAULT_CONFIG,
    )

    def __init__(self, registry: Callable[[str], object] | None = None) -> None:
        self._registry = registry
        self._prepared_by_sub: dict[str, dict[str, pd.DataFrame]] = {}

    def _resolve_registry(self) -> Callable[[str], object]:
        if self._registry is None:
            from strategies.registry import get_strategy

            self._registry = get_strategy
        return self._registry

    def _cfg(self, cfg: dict) -> dict:
        merged = dict(DEFAULT_CONFIG)
        merged.update(cfg or {})
        return merged

    def _sub_cfg(self, cfg: dict, sub_id: str) -> dict:
        sub_defaults = getattr(self._registry(sub_id), "meta", None)
        default_config = dict(sub_defaults.default_config) if sub_defaults else {}
        default_config.update(dict(cfg.get("sub_configs", {}).get(sub_id, {})))
        return default_config

    def _sub_strategies(self, cfg: dict) -> list[tuple[str, object]]:
        registry = self._resolve_registry()
        return [(sub_id, registry(sub_id)) for sub_id in cfg["sub_strategies"]]

    def warmup_bars(self, cfg: dict) -> int:
        cfg = self._cfg(cfg)
        sub_warmups = [
            strategy.warmup_bars(self._sub_cfg(cfg, sub_id))
            for sub_id, strategy in self._sub_strategies(cfg)
        ]
        return max([252, *sub_warmups]) + 30

    def indicator_config(self, cfg: dict) -> dict:
        cfg = self._cfg(cfg)
        return {
            "sub_strategies": cfg["sub_strategies"],
            "risk_high_threshold": cfg["risk_high_threshold"],
            "bottom_low_threshold": cfg["bottom_low_threshold"],
            "reversal_volume_ratio": cfg["reversal_volume_ratio"],
        }

    def cache_columns(self, cfg: dict) -> set[str]:
        cfg = self._cfg(cfg)
        columns: set[str] = {"close", "turnover_n"}
        for sub_id, strategy in self._sub_strategies(cfg):
            if hasattr(strategy, "cache_columns"):
                columns |= set(strategy.cache_columns(self._sub_cfg(cfg, sub_id)))
        return columns

    def prepare_all(
        self,
        data: dict[str, pd.DataFrame],
        cfg: dict,
        context: StrategyContext | None = None,
    ) -> dict[str, pd.DataFrame]:
        cfg = self._cfg(cfg)
        merged: dict[str, pd.DataFrame] = {}
        self._prepared_by_sub = {}
        for sub_id, strategy in self._sub_strategies(cfg):
            prepared = strategy.prepare_all(data, self._sub_cfg(cfg, sub_id), context)
            self._prepared_by_sub[sub_id] = prepared
            for code, frame in prepared.items():
                if code not in merged:
                    merged[code] = frame
                    continue
                extra = frame.columns.difference(merged[code].columns)
                merged[code] = merged[code].join(frame[extra], how="outer").sort_index()
        return merged

    def select_prepared(
        self,
        data: dict[str, pd.DataFrame],
        cfg: dict,
        context: StrategyContext,
    ) -> list[Candidate]:
        cfg = self._cfg(cfg)
        if not cfg.get("enabled", True):
            return []
        return []  # Task 7 实现合并与风控

    def select(
        self,
        data: dict[str, pd.DataFrame],
        cfg: dict,
        context: StrategyContext,
    ) -> list[Candidate]:
        return self.select_prepared(self.prepare_all(data, cfg, context), cfg, context)
