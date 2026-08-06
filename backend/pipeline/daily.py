# -*- coding: utf-8 -*-
"""
每日计算流水线（CLI 入口，纯本地数据）
  数据全部来自本地行情库（data/store/），不连接数据源；
  请先用 python -m backend.pipeline.download 更新本地库。
  用法：
    python -m backend.pipeline.daily                          # watchlist 全部品种
    python -m backend.pipeline.daily --symbols sc2609.INE     # 指定品种
    python -m backend.pipeline.daily --strategy zxgl_xdd      # 指定策略（默认读注册表）
  产物：
    data/json/{品种}.json        看板数据（K线+信号+通道）
    data/csv/signals_{品种}.csv  逐 bar 信号明细
"""
import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.core.config import CFG, load_contracts, PARAMS, CSV_DIR, JSON_DIR
from backend.core.store import Store
from backend.datasource import INDEX_SOURCE
from backend.strategy import DEFAULT_STRATEGY, get_strategy

EXPORT_COLS = ["open", "high", "low", "close", "volume", "ccl", "PQ", "PR", "NN", "GG",
               "AA1", "ZZ1", "TT1", "PANZHENG", "KK", "PP", "DD", "EE",
               "SIGNAL", "POS", "SB", "DSB", "DSBE", "DSBE_NOTE"]


def _json_safe(value):
    """将 Pandas/策略计算中的 NaN、无穷值递归替换为标准 JSON null。"""
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return value


def build_payload(symbol, df):
    """把策略结果转换成前端 API 契约，便于独立测试。"""
    payload = {
        "symbol": symbol,
        "dates": df.index.strftime("%Y-%m-%d").tolist(),
        "ohlc": df[["open", "close", "low", "high"]].round(4).values.tolist(),  # ECharts 顺序 O C L H
        "volume": df["volume"].fillna(0).tolist(),
        "opi": df["ccl"].fillna(0).tolist(),
        "PQ": df["PQ"].fillna(False).tolist(),
        "PR": df["PR"].fillna(False).tolist(),
        "NN": df["NN"].where(df["NN"].notna(), None).tolist(),
        "GG": df["GG"].where(df["GG"].notna(), None).tolist(),
        "signals": [{"i": i, "type": s, "price": float(df["low"].iloc[i]) if s in ("BK", "BP") else float(df["high"].iloc[i])}
                    for i, s in enumerate(df["SIGNAL"]) if s],
        "SB": df["SB"].fillna(False).tolist(),
        "DSB": df["DSB"].fillna(False).tolist(),
        "AA1": df["AA1"].tolist(),
        "ZZ1": df["ZZ1"].tolist(),
        "TT1": df["TT1"].tolist(),
        "DSBE": df["DSBE"].fillna(False).tolist(),
        "DSBE_NOTE": [note if isinstance(note, str) and note else None
                      for note in df["DSBE_NOTE"]],
        "KK": df["KK"].where(df["KK"].notna(), None).tolist(),
        "PP": df["PP"].where(df["PP"].notna(), None).tolist(),
        "DD": df["DD"].where(df["DD"].notna(), None).tolist(),
        "EE": df["EE"].where(df["EE"].notna(), None).tolist(),
        "POS": df["POS"].tolist(),
        "ZD": df["ZD"].where(df["ZD"].notna(), None).tolist(),
    }
    return _json_safe(payload)


def export(symbol, df):
    """导出看板 JSON + 信号 CSV"""
    key = symbol.split(".")[0]
    df[EXPORT_COLS].to_csv(CSV_DIR / f"signals_{key}.csv", encoding="utf-8-sig")
    payload = build_payload(symbol, df)
    with open(JSON_DIR / f"{key}.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, allow_nan=False)
    n_sig = df["SIGNAL"].ne("").sum()
    counts = {name: int(count) for name, count in
              df["SIGNAL"][df["SIGNAL"].ne("")].value_counts().items()}
    print(f"  [{symbol}] {len(df)} 根日K，交易信号 {int(n_sig)} 个：{counts}")


def main():
    ap = argparse.ArgumentParser(description="期货指标监测 · 每日计算流水线（本地数据）")
    ap.add_argument("--symbols", nargs="+", default=None, help="只算这些品种（默认合约池全部）")
    ap.add_argument("--strategy", default=DEFAULT_STRATEGY)
    args = ap.parse_args()
    strategy = get_strategy(args.strategy)

    watchlist = load_contracts()
    if args.symbols:
        watchlist = [e for e in watchlist if e["symbol"] in args.symbols]
        missing = set(args.symbols) - {e["symbol"] for e in watchlist}
        if missing:
            raise SystemExit(f"{missing} 不在合约池 config/contracts.yaml 中，请先添加")

    start = CFG["data"]["start_date"]
    store = Store()
    idx_sym = CFG["symbols"]["index"]
    idx_d = store.read(INDEX_SOURCE, "index_daily", idx_sym)      # 指数固定 iFinD 本地库

    print(f"[启动] 策略={strategy.STRATEGY_NAME} 数据=本地行情库 起始={start}（终点=库内最新）")
    for entry in watchlist:
        sym, source = entry["symbol"], entry["source"]
        d = store.read(source, "futures_daily", sym)
        w = store.read(source, "futures_weekly", sym)
        df = strategy.compute(d, w, idx_d, PARAMS)
        df = df.loc[start:]                            # 裁掉预热段，终点跟随库内最新数据
        export(sym, df)
    print(f"[完成] 产物：{JSON_DIR}  {CSV_DIR}")


if __name__ == "__main__":
    main()
