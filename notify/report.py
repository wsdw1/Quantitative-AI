"""
notify/report.py
~~~~~~~~~~~~~~~~
每日综合报告（市场环境 + 各策略选股结果）的纯文本 / HTML 构建器。

输入数据均为字典（候选结果用 CandidateRun.to_dict() 的结构），
不直接依赖文件或数据库，便于测试与复用。
"""
from __future__ import annotations

import html
from datetime import datetime
from typing import Any

STRATEGY_ORDER = [
    ("resonance", "多策略共振"),
    ("b1", "B1 量化初选"),
    ("volume_new_high", "缩量新高"),
    ("high_52w_momentum", "52周新高动量"),
]

INDEX_NAMES = {
    "000001.SH": "上证指数",
    "399001.SZ": "深证成指",
    "399006.SZ": "创业板指",
    "000300.SH": "沪深300",
    "000905.SH": "中证500",
    "000688.SH": "科创50",
    "899050.BJ": "北证50",
}

REGIME_LABELS = {
    "risk": "风险区（高位，注意回撤风险）",
    "bottom": "底部区（低位反转确认）",
    "neutral": "中性区",
}


def _fmt_score(value: Any) -> str:
    try:
        return f"{float(value):.4f}"
    except (TypeError, ValueError):
        return "-"


def _fmt_close(value: Any) -> str:
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "-"


def _sorted_candidates(run: dict) -> list[dict]:
    items = list(run.get("candidates") or [])
    return sorted(items, key=lambda c: float(c.get("score", 0) or 0), reverse=True)


def _regime_label(market: dict | None) -> str:
    if not market or not market.get("available"):
        return "暂无数据"
    return REGIME_LABELS.get(str(market.get("regime", "neutral")), "中性区")


def _market_text(market: dict | None) -> list[str]:
    lines: list[str] = []
    if not market or not market.get("available"):
        return ["指数位置：暂无数据"]
    for item in market.get("market") or []:
        code = str(item.get("code", ""))
        name = INDEX_NAMES.get(code, code)
        close = _fmt_close(item.get("close"))
        position = item.get("position")
        reversal = " 反转确认" if item.get("reversal") else ""
        lines.append(f"  {code} {name}  {close}  {position}分位{reversal}")
    return lines or ["指数位置：暂无数据"]


def _breadth_text(breadth: dict | None) -> list[str]:
    if not breadth or not breadth.get("available"):
        return ["市场宽度：暂无数据"]
    status = breadth.get("status", "-")
    score = breadth.get("score")
    guidance = breadth.get("position_guidance", "-")
    score_txt = f"{float(score):.1f}" if score is not None else "-"
    lines = [f"市场宽度：{status}（得分 {score_txt}），{guidance}"]
    for comp in breadth.get("components") or []:
        lines.append(f"  {comp.get('name', '')}: {comp.get('value_pct', '-')}%（{comp.get('signal', '')}）")
    return lines


def _strategy_text(strategy_id: str, display_name: str, run: dict | None, top: int) -> list[str]:
    if run is None:
        return [f"{display_name}（{strategy_id}）：无数据"]
    meta = run.get("meta") or {}
    scanned = meta.get("scanned", "-")
    sorted_candidates = _sorted_candidates(run)
    lines = [f"{display_name}（{strategy_id}）：扫描 {scanned} 只，命中 {len(sorted_candidates)} 只"]
    if not sorted_candidates:
        lines.append("  无符合条件")
        return lines
    for i, c in enumerate(sorted_candidates[:top], 1):
        lines.append(
            f"  {i:>2} {c.get('code', ''):>8} {str(c.get('name', c.get('code', ''))):<8} "
            f"{_fmt_close(c.get('close')):>8}  {_fmt_score(c.get('score'))}"
        )
    if len(sorted_candidates) > top:
        lines.append(f"  （仅显示前 {top} 只，共 {len(sorted_candidates)} 只）")
    return lines


def build_text_report(
    candidates: dict[str, dict],
    market: dict | None = None,
    breadth: dict | None = None,
    top: int = 10,
    title_date: str | None = None,
) -> str:
    date = title_date or datetime.now().strftime("%Y-%m-%d")
    lines = [f"每日策略与市场报告 {date}", "=" * 40, "", "[市场环境]"]
    lines.append(f"市场状态：{_regime_label(market)}")
    lines += _market_text(market)
    lines += _breadth_text(breadth)
    lines += ["", "[策略选股结果]"]
    for strategy_id, display_name in STRATEGY_ORDER:
        lines += _strategy_text(strategy_id, display_name, candidates.get(strategy_id), top)
    lines += ["", "本报告由 quant 项目自动生成，仅供研究参考，不构成投资建议。"]
    return "\n".join(lines)


def _strategy_html(strategy_id: str, display_name: str, run: dict | None, top: int) -> str:
    if run is None:
        return f'<h3 id="strategy-{strategy_id}">{html.escape(display_name)}</h3><p>无数据</p>'
    meta = run.get("meta") or {}
    scanned = meta.get("scanned", "-")
    sorted_candidates = _sorted_candidates(run)
    rows = []
    for i, c in enumerate(sorted_candidates[:top], 1):
        rows.append(
            "<tr>"
            f"<td>{i}</td><td>{html.escape(str(c.get('code', '')))}</td>"
            f"<td>{html.escape(str(c.get('name', c.get('code', ''))))}</td>"
            f"<td>{_fmt_close(c.get('close'))}</td><td>{_fmt_score(c.get('score'))}</td>"
            "</tr>"
        )
    extra = ""
    if len(sorted_candidates) > top:
        extra = f'<p class="muted">仅显示前 {top} 只（共 {len(sorted_candidates)} 只）</p>'
    if not rows:
        rows.append('<tr><td colspan="5" class="muted">无符合条件</td></tr>')
    return (
        f'<h3 id="strategy-{strategy_id}">{html.escape(display_name)}</h3>'
        f'<p>扫描 {html.escape(str(scanned))} 只，命中 {len(sorted_candidates)} 只</p>'
        '<table>'
        "<tr><th>#</th><th>代码</th><th>名称</th><th>收盘</th><th>评分</th></tr>"
        + "".join(rows)
        + "</table>"
        + extra
    )


def _market_html(market: dict | None) -> str:
    if not market or not market.get("available"):
        return '<p class="muted">指数位置：暂无数据</p>'
    rows = []
    for item in market.get("market") or []:
        code = str(item.get("code", ""))
        name = INDEX_NAMES.get(code, code)
        reversal = "是" if item.get("reversal") else "否"
        rows.append(
            "<tr>"
            f"<td>{html.escape(code)}</td><td>{html.escape(name)}</td>"
            f"<td>{_fmt_close(item.get('close'))}</td>"
            f"<td>{item.get('position', '-')}</td><td>{reversal}</td>"
            "</tr>"
        )
    return (
        '<table><tr><th>指数</th><th>名称</th><th>最新点位</th><th>252日位置分位</th><th>反转确认</th></tr>'
        + "".join(rows)
        + "</table>"
    )


def _breadth_html(breadth: dict | None) -> str:
    if not breadth or not breadth.get("available"):
        return '<p class="muted">市场宽度：暂无数据</p>'
    score = breadth.get("score")
    score_txt = f"{float(score):.1f}" if score is not None else "-"
    rows = []
    for comp in breadth.get("components") or []:
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(comp.get('name', '')))}</td>"
            f"<td>{comp.get('value_pct', '-')}%</td>"
            f"<td>{html.escape(str(comp.get('signal', '')))}</td>"
            "</tr>"
        )
    components = (
        '<table><tr><th>指标</th><th>数值</th><th>信号</th></tr>' + "".join(rows) + "</table>"
        if rows
        else ""
    )
    return (
        f"<p>市场宽度得分 {html.escape(score_txt)}，当前{html.escape(str(breadth.get('status', '')))}，"
        f"风险等级{html.escape(str(breadth.get('risk_level', '')))}。</p>"
        f"<p><strong>{html.escape(str(breadth.get('position_guidance', '')))}</strong></p>"
        + components
    )


def build_html_report(
    candidates: dict[str, dict],
    market: dict | None = None,
    breadth: dict | None = None,
    top: int = 10,
    title_date: str | None = None,
) -> str:
    date = title_date or datetime.now().strftime("%Y-%m-%d")
    strategy_sections = "".join(
        _strategy_html(strategy_id, display_name, candidates.get(strategy_id), top)
        for strategy_id, display_name in STRATEGY_ORDER
    )
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>每日策略与市场报告 {html.escape(date)}</title>
<style>
body {{ font-family: -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif; max-width: 860px;
       margin: 24px auto; padding: 0 16px; color: #1f2328; line-height: 1.6; }}
h1 {{ font-size: 22px; border-bottom: 2px solid #d0d7de; padding-bottom: 8px; }}
h2 {{ font-size: 18px; margin-top: 28px; color: #0969da; }}
h3 {{ font-size: 15px; margin-bottom: 4px; }}
table {{ border-collapse: collapse; width: 100%; margin: 8px 0 12px; font-size: 13px; }}
th, td {{ border: 1px solid #d0d7de; padding: 5px 8px; text-align: left; }}
th {{ background: #f6f8fa; }}
.muted {{ color: #57606a; }}
.tag {{ display: inline-block; padding: 1px 8px; border-radius: 10px; font-size: 12px;
       background: #fff8c5; border: 1px solid #d4a72c; }}
footer {{ margin-top: 32px; padding-top: 8px; border-top: 1px solid #d0d7de;
         color: #57606a; font-size: 12px; }}
</style>
</head>
<body>
<h1>每日策略与市场报告 {html.escape(date)}</h1>

<h2>一、市场环境</h2>
<p>市场状态：<span class="tag">{html.escape(_regime_label(market))}</span></p>
{_market_html(market)}
{_breadth_html(breadth)}

<h2>二、策略选股结果</h2>
{strategy_sections}

<footer>
生成时间：{datetime.now():%Y-%m-%d %H:%M:%S}　|　本报告由 quant 项目自动生成，仅供研究参考，不构成投资建议。
</footer>
</body>
</html>"""


def build_daily_report(
    candidates: dict[str, dict],
    market: dict | None = None,
    breadth: dict | None = None,
    top: int = 10,
    title_date: str | None = None,
) -> tuple[str, str]:
    """返回 (纯文本, HTML) 两份内容的每日综合报告。"""
    return (
        build_text_report(candidates, market=market, breadth=breadth, top=top, title_date=title_date),
        build_html_report(candidates, market=market, breadth=breadth, top=top, title_date=title_date),
    )
