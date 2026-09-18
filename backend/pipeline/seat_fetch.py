# -*- coding: utf-8 -*-
"""席位追踪第 1 步：抓取会员持仓数据（繁微 REST API）→ data/seat/seat_data_<end>.csv。

    python -m backend.pipeline.seat_fetch --start 20260811 --end 20260917
    python -m backend.pipeline.seat_fetch --end 20260918   # start 默认向前 40 自然日

CSV 缓存已存在则直接跳过 API 请求（幂等，可反复重跑）。
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import sys
import time

import pandas as pd

from backend.pipeline.seat_core import (
    FIELDS,
    SEAT_DIR,
    SLEEP_SEC,
    SYMBOLS,
    get_member_rank,
)


def fetch_all(start_date, end_date):
    rows_all, failed = [], []
    for i, sym in enumerate(SYMBOLS, 1):
        try:
            resp = get_member_rank(sym, start_date, end_date, FIELDS)
            rows = resp.get("data", {}).get("data", []) or []
            for r in rows:
                r["symbol"] = sym
            rows_all.extend(rows)
            print(f"[{i:2d}/{len(SYMBOLS)}] {sym:4s} -> {len(rows)} 行")
            if not rows:
                failed.append((sym, f"code={resp.get('code')} empty"))
        except Exception as e:  # noqa: BLE001
            failed.append((sym, repr(e)))
            print(f"[{i:2d}/{len(SYMBOLS)}] {sym:4s} -> 失败: {e!r}")
        time.sleep(SLEEP_SEC)
    if failed:
        print("\n异常/空返回品种:", failed)
    return rows_all


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", help="开始日期 YYYYMMDD（默认 end 向前 40 自然日，覆盖近20交易日分位）")
    ap.add_argument("--end", required=True, help="结束日期 YYYYMMDD（决定缓存文件名）")
    args = ap.parse_args(argv)
    end = args.end
    start = args.start or (datetime.strptime(end, "%Y%m%d") - timedelta(days=40)).strftime("%Y%m%d")

    SEAT_DIR.mkdir(parents=True, exist_ok=True)
    out_csv = SEAT_DIR / f"seat_data_{end}.csv"
    if out_csv.exists():
        df = pd.read_csv(out_csv, dtype={"trade_date": str})
        print(f"缓存命中: {out_csv} ({len(df)} 行)，跳过 API 请求")
    else:
        rows = fetch_all(start, end)
        if not rows:
            print("未抓到任何数据，退出")
            sys.exit(1)
        df = pd.DataFrame(rows)
        df.to_csv(out_csv, index=False)
        print(f"\n已保存 {out_csv} ({len(df)} 行)")

    df["trade_date"] = df["trade_date"].astype(str)
    print(f"\ntrade_date 范围: {df['trade_date'].min()} ~ {df['trade_date'].max()}")
    print(f"覆盖品种数: {df['symbol'].nunique()} / {len(SYMBOLS)}")


if __name__ == "__main__":
    main()
