# -*- coding: utf-8 -*-
"""
麦语言原语库：文华麦语言函数在 pandas 上的等价实现
所有函数语义与文华 WT8 保持一致（已对 HHVBARS/LLVBARS 与文华打印值做过数值核对）
"""
import math

import pandas as pd


def hhv(s, n):  return s.rolling(n, min_periods=n).max()      # HHV(X,N) 最高值
def llv(s, n):  return s.rolling(n, min_periods=n).min()      # LLV(X,N) 最低值
def ma(s, n):   return s.rolling(n, min_periods=n).mean()     # MA(X,N) 均线
def ref(s, n):  return s.shift(n)                             # REF(X,N) 前N根值


def hhvbars(s, n):
    """HHVBARS: 最近 n 周期内最高值距当前的根数（当前=0，并列取最近一次）"""
    v = s.values
    out = []
    for i in range(len(v)):
        if i < n - 1:
            out.append(float("nan")); continue
        w = v[i - n + 1: i + 1]
        mx = w.max()
        pos = max(j for j, x in enumerate(w) if x == mx)
        out.append(float(len(w) - 1 - pos))
    return pd.Series(out, index=s.index)


def llvbars(s, n):
    """LLVBARS: 最近 n 周期内最低值距当前的根数（当前=0，并列取最近一次）"""
    v = s.values
    out = []
    for i in range(len(v)):
        if i < n - 1:
            out.append(float("nan")); continue
        w = v[i - n + 1: i + 1]
        mn = w.min()
        pos = max(j for j, x in enumerate(w) if x == mn)
        out.append(float(len(w) - 1 - pos))
    return pd.Series(out, index=s.index)


def sma_cn(x, n, m):
    """麦语言 SMA(X,N,M): Y=(M*X+(N-M)*Y')/N，即 alpha=M/N 的指数平滑"""
    a = m / n
    out, prev = [], float("nan")
    for v in x:
        prev = v if math.isnan(prev) else a * v + (1 - a) * prev
        out.append(prev)
    return pd.Series(out, index=x.index)


def every(cond, n):
    """EVERY(cond,N): 最近 N 根全部成立"""
    return cond.astype(float).rolling(n, min_periods=n).min().fillna(0) == 1


def intpart(x):
    """麦语言 INTPART：向零取整；NaN/inf 等非有限值原样保留为 NaN"""
    if x is None or not isinstance(x, (int, float)) or not math.isfinite(x):
        return float("nan")
    return float(math.trunc(x))
