# 文件夹结构重组 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 `quant/` 与仓库根 `工作/` 的目录结构按已确认设计重组：入口脚本归位 `scripts/`、文档归位 `docs/`、命名统一英文小写、删除重复脚本与部署产物，本地与线上保持一致。

**Architecture:** 保持 `quant/` 顶层 Python 包不变；只移动可执行入口与文档文件（`git mv` 保留历史），同步修正入口脚本的相对路径、`sys.path` 引导、bat 内容、README 命令与文档链接；最后全量测试并推送，触发一次线上 Actions 验证。

**Tech Stack:** Python 3.12、pytest、Git、GitHub Actions、PowerShell（bat 脚本）。

## Global Constraints

- 仓库根：`C:\Users\shunw\Documents\ChatGPT\工作`（git 仓库 `wsdw1/work`，本地与线上同一仓库）。
- 项目根：`quant/`；所有 Python 命令在本计划中用仓库根相对路径（如 `quant/scripts/run_all.py`），实际执行时 `cd` 到 `quant/`。
- 使用 `.venv` 的 Python 执行：`quant\.venv\Scripts\python.exe`（Windows）或 `python`（若已激活 venv）。
- GitHub 推送需要代理：`$env:HTTPS_PROXY='http://127.0.0.1:7897'`，并已登录 `gh`（`workflow` scope）。
- 禁止改动：策略逻辑、`data/` 目录布局、`.env*`、`web/`、`tests/` 内容、`.github/workflows/daily-strategy-report.yml`。
- 文件移动一律使用 `git mv` 以保留历史；删除的文件用 `git rm`。
- 每完成一个 Task 必须提交一次，提交信息见各 Task 末尾。

---

### Task 1: 入口脚本归位 `scripts/` 并修正路径

**Files:**
- Move: `quant/run_all.py` → `quant/scripts/run_all.py`
- Move: `quant/start_web.py` → `quant/scripts/start_web.py`
- Move: `quant/stop_web.py` → `quant/scripts/stop_web.py`
- Move: `quant/desktop_app.py` → `quant/scripts/desktop_app.py`
- Move: `quant/start_console.bat` → `quant/scripts/start_console.bat`
- Move: `quant/stop_console.bat` → `quant/scripts/stop_console.bat`
- Delete: `quant/启动控制台.bat`、`quant/启动桌面版.bat`、`quant/停止控制台.bat`
- Create: `quant/scripts/__init__.py`（空文件）
- Modify: `quant/scripts/run_all.py`、`quant/scripts/start_web.py`、`quant/scripts/desktop_app.py`、`quant/scripts/start_console.bat`、`quant/scripts/stop_console.bat`
- Test: `quant/tests/test_desktop_app.py`

**Interfaces:**
- Consumes: 无（纯文件移动 + 路径修正）
- Produces: `python quant/scripts/run_all.py --help` 可运行；`from scripts import desktop_app` 可导入；bat 双击可用

- [ ] **Step 1: 移动文件**

在 `C:\Users\shunw\Documents\ChatGPT\工作` 执行：

```powershell
git mv quant/run_all.py quant/scripts/run_all.py
git mv quant/start_web.py quant/scripts/start_web.py
git mv quant/stop_web.py quant/scripts/stop_web.py
git mv quant/desktop_app.py quant/scripts/desktop_app.py
git mv quant/start_console.bat quant/scripts/start_console.bat
git mv quant/stop_console.bat quant/scripts/stop_console.bat
git rm quant/启动控制台.bat quant/启动桌面版.bat quant/停止控制台.bat
```

- [ ] **Step 2: 新建 `quant/scripts/__init__.py`（空文件）**

- [ ] **Step 3: 修正 `quant/scripts/run_all.py` 的导入引导**

把文件顶部的 `ROOT = Path(__file__).resolve().parent` 改为：

```python
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
```

并确保该行位于 `from pipeline.runtime import run_pipeline` 之前（`sys` 已导入；如顺序不对，将 import 移到 ROOT 之后）。

- [ ] **Step 4: 修正 `quant/scripts/start_web.py` 的根路径**

将 `ROOT = Path(__file__).resolve().parent` 改为 `ROOT = Path(__file__).resolve().parent.parent`（该文件内 `WEB_DIR`、`LOG_DIR` 均基于 `ROOT`，自动跟随）。

- [ ] **Step 5: 修正 `quant/scripts/desktop_app.py`**

同 Step 4 修改 `ROOT`；并将文件头部 docstring 中的命令示例从 `python desktop_app.py` 改为 `python scripts/desktop_app.py`。

- [ ] **Step 6: 修正两个 bat 的路径**

`quant/scripts/start_console.bat` 内容改为：

```bat
@echo off
setlocal
cd /d "%~dp0.."
python "%~dp0start_web.py" --prod
if errorlevel 1 (
  echo.
  echo Start failed. Check logs\backend.log.
)
pause
```

`quant/scripts/stop_console.bat` 内容改为：

```bat
@echo off
setlocal
cd /d "%~dp0.."
python "%~dp0stop_web.py"
pause
```

- [ ] **Step 7: 更新桌面版测试的导入**

`quant/tests/test_desktop_app.py` 中 `import desktop_app` 改为 `from scripts import desktop_app`。

- [ ] **Step 8: 验证**

```powershell
cd C:\Users\shunw\Documents\ChatGPT\工作\quant
.\.venv\Scripts\python.exe -m pytest tests/test_desktop_app.py -q
.\.venv\Scripts\python.exe scripts/run_all.py --help
```

预期：测试 PASS；`--help` 正常输出参数说明且无 ImportError。

- [ ] **Step 9: 提交**

```bash
git add -A quant/
git commit -m "refactor: 入口脚本统一归位到 scripts/，删除中文别名"
```

---

### Task 2: `pipeline/Selector.py` 重命名为 `selector.py`

**Files:**
- Move: `quant/pipeline/Selector.py` → `quant/pipeline/selector.py`
- Modify: `quant/strategies/b1/strategy.py`、`quant/tests/test_backtest.py`
- Test: `quant/tests/test_backtest.py`、`quant/tests/test_resonance.py`

**Interfaces:**
- Produces: `from pipeline.selector import B1Selector, compute_weekly_ma`（类名与函数名不变，仅模块名变小写）

- [ ] **Step 1: 重命名并更新引用**

```powershell
git mv quant/pipeline/Selector.py quant/pipeline/selector.py
```

`quant/strategies/b1/strategy.py` 中 `from pipeline.Selector import (` 改为 `from pipeline.selector import (`。

`quant/tests/test_backtest.py` 中 `from pipeline.Selector import compute_weekly_ma` 改为 `from pipeline.selector import compute_weekly_ma`。

- [ ] **Step 2: 验证**

```powershell
cd C:\Users\shunw\Documents\ChatGPT\工作\quant
.\.venv\Scripts\python.exe -m pytest tests/test_backtest.py tests/test_resonance.py -q
```

预期：全部 PASS。

- [ ] **Step 3: 提交**

```bash
git add -A quant/
git commit -m "refactor: pipeline/Selector.py 重命名为 selector.py"
```

---

### Task 3: 文档归位 `docs/`

**Files:**
- Move: `quant/DESIGN.md` → `quant/docs/design.md`
- Move: `quant/PRODUCT.md` → `quant/docs/product.md`
- Move: `quant/项目任务表.md` → `quant/docs/project-tasks.md`
- Move: `quant/StockTradebyZ分析对比.md` → `quant/docs/stock-tradebyz-analysis.md`
- Move: `工作/baijiushi-projects.md` → `工作/docs/baijiushi-projects.md`

**Interfaces:**
- Produces: `docs/` 下新文档路径；README 后续 Task 引用这些路径

- [ ] **Step 1: 移动文档（在 `C:\Users\shunw\Documents\ChatGPT\工作` 执行）**

```powershell
git mv quant/DESIGN.md quant/docs/design.md
git mv quant/PRODUCT.md quant/docs/product.md
git mv quant/项目任务表.md quant/docs/project-tasks.md
git mv quant/StockTradebyZ分析对比.md quant/docs/stock-tradebyz-analysis.md
git mv baijiushi-projects.md docs/baijiushi-projects.md
```

- [ ] **Step 2: 提交**

```bash
git add -A
git commit -m "docs: 项目文档统一归位到 docs/"
```

---

### Task 4: 仓库根索引与命令更新

**Files:**
- Modify: `工作/README.md`、`quant/README.md`
- Delete: `工作/quant_deploy_stage/`（未跟踪，直接删除）

**Interfaces:**
- Consumes: Task 3 的 `docs/baijiushi-projects.md` 新路径
- Produces: 新命令用法 `python scripts/run_all.py ...`、`scripts\start_console.bat`

- [ ] **Step 1: 重写 `工作/README.md`**

```markdown
# work

个人工作项目私有仓库。

## 项目

- [quant](./quant/)：A股量化选股策略系统，详见 [quant/README.md](./quant/README.md)
- 项目清单：[docs/baijiushi-projects.md](./docs/baijiushi-projects.md)

## 线上自动化

- GitHub Actions 每日策略与市场报告（工作日北京时间 16:30）：[.github/workflows/daily-strategy-report.yml](./.github/workflows/daily-strategy-report.yml)
```

- [ ] **Step 2: 更新 `quant/README.md` 的命令与路径**

逐处替换（保持其余内容不变）：

| 原文 | 改为 |
|---|---|
| `python run_all.py` | `python scripts/run_all.py` |
| `start_console.bat` | `scripts\start_console.bat` |
| `stop_console.bat` | `scripts\stop_console.bat` |
| `python desktop_app.py` | `python scripts/desktop_app.py` |
| `启动桌面版.bat` | `scripts\desktop_app.py` |

同时把第 7 行 `- \`run_all.py\` 保留为命令行入口。` 改为 `- \`scripts/run_all.py\` 保留为命令行入口。`。

- [ ] **Step 3: 删除部署产物目录**

写一个临时 PowerShell 脚本（或直接在 PowerShell 执行，若被策略拦截则用 `pwsh -File`）：

```powershell
if (Test-Path -LiteralPath 'C:\Users\shunw\Documents\ChatGPT\工作\quant_deploy_stage') {
  Remove-Item -LiteralPath 'C:\Users\shunw\Documents\ChatGPT\工作\quant_deploy_stage' -Recurse -Force
}
```

删除后确认 `Test-Path` 为 `False`。

- [ ] **Step 4: 提交**

```bash
git add -A
git commit -m "chore: 仓库根 README 索引，更新命令路径，删除部署产物"
```

---

### Task 5: 全量验证、推送与线上触发

**Files:**
- 无新增/修改；验证与部署

**Interfaces:**
- Consumes: Task 1–4 的全部改动

- [ ] **Step 1: 全量测试**

```powershell
cd C:\Users\shunw\Documents\ChatGPT\工作\quant
.\.venv\Scripts\python.exe -m pytest tests -q
```

预期：全部 PASS（约 81 个）。

- [ ] **Step 2: 入口与每日脚本冒烟**

```powershell
.\.venv\Scripts\python.exe scripts/run_all.py --help
.\.venv\Scripts\python.exe scripts/daily_report.py --data-mode existing --skip-trade-check
```

预期：`--help` 正常；每日脚本完成选股并发邮件（会真实发一封邮件，属预期验证行为）。

- [ ] **Step 3: 推送**

```powershell
$env:HTTPS_PROXY='http://127.0.0.1:7897'
$env:HTTP_PROXY='http://127.0.0.1:7897'
git push origin main
```

预期：推送成功，`origin/main` 与本地 HEAD 一致。

- [ ] **Step 4: 触发线上运行**

```powershell
gh workflow run daily-strategy-report --repo wsdw1/work
Start-Sleep -Seconds 10
gh run list --workflow daily-strategy-report --repo wsdw1/work -L 1
```

预期：新 run `in_progress`；约 5–10 分钟后 `completed success`（日志中 `fetch_indices` 先于选股执行，邮件发送成功）。

- [ ] **Step 5: 线上日志确认**

```powershell
gh run view <run_id> --repo wsdw1/work --log | Select-String '选股完成|fetch_indices done|邮件发送成功'
```

预期：四个策略与指数、邮件全部正常，路径未因重组而断。
