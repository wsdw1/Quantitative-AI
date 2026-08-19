"""
scripts/daily_report.py
~~~~~~~~~~~~~~~~~~~~~~~
每日线上自动选股 + 邮件汇报入口（供 GitHub Actions 定时任务调用）。

流程：
  1. 读取 SMTP / Tushare 配置（优先环境变量，其次 .env.local）
  2. 用 Tushare 交易日历判断今天是否 A 股交易日，非交易日直接退出（不发邮件）
  3. 增量更新行情 -> 运行默认选股策略 -> 把候选结果发送到邮箱
  4. 任何一步失败：发送错误通知邮件并返回非 0 退出码，便于 Actions 告警

用法：
    python scripts/daily_report.py                        # 线上默认（增量 + 发邮件）
    python scripts/daily_report.py --data-mode existing   # 本地调试：直接用现有数据
    python scripts/daily_report.py --skip-trade-check     # 跳过交易日判断
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from pipeline.runtime import run_pipeline  # noqa: E402

logger = logging.getLogger(__name__)


def _is_trading_day() -> bool:
    """用 Tushare 交易日历判断今天是否开市。接口异常时按开市处理，不阻塞主流程。"""
    from data.data_fetcher import _load_local_env_file  # noqa: PLC0415

    _load_local_env_file()
    import os

    import tushare as ts

    token = os.environ.get("TUSHARE_TOKEN", "").strip()
    if not token:
        logger.warning("未检测到 TUSHARE_TOKEN，跳过交易日判断")
        return True
    try:
        pro = ts.pro_api(token)
        today = datetime.now().strftime("%Y%m%d")
        cal = pro.trade_cal(exchange="", start_date=today, end_date=today, is_open="1")
        return cal is not None and not cal.empty
    except Exception as exc:  # noqa: BLE001
        logger.warning("交易日历接口调用失败，按开市处理：%s", exc)
        return True


def _send_error_email(subject: str, detail: str) -> None:
    from notify.mailer import send_email  # noqa: PLC0415

    try:
        send_email(subject, body_text=detail)
    except Exception as exc:  # noqa: BLE001
        logger.exception("发送错误通知邮件失败：%s", exc)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python scripts/daily_report.py", description="每日选股 + 邮件汇报")
    parser.add_argument(
        "--data-mode",
        choices=["existing", "incremental", "refresh", "cache-only"],
        default="incremental",
        help="数据模式（默认 incremental：增量更新后再选股）",
    )
    parser.add_argument(
        "--mail-to", default=None,
        help="邮件收件人（默认取 .env.local / 环境变量 MAIL_TO）",
    )
    parser.add_argument(
        "--skip-trade-check", action="store_true",
        help="跳过交易日历判断（调试用）",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )
    today = datetime.now().strftime("%Y-%m-%d")

    # 先校验 SMTP 配置，配置缺失时尽早失败并给出明确提示。
    from notify.mailer import smtp_config, send_candidates_report  # noqa: PLC0415

    try:
        smtp_config()
    except Exception as exc:  # noqa: BLE001
        logger.error("SMTP 配置缺失：%s", exc)
        return 1

    if not args.skip_trade_check and not _is_trading_day():
        logger.info("今天不是 A 股交易日，跳过运行（%s）", today)
        return 0

    logger.info("开始每日选股任务（data_mode=%s）", args.data_mode)
    try:
        run_pipeline(data_mode=args.data_mode, no_dashboard=True)
    except Exception as exc:  # noqa: BLE001
        logger.exception("每日选股流程执行失败")
        _send_error_email(
            f"【quant】每日任务失败 {today}",
            f"选股流程执行失败：\n{exc}\n\n详情见日志。",
        )
        return 1

    try:
        to = send_candidates_report(to=args.mail_to)
        logger.info("选股结果邮件已发送到 %s", to)
        print(f"[OK] 选股结果已发送到 {to}")
    except Exception as exc:  # noqa: BLE001
        logger.exception("发送选股结果邮件失败")
        _send_error_email(
            f"【quant】邮件发送失败 {today}",
            f"选股已跑完，但结果邮件发送失败：\n{exc}",
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
