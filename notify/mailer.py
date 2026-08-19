"""
notify/mailer.py
~~~~~~~~~~~~~~~~
基于 QQ 邮箱 SMTP 的邮件发送模块。

配置项（项目根目录 .env.local，已加入 .gitignore）：
    SMTP_HOST=smtp.qq.com
    SMTP_PORT=465
    SMTP_USER=你的QQ邮箱
    SMTP_AUTH_CODE=你的SMTP授权码
    MAIL_TO=收件人（不填则默认发给自己）

用法：
    python -m notify.mailer test                  # 发送一封测试邮件
    python -m notify.mailer candidates [--top N]  # 发送最新选股结果
    python -m notify.mailer verification          # 发送共振策略回测汇总
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import smtplib
import ssl
import sys
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env.local"
CANDIDATES_FILE = ROOT / "data" / "candidates" / "candidates_latest.json"
VERIFICATION_SUMMARY = ROOT / "data" / "resonance_verification" / "summary.csv"

_DEFAULT_HOST = "smtp.qq.com"
_DEFAULT_PORT = 465
_PHASE_LABELS = {
    "calibration-1": "校准组 1（弱市）",
    "calibration-2": "校准组 2",
    "validation": "验证组（强趋势）",
}


def _load_env(env_file: Path = ENV_FILE) -> None:
    """从项目根目录 .env.local 读取本地密钥，已有环境变量优先。"""
    if not env_file.exists():
        return
    try:
        for raw_line in env_file.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and value and key not in os.environ:
                os.environ[key] = value
    except Exception as exc:  # pragma: no cover
        logger.warning("读取 %s 失败: %s", env_file, exc)


def smtp_config() -> dict:
    """读取并校验 SMTP 配置，不打印任何密钥内容。"""
    _load_env()
    host = os.environ.get("SMTP_HOST", "").strip() or _DEFAULT_HOST
    try:
        port = int(os.environ.get("SMTP_PORT", _DEFAULT_PORT))
    except (TypeError, ValueError):
        port = _DEFAULT_PORT
    user = os.environ.get("SMTP_USER", "").strip()
    auth_code = (os.environ.get("SMTP_AUTH_CODE") or os.environ.get("SMTP_PASSWORD", "")).strip()
    to = os.environ.get("MAIL_TO", "").strip() or user
    if not user or not auth_code:
        raise RuntimeError("缺少 SMTP 配置：请在 .env.local 中填写 SMTP_USER 和 SMTP_AUTH_CODE。")
    if not to:
        raise RuntimeError("缺少收件人：请在 .env.local 中填写 MAIL_TO。")
    return {"host": host, "port": port, "user": user, "auth_code": auth_code, "to": to}


def send_email(
    subject: str,
    body_text: str | None = None,
    body_html: str | None = None,
    to: str | None = None,
) -> str:
    """通过 QQ 邮箱 SMTP 发送一封邮件，返回收件人地址。"""
    cfg = smtp_config()
    recipients = (to or cfg["to"]).strip()

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg["user"]
    msg["To"] = recipients
    if body_text:
        msg.set_content(body_text)
    if body_html:
        msg.add_alternative(body_html, subtype="html")

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(cfg["host"], cfg["port"], context=context, timeout=30) as server:
        server.login(cfg["user"], cfg["auth_code"])
        server.send_message(msg)

    logger.info("邮件发送成功 subject=%s to=%s", subject, recipients)
    return recipients


def _load_latest_candidates() -> dict:
    if not CANDIDATES_FILE.exists():
        raise FileNotFoundError(f"找不到候选结果文件：{CANDIDATES_FILE}")
    return json.loads(CANDIDATES_FILE.read_text(encoding="utf-8"))


def build_candidates_report(top: int = 20) -> tuple[str, str]:
    """把最新选股结果格式化成纯文本 + HTML 两种内容。"""
    data = _load_latest_candidates()
    meta = data.get("meta", {})
    pick_date = data.get("pick_date", "未知")
    run_date = data.get("run_date", pick_date)
    candidates = data.get("candidates", [])
    strategy = meta.get("strategy", "")
    strategy_name = meta.get("strategy_name", "")
    scanned = meta.get("scanned", "-")
    selected = meta.get("selected", len(candidates))

    head_lines = [
        f"选股日期：{pick_date}（任务运行于 {run_date}）",
        f"策略：{strategy_name or strategy} ({strategy})",
        f"扫描数量：{scanned} 只，命中数量：{selected} 只",
        "",
    ]
    header = f"{'#':>3}  {'代码':>8}  {'名称':<8}  {'收盘':>8}  {'评分':>10}"
    text_rows = [header, "-" * len(header)]
    html_rows = []
    for i, c in enumerate(candidates[:top], 1):
        code = str(c.get("code", ""))
        name = str(c.get("name", code))
        close = float(c.get("close", 0) or 0)
        score = float(c.get("score", 0) or 0)
        text_rows.append(
            f"{i:>3}  {code:>8}  {name[:8]:<8}  {close:>8.2f}  {score:>10.4f}"
        )
        html_rows.append(
            f"<tr><td>{i}</td><td>{code}</td><td>{name}</td>"
            f"<td>{close:.2f}</td><td>{score:.4f}</td></tr>"
        )
    if len(candidates) > top:
        text_rows.append(f"... 共 {len(candidates)} 只，仅显示前 {top} 只")
        html_rows.append(f'<tr><td colspan="5">… 共 {len(candidates)} 只，仅显示前 {top} 只</td></tr>')

    text = "\n".join(head_lines + text_rows)
    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>
<h3>量化选股结果</h3>
<p>选股日期：{pick_date}（任务运行于 {run_date}）</p>
<p>策略：{strategy_name or strategy} ({strategy})</p>
<p>扫描数量：{scanned} 只，命中数量：{selected} 只</p>
<table border="1" cellspacing="0" cellpadding="4" style="border-collapse:collapse">
<tr><th>#</th><th>代码</th><th>名称</th><th>收盘</th><th>评分</th></tr>
{''.join(html_rows)}
</table></body></html>"""
    return text, html


def build_verification_report() -> tuple[str, str]:
    """把共振策略回测汇总（summary.csv）格式化成纯文本 + HTML。"""
    if not VERIFICATION_SUMMARY.exists():
        raise FileNotFoundError(f"找不到回测汇总文件：{VERIFICATION_SUMMARY}")
    with VERIFICATION_SUMMARY.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise ValueError("回测汇总文件为空。")

    phases: dict[str, list[dict]] = {}
    for row in rows:
        phases.setdefault(row.get("phase", ""), []).append(row)

    text_parts = ["共振策略回测汇总", "=" * 60]
    html_parts = ['<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>',
                  "<h3>共振策略回测汇总</h3>"]
    for phase, items in phases.items():
        label = _PHASE_LABELS.get(phase, phase)
        text_parts += [f"\n[{label}]", f"{'策略':<24}{'信号数':>8}{'胜率%':>9}{'均收益%':>10}{'盈亏因子':>10}"]
        html_parts.append(f"<h4>{label}</h4><table border=\"1\" cellspacing=\"0\" cellpadding=\"4\" "
                          "style=\"border-collapse:collapse\"><tr><th>策略</th><th>信号数</th>"
                          "<th>胜率%</th><th>均收益%</th><th>盈亏因子</th></tr>")
        for item in items:
            name = item.get("label", item.get("strategy_id", "-"))
            win = item.get("win_rate_pct", "-")
            avg = item.get("average_return_pct", "-")
            pf = item.get("profit_factor", "-")
            n = item.get("signal_count", "-")
            text_parts.append(f"{name:<24}{n:>8}{str(win):>9}{str(avg):>10}{str(pf):>10}")
            html_parts.append(f"<tr><td>{name}</td><td>{n}</td><td>{win}</td><td>{avg}</td><td>{pf}</td></tr>")
        html_parts.append("</table>")
    html_parts.append("</body></html>")
    return "\n".join(text_parts), "".join(html_parts)


def send_candidates_report(top: int = 20, to: str | None = None) -> str:
    text, html = build_candidates_report(top=top)
    subject = f"【quant】选股结果 {datetime.now():%Y-%m-%d}"
    return send_email(subject, body_text=text, body_html=html, to=to)


def send_verification_report(to: str | None = None) -> str:
    text, html = build_verification_report()
    subject = f"【quant】共振策略回测汇总 {datetime.now():%Y-%m-%d}"
    return send_email(subject, body_text=text, body_html=html, to=to)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m notify.mailer", description="QQ 邮箱 SMTP 发送")
    sub = parser.add_subparsers(dest="command", required=True)

    p_test = sub.add_parser("test", help="发送一封测试邮件")
    p_test.add_argument("--to", default=None, help="收件人（默认 .env.local 的 MAIL_TO）")

    p_cand = sub.add_parser("candidates", help="发送最新选股结果")
    p_cand.add_argument("--top", type=int, default=20, help="显示前 N 只（默认 20）")
    p_cand.add_argument("--to", default=None)

    p_ver = sub.add_parser("verification", help="发送共振策略回测汇总")
    p_ver.add_argument("--to", default=None)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

    if args.command == "test":
        now = datetime.now()
        text = (
            "QQ 邮箱 SMTP 接入测试成功。\n\n"
            f"发送时间：{now:%Y-%m-%d %H:%M:%S}\n"
            "此邮件由 quant 项目的 notify/mailer.py 发出。"
        )
        html = f"<h3>QQ 邮箱 SMTP 接入测试成功</h3><p>发送时间：{now:%Y-%m-%d %H:%M:%S}</p>" \
               "<p>此邮件由 quant 项目的 notify/mailer.py 发出。</p>"
        send_email("【quant】SMTP 接入测试", text, html, to=args.to)
        print("测试邮件发送成功。")
        return 0

    if args.command == "candidates":
        send_candidates_report(top=args.top, to=args.to)
        print("选股结果邮件发送成功。")
        return 0

    if args.command == "verification":
        send_verification_report(to=args.to)
        print("回测汇总邮件发送成功。")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
