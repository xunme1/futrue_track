# -*- coding: utf-8 -*-
"""
数据源抽象基类：所有数据源（iFinD / 米筐 / 未来新增）实现统一接口
新增数据源只需继承本类并注册到 backend.datasource.SOURCES
"""
from abc import ABC, abstractmethod


class DataSource(ABC):
    name = "base"

    def login(self):
        """建立连接/鉴权（无状态源可留空）"""

    def logout(self):
        """释放连接"""

    @abstractmethod
    def futures_daily(self, symbol: str, start: str, end: str):
        """期货日线 → DataFrame[open/high/low/close/volume/ccl]，DatetimeIndex"""

    def futures_4h(self, symbol: str, start: str, end: str):
        """期货 4 小时线；仅支持该周期的数据源应覆盖此方法。"""
        raise NotImplementedError(f"[{self.name}] does not provide 4-hour futures bars")

    def futures_weekly(self, symbol: str, start: str, end: str, daily_df=None):
        """期货周线：默认由日线聚合（W-FRI）；源原生支持周线可覆盖"""
        if daily_df is None:
            daily_df = self.futures_daily(symbol, start, end)
        return resample_weekly(daily_df)

    @abstractmethod
    def index_daily(self, symbol: str, start: str, end: str):
        """指数日线（如南华商品指数）→ DataFrame[open/high/low/close]"""


def resample_weekly(df):
    """日线聚合周线（与交易所交易周一致，周五为周末标记）"""
    agg = {"open": "first", "high": "max", "low": "min", "close": "last",
           "volume": "sum", "ccl": "last"}
    agg = {k: v for k, v in agg.items() if k in df.columns}
    return df.resample("W-FRI").agg(agg).dropna(subset=["close"])
