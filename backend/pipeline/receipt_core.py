# -*- coding: utf-8 -*-
"""仓单研究监控共享层：单位换算、指标计算、规则信号（纯计算，不依赖 rqdatac）。

数据口径（米筐 futures.get_warehouse_stocks）：
  - on_warrant 注册仓单量，原始单位随交易所不同：CZCE=张，SHFE/INE=吨，DCE=手，GFEX=张
  - effective_forecast 有效预报（仅 CZCE，单位同为张）
  - warrant_units 张→手换算（仅 CZCE 等提供，如 CF=8 手/张、MA=1 手/张）
  - multiplier 合约乘数（吨/手），SHFE/INE 吨→手换算用；由抓取层从 all_instruments 补齐
折手（lots）统一换算规则见 to_lots()；换算不出的品种虚实比记 None，页面显示「—」。
"""
from __future__ import annotations

import math
import re
from pathlib import Path

import pandas as pd

from backend.core.config import DATA_DIR
from backend.pipeline.seat_core import CN_NAME  # noqa: F401  （中文名映射复用席位模块）
from backend.pipeline.seat_fetch import FINANCIAL_SYMBOLS  # noqa: F401

RECEIPT_DIR = DATA_DIR / "receipt"
HISTORY_DIR = RECEIPT_DIR / "history"

HISTORY_COLUMNS = [
    "date", "symbol", "exchange", "on_warrant",
    "effective_forecast", "warrant_units", "multiplier", "deliverable",
]

# 原始单位展示标签（仅展示用，换算规则见 to_lots）
UNIT_LABEL = {"CZCE": "张", "SHFE": "吨", "INE": "吨", "DCE": "手", "GFEX": "张"}

BACKFILL_START = "20210101"  # 已实测米筐仓单接口可取 2021 年初至今


def underlying_of(symbol: str) -> str:
    """合约代码 → 品种代码：cu2610.SHF→CU，MA611.CZC→MA，rb2701→RB。"""
    base = str(symbol).split(".")[0].upper()
    return re.sub(r"\d+$", "", base)


def _num(value):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def to_lots(on_warrant, exchange, warrant_units=None, multiplier=None):
    """注册仓单量 → 折手（约等于期货持仓口径的手数）；无法换算返回 None。

    CZCE/GFEX：张 × warrant_units（张→手）；SHFE/INE：吨 ÷ 合约乘数；DCE：原值即手。
    """
    value = _num(on_warrant)
    if value is None:
        return None
    wu = _num(warrant_units)
    if wu:
        return value * wu
    if str(exchange or "").upper() in ("SHFE", "INE"):
        mult = _num(multiplier)
        return value / mult if mult else None
    return value


def ef_to_lots(effective_forecast, warrant_units=None):
    """有效预报 → 折手（仅 CZCE 有该字段；张 × warrant_units）。"""
    value = _num(effective_forecast)
    if value is None:
        return None
    wu = _num(warrant_units)
    return value * wu if wu else None


def percentile(values, current):
    """current 在 values 中的经验分位（≤ 占比）；样本 <10 返回 None。"""
    sample = [_num(v) for v in values]
    sample = [v for v in sample if v is not None]
    current = _num(current)
    if current is None or len(sample) < 10:
        return None
    return round(sum(v <= current for v in sample) / len(sample), 2)


def load_history(path) -> pd.DataFrame:
    """读取单品种 history CSV，附加 lots/ef_lots 计算列，按日期升序。"""
    df = pd.read_csv(path, dtype={"date": str})
    df["date"] = df["date"].astype(str).str.replace("-", "").str[:8]
    df = _attach_lots(df)
    return df.sort_values("date").reset_index(drop=True)


def _attach_lots(df: pd.DataFrame) -> pd.DataFrame:
    """为原始 history 行附加 lots/ef_lots 计算列（已有则跳过）。"""
    if "lots" in df.columns and "ef_lots" in df.columns:
        return df
    lots, ef_lots = [], []
    for row in df.itertuples():
        lots.append(to_lots(row.on_warrant, row.exchange, row.warrant_units, row.multiplier))
        ef_lots.append(ef_to_lots(row.effective_forecast, row.warrant_units))
    df = df.copy()
    df["lots"] = lots
    df["ef_lots"] = ef_lots
    return df


def evaluate_signals(metrics: dict) -> list:
    """规则信号（确定性输出；tone: long=偏多 / short=偏空 / warn=提示）。

    R1 供应压力：仓单处历史高位（分位≥80%）且近 5 日继续增加 → 偏空
    R2 供应偏紧：仓单处历史低位（分位≤20%）且近 5 日继续减少（注销）→ 偏多
    R3 高位套保：主力价近一年分位≥70% 且仓单 5 日增幅≥10% → 偏空
    R4 注销潮：20 日累计减幅≥20% → 偏多
    R5 虚实比警示：仓单折手/主力持仓 <5% → 可交割货源偏紧（逼仓风险关注）
    """
    out = []
    pct_all = metrics.get("pct_all")
    chg5 = metrics.get("change_5d")
    chg5_pct = metrics.get("change_5d_pct")
    chg20_pct = metrics.get("change_20d_pct")
    price_pct = metrics.get("price_pct_1y")
    vratio = metrics.get("virtual_ratio")

    if pct_all is not None and pct_all >= 0.8 and chg5 is not None and chg5 > 0:
        out.append({"code": "R1", "tone": "short", "title": "供应压力",
                    "detail": f"仓单处历史 {pct_all:.0%} 分位且近5日增加 {chg5:+,.0f} 手："
                              f"可交割货源充裕，价格上方承压"})
    if pct_all is not None and pct_all <= 0.2 and chg5 is not None and chg5 < 0:
        out.append({"code": "R2", "tone": "long", "title": "供应偏紧",
                    "detail": f"仓单处历史 {pct_all:.0%} 分位且近5日减少 {chg5:+,.0f} 手："
                              f"可交割货源偏紧，下方有支撑"})
    if (price_pct is not None and price_pct >= 0.7
            and chg5_pct is not None and chg5_pct >= 0.10):
        out.append({"code": "R3", "tone": "short", "title": "高位套保",
                    "detail": f"价格近一年 {price_pct:.0%} 分位 + 仓单5日增幅 {chg5_pct:+.1%}："
                              f"高价诱发套保注册，警惕逢高回落"})
    if chg20_pct is not None and chg20_pct <= -0.20:
        out.append({"code": "R4", "tone": "long", "title": "注销潮",
                    "detail": f"仓单20日累计 {chg20_pct:+.1%}："
                              f"货物回流现货，盘面可交割压力快速消退"})
    if vratio is not None and vratio < 0.05:
        out.append({"code": "R5", "tone": "warn", "title": "虚实比警示",
                    "detail": f"仓单折手仅为主力持仓的 {vratio:.1%}："
                              f"可交割货源相对持仓偏紧，留意挤仓风险"})
    return out


def _pct_change(last, base):
    last, base = _num(last), _num(base)
    if last is None or not base:
        return None
    return (last - base) / base


def compute_symbol_metrics(hist: pd.DataFrame, price: pd.DataFrame | None = None) -> dict:
    """单品种指标包：最新值、增减、分位、虚实比、价格联动。"""
    m = {"available": False}
    if hist is None or hist.empty:
        return m
    hist = _attach_lots(hist)
    hist = hist.dropna(subset=["lots"]).reset_index(drop=True)
    if hist.empty:
        return m
    lots = hist["lots"].tolist()
    last = hist.iloc[-1]
    lots_now = float(last["lots"])
    m.update({
        "available": True,
        "date": str(last["date"]),
        "exchange": str(last.get("exchange") or ""),
        "on_warrant": _num(last.get("on_warrant")),
        "lots": lots_now,
        "effective_forecast": _num(last.get("effective_forecast")),
        "ef_lots": _num(last.get("ef_lots")),
        "deliverable": _num(last.get("deliverable")),
        "change_1d": lots_now - lots[-2] if len(lots) >= 2 else None,
        "change_5d": lots_now - lots[-6] if len(lots) >= 6 else None,
        "change_20d": lots_now - lots[-21] if len(lots) >= 21 else None,
        "change_5d_pct": _pct_change(lots_now, lots[-6]) if len(lots) >= 6 else None,
        "change_20d_pct": _pct_change(lots_now, lots[-21]) if len(lots) >= 21 else None,
        "pct_all": percentile(lots, lots_now),
        "pct_1y": percentile(lots[-250:], lots_now),
    })
    if m["ef_lots"] is not None:
        m["total_lots"] = lots_now + m["ef_lots"]

    # 主力合约价格/持仓联动（虚实比、价格分位）
    if price is not None and not price.empty:
        price = price.sort_index()
        closes = [_num(v) for v in price["close"].tolist()]
        closes = [c for c in closes if c is not None]
        if closes:
            m["close"] = closes[-1]
            m["price_pct_1y"] = percentile(closes[-250:], closes[-1])
        ccl = _num(price["ccl"].iloc[-1]) if "ccl" in price.columns else None
        if ccl and ccl > 0:
            m["oi"] = ccl
            m["virtual_ratio"] = lots_now / ccl
    m["signals"] = evaluate_signals(m)
    return m


def build_series(hist: pd.DataFrame, price: pd.DataFrame | None = None,
                 daily_n=250, change_n=60, seasonal_years=5, seasonal_step=3) -> dict:
    """图表序列：日线双轴 / 日增减柱 / 分年季节性 / 仓单+有效预报。数值已取整控体积。"""
    hist = _attach_lots(hist)
    hist = hist.dropna(subset=["lots"]).reset_index(drop=True)
    dates = hist["date"].tolist()
    lots = hist["lots"].tolist()

    closes = [None] * len(dates)
    if price is not None and not price.empty:
        cmap = {t.strftime("%Y%m%d"): _num(c)
                for t, c in zip(price.index, price["close"].tolist())}
        closes = [cmap.get(d) for d in dates]
    daily = [[d, round(v, 2), (round(c, 4) if c is not None else None)]
             for d, v, c in zip(dates, lots, closes)][-daily_n:]

    deltas = [None] + [b - a for a, b in zip(lots, lots[1:])]
    changes = [[d, round(v, 1)] for d, v in zip(dates, deltas) if v is not None][-change_n:]

    doy = pd.to_datetime(dates, format="%Y%m%d").dayofyear.tolist()
    years = pd.to_datetime(dates, format="%Y%m%d").year.tolist()
    latest_year = max(years)
    seasonal = {}
    for year in sorted(set(years)):
        if year < latest_year - seasonal_years + 1:
            continue
        pts = [[dy, round(v, 1)] for dy, v, y in zip(doy, lots, years) if y == year]
        if year != latest_year and len(pts) < 60:
            continue  # 早年数据不全的年份不画，避免误导
        seasonal[str(year)] = pts[::seasonal_step] if year != latest_year else pts

    ef = None
    if hist["ef_lots"].notna().any():
        ef = [[d, round(v, 1), round(_num(e) or 0.0, 1)]
              for d, v, e in zip(dates, lots, hist["ef_lots"].tolist())][-daily_n:]

    return {"daily": daily, "changes": changes, "seasonal": seasonal, "ef": ef}
