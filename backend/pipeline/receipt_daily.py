# -*- coding: utf-8 -*-
"""仓单研究监控每日编排：米筐仓单抓取 → 自包含 HTML 报告。

    python -m backend.pipeline.receipt_daily                  # 最近工作日
    python -m backend.pipeline.receipt_daily --date 20261009  # 指定交易日

交易所仓单日报约每交易日傍晚更新，建议 18:20 之后执行（服务器 crontab 已配）。
接口尚未更新到目标日时按实际最新交易日归档，不产生错位文件。
抓数失败退出码 1（阻断）；HTML 渲染失败仅警告，不破坏已归档 history。
"""
from __future__ import annotations

import argparse
import sys

from backend.pipeline.receipt_fetch import last_weekday, run as fetch_run


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", help="目标交易日 YYYYMMDD（默认最近工作日）")
    ap.add_argument("--backfill", action="store_true", help="全量回填后出报告（首次部署用）")
    args = ap.parse_args(argv)

    target = args.date or last_weekday()
    print(f"[1/2] 抓取仓单数据（目标日 {target}）…")
    effective, _ = fetch_run(date=target, backfill=args.backfill)
    if not effective:
        sys.exit("[错误] 未抓到任何仓单数据")
    if effective != target:
        print(f"[提示] 接口最新数据日为 {effective}（目标 {target} 尚未更新），按 {effective} 归档")

    print("[2/2] 生成 HTML 报告…")
    try:
        from backend.pipeline.receipt_html import build_facts, publish_report
        facts = build_facts(as_of=effective)
        if not facts["symbols"]:
            sys.exit("[错误] history 为空，无法出报告")
        html_path, json_path = publish_report(facts)
        print(f"[2/2] HTML 日报: {html_path}")
        print(f"[2/2] 审计 JSON: {json_path}")
    except Exception as exc:  # noqa: BLE001 - 渲染失败不破坏 history 归档
        print(f"[警告] 仓单 HTML 报告生成失败: {exc!r}；history 数据不受影响")


if __name__ == "__main__":
    main()
