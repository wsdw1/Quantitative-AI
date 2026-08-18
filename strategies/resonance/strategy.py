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
        sub_results: list[list[Candidate]] = []
        for sub_id, strategy in self._sub_strategies(cfg):
            prepared = self._prepared_by_sub.get(sub_id) or data
            sub_results.append(strategy.select_prepared(prepared, self._sub_cfg(cfg, sub_id), context))
        candidates = self._merge(sub_results, cfg)
        return self._apply_regime(candidates, cfg, context, data)

    def _merge(self, sub_results: list[list[Candidate]], cfg: dict) -> list[Candidate]:
        merged: dict[str, dict] = {}
        for results in sub_results:
            if not results:
                continue
            ranks = pd.Series([float(item.score) for item in results]).rank(pct=True, method="average")
            for item, rank in zip(results, ranks):
                entry = merged.setdefault(item.code, {"candidate": item, "hits": {}, "rank_sum": 0.0})
                entry["hits"][str(item.strategy)] = float(item.score)
                entry["rank_sum"] += float(rank)
        candidates: list[Candidate] = []
        for entry in merged.values():
            if len(entry["hits"]) < int(cfg["min_hits"]):
                continue
            candidate = entry["candidate"]
            candidate.strategy = self.meta.id
            candidate.extra["hit_count"] = len(entry["hits"])
            candidate.extra["hits"] = entry["hits"]
            candidate.extra["combined_score"] = round(entry["rank_sum"], 4)
            candidate.score = round(entry["rank_sum"], 4)
            candidates.append(candidate)
        candidates.sort(key=lambda item: item.score, reverse=True)
        max_candidates = int(cfg.get("max_candidates", 0))
        return candidates[:max_candidates] if max_candidates else candidates

    def _apply_regime(
        self,
        candidates: list[Candidate],
        cfg: dict,
        context: StrategyContext,
        data: dict[str, pd.DataFrame],
    ) -> list[Candidate]:
        from market_analysis.positions import market_regime, stock_positions

        as_of = context.pick_date.strftime("%Y-%m-%d")
        regime_payload = market_regime(
            as_of=as_of,
            risk_threshold=float(cfg["risk_high_threshold"]),
            bottom_threshold=float(cfg["bottom_low_threshold"]),
            reversal_volume_ratio=float(cfg["reversal_volume_ratio"]),
        )
        regime = regime_payload.get("regime", "neutral")
        codes = [item.code for item in candidates]
        positions = stock_positions(codes, as_of=as_of) if codes else {}
        for item in candidates:
            item.extra["regime"] = regime
            item.extra["market_pos"] = {entry["code"]: entry["position"] for entry in regime_payload.get("market", [])}
            item.extra["board_pos"] = {
                board: entry["position_risk"] for board, entry in regime_payload.get("boards", {}).items()
            }
            item.extra["stock_pos"] = positions.get(item.code)
            if (
                regime == "risk"
                and item.extra["stock_pos"] is not None
                and item.extra["stock_pos"] >= float(cfg["risk_high_threshold"])
            ):
                item.extra["risk_marked"] = True
                if cfg["high_position_action"] != "exclude":
                    item.score = float(item.score) * float(cfg["downweight_factor"])
        if regime == "risk" and cfg["high_position_action"] == "exclude":
            candidates = [item for item in candidates if not item.extra.get("risk_marked")]
        if regime == "bottom" and cfg.get("bottom_fishing_enabled", True):
            candidates.extend(self._bottom_pool(data, cfg, context))
        candidates.sort(key=lambda item: item.score, reverse=True)
        max_candidates = int(cfg.get("max_candidates", 0))
        return candidates[:max_candidates] if max_candidates else candidates

    def _bottom_pool(
        self,
        data: dict[str, pd.DataFrame],
        cfg: dict,
        context: StrategyContext,
    ) -> list[Candidate]:
        from market_analysis.positions import stock_positions

        cap = float(cfg["bottom_stock_pos_cap"])
        ratio = float(cfg["reversal_volume_ratio"])
        codes = [code for code in data if context.pool is None or code in context.pool]
        positions = stock_positions(codes, as_of=context.pick_date.strftime("%Y-%m-%d"))
        result: list[Candidate] = []
        for code, frame in data.items():
            if context.pool is not None and code not in context.pool:
                continue
            if context.pick_date not in frame.index:
                continue
            position = positions.get(code)
            if position is None or position > cap:
                continue
            row = frame.loc[context.pick_date]
            if isinstance(row, pd.DataFrame):
                row = row.iloc[-1]
            pct_chg = row.get("pct_chg")
            column = "vol" if "vol" in frame.columns else "volume"
            volume = row.get(column)
            ma = frame[column].rolling(20, min_periods=5).mean()
            if context.pick_date not in ma.index or not (float(ma.loc[context.pick_date]) > 0):
                continue
            if not (float(pct_chg or 0) > 0 and float(volume or 0) / float(ma.loc[context.pick_date]) >= ratio):
                continue
            result.append(Candidate(
                code=code,
                name=context.names.get(code, code),
                date=str(context.pick_date.date()),
                strategy=self.meta.id,
                close=float(row.get("close") or 0.0),
                turnover_n=float(row.get("turnover_n") or 0.0),
                score=0.0,
                extra={"bottom_signal": True, "regime": "bottom", "stock_pos": position},
            ))
        return result

    def select(
        self,
        data: dict[str, pd.DataFrame],
        cfg: dict,
        context: StrategyContext,
    ) -> list[Candidate]:
        return self.select_prepared(self.prepare_all(data, cfg, context), cfg, context)
