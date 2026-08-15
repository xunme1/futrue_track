# -*- coding: utf-8 -*-
"""4-hour variant of the ZXGL/XDD strategy.

It keeps the original M/W channel and OPI formulas, using the current weekly
state built from completed 4-hour bars as its direction filter. Relative
strength to NHCI is intentionally absent: BAR_COLOR is only the current bar's
price direction.
"""
import math

import pandas as pd

from ..core.mylang import every, hhv, hhvbars, hv, intpart, llv, llvbars, lv, ma, ref, sma_cn
from .weekly_permissions import current_weekly_permissions

STRATEGY_NAME = "zxgl_4h"
DESCRIPTION = "周线定方向 → 4小时 M头/W底突破 → 盘整过滤 → OPI资金确认"


def _weekly_permissions(df, weekly, p):
    """Compatibility wrapper for the current-week Wenhua permission."""
    return current_weekly_permissions(df, weekly, p)


def compute(fut_4h, fut_weekly, p):
    """Compute 4-hour signals from completed bars and current-week filters."""
    df = fut_4h.copy()
    C, O, H, L, OPI = df["close"], df["open"], df["high"], df["low"], df["ccl"]
    G1, G2 = p["G1"], p["G2"]
    LB, SH = p["lookback_shape"], p["shoulder_min_bars"]

    # The 4-hour consolidation gate is deliberately independent from the
    # daily/weekly 3% threshold.  A smooth intraday trend often stays within
    # 3% over seven bars, so use the explicitly configured 4-hour threshold.
    panzheng_threshold = p.get("panzheng_threshold_4h", 0.005)
    df["AA1"], df["ZZ1"], df["TT1"] = _weekly_permissions(df, fut_weekly, p)
    HH7, LL7 = hhv(H, p["rs_window"]), llv(L, p["rs_window"])
    MA7 = ma(C, p["panzheng_range"])
    df["ZD"] = (HH7 + LL7) / 2
    df["PANZHENG"] = (HH7 - LL7) / MA7 < panzheng_threshold
    df["BAR_COLOR"] = pd.Series("gray", index=df.index)
    df.loc[C > O, "BAR_COLOR"] = "red"
    df.loc[C < O, "BAR_COLOR"] = "blue"

    # M top / short channel.
    HN30 = hhvbars(H, LB)
    peak = (H < ref(H, 1)) & (ref(H, 1) > ref(H, 2)) & (ref(HN30, 1) >= SH)
    hn8, last = [], float("nan")
    for is_peak in peak:
        last = 1 if is_peak else (last + 1 if not math.isnan(last) else float("nan"))
        hn8.append(last)
    neck_s = [ref(L, int(n)).iloc[i] if not math.isnan(n) and i - int(n) >= 0 else float("nan")
              for i, n in enumerate(hn8)]
    kk, pp = [], []
    for i, n in enumerate(HN30):
        if math.isnan(n):
            kk.append(float("nan")); pp.append(float("nan")); continue
        lo, hi = L.iloc[max(0, i - int(n)):i + 1].min(), H.iloc[max(0, i - int(n)):i + 1].max()
        kk.append(lo + (hi - lo) / G1)
        pp.append(lo + (hi - lo) / G2)
    df["KK"], df["PP"] = kk, pp
    raw_sk = df["TT1"] & df["AA1"] & (C < pd.concat([df["KK"], pd.Series(neck_s, index=df.index)], axis=1).min(axis=1)) & ~df["PANZHENG"]
    raw_bp = C > df["PP"]

    # W bottom / long channel.
    LN30 = llvbars(L, LB)
    trough = (L > ref(L, 1)) & (ref(L, 1) < ref(L, 2)) & (ref(LN30, 1) >= SH)
    ln8, last = [], float("nan")
    for is_trough in trough:
        last = 1 if is_trough else (last + 1 if not math.isnan(last) else float("nan"))
        ln8.append(last)
    neck_b = [ref(H, int(n)).iloc[i] if not math.isnan(n) and i - int(n) >= 0 else float("nan")
              for i, n in enumerate(ln8)]
    dd, ee = [], []
    for i, n in enumerate(LN30):
        if math.isnan(n):
            dd.append(float("nan")); ee.append(float("nan")); continue
        lo, hi = L.iloc[max(0, i - int(n)):i + 1].min(), H.iloc[max(0, i - int(n)):i + 1].max()
        dd.append(hi - (hi - lo) / G1)
        ee.append(hi - (hi - lo) / G2)
    df["DD"], df["EE"] = dd, ee
    raw_bk = df["TT1"] & df["ZZ1"] & (C > pd.concat([df["DD"], pd.Series(neck_b, index=df.index)], axis=1).max(axis=1)) & ~df["PANZHENG"]
    raw_sp = C < df["EE"]

    signals, pos = [], 0
    for i in range(len(df)):
        signal = ""
        if pos == 0:
            if bool(raw_bk.iloc[i]): signal, pos = "BK", 1
            elif bool(raw_sk.iloc[i]): signal, pos = "SK", -1
        elif pos == 1 and bool(raw_sp.iloc[i]):
            signal, pos = "SP", 0
        elif pos == -1 and bool(raw_bp.iloc[i]):
            signal, pos = "BP", 0
        signals.append(signal)
    df["SIGNAL"] = signals
    df["POS"] = pd.Series(signals, index=df.index).replace("", float("nan")).map(
        {"BK": 1, "SK": -1, "SP": 0, "BP": 0}
    ).ffill().fillna(0).astype(int)

    # OPI funding signals: unchanged from the verified daily implementation.
    rsv = (C - llv(L, 9)) / (hhv(H, 9) - llv(L, 9)) * 100
    k, d = sma_cn(rsv.fillna(50), 3, 1), sma_cn(sma_cn(rsv.fillna(50), 3, 1), 3, 1)
    xx = (OPI >= (1 + p["opi_surge_pct"]) * ref(OPI, 1)) & (C > (1 + p["price_confirm_pct"]) * ref(C, 1))
    ii = (OPI >= (1 + p["opi_surge_pct"]) * ref(OPI, 2)) & (ref(OPI, 1) > ref(OPI, 2)) & (C > hv(C, 2)) & (C > 1.015 * L)
    zc = ((OPI >= (1 + p["opi_super_pct"]) * ref(OPI, 1)) | ((OPI - ref(OPI, 1)) > p["opi_super_abs"])) & (C > (1 + p["price_confirm_pct"]) * ref(C, 1))
    kc = (xx | ii) & ((k > d) | (d - k < 8)) & (k < 85)
    df["SB"] = (kc & ref(every(kc == 0, 4), 1).astype("boolean").fillna(False)) | zc
    body_hi, body_lo = pd.concat([O, C], axis=1).max(axis=1), pd.concat([O, C], axis=1).min(axis=1)
    aa = (C > hv(body_hi, 2) - (hv(body_hi, 2) - lv(body_lo, 2)) / 3) & (ref(C, 2) < ref(O, 2)) & (C - L > 0.015 * L)
    bb = (OPI - ref(OPI, 1)).abs() + (ref(OPI, 2) - ref(OPI, 1)).abs() > p["wash_pct"] * pd.concat([ref(OPI, 1), ref(OPI, 2)], axis=1).min(axis=1)
    cc = aa & bb
    df["DSB"] = cc & ref(every(cc == 0, 3), 1).astype("boolean").fillna(False) & (C < hhv(C, 10))
    aae = (C < lv(body_lo, 2) + (hv(body_hi, 2) - lv(body_lo, 2)) / 3) & (ref(C, 2) > ref(O, 2)) & (hhv(body_hi, 3) - llv(body_lo, 3) > 0.02 * L) & (C < O)
    kke = (OPI - ref(OPI, 1)).abs() + (ref(OPI, 2) - ref(OPI, 1)).abs() + (ref(OPI, 2) - ref(OPI, 3)).abs() > p["wash_hard_pct"] * pd.concat([ref(OPI, 1), ref(OPI, 2)], axis=1).min(axis=1)
    dsbe = aae & (bb | kke)
    df["DSBE"] = dsbe & ref(every(dsbe == 0, 3), 1).astype("boolean").fillna(False)
    df["DSBE_NOTE"] = ""
    df.loc[df["DSBE"] & (OPI < 0.98 * ref(OPI, 1)), "DSBE_NOTE"] = "减仓耗散筹码变轻"
    df.loc[df["DSBE"] & ((OPI - ref(OPI, 1)) > 0.02 * OPI), "DSBE_NOTE"] = "增仓能量增强"
    return df
