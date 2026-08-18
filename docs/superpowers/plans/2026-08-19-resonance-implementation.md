# resonance（多策略共振 + 位置风控）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现一个注册进现有策略框架的 `resonance` 元策略：并行运行多个子策略、按命中次数共振合并，并用真实指数/板块/行业位置（252 日分位）做高位风险提示与低位反转抄底。

**Architecture:** 数据层新增 `index_prices` 表与 `stocks.industry` 列（TUShare 指数日线 + 申万行业映射）；新增 `market_analysis/positions.py` 统一计算位置与状态（risk/bottom/neutral）；新增 `strategies/resonance/` 元策略，内部调用现有子策略 `select_prepared` 后合并，复用现有回测框架；最后补 API 与前端面板。

**Tech Stack:** Python 3.12（.venv）、pandas、numpy（≥1.20，`sliding_window_view`）、TUShare pro API、SQLite、FastAPI、Vue 3 + TypeScript；测试用 unittest（现有风格）。

## Global Constraints

- 所有配置键用 snake_case，值全部可进 `config/rules_preselect.yaml`；阈值默认：`risk_high_threshold=85`、`bottom_low_threshold=15`、`reversal_volume_ratio=1.2`、`downweight_factor=0.5`、`bottom_stock_pos_cap=30`。
- 行情列名统一为 `open/high/low/close/volume/amount/pct_chg/turnover_n`；指数帧用 `vol`（沿用 TUShare `index_daily` 字段）。
- 位置定义：收盘价在近 252 个交易日的分位（0–100）；反转确认 = 当日收涨且量比 ≥ 1.2（量比 = 当日量 / 20 日均量）。
- 无指数/行业数据时必须降级为 `neutral`，只做共振，不允许抛异常中断选股。
- 每个任务 TDD：先写失败测试 → 验证失败 → 最小实现 → 验证通过 → 提交。提交只含本任务相关文件，不夹带工作区其他未提交改动。
- 测试运行命令：`cd quant && .venv\Scripts\python.exe -m unittest tests\test_xxx.py -v`；全量回归：`.venv\Scripts\python.exe -m unittest discover -s tests -v`。
- 回测假设沿用现有框架（次日开盘买入、第 X 日收盘卖出、不计费），报告时必须注明。
- 界面文案为简体中文；接口保持现有 FastAPI 风格（`@app.get` + `dict` 返回 + `HTTPException`）。

---

## Task 0: 扩充本地个股历史（M0 数据前置，运维步骤）

**Files:**
- Modify: `config/fetch_data.yaml`

**Interfaces:**
- Consumes: 现有 `pipeline/fetch_data.py` 与 TUShare token（`.env.local`）
- Produces: `daily_prices` 覆盖 ≥1500 交易日，使 252 日分位与校准/验证拆分可行

说明：位置与回测的有效性依赖足够历史。当前 `daily_prices` 只有约 470 交易日（2024-09-11 起），252 分位预热后个股位置约 2025-09 才有效。

- [ ] **Step 1: 修改历史窗口配置**

将 `config/fetch_data.yaml` 中 `data.history_days: 700` 改为 `history_days: 1800`（约 7 年，覆盖 252 日预热 + 校准/验证期）。

- [ ] **Step 2: 全量重建个股历史**

运行（约 28–40 分钟，受 TUShare 每分钟 195 次限额约束；失败自动重试）：

```powershell
cd C:\Users\shunw\Documents\ChatGPT\工作\quant
.venv\Scripts\python.exe pipeline\fetch_data.py --force-refresh
```

`--force-refresh` 会重建 `data/raw/*_qfq.csv` 并回灌 SQLite。期间可并行进行 Task 1–2 的代码开发（不依赖本任务）。

- [ ] **Step 3: 验证覆盖范围**

```powershell
.venv\Scripts\python.exe -c "import sqlite3; c=sqlite3.connect('data/oversell.db'); print(c.execute(\"select min(trade_date), max(trade_date), count(distinct trade_date) from daily_prices where adjust='qfq'\").fetchone())"
```

Expected: `min` 早于 2021-01-01，且 `count(distinct trade_date) >= 1500`。

- [ ] **Step 4: 提交**

```bash
git add quant/config/fetch_data.yaml
git commit -m "chore(data): expand local history window to 1800 days"
```

---

## Task 1: index_prices 存储层

**Files:**
- Modify: `storage/database.py`（`init_db` 增加表；新增 4 个函数 + 1 个私有辅助）
- Test: `tests/test_index_storage.py`（新建）

**Interfaces:**
- Consumes: 现有 `init_db()`、`_connect()`、`DB_PATH`
- Produces:
  - `upsert_index_prices(frames: dict[str, pd.DataFrame]) -> int`
  - `load_index_prices(codes: list[str] | None = None, start_date: str | None = None, end_date: str | None = None) -> dict[str, pd.DataFrame]`
  - `index_price_codes() -> set[str]`
  - `index_price_signature() -> tuple[int, str | None, str | None]`

- [ ] **Step 1: 写失败测试 `tests/test_index_storage.py`**

```python
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

import storage.database as database


def _index_frame(closes: list[float], code: str = "000001.SH") -> pd.DataFrame:
    dates = pd.bdate_range("2026-01-01", periods=len(closes))
    frame = pd.DataFrame(index=dates)
    frame["open"] = closes
    frame["high"] = [v + 1 for v in closes]
    frame["low"] = [v - 1 for v in closes]
    frame["close"] = closes
    frame["vol"] = 1000.0
    frame["amount"] = [v * 1000 for v in closes]
    frame["pct_chg"] = 0.5
    return frame


class IndexStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_path = database.DB_PATH
        self.tmp = tempfile.TemporaryDirectory()
        database.DB_PATH = Path(self.tmp.name) / "index.db"

    def tearDown(self) -> None:
        database.DB_PATH = self.original_path
        self.tmp.cleanup()

    def test_upsert_and_load_round_trip(self) -> None:
        frame = _index_frame([10.0, 11.0, 12.0])
        rows = database.upsert_index_prices({"000001.SH": frame})
        self.assertEqual(rows, 3)
        loaded = database.load_index_prices(codes=["000001.SH"])
        self.assertEqual(list(loaded), ["000001.SH"])
        self.assertEqual(loaded["000001.SH"].index[0].strftime("%Y-%m-%d"), "2026-01-01")
        self.assertEqual(float(loaded["000001.SH"].loc[loaded["000001.SH"].index[0], "close"]), 10.0)

    def test_upsert_overwrites_same_key(self) -> None:
        frame = _index_frame([10.0, 11.0, 12.0])
        database.upsert_index_prices({"000001.SH": frame})
        frame2 = frame.copy()
        frame2.iloc[-1, frame2.columns.get_loc("close")] = 99.0
        database.upsert_index_prices({"000001.SH": frame2})
        loaded = database.load_index_prices()["000001.SH"]
        self.assertEqual(float(loaded["close"].iloc[-1]), 99.0)
        self.assertEqual(len(loaded), 3)

    def test_signature_and_codes(self) -> None:
        self.assertEqual(database.index_price_codes(), set())
        self.assertIsNone(database.index_price_signature()[1])
        database.upsert_index_prices({"399006.SZ": _index_frame([1.0, 2.0])})
        self.assertEqual(database.index_price_codes(), {"399006.SZ"})
        self.assertEqual(database.index_price_signature()[0], 2)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试确认失败**

```powershell
.venv\Scripts\python.exe -m unittest tests\test_index_storage.py -v
```

Expected: FAIL / ImportError（函数不存在）。

- [ ] **Step 3: 实现存储函数**

在 `storage/database.py` 中：

```python
def _float_or_none(value: Any) -> float | None:
    try:
        result = float(value)
        return None if not np.isfinite(result) else result
    except (TypeError, ValueError):
        return None
```

在 `init_db()` 的 `executescript` 末尾追加：

```sql
CREATE TABLE IF NOT EXISTS index_prices (
    code TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    open REAL, high REAL, low REAL, close REAL,
    vol REAL, amount REAL, pct_chg REAL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(code, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_index_prices_code ON index_prices(code, trade_date);
```

在 `init_db()` 后追加：

```python
def upsert_index_prices(frames: dict[str, pd.DataFrame]) -> int:
    """Persist index daily bars. frames: {code: DataFrame(DatetimeIndex, cols open/high/low/close/vol/amount/pct_chg)}."""
    init_db()
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    rows: list[tuple[Any, ...]] = []
    for code, frame in frames.items():
        for ts, row in frame.iterrows():
            rows.append((
                str(code), ts.strftime("%Y-%m-%d"),
                _float_or_none(row.get("open")), _float_or_none(row.get("high")),
                _float_or_none(row.get("low")), _float_or_none(row.get("close")),
                _float_or_none(row.get("vol")), _float_or_none(row.get("amount")),
                _float_or_none(row.get("pct_chg")), now,
            ))
    if not rows:
        return 0
    with _connect() as conn:
        conn.executemany(
            """INSERT OR REPLACE INTO index_prices
               (code, trade_date, open, high, low, close, vol, amount, pct_chg, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            rows,
        )
    return len(rows)


def load_index_prices(
    codes: list[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, pd.DataFrame]:
    """Read index daily bars as {code: DataFrame(DatetimeIndex)} sorted by date."""
    init_db()
    clauses: list[str] = []
    params: list[Any] = []
    if codes:
        clauses.append(f"code IN ({','.join('?' for _ in codes)})")
        params.extend(codes)
    if start_date:
        clauses.append("trade_date>=?")
        params.append(start_date)
    if end_date:
        clauses.append("trade_date<=?")
        params.append(end_date)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    with _connect() as conn:
        frame = pd.read_sql_query(
            f"SELECT * FROM index_prices{where} ORDER BY code, trade_date",
            conn, params=params,
        )
    if frame.empty:
        return {}
    result: dict[str, pd.DataFrame] = {}
    for code, group in frame.groupby("code", sort=False):
        group = group.copy()
        group.index = pd.to_datetime(group.pop("trade_date"))
        result[code] = group.drop(columns=["code", "updated_at"], errors="ignore")
    return result


def index_price_codes() -> set[str]:
    init_db()
    with _connect() as conn:
        rows = conn.execute("SELECT DISTINCT code FROM index_prices").fetchall()
    return {str(row[0]) for row in rows}


def index_price_signature() -> tuple[int, str | None, str | None]:
    init_db()
    with _connect() as conn:
        row = conn.execute("SELECT count(*), min(trade_date), max(trade_date) FROM index_prices").fetchone()
    return int(row[0] or 0), row[1], row[2]
```

（`np`、`datetime`、`Any` 在 `database.py` 已有 import；若无 `np` 则补 `import numpy as np`。）

- [ ] **Step 4: 运行测试确认通过**

```powershell
.venv\Scripts\python.exe -m unittest tests\test_index_storage.py -v
```

Expected: 3 个测试全部 PASS。

- [ ] **Step 5: 提交**

```bash
git add quant/storage/database.py quant/tests/test_index_storage.py
git commit -m "feat(storage): index_prices table and load/upsert functions"
```

---

## Task 2: stocks.industry 列与行业映射存储

**Files:**
- Modify: `storage/database.py`
- Test: `tests/test_index_storage.py`（追加用例）

**Interfaces:**
- Consumes: 现有 `stocks` 表（code/name/ts_code/market/list_date/updated_at）
- Produces:
  - `upsert_stock_industries(mapping: dict[str, str]) -> int`
  - `load_stock_industries() -> dict[str, str]`

- [ ] **Step 1: 写失败测试（追加到 `tests/test_index_storage.py`）**

```python
    def test_industry_mapping_round_trip(self) -> None:
        database.upsert_stocks(pd.DataFrame(
            [{"code": "000001", "name": "平安银行", "ts_code": "000001.SZ", "market": "主板", "list_date": "19910403"}]
        ))
        updated = database.upsert_stock_industries({"000001": "银行"})
        self.assertEqual(updated, 1)
        self.assertEqual(database.load_stock_industries(), {"000001": "银行"})

    def test_industry_mapping_skips_unknown_code(self) -> None:
        self.assertEqual(database.upsert_stock_industries({"999999": "未知"}), 0)
        self.assertEqual(database.load_stock_industries(), {})
```

（`upsert_stocks` 已存在于 `storage/database.py`，签名 `upsert_stocks(stock_list: pd.DataFrame)`。）

- [ ] **Step 2: 运行测试确认失败**

Expected: AttributeError（`upsert_stock_industries` 不存在）。

- [ ] **Step 3: 实现**

`init_db()` 的 `with _connect() as conn:` 块内、`executescript` 之后追加迁移：

```python
        columns = {row[1] for row in conn.execute("PRAGMA table_info(stocks)")}
        if "industry" not in columns:
            conn.execute("ALTER TABLE stocks ADD COLUMN industry TEXT")
```

模块级追加：

```python
def upsert_stock_industries(mapping: dict[str, str]) -> int:
    """Set industry for existing stocks. mapping: {6-digit code: industry name}."""
    init_db()
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    updated = 0
    with _connect() as conn:
        for code, industry in mapping.items():
            cursor = conn.execute(
                "UPDATE stocks SET industry=?, updated_at=? WHERE code=?",
                (str(industry), now, str(code).zfill(6)),
            )
            updated += int(cursor.rowcount)
    return updated


def load_stock_industries() -> dict[str, str]:
    init_db()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT code, industry FROM stocks WHERE industry IS NOT NULL AND industry != ''"
        ).fetchall()
    return {str(row[0]).zfill(6): str(row[1]) for row in rows}
```

- [ ] **Step 4: 运行测试确认通过**

Expected: 新增 2 个用例 PASS，原有 3 个仍 PASS。

- [ ] **Step 5: 提交**

```bash
git add quant/storage/database.py quant/tests/test_index_storage.py
git commit -m "feat(storage): stocks.industry column and mapping load/upsert"
```

---

## Task 3: 指数与行业抓取模块（pipeline/fetch_indices.py）

**Files:**
- Create: `pipeline/fetch_indices.py`
- Test: `tests/test_fetch_indices.py`（新建）

**Interfaces:**
- Consumes: `storage.database.upsert_index_prices/load_index_prices/index_price_codes`、`upsert_stock_industries`（Task 1–2）
- Produces:
  - `MARKET_INDEX_CODES: list[str]`
  - `load_token(env_path: Path) -> str`
  - `normalize_index_frame(df: pd.DataFrame | None) -> pd.DataFrame`
  - `fetch_index_bars(pro, code: str, start_date: str, end_date: str) -> pd.DataFrame`
  - `fetch_stock_industries(pro) -> dict[str, str]`
  - `run(use_cache_only=False, force_refresh=False, sync_industries=True, stop_event=None) -> dict`

- [ ] **Step 1: 写失败测试 `tests/test_fetch_indices.py`**

```python
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

import storage.database as database
import pipeline.fetch_indices as fetch_indices


class FakePro:
    def index_daily(self, ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        dates = pd.bdate_range("2026-01-05", periods=3).strftime("%Y%m%d")
        return pd.DataFrame({
            "ts_code": [ts_code] * 3,
            "trade_date": dates,
            "close": [10.0, 10.5, 11.0],
            "open": [9.9, 10.4, 10.9],
            "high": [10.1, 10.6, 11.1],
            "low": [9.8, 10.3, 10.8],
            "pre_close": [9.9, 10.0, 10.5],
            "change": [0.1, 0.5, 0.5],
            "pct_chg": [1.01, 5.0, 4.76],
            "vol": [1000.0, 1200.0, 900.0],
            "amount": [10000.0, 12000.0, 9000.0],
        })

    def stock_basic(self, **kwargs) -> pd.DataFrame:
        return pd.DataFrame({
            "ts_code": ["000001.SZ"],
            "name": ["平安银行"],
            "industry": ["银行"],
            "market": ["主板"],
        })


class FetchIndicesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_path = database.DB_PATH
        self.tmp = tempfile.TemporaryDirectory()
        database.DB_PATH = Path(self.tmp.name) / "fetch.db"
        database.init_db()

    def tearDown(self) -> None:
        database.DB_PATH = self.original_path
        self.tmp.cleanup()

    def test_normalize_index_frame(self) -> None:
        raw = FakePro().index_daily("000001.SH", "20260101", "20260131")
        frame = fetch_indices.normalize_index_frame(raw)
        self.assertIsInstance(frame.index, pd.DatetimeIndex)
        self.assertEqual(list(frame.columns), ["open", "high", "low", "close", "vol", "amount", "pct_chg"])
        self.assertEqual(len(frame), 3)

    def test_run_fetches_and_persists(self) -> None:
        result = fetch_indices.run(
            pro=FakePro(),
            codes=["000001.SH"],
            start_date="20260101",
            end_date="20260131",
            sync_industries=False,
        )
        self.assertEqual(result["rows"], 3)
        self.assertEqual(database.index_price_codes(), {"000001.SH"})

    def test_incremental_starts_after_latest_date(self) -> None:
        first = FakePro().index_daily("000001.SH", "20260101", "20260131")
        fetch_indices.run(pro=FakePro(), codes=["000001.SH"], start_date="20260101", end_date="20260131", sync_industries=False)
        with patch.object(fetch_indices, "fetch_index_bars", wraps=fetch_indices.fetch_index_bars) as mocked:
            fetch_indices.run(pro=FakePro(), codes=["000001.SH"], start_date="20260101", end_date="20260131", sync_industries=False)
            called = [call.args for call in mocked.call_args_list]
            self.assertTrue(all(start >= "20260107" for _, _, start, _ in called))

    def test_sync_industries_persists_mapping(self) -> None:
        database.upsert_stocks(pd.DataFrame(
            [{"code": "000001", "name": "平安银行", "ts_code": "000001.SZ", "market": "主板", "list_date": "19910403"}]
        ))
        fetch_indices.run(pro=FakePro(), codes=[], start_date="20260101", end_date="20260131", sync_industries=True)
        self.assertEqual(database.load_stock_industries(), {"000001": "银行"})


if __name__ == "__main__":
    unittest.main()
```

（`run` 需要接受 `pro`、`codes`、`start_date`、`end_date`、`sync_industries` 参数以支持测试注入；CLI 再包一层。）

- [ ] **Step 2: 运行测试确认失败**

Expected: ModuleNotFoundError。

- [ ] **Step 3: 实现 `pipeline/fetch_indices.py`**

```python
"""Fetch market/industry index bars and stock industry mapping from TUShare."""
from __future__ import annotations

import argparse
import logging
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
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
            return {"rows": 0, "codes": [], "cached_only": True}
        import tushare as ts

        ts.set_token(load_token())
        pro = ts.pro_api()
    end_date = end_date or datetime.now().strftime("%Y%m%d")
    start_date = start_date or DEFAULT_START
    existing = index_price_codes()
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
```

- [ ] **Step 4: 运行测试确认通过**

Expected: 4 个用例 PASS（含增量起点断言）。

- [ ] **Step 5: 提交**

```bash
git add quant/pipeline/fetch_indices.py quant/tests/test_fetch_indices.py
git commit -m "feat(fetch): index bars and industry mapping fetcher"
```

---

## Task 4: 位置与状态模块（market_analysis/positions.py）

**Files:**
- Create: `market_analysis/positions.py`
- Test: `tests/test_positions.py`（新建）

**Interfaces:**
- Consumes: `storage.database.load_index_prices/index_price_signature/load_daily_prices`（Task 1）
- Produces:
  - `compute_position(close: pd.Series, window: int = 252) -> pd.Series`
  - `reversal_confirmed(frame: pd.DataFrame, date: pd.Timestamp, volume_ratio: float = 1.2) -> bool`
  - `classify(position: float | None, reversal: bool, risk_threshold: float = 85.0, bottom_threshold: float = 15.0) -> str`
  - `market_positions(as_of: str | None = None) -> dict`
  - `board_positions(as_of: str | None = None) -> dict`
  - `industry_positions(as_of: str | None = None) -> dict`
  - `stock_positions(codes: list[str], as_of: str | None = None, adjust: str = "qfq") -> dict[str, float | None]`
  - `market_regime(as_of: str | None = None, risk_threshold: float = 85.0, bottom_threshold: float = 15.0, reversal_volume_ratio: float = 1.2) -> dict`

- [ ] **Step 1: 写失败测试 `tests/test_positions.py`**

```python
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

import storage.database as database
from market_analysis.positions import (
    board_positions,
    classify,
    compute_position,
    industry_positions,
    market_positions,
    market_regime,
    reversal_confirmed,
    stock_positions,
)


def _index_frame(closes: list[float], pct_chg: float = 0.5, vol: float = 1000.0, code: str = "000001.SH") -> pd.DataFrame:
    dates = pd.bdate_range("2025-01-01", periods=len(closes))
    frame = pd.DataFrame(index=dates)
    frame["open"] = closes
    frame["high"] = [v + 1 for v in closes]
    frame["low"] = [v - 1 for v in closes]
    frame["close"] = closes
    frame["vol"] = vol
    frame["amount"] = [v * vol for v in closes]
    frame["pct_chg"] = pct_chg
    return frame


def _stock_frame(closes: np.ndarray) -> pd.DataFrame:
    dates = pd.bdate_range("2025-01-01", periods=len(closes))
    close = pd.Series(closes, index=dates, dtype=float)
    frame = pd.DataFrame(index=dates)
    frame["open"] = close * 0.995
    frame["high"] = close * 1.01
    frame["low"] = close * 0.99
    frame["close"] = close
    frame["volume"] = 1_000_000.0
    frame["amount"] = close * 1_000_000
    frame["pct_chg"] = close.pct_change().fillna(0) * 100
    frame["turnover_n"] = frame["amount"].rolling(20, min_periods=1).sum()
    return frame


class PositionTests(unittest.TestCase):
    def test_compute_position_monotonic_series(self) -> None:
        close = pd.Series(np.linspace(10, 20, 300))
        pos = compute_position(close, window=252)
        self.assertEqual(float(pos.iloc[-1]), 100.0)
        self.assertTrue(np.isnan(pos.iloc[250]))
        self.assertFalse(np.isnan(pos.iloc[251]))

    def test_compute_position_flat_series_is_high(self) -> None:
        close = pd.Series(np.full(300, 15.0))
        pos = compute_position(close, window=252)
        self.assertEqual(float(pos.iloc[-1]), 100.0)

    def test_reversal_confirmed_boundary(self) -> None:
        frame = _index_frame([10.0] * 20 + [10.5], pct_chg=1.0, vol=1000.0)
        frame.iloc[-1, frame.columns.get_loc("vol")] = 1300.0
        self.assertTrue(reversal_confirmed(frame, frame.index[-1]))
        frame.iloc[-1, frame.columns.get_loc("vol")] = 1100.0
        self.assertFalse(reversal_confirmed(frame, frame.index[-1]))

    def test_classify_precedence_and_boundaries(self) -> None:
        self.assertEqual(classify(90.0, reversal=False), "risk")
        self.assertEqual(classify(85.0, reversal=False), "risk")
        self.assertEqual(classify(84.9, reversal=False), "neutral")
        self.assertEqual(classify(15.0, reversal=True), "bottom")
        self.assertEqual(classify(15.0, reversal=False), "neutral")
        self.assertEqual(classify(None, reversal=True), "neutral")
        self.assertEqual(classify(0.0, reversal=False), "neutral")

    def test_market_positions_and_regime_with_db(self) -> None:
        self.original_path = database.DB_PATH
        self.tmp = tempfile.TemporaryDirectory()
        database.DB_PATH = Path(self.tmp.name) / "positions.db"
        try:
            database.upsert_index_prices({"000001.SH": _index_frame(np.linspace(10, 20, 280).tolist(), code="000001.SH")})
            payload = market_positions(as_of="2025-12-31")
            self.assertTrue(payload["available"])
            self.assertEqual(payload["market"][0]["code"], "000001.SH")
            self.assertGreaterEqual(payload["market"][0]["position"], 90.0)
            self.assertEqual(market_regime(as_of="2025-12-31")["regime"], "risk")
        finally:
            database.DB_PATH = self.original_path
            self.tmp.cleanup()

    def test_no_data_degrades_to_neutral(self) -> None:
        self.original_path = database.DB_PATH
        self.tmp = tempfile.TemporaryDirectory()
        database.DB_PATH = Path(self.tmp.name) / "positions-empty.db"
        try:
            payload = market_positions()
            self.assertFalse(payload["available"])
            self.assertEqual(payload["regime"], "neutral")
            self.assertEqual(board_positions()["available"], False)
            self.assertEqual(industry_positions()["available"], False)
            self.assertEqual(market_regime()["regime"], "neutral")
            self.assertEqual(stock_positions(["000001"]), {})
        finally:
            database.DB_PATH = self.original_path
            self.tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试确认失败**

Expected: ModuleNotFoundError。

- [ ] **Step 3: 实现 `market_analysis/positions.py`**

```python
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
        payload: dict[str, Any] = {"available": False, "as_of": as_of, "regime": "neutral", "market": [], "boards": {}, "industries": []}
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
    payload = {"available": True, "as_of": as_of, "regime": regime, "market": market, "boards": boards, "industries": industries}
    with _CACHE_LOCK:
        _CACHE.clear()
        _CACHE[cache_key] = payload
    return payload


def market_positions(as_of: str | None = None) -> dict[str, Any]:
    return _positions_payload(as_of)


def board_positions(as_of: str | None = None) -> dict[str, Any]:
    return {"available": market_positions(as_of)["available"], "boards": market_positions(as_of)["boards"]}


def industry_positions(as_of: str | None = None) -> dict[str, Any]:
    return {"available": market_positions(as_of)["available"], "industries": market_positions(as_of)["industries"]}


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
        board["reversal"] and board["position_bottom"] <= bottom_threshold for board in payload["boards"].values()
    )
    return {
        "regime": "risk" if risk else ("bottom" if bottom else "neutral"),
        "available": True,
        "market": payload["market"],
        "boards": payload["boards"],
    }
```

（`market_regime` 中 `reversal_confirmed` 的调用从缓存 payload 的 `reversal` 字段取即可，避免重复查库；实现时以 `item["reversal"]` 为准。）

- [ ] **Step 4: 运行测试确认通过**

Expected: 6 个用例 PASS。

- [ ] **Step 5: 提交**

```bash
git add quant/market_analysis/positions.py quant/tests/test_positions.py
git commit -m "feat(positions): 252d position percentiles and risk/bottom regime"
```

---

## Task 5: 市场位置 API

**Files:**
- Modify: `backend/app.py`
- Test: `tests/test_positions_api.py`（新建）

**Interfaces:**
- Consumes: `market_analysis.positions.market_positions/board_positions/industry_positions`
- Produces: `GET /api/market/positions` → `{"available", "as_of", "regime", "market": [...], "boards": {...}, "industries": [...]}`

- [ ] **Step 1: 写失败测试 `tests/test_positions_api.py`**

```python
from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import backend.app as backend_app


class MarketPositionsApiTests(unittest.TestCase):
    def test_positions_endpoint_returns_payload(self) -> None:
        fake = {
            "available": True, "as_of": "2026-08-18", "regime": "risk",
            "market": [{"code": "000001.SH", "position": 54.8, "close": 3990.3, "reversal": False, "trade_date": "2026-08-18"}],
            "boards": {"main": {"position_risk": 54.8, "position_bottom": 54.8, "reversal": False, "codes": ["000001.SH"]}},
            "industries": [{"code": "801010.SI", "position": 23.8, "reversal": False, "trade_date": "2026-08-18"}],
        }
        with patch("backend.app.market_positions", return_value=fake), \
             patch("backend.app.board_positions", return_value={"available": True, "boards": fake["boards"]}), \
             patch("backend.app.industry_positions", return_value={"available": True, "industries": fake["industries"]}):
            client = TestClient(backend_app.app)
            response = client.get("/api/market/positions?as_of=2026-08-18")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["regime"], "risk")
        self.assertEqual(payload["market"][0]["code"], "000001.SH")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试确认失败**

Expected: 404（路由不存在）。

- [ ] **Step 3: 实现**

在 `backend/app.py` 顶部 import 区追加：

```python
from market_analysis.positions import board_positions, industry_positions, market_positions
```

在 `/api/market/breadth` 路由之后追加：

```python
@app.get("/api/market/positions")
def get_market_positions(as_of: str | None = None) -> dict[str, Any]:
    try:
        market = market_positions(as_of=as_of)
        boards = board_positions(as_of=as_of)
        industries = industry_positions(as_of=as_of)
        return {
            "available": market["available"],
            "as_of": as_of,
            "regime": market.get("regime", "neutral"),
            "market": market.get("market", []),
            "boards": boards.get("boards", {}),
            "industries": industries.get("industries", []),
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("market positions calculation failed")
        raise HTTPException(status_code=500, detail=f"市场位置计算失败: {exc}") from exc
```

- [ ] **Step 4: 运行测试确认通过**

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add quant/backend/app.py quant/tests/test_positions_api.py
git commit -m "feat(api): /api/market/positions endpoint"
```

---

## Task 6: resonance 策略骨架与注册

**Files:**
- Create: `strategies/resonance/__init__.py`、`strategies/resonance/strategy.py`
- Modify: `strategies/registry.py`、`config/rules_preselect.yaml`
- Test: `tests/test_resonance.py`（新建）

**Interfaces:**
- Consumes: `strategies.base`、`pipeline.schemas.Candidate`、各子策略 `select_prepared/prepare_all`、`strategies.registry.get_strategy`
- Produces: `ResonanceStrategy`（`meta.id="resonance"`），注册进 registry；`prepare_all` 返回合并列帧；`select_prepared` 在 Task 7 补合并/风控

- [ ] **Step 1: 写失败测试 `tests/test_resonance.py`**

```python
from __future__ import annotations

import unittest

import pandas as pd

from strategies.registry import get_strategy, list_strategies
from strategies.resonance.strategy import ResonanceStrategy


class ResonanceSkeletonTests(unittest.TestCase):
    def test_strategy_is_registered(self) -> None:
        strategy = get_strategy("resonance")
        self.assertIsInstance(strategy, ResonanceStrategy)
        self.assertIn("resonance", {item.id for item in list_strategies()})

    def test_default_config_has_required_keys(self) -> None:
        cfg = ResonanceStrategy()._cfg({})
        self.assertEqual(cfg["sub_strategies"], ["b1", "volume_new_high", "high_52w_momentum"])
        self.assertEqual(cfg["min_hits"], 2)
        self.assertEqual(cfg["risk_high_threshold"], 85.0)
        self.assertEqual(cfg["bottom_low_threshold"], 15.0)
        self.assertEqual(cfg["reversal_volume_ratio"], 1.2)
        self.assertEqual(cfg["high_position_action"], "downweight")
        self.assertEqual(cfg["downweight_factor"], 0.5)
        self.assertEqual(cfg["bottom_stock_pos_cap"], 30.0)

    def test_warmup_covers_longest_sub_strategy_and_252(self) -> None:
        strategy = ResonanceStrategy()
        warmup = strategy.warmup_bars({})
        self.assertGreaterEqual(warmup, 252 + 30)

    def test_prepare_all_merges_columns_from_sub_strategies(self) -> None:
        strategy = ResonanceStrategy(registry=lambda sid: _FakeSubStrategy(sid))
        frame = pd.DataFrame(
            {"close": [10.0, 11.0, 12.0], "volume": [100.0, 100.0, 100.0], "amount": [1000.0, 1000.0, 1000.0]},
            index=pd.bdate_range("2026-01-01", periods=3),
        )
        merged = strategy.prepare_all({"000001": frame}, strategy._cfg({}))
        self.assertIn("kdj_col", merged["000001"].columns)
        self.assertIn("mom_col", merged["000001"].columns)


class _FakeSubStrategy:
    meta = type("Meta", (), {"default_config": {}})()

    def __init__(self, strategy_id: str) -> None:
        self.strategy_id = strategy_id

    def warmup_bars(self, _: dict) -> int:
        return 100

    def prepare_all(self, data: dict[str, pd.DataFrame], _: dict, __=None) -> dict[str, pd.DataFrame]:
        result = {}
        for code, frame in data.items():
            item = frame.copy()
            item["kdj_col"] = 1.0
            item["mom_col"] = 2.0
            result[code] = item
        return result

    def select_prepared(self, data: dict[str, pd.DataFrame], _: dict, context) -> list:
        return []


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试确认失败**

Expected: ImportError（`strategies.resonance` 不存在）。

- [ ] **Step 3: 实现策略骨架**

`strategies/resonance/__init__.py`：

```python
"""Multi-strategy resonance meta-strategy."""

from strategies.resonance.strategy import ResonanceStrategy

__all__ = ["ResonanceStrategy"]
```

`strategies/resonance/strategy.py`：

```python
"""Resonance meta-strategy: sub-strategy consensus + position regime overlay."""
from __future__ import annotations

import logging
from typing import Callable

import pandas as pd

from pipeline.schemas import Candidate
from strategies.base import StrategyContext, StrategyMeta
from strategies.registry import get_strategy as default_get_strategy

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
        self._registry = registry or default_get_strategy
        self._prepared_by_sub: dict[str, dict[str, pd.DataFrame]] = {}

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
        return [(sub_id, self._registry(sub_id)) for sub_id in cfg["sub_strategies"]]

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
```

`strategies/registry.py` 修改：顶部 import 加 `from strategies.resonance.strategy import ResonanceStrategy`；`_STRATEGIES` 字典加 `"resonance": ResonanceStrategy(),`。

`config/rules_preselect.yaml` 的 `strategies:` 段追加：

```yaml
  resonance:
    enabled: true
    sub_strategies: [b1, volume_new_high, high_52w_momentum]
    min_hits: 2
    max_candidates: 30
    risk_high_threshold: 85
    bottom_low_threshold: 15
    reversal_volume_ratio: 1.2
    high_position_action: downweight
    downweight_factor: 0.5
    bottom_fishing_enabled: true
    bottom_stock_pos_cap: 30
```

- [ ] **Step 4: 运行测试确认通过**

Expected: 4 个用例 PASS。

- [ ] **Step 5: 提交**

```bash
git add quant/strategies/resonance quant/strategies/registry.py quant/config/rules_preselect.yaml quant/tests/test_resonance.py
git commit -m "feat(strategies): resonance meta-strategy skeleton and registration"
```

---

## Task 7: resonance 合并、风控与抄底池

**Files:**
- Modify: `strategies/resonance/strategy.py`
- Test: `tests/test_resonance.py`（追加用例）

**Interfaces:**
- Consumes: `market_analysis.positions.market_regime/stock_positions`（Task 4）、`pipeline.schemas.Candidate`
- Produces: `select_prepared` 输出候选，`extra` 含 `hit_count/hits/combined_score/regime/market_pos/board_pos/stock_pos/bottom_signal`

- [ ] **Step 1: 写失败测试（追加到 `tests/test_resonance.py`）**

```python
from unittest.mock import patch

from pipeline.schemas import Candidate
from strategies.base import StrategyContext


def _candidate(code: str, score: float) -> Candidate:
    return Candidate(
        code=code, name=code, date="2026-08-18", strategy="resonance",
        close=10.0, turnover_n=1000.0, score=score,
    )


class ResonanceMergeTests(unittest.TestCase):
    def _context(self) -> StrategyContext:
        return StrategyContext(
            pick_date=pd.Timestamp("2026-08-18"),
            names={"000001": "股票1", "000002": "股票2", "000003": "股票3"},
            pool={"000001", "000002", "000003"},
            progress_enabled=False,
        )

    def test_merge_requires_min_hits_and_sums_percentile_ranks(self) -> None:
        strategy = ResonanceStrategy(registry=lambda sid: _FakeSubStrategy(sid))
        cfg = strategy._cfg({})
        sub_results = [
            [_candidate("000001", 1.0), _candidate("000002", 2.0)],
            [_candidate("000002", 1.0), _candidate("000003", 2.0)],
        ]
        merged = strategy._merge(sub_results, cfg)
        codes = {item.code for item in merged}
        self.assertEqual(codes, {"000002"})  # 只有 2 次命中的入选
        hit = next(item for item in merged if item.code == "000002")
        self.assertEqual(hit.extra["hit_count"], 2)
        self.assertEqual(hit.strategy, "resonance")
        self.assertIn("hits", hit.extra)

    def test_risk_regime_downweights_high_position_stocks(self) -> None:
        strategy = ResonanceStrategy(registry=lambda sid: _FakeSubStrategy(sid))
        candidates = [_candidate("000001", 1.0), _candidate("000002", 1.0)]
        fake_regime = {
            "regime": "risk", "available": True,
            "market": [{"code": "000001.SH", "position": 90.0}],
            "boards": {},
        }
        with patch("strategies.resonance.strategy.market_regime", return_value=fake_regime), \
             patch("strategies.resonance.strategy.stock_positions", return_value={"000001": 90.0, "000002": 20.0}):
            result = strategy._apply_regime(candidates, strategy._cfg({}), self._context(), {})
        by_code = {item.code: item for item in result}
        self.assertEqual(by_code["000001"].extra["regime"], "risk")
        self.assertEqual(by_code["000001"].score, 0.5)  # 1.0 * 0.5 降权
        self.assertEqual(by_code["000002"].score, 1.0)

    def test_bottom_regime_adds_pool_candidates(self) -> None:
        strategy = ResonanceStrategy(registry=lambda sid: _FakeSubStrategy(sid))
        frame = pd.DataFrame(
            {"close": [10.0] * 20 + [10.5], "pct_chg": [0.0] * 20 + [5.0],
             "volume": [1000.0] * 21, "amount": [10000.0] * 21},
            index=pd.bdate_range("2026-07-20", periods=21),
        )
        frame.iloc[-1, frame.columns.get_loc("volume")] = 1400.0
        data = {"000003": frame}
        fake_regime = {"regime": "bottom", "available": True, "market": [], "boards": {}}
        with patch("strategies.resonance.strategy.market_regime", return_value=fake_regime), \
             patch("strategies.resonance.strategy.stock_positions", return_value={"000003": 5.0}):
            result = strategy._apply_regime([], strategy._cfg({}), self._context(), data)
        self.assertEqual([item.code for item in result], ["000003"])
        self.assertTrue(result[0].extra["bottom_signal"])
```

（`_FakeSubStrategy` 的 `meta` 需返回可空对象以支持 `_sub_cfg`，给 `_FakeSubStrategy` 加 `meta = type("M", (), {"default_config": {}})()`；`_apply_regime` 签名：`_apply_regime(candidates, cfg, context, data)`。）

- [ ] **Step 2: 运行测试确认失败**

Expected: AttributeError（`_merge`/`_apply_regime` 不存在）。

- [ ] **Step 3: 实现合并与风控**

在 `strategies/resonance/strategy.py` 顶部 import 追加：

```python
import numpy as np

from market_analysis.positions import market_regime, stock_positions
from strategies.base import StrategyContext, StrategyMeta
```

替换 `select_prepared` 的 `return []`，并追加两个私有方法：

```python
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
            if regime == "risk" and item.extra["stock_pos"] is not None and item.extra["stock_pos"] >= float(cfg["risk_high_threshold"]):
                if cfg["high_position_action"] == "exclude":
                    item.extra["risk_marked"] = True
                else:
                    item.score = float(item.score) * float(cfg["downweight_factor"])
                    item.extra["risk_marked"] = True
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
```

- [ ] **Step 4: 运行测试确认通过**

Expected: 追加 3 个用例 PASS，原有 4 个仍 PASS。

- [ ] **Step 5: 提交**

```bash
git add quant/strategies/resonance/strategy.py quant/tests/test_resonance.py
git commit -m "feat(strategies): resonance merge, risk downweight and bottom pool"
```

---

## Task 8: 回测验证脚本与三组对比

**Files:**
- Create: `scripts/run_resonance_verification.py`

**Interfaces:**
- Consumes: `backtest.service.run_backtest`、`backtest.schemas.BacktestRequest`、`storage.database`
- Produces: `data/resonance_verification/summary.csv` 与控制台对比表

- [ ] **Step 1: 实现脚本 `scripts/run_resonance_verification.py`**

```python
"""Run calibration/validation backtests for resonance vs baselines.

Usage:
    python scripts/run_resonance_verification.py

Requires: local stock history >= 1500 trading days (Task 0) and index data
(Task 3: python pipeline/fetch_indices.py).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from backtest.schemas import BacktestRequest  # noqa: E402
from backtest.service import run_backtest  # noqa: E402

HOLDING_PERIODS = [5, 10, 20]
RUNS = [
    ("b1", "baseline-b1"),
    ("volume_new_high", "baseline-volume_new_high"),
    ("high_52w_momentum", "baseline-high_52w_momentum"),
    ("resonance", "resonance-no-regime"),
    ("resonance", "resonance-full"),
]


def _config(strategy_id: str, with_regime: bool) -> dict:
    resonance = {
        "enabled": True,
        "sub_strategies": ["b1", "volume_new_high", "high_52w_momentum"],
        "min_hits": 2,
        "max_candidates": 30,
        "risk_high_threshold": 85,
        "bottom_low_threshold": 15,
        "reversal_volume_ratio": 1.2,
        "high_position_action": "downweight",
        "downweight_factor": 0.5,
        "bottom_fishing_enabled": with_regime,
        "bottom_stock_pos_cap": 30,
    }
    return {
        "global": {"adjust": "qfq", "top_m": 3000, "n_turnover_days": 43, "markets": ["main", "gem", "star", "bse"]},
        "strategies": {strategy_id: resonance if strategy_id == "resonance" else {}},
    }


def main() -> None:
    rows: list[dict] = []
    for start, end, phase in [
        ("2023-01-03", "2024-06-28", "calibration-1"),
        ("2024-07-01", "2025-06-30", "calibration-2"),
        ("2025-07-01", "2026-08-18", "validation"),
    ]:
        for strategy_id, label in RUNS:
            request = BacktestRequest(
                strategy_id=strategy_id,
                start_date=start,
                end_date=end,
                holding_periods=HOLDING_PERIODS,
                config=_config(strategy_id, with_regime="full" in label),
            )
            result = run_backtest(f"verify-{phase}-{label}", request)
            metrics = result.metrics
            rows.append({
                "phase": phase,
                "label": label,
                "strategy_id": strategy_id,
                "signal_count": metrics["signal_count"],
                "win_rate_pct": metrics["win_rate_pct"],
                "average_return_pct": metrics["average_return_pct"],
                "profit_factor": metrics["profit_factor"],
                "signal_day_count": metrics["signal_day_count"],
            })
            print(phase, label, metrics["signal_count"], metrics["win_rate_pct"], metrics["average_return_pct"])
    out_dir = _ROOT / "data" / "resonance_verification"
    out_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(out_dir / "summary.csv", index=False, encoding="utf-8-sig")
    print("saved:", out_dir / "summary.csv")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 运行前置数据校验**

```powershell
.venv\Scripts\python.exe pipeline\fetch_indices.py --codes all
.venv\Scripts\python.exe -c "import sqlite3; c=sqlite3.connect('data/oversell.db'); print(c.execute(\"select min(trade_date), count(distinct trade_date) from daily_prices where adjust='qfq'\").fetchone())"
```

Expected: 个股历史 ≥1500 交易日（否则先完成 Task 0）。

- [ ] **Step 3: 运行验证脚本**

```powershell
.venv\Scripts\python.exe scripts\run_resonance_verification.py
```

Expected: 输出 calibration/validation × 5 组对比行；`summary.csv` 生成。

- [ ] **Step 4: 依据结果定稿阈值**

仅允许用 calibration 列调整 `config/rules_preselect.yaml` 中 resonance 阈值；validation 只做一次确认。若信号量 <200 或验证期未跑，必须在 README/回测报告标注"样本外未验证"。

- [ ] **Step 5: 提交**

```bash
git add quant/scripts/run_resonance_verification.py
git commit -m "feat(scripts): resonance calibration/validation backtest runner"
```

---

## Task 9: 前端市场位置面板与候选表字段

**Files:**
- Create: `web/src/components/MarketPositionsPanel.vue`
- Modify: `web/src/App.vue`（挂载面板）、`web/src/components/CandidateTable.vue`（命中次数/状态徽标列）

**Interfaces:**
- Consumes: `GET /api/market/positions`（Task 5）、候选 `extra.hit_count/regime`
- Produces: 市场位置面板 UI 与候选表新列

- [ ] **Step 1: 创建 `web/src/components/MarketPositionsPanel.vue`**

```vue
<script setup lang="ts">
import { onMounted, ref } from "vue";
import { api } from "../api";

interface PositionItem { code: string; position: number; close: number; reversal: boolean; trade_date: string }
interface BoardItem { position_risk: number; position_bottom: number; reversal: boolean; codes: string[] }

const regime = ref("neutral");
const market = ref<PositionItem[]>([]);
const boards = ref<Record<string, BoardItem>>({});
const industries = ref<PositionItem[]>([]);
const available = ref(false);

const regimeClass = (value: string) => ({ risk: "regime-risk", bottom: "regime-bottom", neutral: "regime-neutral" })[value] || "regime-neutral";
const positionClass = (value: number) => (value >= 85 ? "pos-high" : value <= 15 ? "pos-low" : "pos-mid");

onMounted(async () => {
  const payload = await api<{ available: boolean; regime: string; market: PositionItem[]; boards: Record<string, BoardItem>; industries: PositionItem[] }>("/api/market/positions");
  available.value = payload.available;
  regime.value = payload.regime;
  market.value = payload.market;
  boards.value = payload.boards;
  industries.value = payload.industries;
});
</script>

<template>
  <section class="panel market-positions-panel">
    <header class="panel-header">
      <span>市场位置</span>
      <span v-if="available" :class="['regime-badge', regimeClass(regime)]">
        {{ regime === "risk" ? "风险区" : regime === "bottom" ? "抄底区" : "中性" }}
      </span>
      <span v-else class="regime-badge regime-neutral">无数据</span>
    </header>
    <div v-if="available">
      <div class="index-grid">
        <div v-for="item in market" :key="item.code" class="index-card">
          <strong>{{ item.code }}</strong>
          <span :class="positionClass(item.position)">{{ item.position.toFixed(1) }}</span>
          <small>{{ item.reversal ? "反转确认" : "" }} · {{ item.trade_date }}</small>
        </div>
      </div>
      <table class="positions-table">
        <thead><tr><th>行业</th><th>位置</th><th>状态</th></tr></thead>
        <tbody>
          <tr v-for="item in industries" :key="item.code">
            <td>{{ item.code }}</td>
            <td :class="positionClass(item.position)">{{ item.position.toFixed(1) }}</td>
            <td>{{ item.reversal ? "反转" : item.position >= 85 ? "风险" : item.position <= 15 ? "低位" : "" }}</td>
          </tr>
        </tbody>
      </table>
    </div>
    <p v-else class="muted">暂无指数数据，请先运行指数抓取。</p>
  </section>
</template>

<style scoped>
.index-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(120px, 1fr)); gap: 8px; }
.index-card { border: 1px solid var(--border, #d7e0de); border-radius: 6px; padding: 8px; display: flex; flex-direction: column; }
.regime-badge { padding: 2px 8px; border-radius: 999px; font-size: 12px; }
.regime-risk { background: #c43d3d; color: #fff; }
.regime-bottom { background: #087f78; color: #fff; }
.regime-neutral { background: #d7e0de; color: #29484d; }
.pos-high { color: #c43d3d; font-weight: 700; }
.pos-low { color: #087f78; font-weight: 700; }
.pos-mid { color: #526c71; }
.positions-table { width: 100%; border-collapse: collapse; margin-top: 10px; }
.positions-table th, .positions-table td { border-bottom: 1px solid #d7e0de; padding: 4px 6px; text-align: left; }
</style>
```

（`api` 辅助函数按 `web/src` 现有封装导入；若实际文件是 `web/src/api.ts`，按现有路径调整 import。样式变量沿用 `web/src/styles.css`。）

- [ ] **Step 2: 挂载到 `web/src/App.vue`**

在 `<script setup>` import 区追加 `import MarketPositionsPanel from "./components/MarketPositionsPanel.vue";`，并在 `MarketBreadthPanel` 所在位置附近追加：

```vue
<MarketPositionsPanel />
```

- [ ] **Step 3: 扩展 `web/src/components/CandidateTable.vue` 候选列**

在表格 `thead` 追加 `<th>命中</th><th>状态</th>`；在行内追加：

```vue
<td>{{ row.extra?.hit_count ?? "" }}</td>
<td>
  <span v-if="row.extra?.regime === 'risk'" class="trade-status risk">风险</span>
  <span v-else-if="row.extra?.regime === 'bottom'" class="trade-status bottom">抄底</span>
  <span v-else-if="row.extra?.bottom_signal" class="trade-status bottom">抄底信号</span>
  <span v-else></span>
</td>
```

- [ ] **Step 4: 构建验证**

```powershell
cd web
npm run build
```

Expected: 构建成功，无类型错误。

- [ ] **Step 5: 提交**

```bash
git add quant/web/src/components/MarketPositionsPanel.vue quant/web/src/App.vue quant/web/src/components/CandidateTable.vue
git commit -m "feat(web): market positions panel and candidate regime columns"
```

---

## Task 10: 文档更新与全量回归

**Files:**
- Modify: `README.md`、`项目任务表.md`

- [ ] **Step 1: 更新 README**

在 README 的策略列表加入 `resonance`（多策略共振 + 位置风控），说明：子策略集合、min_hits、阈值、抄底池，以及运行命令：

```bash
python run_all.py --data-mode existing --strategy-id resonance --no-dashboard
python -m pipeline.fetch_indices --codes all
```

更新"策略回测"段落：resonance 已加入回测页可选策略；回测报告注明"不计佣金/滑点，样本外未验证时明确标注"。

- [ ] **Step 2: 更新项目任务表**

在 `项目任务表.md` 追加"resonance 多策略共振"完成记录：M0–M5 状态、验证期结论、阈值定稿值。

- [ ] **Step 3: 全量回归**

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Expected: 全部 PASS（含既有用例，确保无回归）。

- [ ] **Step 4: 提交**

```bash
git add quant/README.md quant/项目任务表.md
git commit -m "docs: resonance feature docs and task table update"
```

---

## Self-Review 结论

- Spec 覆盖：数据层（Task 0–3）、位置模块（Task 4）、API（Task 5）、元策略（Task 6–7）、回测验证（Task 8）、前端（Task 9）、文档（Task 10），与设计文档第 5–11 节一一对应。
- 占位符：无 TBD/TODO；每个代码步骤均给出可运行代码与测试。
- 类型一致性：`upsert_index_prices/load_index_prices`、`market_regime`、`stock_positions`、`ResonanceStrategy._merge/_apply_regime/_bottom_pool` 的签名在任务间一致；`_apply_regime` 增加 `data` 参数贯穿 Task 7 测试与实现。
- 风险项：Task 8 的验证依赖 Task 0 数据扩充；若 TUShare 配额或时间不允许，验证结论必须标注"样本外未验证"，不得删除该标注。
