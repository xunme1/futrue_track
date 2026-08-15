# -*- coding: utf-8 -*-
"""
策略：ZXGL 周线过滤 + XDD 强弱对比 + M头/W底形态 + OPI 资金确认
（文华麦语言策略的 Python 复刻，KK/PP/DD/EE 已与文华打印值逐点核对一致）

compute() 为纯函数：输入日线/周线/指数 DataFrame 与参数字典，输出含全部信号的 DataFrame。
新增策略时仿照本模块新建文件并实现同名 compute() 即可。
"""
import math

import pandas as pd

from ..core.mylang import (every, hhv, hhvbars, hv, intpart, llv, llvbars, lv, ma,
                           ref, sma_cn)
from .weekly_permissions import current_weekly_permissions

STRATEGY_NAME = "zxgl_xdd"
DESCRIPTION = "周线定方向 → M头/W底突破 → 盘整过滤 → 南华指数强弱 → OPI资金确认"


def compute(fut_d, fut_w, idx_d, p):
    """输入：期货日线/周线、指数日线、策略参数 p → 输出：含全部信号的 DataFrame"""
    df = fut_d.copy()
    C, O, H, L, OPI = df["close"], df["open"], df["high"], df["low"], df["ccl"]
    G1, G2 = p["G1"], p["G2"]
    LB = p["lookback_shape"]        # 30
    SH = p["shoulder_min_bars"]     # 8

    # ---------- 1. 周线过滤（ZXGL）----------
    # 麦语言 #IMPORT[WEEK,1,ZXGL] 引用当周实时周线；本地按当前已完成日K聚合，
    # 避免将周五的最终数据倒灌给同周较早日线。
    df["AA1"], df["ZZ1"], df["TT1"] = current_weekly_permissions(df, fut_w, p)

    # ---------- 2. 本品种 7 日位置 / 盘整 ----------
    HH7, LL7 = hhv(H, p["rs_window"]), llv(L, p["rs_window"])
    MA7 = ma(C, p["panzheng_range"])
    df["ZD"] = (HH7 + LL7) / 2
    df["K1"] = (C - LL7) / llv(C, p["rs_window"])      # 相对7日低点涨幅
    df["K2"] = (C - HH7) / hhv(C, p["rs_window"])      # 相对7日高点跌幅（≤0）
    df["PANZHENG"] = (HH7 - LL7) / MA7 < p["panzheng_threshold"]
    df["JC1"] = ((C - LL7) / ((HH7 - LL7) / 5).replace(0, float("nan"))).apply(intpart) + 1   # 7日区间5分层
    df["JC2"] = ((C - L) / ((H - L) / 5).replace(0, float("nan"))).apply(intpart) + 1  # 当日5分层

    # ---------- 3. 相对南华指数强弱（XDD 复刻）----------
    ix = idx_d.reindex(df.index).ffill()
    iH7, iL7 = hhv(ix["high"], p["rs_window"]), llv(ix["low"], p["rs_window"])
    J1 = (ix["close"] - iL7) / llv(ix["close"], p["rs_window"])
    J2 = (ix["close"] - iH7) / hhv(ix["close"], p["rs_window"])
    JC1W = ((ix["close"] - iL7) / ((iH7 - iL7) / 5).replace(0, float("nan"))).apply(intpart) + 1
    JC2R = ((ix["close"] - ix["low"]) / ((ix["high"] - ix["low"]) / 5).replace(0, float("nan"))).apply(intpart) + 1
    up = C > df["ZD"]
    dn = C < df["ZD"]
    df["PQ"] = (up & (df["K1"] > p["rs_strong_up"] * J1)) | (dn & (df["K2"] > p["rs_strong_dn"] * J2))  # 强于指数
    df["PR"] = (up & (df["K1"] < p["rs_weak_up"] * J1)) | (dn & (df["K2"] < p["rs_weak_dn"] * J2))      # 弱于指数
    df["NN"] = df["JC1"] - JC1W      # 7日位置差（红字）
    df["GG"] = df["JC2"] - JC2R      # 当日力度差（绿字）

    # ---------- 4. M头做空（HN30/HN8 形态）----------
    HN30 = hhvbars(H, LB)            # 距30日最高点的根数
    peak = (H < ref(H, 1)) & (ref(H, 1) > ref(H, 2)) & (ref(HN30, 1) >= SH)  # 右肩：局部高点且距顶≥8根
    HN8, last = [], float("nan")     # BARSLAST(peak)+1
    for i, pk in enumerate(peak):
        last = 1 if pk else (last + 1 if not math.isnan(last) else float("nan"))
        HN8.append(last)
    df["HN8"] = HN8
    LLv = [ref(L, int(n)).iloc[i] if not math.isnan(n) and i - int(n) >= 0 else float("nan") for i, n in enumerate(HN8)]
    df["NECK_S"] = LLv               # M头颈线（右肩处最低价）
    KK, PP = [], []                  # 动态窗口 LLV/HHV(L,HN30+1)
    for i, n in enumerate(HN30):
        if math.isnan(n):
            KK.append(float("nan")); PP.append(float("nan")); continue
        lo = L.iloc[max(0, i - int(n)): i + 1].min()
        hi = H.iloc[max(0, i - int(n)): i + 1].max()
        KK.append(lo + (hi - lo) / G1)   # 开空阈值 = 区间 1/G1 分位（G1=4 → 25%）
        PP.append(lo + (hi - lo) / G2)   # 平空阈值 = 区间 1/G2 分位（G2=3 → 33%）
    df["KK"], df["PP"] = KK, PP
    raw_SK = df["TT1"] & df["AA1"] & (C < pd.concat([df["KK"], df["NECK_S"]], axis=1).min(axis=1)) & (~df["PANZHENG"])
    raw_BP = C > df["PP"]

    # ---------- 5. W底做多（镜像）----------
    LN30 = llvbars(L, LB)
    trough = (L > ref(L, 1)) & (ref(L, 1) < ref(L, 2)) & (ref(LN30, 1) >= SH)
    LN8, last = [], float("nan")
    for i, t_ in enumerate(trough):
        last = 1 if t_ else (last + 1 if not math.isnan(last) else float("nan"))
        LN8.append(last)
    HHv = [ref(H, int(n)).iloc[i] if not math.isnan(n) and i - int(n) >= 0 else float("nan") for i, n in enumerate(LN8)]
    df["NECK_B"] = HHv
    DD, EE = [], []
    for i, n in enumerate(LN30):
        if math.isnan(n):
            DD.append(float("nan")); EE.append(float("nan")); continue
        lo = L.iloc[max(0, i - int(n)): i + 1].min()
        hi = H.iloc[max(0, i - int(n)): i + 1].max()
        DD.append(hi - (hi - lo) / G1)   # 开多阈值 = 区间 (1-1/G1) 分位
        EE.append(hi - (hi - lo) / G2)   # 平多阈值 = 区间 (1-1/G2) 分位
    df["DD"], df["EE"] = DD, EE
    raw_BK = df["TT1"] & df["ZZ1"] & (C > pd.concat([df["DD"], df["NECK_B"]], axis=1).max(axis=1)) & (~df["PANZHENG"])
    raw_SP = C < df["EE"]

    # ---------- 6. AUTOFILTER：开平仓状态机（信号严格交替）----------
    sig = []
    pos = 0  # 0=空仓 1=持多 -1=持空
    for i in range(len(df)):
        s = ""
        if pos == 0:
            if bool(raw_BK.iloc[i]): s, pos = "BK", 1
            elif bool(raw_SK.iloc[i]): s, pos = "SK", -1
        elif pos == 1:
            if bool(raw_SP.iloc[i]): s, pos = "SP", 0
        else:
            if bool(raw_BP.iloc[i]): s, pos = "BP", 0
        sig.append(s)
    df["SIGNAL"] = sig
    df["POS"] = pd.Series(sig, index=df.index).replace("", float("nan")).map({"BK": 1, "SK": -1, "SP": 0, "BP": 0}).ffill().fillna(0).astype(int)

    # ---------- 7. OPI 资金信号（第三部分）----------
    RSV = (C - llv(L, 9)) / (hhv(H, 9) - llv(L, 9)) * 100
    Kk = sma_cn(RSV.fillna(50), 3, 1)
    Dd = sma_cn(Kk, 3, 1)
    XX = (OPI >= (1 + p["opi_surge_pct"]) * ref(OPI, 1)) & (C > (1 + p["price_confirm_pct"]) * ref(C, 1))
    # 麦语言 HV/LV 不含当前 K；不可用 HHV/LLV 代替，否则 C > HHV(C, 2) 永不成立。
    II = (OPI >= (1 + p["opi_surge_pct"]) * ref(OPI, 2)) & (ref(OPI, 1) > ref(OPI, 2)) & (C > hv(C, 2)) & (C > 1.015 * L)
    ZC = ((OPI >= (1 + p["opi_super_pct"]) * ref(OPI, 1)) | ((OPI - ref(OPI, 1)) > p["opi_super_abs"])) & (C > (1 + p["price_confirm_pct"]) * ref(C, 1))
    KC = (XX | II) & ((Kk > Dd) | (Dd - Kk < 8)) & (Kk < 85)
    first_kc = ref(every(KC == 0, 4), 1).astype("boolean").fillna(False)
    df["SB"] = (KC & first_kc) | ZC                                                   # 多头增仓（近5根首次）
    body_hi, body_lo = pd.concat([O, C], axis=1).max(axis=1), pd.concat([O, C], axis=1).min(axis=1)
    AA_ = (C > hv(body_hi, 2) - (hv(body_hi, 2) - lv(body_lo, 2)) / 3) & (ref(C, 2) < ref(O, 2)) & (C - L > 0.015 * L)
    BB_ = (OPI - ref(OPI, 1)).abs() + (ref(OPI, 2) - ref(OPI, 1)).abs() > p["wash_pct"] * pd.concat([ref(OPI, 1), ref(OPI, 2)], axis=1).min(axis=1)
    CC_ = AA_ & BB_
    first_cc = ref(every(CC_ == 0, 3), 1).astype("boolean").fillna(False)
    df["DSB"] = CC_ & first_cc & (C < hhv(C, 10))                                    # 洗盘后站起来
    AAE = (C < lv(body_lo, 2) + (hv(body_hi, 2) - lv(body_lo, 2)) / 3) & (ref(C, 2) > ref(O, 2)) & (hhv(body_hi, 3) - llv(body_lo, 3) > 0.02 * L) & (C < O)
    BBE = BB_
    KKE = (OPI - ref(OPI, 1)).abs() + (ref(OPI, 2) - ref(OPI, 1)).abs() + (ref(OPI, 2) - ref(OPI, 3)).abs() > p["wash_hard_pct"] * pd.concat([ref(OPI, 1), ref(OPI, 2)], axis=1).min(axis=1)
    CCE = AAE & (BBE | KKE)
    first_cce = ref(every(CCE == 0, 3), 1).astype("boolean").fillna(False)
    df["DSBE"] = CCE & first_cce                                                       # 反击扑灭
    df["DSBE_NOTE"] = ""
    df.loc[df["DSBE"] & (OPI < 0.98 * ref(OPI, 1)), "DSBE_NOTE"] = "减仓耗散筹码变轻"
    df.loc[df["DSBE"] & ((OPI - ref(OPI, 1)) > 0.02 * OPI), "DSBE_NOTE"] = "增仓能量增强"
    return df
