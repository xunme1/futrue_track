import unittest

import pandas as pd

from backend.core.config import PARAMS
from backend.core.mylang import hv, lv
from backend.strategy.zxgl_xdd import compute


class MyLanguageAndOpiSignalTests(unittest.TestCase):
    def test_hv_and_lv_exclude_the_current_bar(self):
        values = pd.Series([10.0, 30.0, 20.0, 40.0])
        self.assertTrue(pd.isna(hv(values, 2).iloc[1]))
        self.assertEqual(hv(values, 2).iloc[2], 30.0)
        self.assertEqual(hv(values, 2).iloc[3], 30.0)
        self.assertEqual(lv(values, 2).iloc[2], 10.0)
        self.assertEqual(lv(values, 2).iloc[3], 20.0)

    def test_sb_can_be_triggered_by_the_ii_branch(self):
        """II 需要突破前两根收盘，不是突破包含当前 K 的最高收盘。"""
        dates = pd.date_range("2026-01-01", periods=45, freq="D")
        close = [100.0] * 44 + [101.0]
        frame = pd.DataFrame({
            "open": [100.0] * 45,
            "high": [101.0] * 44 + [102.0],
            "low": [99.0] * 45,
            "close": close,
            "volume": [1.0] * 45,
            # 最后三根：相对两日前 +6%，但相对昨日仅 +1%，只命中 II，不命中 XX/ZC。
            "ccl": [100.0] * 42 + [100.0, 105.0, 106.0],
        }, index=dates)
        weekly_dates = pd.date_range("2025-10-03", periods=20, freq="W-FRI")
        weekly = pd.DataFrame({
            "open": [100.0] * 20,
            "high": [101.0] * 20,
            "low": [99.0] * 20,
            "close": [100.0] * 20,
            "volume": [1.0] * 20,
            "ccl": [100.0] * 20,
        }, index=weekly_dates)

        result = compute(frame, weekly, frame, PARAMS)
        self.assertTrue(result["SB"].iloc[-1])


if __name__ == "__main__":
    unittest.main()
