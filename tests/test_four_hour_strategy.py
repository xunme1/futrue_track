import unittest

import pandas as pd

from backend.core.config import PARAMS
from backend.pipeline.daily import build_payload
from backend.pipeline.screen import screen_payload
from backend.strategy.zxgl_4h import _weekly_permissions, compute

CONTRACT = {"symbol": "rb2609.SHF", "name": "螺纹钢", "category": "黑色", "exchange": "SHFE"}


class FourHourStrategyTests(unittest.TestCase):
    def test_four_hour_consolidation_uses_its_own_threshold(self):
        dates = pd.date_range("2026-08-01 09:00", periods=10, freq="4h")
        bars = pd.DataFrame({
            "open": [100.0] * 10, "high": [101.0] * 10, "low": [99.0] * 10,
            "close": [100.0] * 10, "volume": [1.0] * 10, "ccl": [100.0] * 10,
            "trading_date": [d.normalize() for d in dates],
        }, index=dates)
        weekly_dates = pd.date_range("2025-09-05", periods=50, freq="W-FRI")
        weekly = pd.DataFrame({"open": [100.0] * 50, "high": [102.0] * 50, "low": [99.0] * 50,
                               "close": [101.0] * 50, "volume": [1.0] * 50, "ccl": [100.0] * 50}, index=weekly_dates)
        params = {**PARAMS, "panzheng_threshold": 0.03, "panzheng_threshold_4h": 0.01}

        result = compute(bars, weekly, params)

        # 2% amplitude is non-consolidating in the 4-hour dashboard, even
        # though it would remain consolidating under the daily 3% threshold.
        self.assertFalse(result["PANZHENG"].iloc[-1])

    def test_weekly_filter_uses_current_week_without_final_week_leakage(self):
        weekly_dates = pd.date_range("2026-06-05", periods=10, freq="W-FRI")
        weekly = pd.DataFrame({
            "open": [100.0] * 10, "high": [101.0] * 10, "low": [99.0] * 10,
            "close": [100.0] * 7 + [50.0, 50.0, 50.0], "volume": [1.0] * 10, "ccl": [1.0] * 10,
        }, index=weekly_dates)
        this_week = weekly_dates[7].to_period("W").start_time
        next_week = weekly_dates[8].to_period("W").start_time
        bars = pd.DataFrame({
            "open": [200.0, 50.0], "high": [201.0, 51.0], "low": [199.0, 49.0], "close": [200.0, 50.0],
            "volume": [1.0, 1.0], "ccl": [1.0, 1.0], "trading_date": [this_week, next_week],
        }, index=pd.to_datetime([this_week + pd.Timedelta(hours=15), next_week + pd.Timedelta(hours=15)]))

        aa1, zz1, tt1 = _weekly_permissions(bars, weekly, PARAMS)

        self.assertTrue(zz1[0])   # 本周直接使用当前已完成 4h K 的 200 收盘。
        self.assertTrue(aa1[1])
        self.assertFalse(aa1[0])

    def test_four_hour_payload_has_price_colors_and_no_relative_strength_fields(self):
        dates = pd.date_range("2026-08-01 09:00", periods=45, freq="4h")
        bars = pd.DataFrame({
            "open": [100.0] * 45, "high": [102.0] * 45, "low": [99.0] * 45,
            "close": [101.0] * 44 + [99.0], "volume": [1.0] * 45, "ccl": [100.0] * 45,
            "trading_date": [d.normalize() for d in dates],
        }, index=dates)
        weekly_dates = pd.date_range("2025-09-05", periods=50, freq="W-FRI")
        weekly = pd.DataFrame({"open": [100.0] * 50, "high": [102.0] * 50, "low": [99.0] * 50,
                               "close": [101.0] * 50, "volume": [1.0] * 50, "ccl": [100.0] * 50}, index=weekly_dates)
        result = compute(bars, weekly, PARAMS)
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
        up["ohlc"][18] = [100, 99, 98, 101]
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
        down["ohlc"][18] = [100, 106, 99, 108]
        down["ohlc"][19] = [101, 100, 99, 102]
        down_hit = screen_payload("down", down, CONTRACT)["long_to_short"]
        self.assertEqual(len(down_hit), 1)
        self.assertEqual(down_hit[0]["stars"], 2)
        self.assertEqual(down_hit[0]["star_reasons"], ["前8根含偏强K", "前8根含减仓倒手指"])

    def test_four_hour_reversal_is_retained_after_a_following_opposite_color_bar(self):
        n = 20
        payload = {
            "symbol": "al2609.SHF", "dates": [f"2026-08-{i + 1:02d} 15:00" for i in range(n)],
            "ohlc": [[100, 101, 99, 102] for _ in range(n)], "bar_colors": ["gray"] * n,
            "POS": [0] * n, "DD": [110.0] * n, "EE": [105.0] * n,
            "KK": [90.0] * n, "PP": [100.0] * n, "SB": [False] * n, "DSBE": [False] * n,
        }
        # 第18根蓝K确认多转空；第19根反弹为红K，但确认信号仍应保留在9根有效期内。
        payload["POS"][15] = 1
        payload["bar_colors"][17:20] = ["red", "blue", "red"]
        payload["ohlc"][17] = [100, 106, 99, 108]
        payload["ohlc"][18] = [101, 100, 99, 102]

        hit = screen_payload("al2609", payload, CONTRACT)["long_to_short"]

        self.assertEqual(len(hit), 1)
        self.assertEqual(hit[0]["signal_date"], payload["dates"][18])
        self.assertEqual(hit[0]["bars_since_signal"], 1)
        self.assertEqual(hit[0]["stars"], 1)

        # 如果保留期内重新开回多仓，旧的多转空必须立刻失效。
        payload["POS"][19] = 1
        payload["signals"] = [{"i": 19, "type": "BK", "price": 102.0}]
        self.assertFalse(screen_payload("al2609", payload, CONTRACT)["long_to_short"])


if __name__ == "__main__":
    unittest.main()
