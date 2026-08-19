# 多策略共振 + 位置风控（resonance）设计文档

日期：2026-08-19
状态：已获用户确认（决策记录见文末）

## 1. 背景与目标

现有三个策略（B1 战法、缩量新高、52 周新高动量）都是单一价格类策略，60 日回测（2026-05-19 → 2026-08-11，持 5 日）表现不佳：B1 胜率 33~35%、52 周新高动量胜率 45.3% 且平均收益为负，仅缩量新高勉强为正（胜率 45.5%，盈亏因子 1.14）。检测发现的系统性问题是：

1. 没有任何市场风险开关，急跌段（2026-06 底 ~ 07 中）所有策略深度回撤；
2. 单一策略信号质量参差，无交叉验证；
3. 系统内没有任何指数/行业数据，"板块位置""全市场位置"无从计算。

本设计目标：注册一个"多策略共振"元策略 `resonance`，接入真实指数与申万行业指数，用"位置分位"定义风险区与抄底区，并用数据验证过的高胜率阈值驱动提示与过滤。

## 2. 现状检测摘要

### 2.1 现有策略

| 策略 | 核心逻辑 | 60 日回测（持5日） |
| --- | --- | --- |
| b1 | J 值超卖 + 日/周线均线多头（+MACD、量比可配） | 胜率 33~35%，均收益 −1.6%~−3.0%，PF 0.5~0.7 |
| volume_new_high | 缩量新高 + `-corr(HIGH,VOL)×rank(stddev(HIGH))` | 胜率 45.5%，均收益 +0.61%，PF 1.14 |
| high_52w_momentum | 接近 252 日高点 + 126 日动量 + 站上 60 日线 + 截面排名 | 胜率 45.3%，均收益 −1.11%，PF 0.80 |

### 2.2 数据现状与缺口

- 本地 `daily_prices`：5542 只、约 252 万行，2024-09-11 → 2026-08-18（约 470 交易日）；
- `stocks` 表无行业字段；系统无任何指数数据；
- TUShare 权限实测可用：`stock_basic`（含申万行业）、`index_daily`（上证/创业板/沪深300/中证500/科创50/北证50/申万行业指数，2023-01 至今全历史）；`index_classify`（申万 L1，31 个）可用；`index_member` 无权限（不需要，行业映射用 `stock_basic.industry`）。

## 3. 胜率研究依据（位置定义）

研究方法：对 6 个市场指数、31 个申万 L1 行业指数、全市场个股（≥300 根K线，5374 只）分别计算"收盘价在近 252 交易日的分位"（0–100），统计未来 5/10/20 交易日收益的胜率与均值，并测试低位叠加反转确认（当日收涨 + 量比 ≥ 1.2）的效果。样本：市场指数 5268 个指数日、行业指数 27218 个、个股约 115 万样本。

### 3.1 高位（风险区）

| 位置分位 | 市场指数 fwd10 胜率 | 市场指数 fwd20 胜率/均值 | 个股 fwd20 胜率/中位 |
| --- | --- | --- | --- |
| 40–80（顺势甜区） | 55–72% | 60–74% / +2~4% | 44–47% |
| 80–90 | 55.6% | 59% / +2.35% | 44.5% |
| **90–100（高位）** | **51.6%** | **49.9% / +0.38%** | **41.3% / −3.04%** |

结论：位置 ≥ 85 后顺势收益明显衰减，90–100 为明确危险区（尤其个股追高）。定义**风险区 = 位置 ≥ 85**。

### 3.2 低位 + 反转（抄底区）

| 场景 | fwd10 胜率 | fwd20 胜率 | fwd20 均值 |
| --- | --- | --- | --- |
| 市场指数位置 ≤ 15，无反转 | 43.8% | 49.2% | +4.82% |
| **市场指数位置 ≤ 15 + 反转确认** | **62.7%** | **79.1%** | **+16.84%** |
| 行业指数位置 ≤ 15 + 反转确认 | 58.3% | 63.4% | +6.84% |
| 个股低位 + 个股反转 | 45.7%（无提升） | — | — |

结论：
- **反转确认必须定义在指数/板块/行业层面，个股层面无效**；
- 定义**抄底区 = 位置 ≤ 15 且当日反转确认**（反转确认 = 当日收涨 + 量比 ≥ 1.2）；
- 位置本身呈非单调：10–40 是"接飞刀"危险段，不应单独作为抄底依据。

### 3.3 阈值定稿（用户已确认）

| 参数 | 值 | 依据 |
| --- | --- | --- |
| 风险区阈值 `risk_high_threshold` | 85 | 3.1 节，≥85 胜率衰减、90–100 最差 |
| 抄底区阈值 `bottom_low_threshold` | 15 | 3.2 节，≤15 + 反转效果最强 |
| 反转量比 `reversal_volume_ratio` | 1.2 | 3.2 节研究口径 |
| 高位个股处理 | 标记 + 降权（0.5 倍），不剔除 | 用户确认，先看数据再收紧 |

> 样本说明：个股本地历史仅约 470 交易日（2024-09 起），252 分位预热后个股位置 2025-09 起才有效；指数 2023-01 起。阈值存在过拟合风险，必须按第 8 节做校准/验证分离，且建议里程碑 0 扩充个股历史（见 8.3）。

## 4. 总体架构

```
数据层（新增）
  index_prices 表：7 个市场指数 + 31 个申万 L1 行业指数日线
  stocks.industry 列：stock_basic 申万行业映射
  fetch_indices 模块：全量 + 每日增量
        │
位置模块 market_analysis/positions.py（新增）
  252 日分位位置（市场/板块/行业/个股）
  状态判定：risk / bottom / neutral；反转确认
        │
元策略 strategies/resonance/（新增，注册进现有框架）
  子策略：b1 + volume_new_high + high_52w_momentum
  合并：hit_count ≥ 2；归一化分数求和
  风控：高位标记降权；低位反转切抄底池
        │
回测（现有 backtest 框架零改动）＋ API/前端展示
```

## 5. 数据层设计

### 5.1 index_prices 表

```sql
CREATE TABLE IF NOT EXISTS index_prices (
  code        TEXT NOT NULL,          -- 如 000001.SH / 801010.SI
  trade_date  TEXT NOT NULL,          -- YYYY-MM-DD
  open REAL, high REAL, low REAL, close REAL,
  vol REAL, amount REAL, pct_chg REAL,
  updated_at TEXT,
  PRIMARY KEY (code, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_index_prices_code ON index_prices(code, trade_date);
```

指数不复权，不需要 adjust 维度。抓取标的：

- 市场指数：`000001.SH` 上证指数、`399001.SZ` 深证成指、`399006.SZ` 创业板指、`000300.SH` 沪深300、`000905.SH` 中证500、`000688.SH` 科创50、`899050.BJ` 北证50；
- 行业指数：申万 L1 31 个（`801010.SI` … `801980.SI`，来自 `index_classify(level=L1, src=SW2021)`）。

### 5.2 stocks.industry

`ALTER TABLE stocks ADD COLUMN industry TEXT`，从 `stock_basic(fields=ts_code,industry)` 一次性全量写入（约 5545 行），按 `ts_code` 映射到本地 `code`。行业口径为申万 L1（当前分类），历史成分变动不可考，页面标注"当前分类"。

### 5.3 抓取模块 pipeline/fetch_indices.py

- 首次全量：2023-01-01 至今；之后增量（以 `index_prices` 内每只标的的最新 `trade_date` 为起点）；
- 复用现有限频/重试模式（约 38 只/次，成本极低）；
- `--use-cache-only` / `--force-refresh` 语义与个股抓取一致；失败重试不中断。

## 6. 位置模块设计（market_analysis/positions.py）

### 6.1 位置计算

`position(series, window=252)` = 当日收盘价在近 252 个交易日的分位（0–100）：

```
pos_t = count(close in [t-251, t] <= close_t) / 252 * 100
```

向量化实现（rolling + 排序计数或 numba 内核），供市场/板块/行业/个股统一复用。

### 6.2 分层与状态

- 市场层：7 个市场指数各自位置；
- 板块层：主板（上证指数 + 深证成指）、创业板（创业板指）、科创板（科创50）、北证（北证50）；板块位置取对应指数位置（主板取上证/深证成指的保守值，即两者中更高者用于风险判定、更低者用于抄底判定）；
- 行业层：31 个行业指数位置；
- 个股层：个股自身 252 日分位（供候选表展示与高位降权）；
- 全市场广度（已有 `breadth.py`）作为辅助展示，不参与判定。

### 6.3 状态判定

```
risk:   市场或所在板块位置 ≥ risk_high_threshold(85)
bottom: 市场或所在板块位置 ≤ bottom_low_threshold(15) 且反转确认
neutral: 其余
```

反转确认（指数层面）：`pct_chg > 0 and vol / vol_ma20 ≥ 1.2`（`vol_ma20` 为该指数 20 日均量）。

### 6.4 配置

阈值全部进 `config/rules_preselect.yaml`（`market_regime` 段），可调；无指数数据时降级为 `neutral`（仅做共振，不做位置过滤），并记录 `positions_available=False`。

## 7. 元策略 resonance 设计（strategies/resonance/）

### 7.1 配置

```yaml
strategies:
  resonance:
    enabled: true
    sub_strategies: [b1, volume_new_high, high_52w_momentum]
    min_hits: 2
    max_candidates: 30
    risk_high_threshold: 85
    bottom_low_threshold: 15
    reversal_volume_ratio: 1.2
    high_position_action: downweight   # downweight | exclude
    downweight_factor: 0.5
    bottom_fishing_enabled: true
    bottom_stock_pos_cap: 30           # 抄底池个股位置上限
```

### 7.2 选股流程（select_prepared）

1. **状态计算**：当日市场/板块/行业位置与状态（第 6 节）；无数据则跳过风控；
2. **子策略并行**：依次调用各子策略 `select_prepared`（共享同一份 prepared 行情数据与 `context.pool`），收集 `code → {sub_strategy: score}`；
3. **共振合并**：`hit_count` = 命中的子策略数；`combined_score` = 各子策略分数在各自截面内的百分位排名（0–1）求和；`hit_count ≥ min_hits` 才入选；
4. **风控过滤**：
   - risk 状态：个股自身位置 ≥ 85 的候选标记 `regime="risk"`，`combined_score × downweight_factor`（`high_position_action=exclude` 时直接剔除）；
   - bottom 状态：启用抄底池——从位置 ≤ `bottom_stock_pos_cap(30)` 且当日个股反转确认（收涨 + 量比 ≥ 1.2）的股票中补充候选，标记 `bottom_signal=True`，与共振池合并排序。注意：研究显示个股反转单独无效，抄底池只在"指数层面低位 + 反转确认"成立时激活，个股反转仅作入池筛选条件，不作为独立信号；
5. **输出**：按 `combined_score` 降序取前 `max_candidates`。

### 7.3 候选输出字段

`extra` 增加：`hit_count`、`hits`（子策略→分数明细）、`combined_score`、`market_pos`、`board_pos`、`industry_pos`、`stock_pos`、`regime`（risk/bottom/neutral）、`bottom_signal`。

### 7.4 框架接口适配

- `warmup_bars` = max(各子策略 warmup, 252) + 30；
- `indicator_config` / `cache_columns` = 各子策略的并集；
- 注册进 `strategies/registry.py`，前端/回测无需感知差异。

## 8. 回测与验证协议

### 8.1 对比组

1. 基线：b1、volume_new_high、high_52w_momentum 单独回测；
2. resonance 无风控（仅共振，`min_hits=2`）；
3. resonance 全量（共振 + 位置风控 + 抄底池）。

### 8.2 指标

沿用现有：胜率、平均/中位收益、盈亏因子、最大回撤、信号数、每日平均候选数；新增：

- 抄底信号单独统计（胜率、均值、信号数）；
- 风控触发天数占比（risk 天数 / bottom 天数 / 总信号日）；
- 高位标记降权前后对比；
- 交易成本敏感性：单边 0 vs ±0.3% 两种口径。

### 8.3 校准/验证与数据扩充（关键前置）

本地个股历史仅约 470 交易日，252 分位预热后个股位置有效样本不足。因此：

1. **里程碑 0 扩充个股历史**：将本地行情历史扩至 ≥1500 交易日（约 6 年，TUShare 个股日线支持；约 28 分钟全量重拉，复用现有 bulk 流程）；
2. 校准期：2023-01 → 2025-06；验证期：2025-07 → 2026-08（具体以数据落地后的真实区间为准，写死为可配置参数）；
3. 阈值只允许用校准期数据调整，验证期仅做一次确认，禁止看结果后反复调参；
4. 若样本不足导致无法拆分，回测报告必须标注"样本外未验证"。

## 9. API 与前端

### 9.1 API

- `GET /api/market/positions`：市场指数位置、板块位置、行业位置、状态（risk/bottom/neutral）、反转确认标记、`updated_at`；
- `GET /api/candidates/latest?strategy_id=resonance`：返回含 `hit_count`/`regime` 等新字段。

### 9.2 前端

- 新增"市场位置"面板：状态卡（风险红/抄底绿/中性灰）+ 指数位置条形图 + 行业位置排序表；
- 候选表新增列：命中次数、状态徽标、个股位置；
- 回测页可直接选 `resonance` 策略。

## 10. 测试计划

- `positions`：已知序列位置计算、反转判定、阈值边界（84/85/86、14/15/16）、无数据降级；
- `resonance`：合并逻辑（1 次命中不入选、2 次入选）、降权/剔除开关、抄底池补充、无位置数据降级；
- 回测：注册可用、参数不变量（改参数不破坏缓存键）、校准/验证配置生效。

## 11. 实施里程碑

1. **M0 数据层**：扩充个股历史 ≥1500 交易日；`index_prices` 表 + `stocks.industry`；`fetch_indices` 抓取入库；
2. **M1 位置模块**：`market_analysis/positions.py` + 单元测试；
3. **M2 元策略**：`strategies/resonance/` + 注册 + 单元测试；
4. **M3 回测验证**：三组对比 + 校准/验证 + 阈值定稿；
5. **M4 展示**：API + 前端面板 + 候选表新字段；
6. **M5 收尾**：README/任务表更新、全量回归、清理研究脚本。

## 12. 风险与假设

- 阈值基于 2023–2026 样本，存在过拟合风险：用校准/验证分离 + 参数可配置对冲；指数反转样本量小（上证 ≤15 + 反转仅 9 例），用市场指数池合并统计；
- 行业分类为当前申万 L1，历史成分变动不可考，回测标注"当前分类实验版"；
- 北证50 数据可用但现有默认股票池为 main，板块层保持可配置；
- 抄底信号与现有"次日开盘买入"假设一致，但涨停/一字板未建模（沿用现有回测假设并报告）；
- 本系统仅用于研究，不构成投资建议。

## 13. 决策记录

| 决策点 | 结论 |
| --- | --- |
| 子策略集合 | 先用现有 3 个（b1、volume_new_high、high_52w_momentum）；价格动量多因子留作第二步 |
| 高位个股处理 | 首版为标记 + 降权；2026-08-19 迭代改为：风险区剔除非动量高位候选、保留动量领先股并标记风险，候选收紧到 15（`high_position_action=exclude` + `risk_max_candidates=15`） |
| 阈值口径 | 高位 ≥85；低位 ≤15；反转 = 收涨 + 量比 ≥1.2 |
| 强趋势市处理 | 新增 `trend_dominant_in_risk=true`：风险区以 high_52w_momentum 候选为主排序，共振多命中做加成 |
