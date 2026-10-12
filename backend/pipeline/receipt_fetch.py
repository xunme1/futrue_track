# -*- coding: utf-8 -*-
"""仓单研究监控第 1 步：米筐注册仓单 → data/receipt/history/<SYM>.csv 增量归档。

    python -m backend.pipeline.receipt_fetch                    # 日常增量（近 20 自然日回扫）
    python -m backend.pipeline.receipt_fetch --backfill         # 首次回填（2021 年初至今）
    python -m backend.pipeline.receipt_fetch --date 20261009 --force

history 文件按 date 去重追加（新覆盖旧），只增不重排；当日快照 receipt_data_<date>.csv
按「不晚于目标日的实际最新日期」归档；新缺失品种单独回填，已有品种只做增量。
无数据响应记录于 fetch_status.json，后续小窗口重试；--backfill/--force 可主动重查。
license：环境变量 FUTURES_RQDATA_LICENSE_KEY 或 config/config.yaml 的 ricequant.license_key。
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import json
from pathlib import Path
import sys

import pandas as pd

from backend.pipeline.dominant_fetch import _login, load_or_fetch
from backend.pipeline.report_store import atomic_json
from backend.pipeline.receipt_core import (
    BACKFILL_START,
    FINANCIAL_SYMBOLS,
    HISTORY_COLUMNS,
    HISTORY_DIR,
    RECEIPT_DIR,
)

BATCH_SIZE = 20
DAILY_LOOKBACK_DAYS = 20


def last_weekday(day=None):
    d = day or datetime.now().date()
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.strftime("%Y%m%d")


def commodity_symbols(date, dominants=None):
    """当日动态商品池品种代码（剔金融期货），升序。"""
    if dominants is None:
        dominants, _ = load_or_fetch(date)
    symbols = sorted({
        str(row.get("symbol") or "").upper()
        for row in dominants
        if str(row.get("symbol") or "").upper() not in FINANCIAL_SYMBOLS
    })
    return [s for s in symbols if s]


def multiplier_map(rq, date, force=False):
    """品种 → 合约乘数（吨/手）；rq 仓单接口对 SHFE/INE 不返回乘数，需从合约表补齐。"""
    cache = RECEIPT_DIR / f"multiplier_{date}.json"
    if cache.exists() and not force:
        return json.loads(cache.read_text(encoding="utf-8"))
    df = rq.all_instruments(type="Future", market="cn", date=date)
    df = df[~df["underlying_symbol"].astype(str).str.contains("_", na=False)]
    table = (df.groupby("underlying_symbol")["contract_multiplier"]
               .agg(lambda s: float(s.dropna().mode().iloc[0]) if s.dropna().size else None))
    result = {str(k): v for k, v in table.items() if v}
    atomic_json(cache, result)
    return result


def fetch_frame(rq, symbols, start_date, end_date):
    """分批抓取并整理成长表（列对齐 HISTORY_COLUMNS）。"""
    frames = []
    for i in range(0, len(symbols), BATCH_SIZE):
        batch = symbols[i:i + BATCH_SIZE]
        df = rq.futures.get_warehouse_stocks(
            underlying_symbols=batch, start_date=start_date, end_date=end_date)
        if df is None or len(df) == 0:
            print(f"[警告] 批次 {batch} 无数据")
            continue
        frames.append(df.reset_index())
        print(f"[抓取] {','.join(batch)} -> {len(df)} 行")
    if not frames:
        return pd.DataFrame(columns=HISTORY_COLUMNS)
    out = pd.concat(frames, ignore_index=True)
    out["date"] = (pd.to_datetime(out["date"]).dt.strftime("%Y%m%d"))
    out = out.rename(columns={"underlying_symbol": "symbol"})
    for col in HISTORY_COLUMNS:
        if col not in out.columns:
            out[col] = None
    return out[HISTORY_COLUMNS]


def append_history(frame, multipliers, directory=None) -> dict:
    """按品种增量写 history/<SYM>.csv（date 去重，新覆盖旧）；返回 {symbol: 净新增行数}。"""
    root = Path(directory) if directory else HISTORY_DIR
    root.mkdir(parents=True, exist_ok=True)
    added = {}
    for symbol, part in frame.groupby("symbol"):
        part = part.copy()
        mult = multipliers.get(str(symbol))
        if mult:
            part["multiplier"] = part["multiplier"].where(
                pd.notna(part["multiplier"]), mult)
        path = root / f"{symbol}.csv"
        old_len = 0
        if path.exists():
            old = pd.read_csv(path, dtype={"date": str})
            old_len = len(old)
            part = pd.concat([old, part], ignore_index=True)
        part["date"] = part["date"].astype(str)
        part = part.drop_duplicates(subset=["date"], keep="last").sort_values("date")
        part.to_csv(path, index=False)
        added[str(symbol)] = len(part) - old_len
    return added


def snapshot(root=None, as_of=None):
    """从 history 汇总不晚于 as_of 的最新快照 → receipt_data_<date>.csv。"""
    root = Path(root) if root else HISTORY_DIR
    rows = []
    for path in sorted(root.glob("*.csv")):
        df = pd.read_csv(path, dtype={"date": str})
        if as_of is not None:
            df = df[df["date"] <= as_of]
        if df.empty:
            continue
        rows.append(df.sort_values("date").iloc[-1])
    if not rows:
        return None, None
    latest = pd.DataFrame(rows)
    max_date = str(latest["date"].max())
    day = latest[latest["date"].astype(str) == max_date]
    out = RECEIPT_DIR / f"receipt_data_{max_date}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    day.to_csv(out, index=False)
    return max_date, out


def run(date=None, backfill=False, start=None, force=False, rq=None):
    """抓取主流程，返回 (effective_date, snapshot_path)。无数据返回 (None, None)。"""
    target = date or last_weekday()
    if rq is None:
        rq = _login()
    symbols = commodity_symbols(target)
    print(f"品种池 {len(symbols)} 个：{' '.join(symbols)}")

    status_path = RECEIPT_DIR / "fetch_status.json"
    try:
        status = json.loads(status_path.read_text(encoding="utf-8"))
        if not isinstance(status, dict):
            status = {}
    except (OSError, ValueError):
        status = {}
    recent_start = (
        datetime.strptime(target, "%Y%m%d") - timedelta(days=DAILY_LOOKBACK_DAYS)
    ).strftime("%Y%m%d")
    requests = {}
    for symbol in symbols:
        previous = status.get(symbol)
        previous = previous if isinstance(previous, dict) else {}
        if start or backfill:
            start_date = start or BACKFILL_START
        elif (HISTORY_DIR / f"{symbol}.csv").exists():
            start_date = recent_start
        elif (not force and previous.get("status") == "no_data"
              and str(previous.get("checked_from") or "99999999") <= BACKFILL_START
              and previous.get("checked_through")):
            # 无数据不等于永久不支持：仍回扫近期；停跑后的未检查区间也不能漏掉。
            start_date = max(BACKFILL_START, min(recent_start, previous["checked_through"]))
        else:
            start_date = BACKFILL_START
        requests.setdefault(start_date, []).append(symbol)

    multipliers = multiplier_map(rq, target, force=force)
    frames = []
    for start_date, batch in sorted(requests.items()):
        print(f"[区间] {start_date}~{target}：{' '.join(batch)}")
        part = fetch_frame(rq, batch, start_date, target)
        found = set(part["symbol"]) if not part.empty else set()
        if not part.empty:
            frames.append(part)
        for symbol in batch:
            previous = status.get(symbol)
            previous = previous if isinstance(previous, dict) else {}
            checked_from, checked_through = start_date, target
            if (symbol not in found and previous.get("status") == "no_data"
                    and str(previous.get("checked_from") or "99999999") <= target
                    and str(previous.get("checked_through") or "") >= start_date):
                checked_from = min(start_date, previous["checked_from"])
                checked_through = max(target, previous["checked_through"])
            status[symbol] = {
                "status": "available" if symbol in found else "no_data",
                "checked_from": checked_from, "checked_through": checked_through,
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            }
    # 只有成功响应（包括空响应）才记录；请求异常不缓存成「不支持」。
    atomic_json(status_path, status)
    if not frames:
        print("[错误] 未取到任何仓单数据")
        return None, None
    frame = pd.concat(frames, ignore_index=True)
    added = append_history(frame, multipliers)
    total_new = sum(added.values())
    print(f"[归档] history 更新 {len(added)} 个品种，净新增 {total_new} 行")
    effective, out = snapshot(as_of=target)
    print(f"[快照] {out}（{effective}）")
    return effective, out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", help="目标交易日 YYYYMMDD（默认最近工作日）")
    ap.add_argument("--backfill", action="store_true", help=f"全量回填（起点 {BACKFILL_START} 或 --start）")
    ap.add_argument("--start", help="自定义抓取起点 YYYYMMDD")
    ap.add_argument("--force", action="store_true", help="忽略乘数缓存，并重新回填尚无历史的品种")
    args = ap.parse_args(argv)
    effective, _ = run(date=args.date, backfill=args.backfill,
                       start=args.start, force=args.force)
    if not effective:
        sys.exit(1)


if __name__ == "__main__":
    main()
