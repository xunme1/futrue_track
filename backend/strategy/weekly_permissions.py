# -*- coding: utf-8 -*-
"""Realtime weekly permission shared by daily and 4-hour strategies."""
import pandas as pd


def current_weekly_permissions(df, weekly, p):
    """Return the current-week AA/ZZ/TT state for every completed input bar.

    Wenhua ``#IMPORT[WEEK,1,ZXGL]`` references the current weekly bar.  The
    weekly row supplied by a data source is only safe for earlier, completed
    weeks; for the input bar's own week we aggregate OHLC through that bar.
    This keeps the realtime Wenhua semantics without using the final weekly
    high, low, or close as future information on historical lower-period bars.
    """
    history = weekly.copy()
    history.index = pd.to_datetime(history.index).normalize()
    history = history[~history.index.duplicated(keep="last")].sort_index()
    history["week"] = history.index.to_period("W")

    if "trading_date" in df.columns:
        dates = pd.Series(pd.to_datetime(df["trading_date"]).dt.normalize().to_numpy(), index=df.index)
    else:
        dates = pd.Series(pd.to_datetime(df.index).normalize(), index=df.index)

    required = max(p["weekly_ma"], p["panzheng_range"])
    partials, aa1, zz1, tt1 = {}, [], [], []
    for timestamp, row in df.iterrows():
        week = dates.loc[timestamp].to_period("W")
        partial = partials.get(week)
        if partial is None:
            partial = {"open": row["open"], "high": row["high"], "low": row["low"], "close": row["close"]}
            partials[week] = partial
        else:
            partial["high"] = max(partial["high"], row["high"])
            partial["low"] = min(partial["low"], row["low"])
            partial["close"] = row["close"]

        previous = history.loc[history["week"] < week, ["open", "high", "low", "close"]]
        bars = pd.concat([previous.tail(required - 1), pd.DataFrame([partial])], ignore_index=True)
        if len(bars) < required:
            aa1.append(False); zz1.append(False); tt1.append(False)
            continue

        ma7 = bars["close"].tail(p["weekly_ma"]).mean()
        pz = ((bars["high"].tail(p["panzheng_range"]).max() - bars["low"].tail(p["panzheng_range"]).min()) / ma7
              < p["panzheng_threshold"])
        close = partial["close"]
        aa1.append(bool(close < ma7))
        zz1.append(bool(close > ma7))
        tt1.append(bool(not pz))
    return aa1, zz1, tt1
