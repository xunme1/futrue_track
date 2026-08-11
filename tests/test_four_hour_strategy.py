import unittest

import pandas as pd

from backend.core.config import PARAMS
from backend.pipeline.daily import build_payload
from backend.pipeline.screen import screen_payload
from backend.strategy.zxgl_4h import _daily_permissions, compute

CONTRACT = {"symbol": "rb2609.SHF", "name": "螺纹钢", "category": "黑色", "exchange": "SHFE"}


class FourHourStrategyTests(unittest.TestCase):
    def test_daily_filter_uses_previous_completed_trading_day(self):
        daily_dates = pd.date_range("2026-08-01", periods=10, freq="D")
        daily = pd.DataFrame({
            "open": [100.0] * 10, "high": [101.0] * 10, "low": [99.0] * 10,
            "close": [100.0] * 7 + [200.0, 50.0, 50.0], "volume": [1.0] * 10, "ccl": [1.0] * 10,
        }, index=daily_dates)
        bars = pd.DataFrame({"trading_date": [pd.Timestamp("2026-08-08"), pd.Timestamp("2026-08-09")]}, index=pd.to_datetime(["2026-08-08 15:00", "2026-08-08 23:00"]))

        aa1, zz1, tt1 = _daily_permissions(bars, daily, PARAMS)

        self.assertFalse(zz1[0])  # 8th must use the 7th, not the 8th daily close.
        self.assertTrue(zz1[1])   # 9th may use the completed 8th daily close.
        self.assertEqual(aa1, [False, False])

    def test_four_hour_payload_has_price_colors_and_no_relative_strength_fields(self):
        dates = pd.date_range("2026-08-01 09:00", periods=45, freq="4h")
        bars = pd.DataFrame({
            "open": [100.0] * 45, "high": [102.0] * 45, "low": [99.0] * 45,
            "close": [101.0] * 44 + [99.0], "volume": [1.0] * 45, "ccl": [100.0] * 45,
            "trading_date": [d.normalize() for d in dates],
        }, index=dates)
        daily_dates = pd.date_range("2026-07-01", periods=50, freq="D")
        daily = pd.DataFrame({"open": [100.0] * 50, "high": [102.0] * 50, "low": [99.0] * 50,
                              "close": [101.0] * 50, "volume": [1.0] * 50, "ccl": [100.0] * 50}, index=daily_dates)
        result = compute(bars, daily, PARAMS)
        payload = build_payload("rb2609.SHF", result, "4h")

        self.assertEqual(payload["bar_colors"][-1], "blue")
        self.assertNotIn("PQ", payload)
        self.assertEqual(len(payload["bar_colors"]), len(payload["dates"]))

    def test_screening_accepts_four_hour_price_colors(self):
        n = 20
        payload = {
            "symbol": "rb2609.SHF", "dates": [f"2026-08-{i + 1:02d} 15:00" for i in range(n)],
            "ohlc": [[100, 101, 99, 102] for _ in range(n)], "bar_colors": ["gray"] * n,
            "POS": [0] * (n - 1) + [1], "DD": [105.0] * n, "EE": [100.0] * n,
            "KK": [95.0] * n, "PP": [98.0] * n,
        }
        hits = screen_payload("rb2609", payload, {"symbol": "rb2609.SHF", "name": "螺纹钢"})
        self.assertEqual(len(hits["long_trend"]), 1)

    def test_four_hour_reversal_stars_are_scored_only_after_standard_gate(self):
        n = 20
        base = {
            "symbol": "rb2609.SHF", "dates": [f"2026-08-{i + 1:02d} 15:00" for i in range(n)],
            "ohlc": [[100, 101, 99, 102] for _ in range(n)], "bar_colors": ["gray"] * n,
            "POS": [0] * n, "DD": [105.0] * n, "EE": [95.0] * n,
            "KK": [90.0] * n, "PP": [100.0] * n,
            "SB": [False] * n, "DSBE": [False] * n,
        }
        # 空转多：9根内曾持空，当前红K突破该压力带；两个增强条件均成立。
        up = {key: value[:] if isinstance(value, list) else value for key, value in base.items()}
        up["POS"][15] = -1
        up["bar_colors"][18], up["bar_colors"][19] = "blue", "red"
        up["SB"][17] = True
        up["ohlc"][19] = [100, 101, 99, 102]
        up_hit = screen_payload("up", up, CONTRACT)["short_to_long"]
        self.assertEqual(len(up_hit), 1)
        self.assertEqual(up_hit[0]["stars"], 2)
        self.assertEqual(up_hit[0]["star_reasons"], ["前8根含偏弱K", "前8根含增仓笑脸"])

        # 即使增强条件齐全，只要没有压力带就不能入选。
        no_band = {key: value[:] if isinstance(value, list) else value for key, value in up.items()}
        no_band["POS"] = [0] * n
        self.assertFalse(screen_payload("no-band", no_band, CONTRACT)["short_to_long"])

        # 多转空：同样先过支撑带+蓝K跌破的标准门槛，再得到两颗增强星。
        down = {key: value[:] if isinstance(value, list) else value for key, value in base.items()}
        down["POS"][15] = 1
        down["EE"][15] = 105.0
        down["bar_colors"][18], down["bar_colors"][19] = "red", "blue"
        down["DSBE"][17] = True
        down["ohlc"][19] = [101, 100, 99, 102]
        down_hit = screen_payload("down", down, CONTRACT)["long_to_short"]
        self.assertEqual(len(down_hit), 1)
        self.assertEqual(down_hit[0]["stars"], 2)
        self.assertEqual(down_hit[0]["star_reasons"], ["前8根含偏强K", "前8根含减仓倒手指"])


if __name__ == "__main__":
    unittest.main()
