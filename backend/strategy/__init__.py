# -*- coding: utf-8 -*-
"""
策略注册表：新增策略时在 backend/strategy/ 下新建模块（实现 compute(fut_d, fut_w, idx_d, p)），
并在此登记即可被流水线调用。
"""
from . import zxgl_xdd

STRATEGIES = {
    zxgl_xdd.STRATEGY_NAME: zxgl_xdd,
}

DEFAULT_STRATEGY = zxgl_xdd.STRATEGY_NAME


def get_strategy(name=None):
    name = name or DEFAULT_STRATEGY
    if name not in STRATEGIES:
        raise KeyError(f"未知策略 '{name}'，已注册: {list(STRATEGIES)}")
    return STRATEGIES[name]


__all__ = ["STRATEGIES", "DEFAULT_STRATEGY", "get_strategy"]
