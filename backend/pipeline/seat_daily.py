# -*- coding: utf-8 -*-
"""席位追踪每日编排：抓数 → 方向图 → 详情/解读 → 高盛主次合约附录。

    python -m backend.pipeline.seat_daily                  # 最近工作日
    python -m backend.pipeline.seat_daily --date 20260917  # 指定交易日

会员持仓数据每交易日收盘后约 17:30 更新，建议 18:00 之后执行（服务器 crontab 已配）。
若接口尚未更新到目标日，自动以实际最新交易日出图出文，不产生错位归档。
抓数失败退出码 1（阻断）；DeepSeek 或高盛附录失败仅警告，不阻断既有产物。
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import os
import sys

import pandas as pd

from backend.pipeline.seat_core import SEAT_DIR, prev_trade_date
from backend.pipeline.seat_fetch import fetch_all
from backend.pipeline.seat_plot import render, setup_font
from backend.pipeline.seat_report import build_detail, generate_analysis

import json


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
        print(f"[1/4] 缓存命中: {csv}（{len(df)} 行）")
    else:
        print(f"[1/4] 抓取 {start} ~ {target} 会员持仓（约 {40 * 1.2:.0f}s）…")
        rows = fetch_all(start, target)
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
            print(f"[1/4] 已保存 {csv}（{len(df)} 行）")
    df["trade_date"] = df["trade_date"].astype(str)
    trade_date = str(df["trade_date"].max())
    prev = prev_trade_date(df, trade_date)
    if not prev:
        sys.exit(f"[错误] {trade_date} 是缓存首个交易日，无前一交易日可对比")

    # ---- 2. 方向图 ----
    setup_font()
    png = SEAT_DIR / f"seat_direction_{trade_date}.png"
    render(df, trade_date, prev, png)
    print(f"[2/4] 方向图: {png}")

    # ---- 3. 详情 JSON + AI 解读（可缺省）----
    detail = build_detail(df, trade_date, prev)
    json_path = SEAT_DIR / f"seat_detail_{trade_date}.json"
    json_path.write_text(json.dumps(detail, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[3/4] 详情 JSON: {json_path}")

    # ---- 4. 高盛主/次合约净持仓图 + 已发布日报原子重渲染（失败不阻断） ----
    try:
        from backend.pipeline.goldman_contract import generate as generate_goldman
        from backend.pipeline.summary_render import rerender_report_for_data_date

        generate_goldman(trade_date, prev, top_n=args.goldman_top)
        data_date = datetime.strptime(trade_date, "%Y%m%d").strftime("%Y-%m-%d")
        report_path = rerender_report_for_data_date(data_date)
        if report_path:
            print(f"[4/4] 高盛主次合约附录已写入日报: {report_path}")
        else:
            print("[4/4] 高盛主次合约图已生成；尚无匹配日报，后续渲染会自动带入")
    except Exception as exc:  # noqa: BLE001 - 附录失败不能破坏原席位日更
        print(f"[警告] 高盛主次合约附录生成失败: {exc!r}；保留既有日报和席位产物")

    api_key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("DEEPSEEK_API") or ""
    if args.no_report or not api_key:
        print("[跳过] AI 解读未生成（--no-report 或未配置 DEEPSEEK_API_KEY）")
        return
    try:
        text, used_model, n_chars = generate_analysis(detail, api_key)
    except RuntimeError as exc:
        print(f"[警告] {exc}；CSV/PNG/JSON 已产出，仅缺 AI 解读")
        return
    md_path = SEAT_DIR / f"seat_analysis_{trade_date}.md"
    md_path.write_text(
        f"# 席位分歧分析 {trade_date}\n\n"
        f"数据：{trade_date} 对比 {prev} · 模型：{used_model} · {detail['data_scope']}\n\n{text}\n",
        encoding="utf-8")
    print(f"[3/4] AI 解读: {md_path}（{used_model}，{n_chars} 字）")


if __name__ == "__main__":
    main()
