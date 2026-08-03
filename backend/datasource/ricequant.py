# -*- coding: utf-8 -*-
"""
米筐 RQData（rqdatac）数据源
  - 覆盖中金所 CFFEX（可测 IM/IF/IC/IH，解决 iFinD 账号无权限问题）
  - 代码自动转换：iFinD 风格 → 米筐风格
      rb8888.SHF → 主力连续（underlying='RB'，adjust_type='none' 直接拼接，贴近文华主连）
      IM2509.CFE → 具体合约 'IM2509'
  - 持仓量 open_interest 统一改名 ccl
  - 周线由日线聚合（base.resample_weekly）
  - 无南华指数：index_daily 明确报错，指数固定走 iFinD
"""
import pandas as pd

from .base import DataSource


class RicequantSource(DataSource):
    name = "ricequant"

    def __init__(self, cfg):
        self._key = (cfg.get("ricequant") or {}).get("license_key", "")
        self._rq = None

    def login(self):
        import rqdatac
        if not self._key:
            raise SystemExit(
                "米筐 license 为空，请设置 FUTURES_RQDATA_LICENSE_KEY "
                "或填写 config.yaml 的 ricequant.license_key"
            )
        # 米筐 license token 的正确鉴权方式：username="license", password=token
        rqdatac.init("license", self._key)
        self._rq = rqdatac

    def futures_daily(self, symbol, start, end):
        import re
        base = symbol.split(".")[0].upper()
        m = re.fullmatch(r"([A-Z]+)(\d{3})", base)
        if m:                                        # 郑商所3位年月码(CF609) → 米筐4位(CF2609)
            base = f"{m.group(1)}2{m.group(2)}"
        if base.endswith("8888"):                       # 主力连续
            df = self._rq.futures.get_dominant_price(
                base[:-4], start_date=start, end_date=end,
                frequency="1d", adjust_type="none")
        else:                                           # 具体合约
            df = self._rq.get_price(
                base, start_date=start, end_date=end, frequency="1d",
                fields=["open", "high", "low", "close", "volume", "open_interest"],
                adjust_type="none", expect_df=True)
        if df is None or len(df) == 0:
            raise RuntimeError(f"[米筐] 未取到 {symbol}（{base}）的数据，请检查合约代码或权限")
        if isinstance(df.index, pd.MultiIndex):         # get_dominant_price 返回 (symbol, datetime) 多级索引
            df = df.reset_index()
            tcol = "datetime" if "datetime" in df.columns else "date"
            df = df.set_index(pd.to_datetime(df[tcol])).drop(columns=[tcol])
        df.index = pd.to_datetime(df.index)
        df.index.name = "time"
        df = df.rename(columns={"open_interest": "ccl"})
        return df[["open", "high", "low", "close", "volume", "ccl"]].sort_index()

    def index_daily(self, symbol, start, end):
        raise NotImplementedError("[米筐] 无南华商品指数，指数数据固定走 iFinD")
