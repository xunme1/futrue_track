# -*- coding: utf-8 -*-
"""
增量下载脚本（每日运行）
  把 合约池（config/contracts.yaml）中所有品种 + 南华指数的最新数据下载并追加到本地行情库（data/store/）。
  - 首次运行：从 start_pad（data.start_date 前推 150 天预热）全量下载
  - 之后每次：只从本地最后日期回扫一小段（日线 10 天 / 周线 75 天）增量追加，
    回扫是为了覆盖数据源对最近 K 线的修正（结算价调整、未完结周线等），历史部分不重复下载
  用法：
    python -m backend.pipeline.download                # 更新 watchlist 全部品种 + 指数
    python -m backend.pipeline.download --symbols sc2609.INE   # 只更新指定品种
    python -m backend.pipeline.download --full         # 强制全量重下（慎用，耗流量）
"""
import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.core.config import CFG, load_contracts
from backend.core.store import Store
from backend.datasource import INDEX_SOURCE, SOURCES, get_source, logout_all

OVERLAP_DAILY = 10    # 日线回扫天数
OVERLAP_WEEKLY = 75   # 周线回扫天数（覆盖未完结周 + 7周均线窗口）


def sync(store, source, dataset, symbol, fetch_fn, start_pad, end, overlap, full=False):
    last = None if full else store.last_date(source, dataset, symbol)
    if last is None:
        fetch_start = start_pad
        mode = "全量"
    else:
        fetch_start = max(start_pad, (last - timedelta(days=overlap)).strftime("%Y-%m-%d"))
        mode = f"增量(自{fetch_start})"
    if fetch_start > end:
        print(f"  [{symbol}] {dataset} 已是最新（{last.date()}），跳过")
        return
    df = fetch_fn(symbol, fetch_start, end)
    if df is None or len(df) == 0:
        print(f"  [{symbol}] {dataset} 数据源无新数据")
        return
    added = store.append(source, dataset, symbol, df)
    print(f"  [{symbol}] {dataset} {mode}：净增 {added} 行，最新 {df.index.max().date()}")


def main():
    ap = argparse.ArgumentParser(description="本地行情库 · 增量下载")
    ap.add_argument("--symbols", nargs="+", default=None, help="只更新这些品种（默认合约池全部）")
    ap.add_argument(
        "--source", choices=sorted(SOURCES), default=None,
        help="临时覆盖所有期货合约的数据源（默认使用 contracts.yaml 中各合约的 source）",
    )
    ap.add_argument("--full", action="store_true", help="强制全量重下")
    args = ap.parse_args()

    watchlist = load_contracts()
    if args.symbols:
        watchlist = [e for e in watchlist if e["symbol"] in args.symbols]
        missing = set(args.symbols) - {e["symbol"] for e in watchlist}
        if missing:
            raise SystemExit(f"{missing} 不在合约池 config/contracts.yaml 中，请先添加")

    start = CFG["data"]["start_date"]
    start_pad = (datetime.strptime(start, "%Y-%m-%d") - timedelta(days=150)).strftime("%Y-%m-%d")
    end = datetime.now().strftime("%Y-%m-%d")          # 下载永远追到"今天"

    store = Store()
    print(f"[开始] 本地库={store.root}  终点={end}  模式={'全量' if args.full else '增量'}")
    try:
        for entry in watchlist:
            sym, source = entry["symbol"], args.source or entry["source"]
            src = get_source(source, CFG)
            print(f"[{sym}] 数据源={source}")
            sync(store, source, "futures_daily", sym, src.futures_daily, start_pad, end, OVERLAP_DAILY, args.full)
            sync(store, source, "futures_weekly", sym, src.futures_weekly, start_pad, end, OVERLAP_WEEKLY, args.full)

        idx_sym = CFG["symbols"]["index"]
        idx_src = get_source(INDEX_SOURCE, CFG)
        print(f"[{idx_sym}] 指数 数据源={INDEX_SOURCE}")
        sync(store, INDEX_SOURCE, "index_daily", idx_sym, idx_src.index_daily, start_pad, end, OVERLAP_DAILY, args.full)
    finally:
        logout_all()
    print("[完成] 本地行情库已更新")


if __name__ == "__main__":
    main()
