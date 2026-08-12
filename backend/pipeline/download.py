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
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.core.config import CFG, load_contracts
from backend.core.store import Store
from backend.datasource import INDEX_SOURCE, SOURCES, get_source, logout_all, reconnect_source
from backend.datasource.ifind import IFindRequestError, IFindSource

OVERLAP_DAILY = 10    # 日线回扫天数
OVERLAP_WEEKLY = 75   # 周线回扫天数（覆盖未完结周 + 7周均线窗口）
OVERLAP_4H = 10       # 4 小时线按日更回扫，覆盖数据源近期修正


def _fetch_with_ifind_retry(source, fetch_fn, symbol, start, end, retries, retry_delay):
    """会话超时后仅重连 iFinD 并重试当前请求，其他错误仍立即失败。"""
    for attempt in range(retries + 1):
        try:
            return fetch_fn(symbol, start, end)
        except IFindRequestError as exc:
            if source != IFindSource.name or not exc.session_expired or attempt == retries:
                raise
            print(
                f"  [{symbol}] iFinD 会话已失效（ec={exc.errorcode}），"
                f"{retry_delay} 秒后重新登录并重试（{attempt + 1}/{retries}）"
            )
            if retry_delay:
                time.sleep(retry_delay)
            reconnect_source(source, CFG)


def sync(store, source, dataset, symbol, fetch_fn, start_pad, end, overlap, full=False,
         ifind_retries=3, ifind_retry_delay=5):
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
    df = _fetch_with_ifind_retry(
        source, fetch_fn, symbol, fetch_start, end, ifind_retries, ifind_retry_delay
    )
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
    ap.add_argument("--ifind-retries", type=int, default=3,
                    help="iFinD 会话失效后的额外重试次数（默认 3）")
    ap.add_argument("--ifind-retry-delay", type=int, default=5,
                    help="iFinD 重新登录前等待秒数（默认 5）")
    ap.add_argument("--timeframe", choices=("1d", "4h", "all"), default="1d",
                    help="下载日线、4小时线，或两者（默认日线，保持兼容）")
    args = ap.parse_args()
    if args.ifind_retries < 0 or args.ifind_retry_delay < 0:
        ap.error("--ifind-retries 与 --ifind-retry-delay 不能为负数")

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
            if args.timeframe in ("1d", "all"):
                sync(store, source, "futures_daily", sym, src.futures_daily, start_pad, end, OVERLAP_DAILY,
                     args.full, args.ifind_retries, args.ifind_retry_delay)
                sync(store, source, "futures_weekly", sym, src.futures_weekly, start_pad, end, OVERLAP_WEEKLY,
                     args.full, args.ifind_retries, args.ifind_retry_delay)
            if args.timeframe in ("4h", "all"):
                sync(store, source, "futures_4h", sym, src.futures_4h, start_pad, end, OVERLAP_4H,
                     args.full, args.ifind_retries, args.ifind_retry_delay)

        if args.timeframe in ("1d", "all"):
            idx_sym = CFG["symbols"]["index"]
            idx_src = get_source(INDEX_SOURCE, CFG)
            print(f"[{idx_sym}] 指数 数据源={INDEX_SOURCE}")
            sync(store, INDEX_SOURCE, "index_daily", idx_sym, idx_src.index_daily, start_pad, end, OVERLAP_DAILY,
                 args.full, args.ifind_retries, args.ifind_retry_delay)
    finally:
        logout_all()
    print("[完成] 本地行情库已更新")


if __name__ == "__main__":
    main()
