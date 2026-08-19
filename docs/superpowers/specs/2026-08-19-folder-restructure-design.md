# 文件夹结构重组设计（本地与线上保持一致）

- 日期：2026-08-19
- 状态：已确认设计，待实施
- 仓库：`wsdw1/work`（本地与线上为同一 git 仓库，重组后 push 即同步）

## 背景与问题

`quant/` 项目经过多轮迭代后，根目录同时存在入口脚本、中英文文档、重复的启动脚本别名，
命名与归类不统一，影响可维护性：

- 中英文两套启动脚本：`启动控制台.bat / 停止控制台.bat`（别名）与 `start_console.bat / stop_console.bat`，且没有"停止桌面版"。
- `pipeline/Selector.py` 大写命名，与其余 `snake_case` 模块不一致。
- 文档混放：`项目任务表.md`、`StockTradebyZ分析对比.md`、`DESIGN.md`、`PRODUCT.md` 全部在项目根。
- 仓库根 `README.md` 只有一行，无索引；`baijiushi-projects.md` 位于根目录。
- `quant_deploy_stage/` 部署产物与源码共存（已 gitignore，本次删除）。

## 目标

1. 本地与线上（GitHub 仓库）结构完全一致。
2. `quant/` 根目录只保留项目入口说明、配置模板与目录；可执行入口统一进 `scripts/`，文档统一进 `docs/`。
3. 命名统一为英文小写（`snake_case` / 英文文档名）。
4. 不改变顶层 Python 包结构、不引入 `src/` 布局、不动策略逻辑。

## 目标结构

### `quant/`

```
quant/
├── README.md                        # 项目唯一根文档
├── docs/                            # 全部文档
│   ├── design.md                    # 原 DESIGN.md
│   ├── product.md                   # 原 PRODUCT.md
│   ├── project-tasks.md             # 原 项目任务表.md
│   ├── stock-tradebyz-analysis.md   # 原 StockTradebyZ分析对比.md
│   ├── strategy-research/           # 已有
│   └── superpowers/                 # 已有（含本设计文档）
├── scripts/                         # 全部可执行入口
│   ├── run_all.py                   # 原根目录 run_all.py
│   ├── start_web.py                 # 原根目录 start_web.py
│   ├── stop_web.py                  # 原根目录 stop_web.py
│   ├── desktop_app.py               # 原根目录 desktop_app.py
│   ├── start_console.bat            # 原根目录 start_console.bat
│   ├── stop_console.bat             # 原根目录 stop_console.bat
│   ├── daily_report.py              # 已有
│   ├── run_resonance_verification.py# 已有
│   └── ...
├── config/ data/ storage/ pipeline/ strategies/
├── market_analysis/ notify/ backtest/ ai_scoring/
├── entry_analysis/ backend/ web/ tests/ skills/
├── requirements.txt / requirements-dev.txt / requirements-run.txt
├── .env.example / .gitignore
```

### 仓库根 `工作/`

```
工作/
├── README.md                        # 索引：quant 项目 + 线上 Actions 说明
├── docs/baijiushi-projects.md       # 原根目录 baijiushi-projects.md
├── .github/workflows/daily-strategy-report.yml   # 不变
├── .gitignore
└── quant/
```

## 变更明细

### 1. 入口脚本归位到 `scripts/`

- 移动：`run_all.py`、`start_web.py`、`stop_web.py`、`desktop_app.py`、`start_console.bat`、`stop_console.bat`。
- 删除中文别名：`启动控制台.bat`、`启动桌面版.bat`、`停止控制台.bat`。
- 路径联动：
  - `run_all.py`：顶部加 `sys.path` 引导（`_ROOT = Path(__file__).resolve().parent.parent`）。
  - `start_web.py` / `desktop_app.py`：`ROOT = Path(__file__).resolve().parent` 改为 `parent.parent`。
  - bat 内容更新为指向 `scripts\` 下脚本，并先 `cd` 到项目根。
- 文档与命令示例统一为 `python scripts/run_all.py ...`。

### 2. 文档归位 `docs/`

- `DESIGN.md` → `docs/design.md`
- `PRODUCT.md` → `docs/product.md`
- `项目任务表.md` → `docs/project-tasks.md`
- `StockTradebyZ分析对比.md` → `docs/stock-tradebyz-analysis.md`
- 仓库根 `baijiushi-projects.md` → `docs/baijiushi-projects.md`
- 更新 README 中的文档链接与引用。

### 3. 命名统一

- `pipeline/Selector.py` → `pipeline/selector.py`
- 同步更新引用：`strategies/b1/strategy.py`、`tests/test_backtest.py`。

### 4. 部署产物

- 删除 `quant_deploy_stage/`（已 gitignore，0.2MB 旧产物）。

### 5. 不变项

- 顶层 Python 包结构（`pipeline/`、`strategies/`、`market_analysis/`、`storage/`、`notify/`、`backtest/`、`ai_scoring/`、`entry_analysis/`、`backend/`）。
- `config/`、`data/`、`web/`、`tests/`、`skills/` 目录位置。
- `.env.example`、`.env.local`、`.gitignore` 位置。
- `.github/workflows/daily-strategy-report.yml`（`working-directory: quant` 不变，`daily_report.py` 本就在 `scripts/`）。

## 数据与配置

- `data/` 子目录分层不变：`raw/`（原始行情）、`candidates/`（选股结果）、`failures/`（失败清单）、`resonance_verification/`（回测汇总）。
- `logs/` 保留在项目根（gitignore 覆盖）。

## 验证

1. 全部 pytest 通过（`tests/`）。
2. 本地 `python scripts/run_all.py --help` 正常。
3. 手动触发一次 GitHub Actions，确认线上路径未断。

## 风险与说明

- 入口脚本移动涉及相对路径与命令示例，实施时逐项核对引用。
- 使用 `git mv` 保留历史。
- 不修改任何策略逻辑、不改变 `data/` 布局、不引入打包配置。
