# -*- coding: utf-8 -*-
"""席位追踪第 1 步：动态商品池会员持仓 → data/seat/seat_data_<end>.csv。

    python -m backend.pipeline.seat_fetch --start 20260811 --end 20260917
    python -m backend.pipeline.seat_fetch --end 20260918

数据源统一为 RiceQuant（繁微品种合计口径已弃用）。逐品种结果和最终 CSV
都会缓存，同日任务可安全重跑。
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import json
from pathlib import Path
import sys
import time

import pandas as pd

from backend.pipeline.dominant_fetch import load_or_fetch
from backend.pipeline.report_store import atomic_json
from backend.pipeline.seat_core import SEAT_DIR


FINANCIAL_SYMBOLS = {"IF", "IH", "IC", "IM", "T", "TF", "TS", "TL"}


def commodity_universe(end_date, dominants=None):
    """Return target-day active commodity rows, excluding financial futures."""
    if dominants is None:
        dominants, _ = load_or_fetch(end_date)
    rows = [
        dict(row) for row in dominants
        if str(row.get("symbol") or "").upper() not in FINANCIAL_SYMBOLS
    ]
    rows.sort(key=lambda row: str(row.get("symbol") or ""))
    return rows


def _day(value):
    return str(value or "").replace("-", "")[:8]


def rq_symbol_rows(symbol, start_date, end_date, rq=None):
    """Normalize RiceQuant long/short top-20 boards to the seat CSV shape."""
    if rq is None:
        from backend.pipeline.goldman_contract import _rq
        rq = _rq()
    merged = {}
    for rank_by, value_key, change_key in (
        ("long", "total_long", "total_long_change"),
        ("short", "total_short", "total_short_change"),
    ):
        frame = rq.futures.get_member_rank(
            symbol, rank_by=rank_by, start_date=start_date, end_date=end_date
        )
        if frame is None or len(frame) == 0:
            continue
        for rec in frame.reset_index().to_dict("records"):
            day = _day(rec.get("trading_date"))
            # 郑商所品种米筐只返回「XX（代客）」形式，去掉后缀归一到公司名
            # （已验证同品种同日不存在「X」与「X（代客）」并存，无重复计数风险）。
            member = str(rec.get("member_name") or "").strip()
            member = member.replace("（代客）", "").replace("(代客)", "")
            if not day or not member:
                continue
            row = merged.setdefault((day, member), {
                "trade_date": day,
                "member_name": member,
                "total_volume": 0,
                "total_volume_change": 0,
                "total_long": 0,
                "total_long_change": 0,
                "total_short": 0,
                "total_short_change": 0,
                "code": symbol,
                "symbol": symbol,
                "source": "ricequant",
            })
            row[value_key] = int(rec.get("volume") or 0)
            row[change_key] = int(rec.get("volume_change") or 0)
    rows = list(merged.values())
    if not any(row["trade_date"] == end_date for row in rows):
        raise ValueError("RiceQuant 目标日无品种排名数据")
    return rows


def fetch_symbol(symbol, start_date, end_date, rq=None):
    """Fetch a symbol's complete history window from RiceQuant."""
    try:
        rows = rq_symbol_rows(symbol, start_date, end_date, rq=rq)
        return rows, {"source": "ricequant", "rows": len(rows)}
    except Exception as exc:  # noqa: BLE001 - one symbol cannot stop the universe
        return [], {
            "source": None,
            "error": f"{type(exc).__name__}: {exc}",
            "rows": 0,
        }


def fetch_all(start_date, end_date, symbols=None, dominants=None, directory=None,
              rq=None, sleep_sec=0, return_manifest=False, force=False):
    """Fetch a dynamic universe with per-symbol source caches."""
    if symbols is None:
        symbols = [
            str(row["symbol"]).upper()
            for row in commodity_universe(end_date, dominants=dominants)
        ]
    else:
        symbols = [str(symbol).upper() for symbol in symbols]
    cache_root = Path(directory) if directory else SEAT_DIR / "source_cache" / end_date
    cache_root.mkdir(parents=True, exist_ok=True)
    rows_all, manifest = [], {}
    for index, symbol in enumerate(symbols, 1):
        cache_path = cache_root / f"{symbol}.json"
        cached = False
        rows, meta = None, None
        if cache_path.exists() and not force:
            try:
                payload = json.loads(cache_path.read_text(encoding="utf-8"))
                if (payload.get("symbol") == symbol
                        and payload.get("start_date") == start_date
                        and payload.get("end_date") == end_date
                        and isinstance(payload.get("rows"), list)):
                    rows = payload["rows"]
                    meta = payload.get("meta") or {}
                    cached = True
            except (OSError, ValueError, TypeError):
                rows = None
        if rows is None:
            rows, meta = fetch_symbol(symbol, start_date, end_date, rq=rq)
            # 只缓存成功产物。限流、网络或临时权限故障必须允许同日重跑时恢复，
            # 不能被一个 source=None 的空响应永久短路。
            if meta.get("source"):
                atomic_json(cache_path, {
                    "symbol": symbol,
                    "start_date": start_date,
                    "end_date": end_date,
                    "rows": rows,
                    "meta": meta,
                })
        rows_all.extend(rows)
        manifest[symbol] = dict(meta, cached=cached)
        source = meta.get("source") or "不可用"
        suffix = " · 缓存" if cached else ""
        print(f"[{index:3d}/{len(symbols)}] {symbol:4s} -> {len(rows):5d} 行 · {source}{suffix}")
        if sleep_sec and not cached and index < len(symbols):
            time.sleep(sleep_sec)
    if return_manifest:
        return rows_all, manifest
    return rows_all


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", help="开始日期 YYYYMMDD（默认 end 向前 40 自然日）")
    ap.add_argument("--end", required=True, help="结束日期 YYYYMMDD（决定缓存文件名）")
    ap.add_argument("--force", action="store_true", help="忽略逐品种源缓存并重新请求")
    args = ap.parse_args(argv)
    end = args.end
    start = args.start or (datetime.strptime(end, "%Y%m%d") - timedelta(days=40)).strftime("%Y%m%d")

    SEAT_DIR.mkdir(parents=True, exist_ok=True)
    out_csv = SEAT_DIR / f"seat_data_{end}.csv"
    source_path = SEAT_DIR / f"seat_sources_{end}.json"
    if out_csv.exists() and not args.force:
        df = pd.read_csv(out_csv, dtype={"trade_date": str})
        print(f"缓存命中: {out_csv} ({len(df)} 行)，跳过 API 请求")
    else:
        rows, manifest = fetch_all(start, end, return_manifest=True, force=args.force)
        if not rows:
            print("未抓到任何数据，退出")
            sys.exit(1)
        df = pd.DataFrame(rows)
        df.to_csv(out_csv, index=False)
        atomic_json(source_path, manifest)
        print(f"\n已保存 {out_csv} ({len(df)} 行)")

    df["trade_date"] = df["trade_date"].astype(str)
    print(f"\ntrade_date 范围: {df['trade_date'].min()} ~ {df['trade_date'].max()}")
    print(f"覆盖品种数: {df['symbol'].nunique()}")


if __name__ == "__main__":
    main()
