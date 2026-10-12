# -*- coding: utf-8 -*-
"""receipt_core 单元测试：单位换算、分位、规则信号、图表序列（全合成数据，不连数据源）。"""
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from backend.pipeline.receipt_core import (
    build_series,
    compute_symbol_metrics,
    evaluate_signals,
    load_history,
    percentile,
    to_lots,
    underlying_of,
)


def make_hist(values, exchange="CZCE", warrant_units=8, multiplier=5, start="20260101", ef=None):
    """合成 history DataFrame：values 为 on_warrant 序列（自然日连排即可，测逻辑不测历法）。"""
    n = len(values)
    dates = (pd.date_range(start, periods=n, freq="D")).strftime("%Y%m%d")
    return pd.DataFrame({
        "date": dates,
        "symbol": "T",
        "exchange": exchange,
        "on_warrant": values,
        "effective_forecast": ef if ef is not None else [None] * n,
        "warrant_units": warrant_units if exchange == "CZCE" else None,
        "multiplier": multiplier,
        "deliverable": None,
    })


class TestUnderlyingOf(unittest.TestCase):
    def test_contract_to_underlying(self):
        self.assertEqual(underlying_of("cu2610.SHF"), "CU")
        self.assertEqual(underlying_of("MA611.CZC"), "MA")
        self.assertEqual(underlying_of("rb2701"), "RB")
        self.assertEqual(underlying_of("IM2612.CFE"), "IM")


class TestToLots(unittest.TestCase):
    def test_czce_zhang_to_lots(self):
        self.assertEqual(to_lots(100, "CZCE", warrant_units=8), 800)

    def test_shfe_ton_to_lots(self):
        self.assertEqual(to_lots(1000, "SHFE", multiplier=10), 100)

    def test_shfe_missing_multiplier_returns_none(self):
        self.assertIsNone(to_lots(1000, "SHFE", multiplier=None))

    def test_ine_like_shfe(self):
        self.assertEqual(to_lots(2000, "INE", multiplier=1000), 2)

    def test_dce_raw_is_lots(self):
        self.assertEqual(to_lots(500, "DCE"), 500)

    def test_none_input(self):
        self.assertIsNone(to_lots(None, "DCE"))


class TestPercentile(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(percentile(list(range(100)), 79), 0.8)

    def test_small_sample_returns_none(self):
        self.assertIsNone(percentile([1, 2, 3], 2))


class TestSignals(unittest.TestCase):
    def base(self, **kw):
        m = {"pct_all": None, "change_5d": None, "change_5d_pct": None,
             "change_20d_pct": None, "price_pct_1y": None, "virtual_ratio": None}
        m.update(kw)
        return m

    def test_r1_supply_pressure(self):
        sigs = evaluate_signals(self.base(pct_all=0.9, change_5d=100))
        self.assertEqual([s["code"] for s in sigs], ["R1"])
        self.assertEqual(sigs[0]["tone"], "short")

    def test_r2_tight_supply(self):
        sigs = evaluate_signals(self.base(pct_all=0.1, change_5d=-50))
        self.assertEqual([s["code"] for s in sigs], ["R2"])
        self.assertEqual(sigs[0]["tone"], "long")

    def test_r3_high_hedge(self):
        sigs = evaluate_signals(self.base(price_pct_1y=0.8, change_5d_pct=0.15))
        self.assertEqual([s["code"] for s in sigs], ["R3"])

    def test_r4_cancellation_wave(self):
        sigs = evaluate_signals(self.base(change_20d_pct=-0.25))
        self.assertEqual([s["code"] for s in sigs], ["R4"])

    def test_r5_virtual_ratio(self):
        sigs = evaluate_signals(self.base(virtual_ratio=0.03))
        self.assertEqual([s["code"] for s in sigs], ["R5"])
        self.assertEqual(sigs[0]["tone"], "warn")

    def test_no_signal_when_mid_range(self):
        sigs = evaluate_signals(self.base(pct_all=0.5, change_5d=10,
                                          change_5d_pct=0.01, change_20d_pct=-0.05,
                                          price_pct_1y=0.4, virtual_ratio=0.5))
        self.assertEqual(sigs, [])


class TestMetrics(unittest.TestCase):
    def test_changes_and_percentile(self):
        hist = make_hist([100] * 30 + [120])
        m = compute_symbol_metrics(hist)
        self.assertTrue(m["available"])
        # CZCE 张×8 → 折手
        self.assertEqual(m["lots"], 960)
        self.assertEqual(m["change_1d"], 160)
        self.assertEqual(m["change_5d"], 160)
        self.assertEqual(m["pct_all"], 1.0)

    def test_virtual_ratio_with_price(self):
        hist = make_hist([100] * 25)  # 折手 800
        price = pd.DataFrame(
            {"close": [3000.0] * 25, "ccl": [20000.0] * 25},
            index=pd.date_range("2026-09-01", periods=25, freq="D"))
        m = compute_symbol_metrics(hist, price)
        self.assertEqual(m["oi"], 20000.0)
        self.assertAlmostEqual(m["virtual_ratio"], 800 / 20000)
        self.assertIn("R5", [s["code"] for s in m["signals"]])

    def test_empty_history(self):
        self.assertFalse(compute_symbol_metrics(pd.DataFrame()).get("available"))


class TestSeries(unittest.TestCase):
    def test_shapes(self):
        hist = make_hist(list(range(100, 400)))
        series = build_series(hist)
        self.assertEqual(len(series["daily"]), 250)
        self.assertEqual(len(series["changes"]), 60)
        self.assertIn("2026", series["seasonal"])
        self.assertIsNone(series["ef"])  # 非郑商所有效预报为空

    def test_ef_series_for_czce(self):
        hist = make_hist([100] * 30, ef=[10] * 30)
        series = build_series(hist)
        self.assertIsNotNone(series["ef"])
        # 仓单折手 800 + 预报折手 80
        self.assertEqual(series["ef"][-1][1], 800)
        self.assertEqual(series["ef"][-1][2], 80)


class TestLoadHistory(unittest.TestCase):
    def test_roundtrip(self):
        hist = make_hist([100, 120, 110])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "T.csv"
            hist.to_csv(path, index=False)
            loaded = load_history(path)
        self.assertEqual(loaded["lots"].tolist(), [800, 960, 880])
        self.assertEqual(loaded["ef_lots"].isna().all(), True)


if __name__ == "__main__":
    unittest.main()
