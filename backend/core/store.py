# -*- coding: utf-8 -*-
"""
本地行情库（Local Store）
  按数据源分目录存放 CSV，后端的计算、API 一律读本地库，不直接连数据源。
  目录结构：
    data/store/
      ifind/
        futures_daily/rb8888.SHF.csv      期货日线（open/high/low/close/volume/ccl）
        futures_weekly/rb8888.SHF.csv     期货周线（open/high/low/close）
        index_daily/NHCI.SL.csv           指数日线
      ricequant/
        futures_daily/IM2609.CFE.csv
        ...
  更新方式：append() 按时间索引去重（保留新值），只增量追加，不重写历史。
"""
from pathlib import Path

import pandas as pd

from .config import DATA_DIR

STORE_ROOT = DATA_DIR / "store"


class Store:
    def __init__(self, root=STORE_ROOT):
        self.root = Path(root)

    def _path(self, source: str, dataset: str, symbol: str) -> Path:
        return self.root / source / dataset / f"{symbol}.csv"

    def exists(self, source, dataset, symbol) -> bool:
        return self._path(source, dataset, symbol).exists()

    def read(self, source, dataset, symbol) -> pd.DataFrame:
        fp = self._path(source, dataset, symbol)
        if not fp.exists():
            raise FileNotFoundError(
                f"本地库没有 {symbol}（{source}/{dataset}），请先运行 python -m backend.pipeline.download")
        df = pd.read_csv(fp, parse_dates=["time"])
        return df.set_index("time").sort_index()

    def append(self, source, dataset, symbol, df) -> int:
        """增量追加：按时间索引去重（新数据覆盖同日旧值），返回净新增行数"""
        fp = self._path(source, dataset, symbol)
        fp.parent.mkdir(parents=True, exist_ok=True)
        df = df.copy()
        df.index = pd.to_datetime(df.index)
        df.index.name = "time"
        old_len = 0
        if fp.exists():
            old = pd.read_csv(fp, parse_dates=["time"]).set_index("time")
            old_len = len(old)
            df = pd.concat([old, df])
        df = df[~df.index.duplicated(keep="last")].sort_index()
        df.to_csv(fp, index_label="time")
        return len(df) - old_len

    def last_date(self, source, dataset, symbol):
        """本地库中该品种最后一条数据的日期；无数据返回 None"""
        fp = self._path(source, dataset, symbol)
        if not fp.exists():
            return None
        t = pd.read_csv(fp, usecols=["time"])["time"]
        if t.empty:
            return None
        return pd.to_datetime(t.max())
