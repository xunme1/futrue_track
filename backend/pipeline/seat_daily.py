# -*- coding: utf-8 -*-
"""席位追踪每日编排：抓数 → 兼容产物 → 高盛合约 → HTML 日报。

    python -m backend.pipeline.seat_daily                  # 最近工作日
    python -m backend.pipeline.seat_daily --date 20260917  # 指定交易日

会员持仓数据每交易日收盘后约 17:30 更新，建议 18:00 之后执行（服务器 crontab 已配）。
若接口尚未更新到目标日，自动以实际最新交易日出图出文，不产生错位归档。
抓数失败退出码 1（阻断）；DeepSeek 或高盛附录失败仅警告，不阻断既有产物。
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import json
import os
import sys

import pandas as pd

from backend.pipeline.seat_core import SEAT_DIR, prev_trade_date
from backend.pipeline.report_store import atomic_json, atomic_text
from backend.pipeline.seat_fetch import FINANCIAL_SYMBOLS, fetch_all
from backend.pipeline.seat_plot import render, setup_font
from backend.pipeline.seat_report import build_detail


def last_weekday(day=None):
    d = day or datetime.now().date()
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.strftime("%Y%m%d")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", help="目标交易日 YYYYMMDD（默认最近工作日）")
    ap.add_argument("--start", help="抓数开始日期 YYYYMMDD（默认目标日向前 40 自然日）")
    ap.add_argument("--no-report", action="store_true", help="跳过 DeepSeek 席位解读；其他产物照常生成")
    ap.add_argument("--goldman-top", type=int, default=15, help="高盛净多/净空图各显示数量（默认15）")
    args = ap.parse_args(argv)

    target = args.date or last_weekday()
    start = args.start or (datetime.strptime(target, "%Y%m%d") - timedelta(days=40)).strftime("%Y%m%d")
    SEAT_DIR.mkdir(parents=True, exist_ok=True)

    # ---- 1. 抓数（缓存幂等；文件名按实际最新交易日，防止接口未更新时错位归档）----
    csv = SEAT_DIR / f"seat_data_{target}.csv"
    if csv.exists():
        df = pd.read_csv(csv, dtype={"trade_date": str})
        print(f"[1/5] 缓存命中: {csv}（{len(df)} 行）")
        source_path = SEAT_DIR / f"seat_sources_{target}.json"
        source_manifest = (json.loads(source_path.read_text(encoding="utf-8"))
                           if source_path.exists() else None)
    else:
        print(f"[1/5] 抓取 {start} ~ {target} 动态商品池会员持仓…")
        rows, source_manifest = fetch_all(start, target, return_manifest=True)
        if not rows:
            sys.exit("[错误] 未抓到任何数据")
        df = pd.DataFrame(rows)
        effective = str(df["trade_date"].astype(str).max())
        csv = SEAT_DIR / f"seat_data_{effective}.csv"
        if effective != target:
            print(f"[提示] 接口最新数据日为 {effective}（目标 {target} 尚未更新），按 {effective} 归档")
        if csv.exists():
            print(f"[提示] {csv} 已存在，沿用既有归档")
        else:
            df.to_csv(csv, index=False)
            atomic_json(SEAT_DIR / f"seat_sources_{effective}.json", source_manifest)
            print(f"[1/5] 已保存 {csv}（{len(df)} 行）")
    df["trade_date"] = df["trade_date"].astype(str)
    trade_date = str(df["trade_date"].max())
    prev = prev_trade_date(df, trade_date)
    if not prev:
        sys.exit(f"[错误] {trade_date} 是缓存首个交易日，无前一交易日可对比")

    # ---- 2. 方向图 ----
    setup_font()
    png = SEAT_DIR / f"seat_direction_{trade_date}.png"
    render(df, trade_date, prev, png)
    print(f"[2/5] 兼容方向图: {png}")

    # ---- 3. 详情 JSON + AI 解读（可缺省）----
    detail = build_detail(df, trade_date, prev)
    json_path = SEAT_DIR / f"seat_detail_{trade_date}.json"
    atomic_json(json_path, detail)
    print(f"[3/5] 兼容详情 JSON: {json_path}")

    # ---- 4. 高盛主/次合约净持仓图 + 已发布日报原子重渲染（失败不阻断） ----
    try:
        from backend.pipeline.goldman_contract import generate as generate_goldman
        from backend.pipeline.summary_render import rerender_report_for_data_date

        generate_goldman(trade_date, prev, top_n=args.goldman_top)
        data_date = datetime.strptime(trade_date, "%Y%m%d").strftime("%Y-%m-%d")
        report_path = rerender_report_for_data_date(data_date)
        if report_path:
            print(f"[4/5] 高盛主次合约附录已写入技术日报: {report_path}")
        else:
            print("[4/5] 高盛主次合约图已生成；尚无匹配技术日报")
    except Exception as exc:  # noqa: BLE001 - 附录失败不能破坏原席位日更
        print(f"[警告] 高盛主次合约附录生成失败: {exc!r}；保留既有日报和席位产物")

    # ---- 5. 自包含 HTML 日报；模型失败时使用确定性模板 ----
    try:
        from backend.pipeline.dominant_fetch import load_or_fetch
        from backend.pipeline.seat_html import (
            build_facts,
            build_signals,
            fallback_narrative,
            fetch_market_context,
            generate_narrative,
            load_goldman_contract,
            publish_report,
        )

        dominants, _ = load_or_fetch(trade_date)
        universe = [row for row in dominants
                    if str(row.get("symbol") or "").upper() not in FINANCIAL_SYMBOLS]
        market = fetch_market_context(dominants, trade_date, prev)
        source_path = SEAT_DIR / f"seat_sources_{trade_date}.json"
        if source_path.exists():
            source_manifest = json.loads(source_path.read_text(encoding="utf-8"))
        facts = build_facts(
            df, trade_date, prev, universe=universe,
            source_manifest=source_manifest, market_context=market,
            goldman_contract=load_goldman_contract(trade_date),
        )
        signals = build_signals(facts)
        api_key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("DEEPSEEK_API") or ""
        if args.no_report or not api_key:
            narrative = fallback_narrative(facts, signals, "--no-report 或未配置模型密钥")
        else:
            narrative = generate_narrative(facts, signals, api_key)
        html_path, report_json = publish_report(facts, narrative, signals)

        # 继续提供旧 Markdown 接口，但内容与新 HTML 使用同一份已验证叙事。
        notes = "\n\n".join(
            f"## {facts['groups'][key]['name']}\n\n{narrative['group_notes'][key]['text']}"
            for key in ("goldman", "major", "retail")
        )
        risks = "\n".join(f"- {value}" for value in narrative["risk_notes"])
        md_path = SEAT_DIR / f"seat_analysis_{trade_date}.md"
        atomic_text(
            md_path,
            f"# 席位分歧分析 {trade_date}\n\n{narrative['overall_summary']['text']}\n\n"
            f"{notes}\n\n## 风险提示\n\n{risks}\n",
        )
        print(f"[5/5] HTML 日报: {html_path}")
        print(f"[5/5] 审计 JSON: {report_json}")
    except Exception as exc:  # noqa: BLE001 - HTML 附加功能不破坏原日更
        print(f"[警告] 席位 HTML 日报生成失败: {exc!r}；保留既有席位产物")


if __name__ == "__main__":
    main()
