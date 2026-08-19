"""Tests for the daily report orchestration order (scripts/daily_report.py)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _load_module() -> object:
    path = Path(__file__).resolve().parent.parent / "scripts" / "daily_report.py"
    spec = importlib.util.spec_from_file_location("daily_report_module", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_daily_run_fetches_indices_before_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_module()
    calls: list[str] = []

    monkeypatch.setattr(module, "_is_trading_day", lambda: True)
    monkeypatch.setattr(module, "run_pipeline", lambda **kwargs: calls.append("pipeline") or None)
    monkeypatch.setattr(module, "_run_other_strategies", lambda pick_date: calls.append("other"))
    monkeypatch.setattr(module, "_ensure_index_data", lambda: calls.append("ensure_index"))

    import notify.mailer as mailer

    monkeypatch.setattr(mailer, "smtp_config", lambda: {"host": "x", "port": 465, "user": "u", "auth_code": "a", "to": "t"})
    monkeypatch.setattr(
        mailer, "send_candidates_report",
        lambda top=10, to=None: calls.append("send") or "t",
    )

    exit_code = module.main(["--data-mode", "existing", "--skip-trade-check", "--top", "10"])

    assert exit_code == 0
    # 指数数据必须先于选股就绪：市场状态判断依赖它，否则线上会退化成"中性区"。
    assert calls == ["ensure_index", "pipeline", "other", "send"]
