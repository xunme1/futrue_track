# -*- coding: utf-8 -*-
"""
iFinD 数据探测脚本（调试工具）
目的：不写策略，先摸清三件事——
  1. 期货主连代码格式（rb9999.SHF 之类是否可取）
  2. 持仓量指标的确切名称（oi / openInterest / position ...）
  3. 南华指数 NHCI.SL 是否可取 + 周线 period:W 是否可用
运行：.venv/Scripts/python tools/probe_ifind.py
"""
from pathlib import Path

import yaml
from iFinDPy import THS_HistoryQuotes, THS_iFinDLogin, THS_iFinDLogout

with open(Path(__file__).resolve().parents[1] / "config" / "config.yaml", encoding="utf-8") as f:
    CFG = yaml.safe_load(f)

ret = THS_iFinDLogin(CFG["account"]["username"], CFG["account"]["password"])
print(f"[登录] 返回码 {ret}（0 或 -201 = 成功）")
if ret not in (0, -201):
    raise SystemExit("登录失败，终止探测")

def probe(name, code, indicators, params, start, end):
    """探测一个代码+指标组合，打印成败与返回结构"""
    try:
        r = THS_HistoryQuotes(code, indicators, params, start, end)
    except Exception as e:
        print(f"[{name}] 调用异常: {e}")
        return None
    ec = getattr(r, "errorcode", "?")
    em = getattr(r, "errmsg", "")
    if ec != 0:
        print(f"[{name}] ✗ errorcode={ec} errmsg={em}")
        return None
    df = r.data
    print(f"[{name}] ✓ {len(df)} 行, 列={list(df.columns)}")
    if len(df):
        print(f"        首行: {df.iloc[0].to_dict()}")
        print(f"        末行: {df.iloc[-1].to_dict()}")
    return df

START, END = "2026-06-01", "2026-07-27"

print("\n===== 1. 期货主连代码格式探测 =====")
df = None
for code in ["rb9999.SHF", "RB9999.SHF", "rb2510.SHF"]:
    df = probe(f"期货 {code}", code, "open;high;low;close;volume",
               "period:D,fill:Blank", START, END)
    if df is not None:
        break

print("\n===== 2. 持仓量指标名探测（在第一个能用的合约上试） =====")
if df is not None:
    for oi_name in ["oi", "openInterest", "position", "oi_futures", "holdvol"]:
        r = probe(f"持仓量:{oi_name}", code, f"close;{oi_name}",
                  "period:D,fill:Blank", START, END)
        if r is not None:
            break

print("\n===== 3. 南华指数 + 周线探测 =====")
probe("南华商品指数 NHCI.SL 日线", "NHCI.SL", "open;high;low;close",
      "period:D,fill:Blank", START, END)
probe("南华商品指数 NHCI.SL 周线", "NHCI.SL", "open;high;low;close",
      "period:W,fill:Blank", START, END)
if df is not None:
    probe(f"期货 {code} 周线", code, "open;high;low;close",
          "period:W,fill:Blank", START, END)

THS_iFinDLogout()
print("\n[完成] 探测结束，已登出")
