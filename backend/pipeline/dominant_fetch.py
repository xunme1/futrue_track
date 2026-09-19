# -*- coding: utf-8 -*-
"""抓取全品种当日主力/次主力合约名单（米筐 get_dominant）→ data/seat/dominant_<date>.json。

    python -m backend.pipeline.dominant_fetch --date 20260918
    python -m backend.pipeline.dominant_fetch            # date 默认当天

品种清单动态取自 rqdatac.all_instruments(type='Future')（剔除含 '_' 的伪主连符号，
已退市品种 get_dominant 返回 None 自动跳过，新品种无需改代码自动覆盖）。
JSON 缓存已存在则直接跳过 API 请求（幂等，可反复重跑）。
license：环境变量 FUTURES_RQDATA_LICENSE_KEY 或 config/config.yaml 的 ricequant.license_key。
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json

from backend.core.config import DATA_DIR, load_config
from backend.pipeline.report_store import atomic_json

INDEX_SYMBOLS = {"IF", "IC", "IH", "IM"}  # 股指 rank=2/3 需 rule=1 或 rule=2


def _login():
    import rqdatac
    cfg = load_config()
    key = (cfg.get("ricequant") or {}).get("license_key", "")
    if not key:
        raise SystemExit(
            "米筐 license 为空，请设置 FUTURES_RQDATA_LICENSE_KEY "
            "或填写 config.yaml 的 ricequant.license_key"
        )
    rqdatac.init("license", key)
    return rqdatac


def list_symbols(rq, date=None):
    """指定日期的全市场活跃期货品种（剔除伪主连等含 '_' 符号）。"""
    kwargs = {"type": "Future", "market": "cn"}
    if date:
        kwargs["date"] = date
    df = rq.all_instruments(**kwargs)
    syms = sorted({str(s) for s in df["underlying_symbol"].unique() if "_" not in str(s)})
    return syms


def _dominant(rq, symbol, date, rule, rank):
    d = rq.futures.get_dominant(symbol, date, date, rule=rule, rank=rank)
    if d is None or len(d) == 0:
        return None
    return str(d.iloc[0])


def fetch_all(rq, symbols, date):
    """逐品种取主力/次主力；退市品种跳过，次主力与主力重复记 None。"""
    rows, skipped = [], []
    for i, sym in enumerate(symbols, 1):
        try:
            main = _dominant(rq, sym, date, rule=0, rank=1)
            if not main:
                skipped.append(sym)
                continue
            rule = 1 if sym in INDEX_SYMBOLS else 0
            sub = _dominant(rq, sym, date, rule=rule, rank=2)
            if sub == main:
                sub = None
            rows.append({"symbol": sym, "main": main, "sub": sub})
            print(f"[{i:3d}/{len(symbols)}] {sym:4s} 主力={main} 次主力={sub or '-'}")
        except Exception as e:  # noqa: BLE001
            skipped.append(sym)
            print(f"[{i:3d}/{len(symbols)}] {sym:4s} 失败: {e!r}")
    if skipped:
        print(f"\n无数据/退市品种({len(skipped)}): {' '.join(skipped)}")
    return rows


def load_or_fetch(date, force=False):
    """Return the dominant/sub-dominant map for *date*, using the daily cache.

    This helper is shared by the Goldman contract-position pipeline.  The
    cache format deliberately remains the original list of
    ``{symbol, main, sub}`` objects so existing cached files stay usable.
    """
    out_dir = DATA_DIR / "seat"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_json = out_dir / f"dominant_{date}.json"
    if out_json.exists() and not force:
        rows = json.loads(out_json.read_text(encoding="utf-8"))
        print(f"缓存命中: {out_json} ({len(rows)} 个品种)，跳过 API 请求")
        return rows, out_json

    rq = _login()
    symbols = list_symbols(rq, date)
    print(f"枚举到 {len(symbols)} 个品种，开始抓取 {date} 主力/次主力…\n")
    rows = fetch_all(rq, symbols, date)
    if not rows:
        raise RuntimeError("未取到任何品种的主力/次主力合约")
    atomic_json(out_json, rows)
    print(f"\n已保存 {out_json} ({len(rows)} 个品种)")
    return rows, out_json


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", help="交易日期 YYYYMMDD（默认当天）")
    ap.add_argument("--force", action="store_true", help="忽略当日缓存，重新请求")
    args = ap.parse_args(argv)
    date = args.date or datetime.now().strftime("%Y%m%d")
    try:
        load_or_fetch(date, force=args.force)
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
