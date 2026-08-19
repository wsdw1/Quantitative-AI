"""Tests for the daily HTML/plain-text report builder (notify/report.py)."""
from __future__ import annotations

import pytest

from notify.report import build_daily_report


def make_run(
    strategy: str,
    strategy_name: str,
    scores: list[float],
    pick_date: str = "2026-08-19",
) -> dict:
    candidates = []
    for i, score in enumerate(scores, 1):
        candidates.append(
            {
                "code": f"{600000 + i:06d}",
                "name": f"股票{i}",
                "date": pick_date,
                "strategy": strategy,
                "close": float(i),
                "turnover_n": 0.0,
                "score": score,
            }
        )
    return {
        "run_date": pick_date,
        "pick_date": pick_date,
        "candidates": candidates,
        "meta": {
            "strategy": strategy,
            "strategy_name": strategy_name,
            "scanned": 3000,
            "selected": len(candidates),
        },
    }


def test_report_lists_top_n_candidates_sorted_by_score() -> None:
    # 12 只，评分故意乱序；只应展示评分前 10，且按评分降序。
    scores = [3.0, 12.0, 1.0, 11.0, 2.0, 10.0, 9.0, 8.0, 7.0, 6.0, 5.0, 4.0]
    run = make_run("resonance", "多策略共振", scores)
    _, html = build_daily_report({"resonance": run}, top=10)

    assert "多策略共振" in html
    assert "命中 12 只" in html
    assert "仅显示前 10 只（共 12 只）" in html
    # 最高分 12 -> 600002 应排第一，其次 11 -> 600004
    first_row = html.index('id="strategy-resonance"')
    assert html.index("600002", first_row) < html.index("600004", first_row)
    # 最低分的 2 只（score 1.0/2.0 -> 600003/600005）不应出现在前十表格里
    assert "600003" not in html[first_row:]
    assert "600005" not in html[first_row:]


def test_report_marks_scan_and_hit_counts() -> None:
    run = make_run("b1", "B1 量化初选", [8.0, 7.0])
    text, _ = build_daily_report({"b1": run}, top=10)

    assert "扫描 3000 只" in text
    assert "命中 2 只" in text


def test_report_handles_empty_strategy() -> None:
    run = make_run("volume_new_high", "缩量新高", [])
    _, html = build_daily_report({"volume_new_high": run}, top=10)

    assert "缩量新高" in html
    assert "命中 0 只" in html
    assert "无符合条件" in html


def test_report_renders_market_and_breadth_sections() -> None:
    market = {
        "available": True,
        "regime": "risk",
        "market": [
            {
                "code": "000001.SH",
                "position": 88.5,
                "close": 3456.78,
                "reversal": False,
                "trade_date": "2026-08-19",
            }
        ],
        "boards": {},
        "industries": [],
    }
    breadth = {
        "available": True,
        "status": "谨慎",
        "risk_level": "较高",
        "score": 33.2,
        "position_guidance": "建议仓位 20%–40%",
        "components": [
            {"name": "站上20日均线", "value_pct": 35.2, "signal": "偏弱", "id": "ma20_breadth"},
        ],
    }
    _, html = build_daily_report({}, market=market, breadth=breadth)

    assert "市场环境" in html
    assert "风险区" in html
    assert "上证指数" in html
    assert "88.5" in html
    assert "建议仓位 20%–40%" in html
    assert "站上20日均线" in html
    assert "35.2" in html


def test_report_handles_unavailable_market_and_breadth() -> None:
    _, html = build_daily_report(
        {},
        market={"available": False, "regime": "neutral", "market": [], "boards": {}},
        breadth={"available": False, "status": "暂无数据"},
    )

    assert "市场环境" in html
    assert "暂无数据" in html


def test_text_version_contains_date_strategies_and_market_status() -> None:
    market = {
        "available": True,
        "regime": "bottom",
        "market": [
            {"code": "399006.SZ", "position": 9.9, "close": 2100.0, "reversal": True, "trade_date": "2026-08-19"}
        ],
        "boards": {},
        "industries": [],
    }
    breadth = {
        "available": True,
        "status": "防守",
        "risk_level": "高",
        "score": 20.0,
        "position_guidance": "建议仓位 0%–20%",
        "components": [],
    }
    run = make_run("high_52w_momentum", "52周新高动量", [6.0, 5.0, 4.0])
    text, _ = build_daily_report({"high_52w_momentum": run}, market=market, breadth=breadth)

    assert "2026-08-19" in text
    assert "52周新高动量" in text
    assert "底部区" in text
    assert "建议仓位 0%–20%" in text
