"""Resonance meta-strategy: sub-strategy consensus + position regime overlay."""
from __future__ import annotations

import logging
import math
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
    "high_position_action": "exclude",
    "downweight_factor": 0.5,
    "risk_max_candidates": 15,
    "trend_dominant_in_risk": True,
    "bottom_fishing_enabled": True,
    "bottom_stock_pos_cap": 30.0,
}


class ResonanceStrategy:
    meta = StrategyMeta(
        id="resonance",
        name="多策略共振",
        description="并行运行子策略并按命中次数共振合并，叠加市场/板块/行业位置风控（风险区剔除高位并收紧候选、顺势主导；低位反转抄底）。",
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
        columns: set[str] = {"close", "turnover_n", "volume", "pct_chg", "pos252", "vol_ma20"}
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
        from market_analysis.positions import compute_position

        for code, frame in merged.items():
            if "pos252" not in frame.columns:
                frame["pos252"] = compute_position(frame["close"], 252)
            if "vol_ma20" not in frame.columns:
                column = "vol" if "vol" in frame.columns else "volume"
                if column in frame.columns:
                    frame["vol_ma20"] = pd.to_numeric(frame[column], errors="coerce").rolling(20, min_periods=5).mean()
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
        sub_results: list[tuple[str, list[Candidate]]] = []
        for sub_id, strategy in self._sub_strategies(cfg):
            prepared = self._prepared_by_sub.get(sub_id) or data
            sub_results.append((sub_id, strategy.select_prepared(prepared, self._sub_cfg(cfg, sub_id), context)))
        candidates = self._merge([items for _, items in sub_results], cfg)
        return self._apply_regime(candidates, cfg, context, data, sub_results)

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
        sub_results: list[tuple[str, list[Candidate]]] | None = None,
    ) -> list[Candidate]:
        from market_analysis.positions import market_regime

        as_of = context.pick_date.strftime("%Y-%m-%d")
        regime_payload = market_regime(
            as_of=as_of,
            risk_threshold=float(cfg["risk_high_threshold"]),
            bottom_threshold=float(cfg["bottom_low_threshold"]),
            reversal_volume_ratio=float(cfg["reversal_volume_ratio"]),
        )
        regime = regime_payload.get("regime", "neutral")
        if regime == "risk" and cfg.get("trend_dominant_in_risk", True) and sub_results:
            momentum_candidates = next(
                (items for sub_id, items in sub_results if sub_id == "high_52w_momentum"),
                [],
            )
            if momentum_candidates:
                candidates = self._trend_merge(candidates, momentum_candidates, cfg)
        positions: dict[str, float | None] = {}
        for code in {item.code for item in candidates}:
            frame = data.get(code)
            if frame is None or context.pick_date not in frame.index or "pos252" not in frame.columns:
                positions[code] = None
                continue
            row = frame.loc[context.pick_date]
            if isinstance(row, pd.DataFrame):
                row = row.iloc[-1]
            value = row.get("pos252")
            positions[code] = float(value) if value is not None and math.isfinite(float(value)) else None
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
            candidates = [
                item for item in candidates
                if not (item.extra.get("risk_marked") and not item.extra.get("trend_leader"))
            ]
        if regime == "bottom" and cfg.get("bottom_fishing_enabled", True):
            candidates.extend(self._bottom_pool(data, cfg, context))
        candidates.sort(key=lambda item: item.score, reverse=True)
        if regime == "risk":
            max_candidates = int(cfg.get("risk_max_candidates", cfg.get("max_candidates", 0)))
        else:
            max_candidates = int(cfg.get("max_candidates", 0))
        return candidates[:max_candidates] if max_candidates else candidates

    def _trend_merge(
        self,
        resonance_candidates: list[Candidate],
        momentum_candidates: list[Candidate],
        cfg: dict,
    ) -> list[Candidate]:
        """Risk-regime trend-dominant pool: momentum leaders primary, resonance multi-hit as bonus."""
        by_code = {item.code: item for item in resonance_candidates}
        ranks = pd.Series([float(item.score) for item in momentum_candidates]).rank(pct=True, method="average")
        merged: list[Candidate] = []
        for item, rank in zip(momentum_candidates, ranks):
            existing = by_code.pop(item.code, None)
            if existing is not None:
                hits = dict(existing.extra.get("hits", {}))
                hits["high_52w_momentum"] = float(item.score)
                existing.extra["hits"] = hits
                existing.extra["hit_count"] = len(hits)
                existing.extra["momentum_rank"] = float(rank)
                existing.extra["trend_leader"] = True
                existing.extra["combined_score"] = round(float(existing.extra.get("combined_score", 0.0)) + float(rank), 4)
                existing.score = round(float(existing.extra["combined_score"]), 4)
                merged.append(existing)
                continue
            item.strategy = self.meta.id
            item.extra["hit_count"] = 1
            item.extra["hits"] = {"high_52w_momentum": float(item.score)}
            item.extra["combined_score"] = round(float(rank), 4)
            item.extra["momentum_rank"] = float(rank)
            item.extra["trend_leader"] = True
            item.score = round(float(rank), 4)
            merged.append(item)
        for item in by_code.values():
            item.extra.setdefault("momentum_rank", 0.0)
            merged.append(item)
        return merged

    def _bottom_pool(
        self,
        data: dict[str, pd.DataFrame],
        cfg: dict,
        context: StrategyContext,
    ) -> list[Candidate]:
        cap = float(cfg["bottom_stock_pos_cap"])
        ratio = float(cfg["reversal_volume_ratio"])
        result: list[Candidate] = []
        for code, frame in data.items():
            if context.pool is not None and code not in context.pool:
                continue
            if context.pick_date not in frame.index:
                continue
            row = frame.loc[context.pick_date]
            if isinstance(row, pd.DataFrame):
                row = row.iloc[-1]
            position = row.get("pos252")
            if position is None or position > cap:
                continue
            pct_chg = row.get("pct_chg")
            column = "vol" if "vol" in frame.columns else "volume"
            vol_ma = row.get("vol_ma20")
            if column not in frame.columns or "pct_chg" not in frame.columns or vol_ma is None:
                continue
            volume = row.get(column)
            if not (float(vol_ma) > 0):
                continue
            if not (float(pct_chg or 0) > 0 and float(volume or 0) / float(vol_ma) >= ratio):
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
