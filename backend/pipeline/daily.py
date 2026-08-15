# -*- coding: utf-8 -*-
"""Local calculation pipeline for the daily and 4-hour dashboards."""
import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.core.config import CFG, load_contracts, PARAMS
from backend.core.store import Store
from backend.core.timeframes import csv_dir, json_dir
from backend.datasource import INDEX_SOURCE
from backend.strategy import DEFAULT_STRATEGY, get_strategy

DAILY_EXPORT_COLS = ["open", "high", "low", "close", "volume", "ccl", "PQ", "PR", "NN", "GG",
                     "AA1", "ZZ1", "TT1", "PANZHENG", "KK", "PP", "DD", "EE",
                     "SIGNAL", "POS", "SB", "DSB", "DSBE", "DSBE_NOTE"]
FOUR_HOUR_EXPORT_COLS = ["open", "high", "low", "close", "volume", "ccl", "trading_date", "BAR_COLOR",
                         "AA1", "ZZ1", "TT1", "PANZHENG", "KK", "PP", "DD", "EE",
                         "SIGNAL", "POS", "SB", "DSB", "DSBE", "DSBE_NOTE"]


def _json_safe(value):
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return value


def _nullable(df, column):
    return df[column].where(df[column].notna(), None).tolist()


def build_payload(symbol, df, timeframe="1d"):
    """Convert strategy output into the dashboard wire format."""
    payload = {
        "symbol": symbol,
        "timeframe": timeframe,
        "dates": df.index.strftime("%Y-%m-%d %H:%M" if timeframe == "4h" else "%Y-%m-%d").tolist(),
        "ohlc": df[["open", "close", "low", "high"]].round(4).values.tolist(),
        "volume": df["volume"].fillna(0).tolist(),
        "opi": df["ccl"].fillna(0).tolist(),
        "signals": [{"i": i, "type": s, "price": float(df["low"].iloc[i]) if s in ("BK", "BP") else float(df["high"].iloc[i])}
                    for i, s in enumerate(df["SIGNAL"]) if s],
        "SB": df["SB"].fillna(False).tolist(),
        "DSB": df["DSB"].fillna(False).tolist(),
        "AA1": df["AA1"].fillna(False).tolist(),
        "ZZ1": df["ZZ1"].fillna(False).tolist(),
        "TT1": df["TT1"].fillna(False).tolist(),
        "DSBE": df["DSBE"].fillna(False).tolist(),
        "DSBE_NOTE": [note if isinstance(note, str) and note else None for note in df["DSBE_NOTE"]],
        "KK": _nullable(df, "KK"), "PP": _nullable(df, "PP"),
        "DD": _nullable(df, "DD"), "EE": _nullable(df, "EE"),
        "POS": df["POS"].tolist(), "ZD": _nullable(df, "ZD"),
    }
    if timeframe == "1d":
        payload.update({
            "PQ": df["PQ"].fillna(False).tolist(), "PR": df["PR"].fillna(False).tolist(),
            "NN": _nullable(df, "NN"), "GG": _nullable(df, "GG"),
        })
    else:
        payload["bar_colors"] = df["BAR_COLOR"].fillna("gray").tolist()
    return _json_safe(payload)


def export(symbol, df, timeframe="1d"):
    """Export a timeframe's dashboard JSON and per-bar signal CSV."""
    key = symbol.split(".")[0]
    target_csv, target_json = csv_dir(timeframe), json_dir(timeframe)
    target_csv.mkdir(parents=True, exist_ok=True)
    target_json.mkdir(parents=True, exist_ok=True)
    cols = DAILY_EXPORT_COLS if timeframe == "1d" else FOUR_HOUR_EXPORT_COLS
    df[[column for column in cols if column in df.columns]].to_csv(target_csv / f"signals_{key}.csv", encoding="utf-8-sig")
    with open(target_json / f"{key}.json", "w", encoding="utf-8") as f:
        json.dump(build_payload(symbol, df, timeframe), f, ensure_ascii=False, allow_nan=False)
    counts = {name: int(count) for name, count in df["SIGNAL"][df["SIGNAL"].ne("")].value_counts().items()}
    print(f"  [{symbol}] {len(df)} 根{timeframe}K，交易信号 {int(df['SIGNAL'].ne('').sum())} 个：{counts}")


def _watchlist(symbols):
    watchlist = load_contracts()
    if not symbols:
        return watchlist
    selected = [entry for entry in watchlist if entry["symbol"] in symbols]
    missing = set(symbols) - {entry["symbol"] for entry in selected}
    if missing:
        raise SystemExit(f"{missing} 不在合约池 config/contracts.yaml 中，请先添加")
    return selected


def _run_daily(store, watchlist, start, strategy_name):
    strategy = get_strategy(strategy_name)
    idx_d = store.read(INDEX_SOURCE, "index_daily", CFG["symbols"]["index"])
    for entry in watchlist:
        symbol, source = entry["symbol"], entry["source"]
        df = strategy.compute(store.read(source, "futures_daily", symbol), store.read(source, "futures_weekly", symbol), idx_d, PARAMS)
        export(symbol, df.loc[start:], "1d")


def _run_4h(store, watchlist, start):
    strategy = get_strategy("zxgl_4h")
    for entry in watchlist:
        symbol, source = entry["symbol"], entry["source"]
        bars = store.read(source, "futures_4h", symbol)
        weekly = store.read(source, "futures_weekly", symbol)
        df = strategy.compute(bars, weekly, PARAMS)
        export(symbol, df.loc[start:], "4h")


def main():
    parser = argparse.ArgumentParser(description="期货指标监测 · 本地计算流水线")
    parser.add_argument("--symbols", nargs="+", default=None)
    parser.add_argument("--strategy", default=DEFAULT_STRATEGY, help="仅日线可指定策略")
    parser.add_argument("--timeframe", choices=("1d", "4h", "all"), default="1d")
    args = parser.parse_args()
    watchlist, store, start = _watchlist(args.symbols), Store(), CFG["data"]["start_date"]
    if args.timeframe in ("1d", "all"):
        _run_daily(store, watchlist, start, args.strategy)
    if args.timeframe in ("4h", "all"):
        _run_4h(store, watchlist, start)


if __name__ == "__main__":
    main()
