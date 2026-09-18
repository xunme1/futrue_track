# -*- coding: utf-8 -*-
"""席位追踪共享层：品种/分组常量、繁微 API 封装、组内净持仓计算。

移植自 seat_track 项目（scripts/fino_api.py + plot_seat_map.py 的数据部分），
口径完全一致：交易所会员持仓前 20 名已披露口径、品种全合约合计、
公司级名称变体合并、组内全员在榜过滤。
密钥只从环境变量读取（FINO_APPKEY / FINO_APPSECRET；服务器经 /etc/future-track.env 注入）。
"""
from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import requests

from backend.core.config import DATA_DIR

SEAT_DIR = DATA_DIR / "seat"

FINO_URL = "https://www.finoview.com.cn/autoApi/foreign/market/get_member_rank"
FINO_HEADERS = {
    "Content-Type": "application/json;charset=UTF-8",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/139.0.0.0 Safari/537.36"
    ),
}

SYMBOLS = (
    "CU AL ZN PB NI SN AU AG RB HC I J JM FG SA MA TA PP L V EG PF BU FU PG "
    "SC A M Y P RM OI CF SR C JD LH AP SI LC"
).split()

FIELDS = ["volume", "long", "short"]
SLEEP_SEC = 1.15  # 接口限流 1 次/秒

GROUPS = [
    ("Q", "高盛", ["高盛", "乾坤"]),
    ("Z", "主力机构", ["国泰君安", "中信期货", "永安期货"]),
    ("R", "散户", ["东方财富", "徽商期货"]),
]

# 会员实体（公司级）：同一公司在不同品种可能有名称变体（如「国泰君安」/「国泰君安期货」），
# 合并计为一家会员参与「全员在榜」判定。
MEMBER_FAMILIES = {
    "Q": [["高盛期货"]],
    "Z": [["国泰君安", "国泰君安期货"], ["中信期货"], ["永安期货"]],
    "R": [["东方财富", "东方财富期货"], ["徽商期货"]],
}

CN_NAME = {
    "CU": "铜", "AL": "铝", "ZN": "锌", "PB": "铅", "NI": "镍", "SN": "锡",
    "AU": "黄金", "AG": "白银", "RB": "螺纹钢", "HC": "热卷", "I": "铁矿石",
    "J": "焦炭", "JM": "焦煤", "FG": "玻璃", "SA": "纯碱", "MA": "甲醇",
    "TA": "PTA", "PP": "聚丙烯", "L": "塑料", "V": "PVC", "EG": "乙二醇",
    "PF": "短纤", "BU": "沥青", "FU": "燃油", "PG": "液化气", "SC": "原油",
    "A": "豆一", "M": "豆粕", "Y": "豆油", "P": "棕榈油", "RM": "菜粕",
    "OI": "菜油", "CF": "棉花", "SR": "白糖", "C": "玉米", "JD": "鸡蛋",
    "LH": "生猪", "AP": "苹果", "SI": "工业硅", "LC": "碳酸锂",
}


def get_member_rank(symbol, start_date, end_date, fields=FIELDS):
    """查询会员持仓排名。接口成功返回 code=1（不是 200），判断成功看 data.data 是否非空。"""
    payload = {
        "symbol": symbol,
        "start_date": start_date,
        "end_date": end_date,
        "fields": fields,
        "appkey": os.environ["FINO_APPKEY"],
        "appsecret": os.environ["FINO_APPSECRET"],
    }
    resp = requests.post(FINO_URL, json=payload, headers=FINO_HEADERS, timeout=15)
    return resp.json()


def load_group_daily(df, keywords):
    """返回 (symbol, trade_date) 级别的组内净持仓表。"""
    mask = pd.Series(False, index=df.index)
    for kw in keywords:
        mask |= df["member_name"].str.contains(kw, na=False)
    sub = df[mask]
    g = sub.groupby(["symbol", "trade_date"], as_index=False)[["total_long", "total_short"]].sum()
    g["net"] = g["total_long"] - g["total_short"]
    return g


def complete_symbols(df, families, date):
    """返回某日组内全部会员公司均在榜（CSV 中有数据行）的品种集合。"""
    day = df[df["trade_date"] == date]
    common = None
    for fam in families:
        syms = set(day.loc[day["member_name"].isin(fam), "symbol"])
        common = syms if common is None else (common & syms)
    return common or set()


def build_rows(g, trade_date, prev_date, with_pct=False):
    """对每个品种计算今/昨净持仓、方向与动作；with_pct 附带近 20 交易日分位。"""
    rows = []
    for sym, gs in g.groupby("symbol"):
        gs = gs.sort_values("trade_date")
        denom = gs["net"].abs().max()
        if not denom or denom == 0:
            continue
        today = gs.loc[gs["trade_date"] == trade_date, "net"]
        prev = gs.loc[gs["trade_date"] == prev_date, "net"]
        if today.empty:
            continue
        net_t = float(today.iloc[0])
        net_p = float(prev.iloc[0]) if not prev.empty else 0.0
        if net_t == 0:
            continue
        if net_t > 0:
            direction = "偏多"
            action = "翻多" if net_p <= 0 else ("加多" if abs(net_t) >= abs(net_p) else "减多")
        else:
            direction = "偏空"
            action = "翻空" if net_p >= 0 else ("加空" if abs(net_t) >= abs(net_p) else "减空")
        row = {"symbol": sym, "net_t": net_t, "net_p": net_p,
               "x_t": max(-1.0, min(1.0, net_t / denom)),
               "x_p": max(-1.0, min(1.0, net_p / denom)),
               "direction": direction, "action": action}
        if with_pct:
            row["pos_pct_20d"] = float((gs["net"] <= net_t).mean())
        rows.append(row)
    rows.sort(key=lambda r: abs(r["net_t"]), reverse=True)
    return rows


def read_seat_csv(path):
    df = pd.read_csv(path, dtype={"trade_date": str})
    df["trade_date"] = df["trade_date"].astype(str)
    return df


def prev_trade_date(df, trade_date):
    """从 CSV 缓存内推导前一交易日；首个交易日返回 None。"""
    dates = sorted(df["trade_date"].unique())
    if trade_date not in dates:
        raise ValueError(f"缓存中没有 {trade_date} 的数据（范围 {dates[0]} ~ {dates[-1]}）")
    idx = dates.index(trade_date)
    return dates[idx - 1] if idx > 0 else None


def latest_csv_date(directory=None):
    """data/seat 下最新 seat_data_*.csv 的日期（YYYYMMDD）。"""
    root = Path(directory) if directory else SEAT_DIR
    files = sorted(root.glob("seat_data_*.csv"))
    if not files:
        return None
    return files[-1].stem.removeprefix("seat_data_")
