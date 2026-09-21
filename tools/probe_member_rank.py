# -*- coding: utf-8 -*-
"""会员持仓双源探测（只读）：米筐 rqdatac 合约级 vs 繁微 REST 品种合计。

    .venv/bin/python tools/probe_member_rank.py                 # 默认用 data/seat 最新日
    .venv/bin/python tools/probe_member_rank.py --date 20260918

凭据从 .env.server 读取（FUTURES_RQDATA_LICENSE_KEY / FINO_APPKEY / FINO_APPSECRET）。
不写任何数据产物，仅打印原始形状，便于核对两个 SDK 的返回字段。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def load_env(path=ROOT / ".env.server"):
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def probe_ricequant(contract, start, end):
    print("=" * 72)
    print(f"[米筐 rqdatac] futures.get_member_rank('{contract}', rank_by='long', "
          f"start_date={start}, end_date={end})")
    key = os.environ["FUTURES_RQDATA_LICENSE_KEY"]
    import rqdatac
    rqdatac.init("license", key)
    for rank_by in ("long", "short", "volume"):
        df = rqdatac.futures.get_member_rank(
            contract, rank_by=rank_by, start_date=start, end_date=end)
        print(f"\n--- rank_by={rank_by} --- shape={None if df is None else df.shape}")
        if df is None or len(df) == 0:
            print("(空)")
            continue
        print("index.name =", df.index.name, "| columns =", list(df.columns))
        sub = df.reset_index()
        print("rank 1-3:", list(sub["member_name"].head(3)))
        hit = sub[sub["member_name"].astype(str).str.contains("高盛|乾坤", na=False)]
        print("高盛/乾坤:", hit.to_string(index=False) if len(hit) else "(未上榜)")
    # 品种层级对照（上期所/中金所由合约数据加总）
    df = rqdatac.futures.get_member_rank("RB", rank_by="long", start_date=end, end_date=end)
    print(f"\n--- 品种层级 'RB' rank_by=long --- shape={None if df is None else df.shape}")
    if df is not None and len(df):
        print(df.reset_index().head(4).to_string(index=False))


def probe_finovie(symbol, start, end):
    print("\n" + "=" * 72)
    print(f"[繁微 REST] get_member_rank symbol={symbol} {start}~{end}")
    import requests
    from backend.pipeline.seat_core import FINO_HEADERS, FINO_URL
    payload = {
        "symbol": symbol, "start_date": start, "end_date": end,
        "fields": ["volume", "long", "short"],
        "appkey": os.environ["FINO_APPKEY"],
        "appsecret": os.environ["FINO_APPSECRET"],
    }
    resp = requests.post(FINO_URL, json=payload, headers=FINO_HEADERS, timeout=20)
    print("HTTP", resp.status_code)
    body = resp.json()
    print("top-level keys:", list(body.keys()), "| code =", body.get("code"),
          "| msg =", body.get("msg"))
    rows = (body.get("data") or {}).get("data") or []
    print("rows =", len(rows))
    if rows:
        print("row keys:", list(rows[0].keys()))
        print("首行:", json.dumps(rows[0], ensure_ascii=False))
        hit = [r for r in rows if "高盛" in str(r.get("member_name", ""))
               or "乾坤" in str(r.get("member_name", ""))]
        print(f"高盛/乾坤命中 {len(hit)} 行，样例:")
        for r in hit[:4]:
            print("  ", json.dumps(r, ensure_ascii=False))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", help="交易日 YYYYMMDD（默认 data/seat 最新 CSV 日期）")
    ap.add_argument("--contract", default="RB2610", help="米筐探测用具体合约")
    ap.add_argument("--symbol", default="RB", help="繁微探测用品种代码")
    args = ap.parse_args(argv)
    load_env()

    date = args.date
    if not date:
        from backend.pipeline.seat_core import latest_csv_date
        date = latest_csv_date()
    if not date:
        sys.exit("无法确定交易日，请用 --date 指定")
    print(f"探测交易日: {date}\n")

    probe_ricequant(args.contract, date, date)
    probe_finovie(args.symbol, date, date)


if __name__ == "__main__":
    main()
