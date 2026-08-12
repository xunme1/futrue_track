# -*- coding: utf-8 -*-
"""
iFinD（同花顺）数据源
已实测确认的数据格式（2026-07-28 探测）：
  - 期货主力连续 = 品种小写 + 8888 + 交易所后缀（rb8888.SHF / sc8888.INE；rb9999 无效）
  - 具体合约 = 合约代码 + 后缀（rb2610.SHF / sc2509.INE / IM2509.CFE）
  - 日线指标 open;high;low;close;volume，持仓量 = ccl
  - 南华商品指数 NHCI.SL（米筐无此指数，固定走本源）
  - 周线 period:W
"""
import pandas as pd
from iFinDPy import THS_HistoryQuotes, THS_iFinDLogin, THS_iFinDLogout

from .base import DataSource


class IFindRequestError(RuntimeError):
    """保留 iFinD 错误码，供下载流水线区分可恢复的会话失效。"""

    def __init__(self, symbol, period, errorcode, errmsg):
        self.symbol = symbol
        self.period = period
        self.errorcode = errorcode
        self.errmsg = errmsg or ""
        super().__init__(
            f"[iFinD] {symbol} {period} 取数失败: ec={errorcode} {self.errmsg}"
        )

    @property
    def session_expired(self):
        return str(self.errorcode) == "-1010" or "logged out" in self.errmsg.lower()


class IFindSource(DataSource):
    name = "ifind"

    def __init__(self, cfg):
        self._acc = cfg.get("account") or {}

    def login(self):
        username = self._acc.get("username", "")
        password = self._acc.get("password", "")
        if not username or not password:
            raise SystemExit(
                "iFinD 凭据为空，请设置 FUTURES_IFIND_USERNAME/FUTURES_IFIND_PASSWORD "
                "或填写 config.yaml 的 account"
            )
        ret = THS_iFinDLogin(username, password)
        if ret not in (0, -201):
            raise SystemExit(f"iFinD 登录失败，返回码 {ret}")

    def logout(self):
        THS_iFinDLogout()

    def _history(self, symbol, indicators, period, start, end):
        r = THS_HistoryQuotes(symbol, indicators, f"period:{period},fill:Blank", start, end)
        if r.get("errorcode") != 0:
            raise IFindRequestError(symbol, period, r.get("errorcode"), r.get("errmsg"))
        t = r["tables"][0]
        df = pd.DataFrame(t["table"])
        df["time"] = pd.to_datetime(t["time"])
        return df.set_index("time").sort_index()

    def futures_daily(self, symbol, start, end):
        return self._history(symbol, "open;high;low;close;volume;ccl", "D", start, end)

    def futures_weekly(self, symbol, start, end, daily_df=None):
        return self._history(symbol, "open;high;low;close", "W", start, end)

    def index_daily(self, symbol, start, end):
        return self._history(symbol, "open;high;low;close", "D", start, end)
