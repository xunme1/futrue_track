# -*- coding: utf-8 -*-
"""
数据源注册表与统一入口
  - 新增数据源：实现 DataSource 后在此注册
  - 指数（南华 NHCI.SL）固定使用 iFinD
"""
from .base import DataSource, resample_weekly
from .ifind import IFindSource
from .ricequant import RicequantSource

SOURCES = {
    IFindSource.name: IFindSource,
    RicequantSource.name: RicequantSource,
}

INDEX_SOURCE = IFindSource.name      # 南华指数只有 iFinD 有

_instances = {}


def get_source(name: str, cfg) -> DataSource:
    """按名称取数据源单例（复用登录态）"""
    if name not in SOURCES:
        raise KeyError(f"未知数据源 '{name}'，已注册: {list(SOURCES)}")
    if name not in _instances:
        _instances[name] = SOURCES[name](cfg)
        _instances[name].login()
    return _instances[name]


def logout_all():
    for src in _instances.values():
        src.logout()
    _instances.clear()


def reconnect_source(name: str, cfg) -> DataSource:
    """刷新已创建的数据源会话，供可恢复的认证错误重试使用。"""
    if name not in _instances:
        return get_source(name, cfg)
    src = _instances[name]
    try:
        src.logout()
    finally:
        src.login()
    return src


__all__ = ["DataSource", "SOURCES", "INDEX_SOURCE", "get_source", "logout_all", "reconnect_source", "resample_weekly"]
