import unittest

from backend.pipeline.screen import BUCKETS, _sort_results, flatten_report, screen_payload, wilder_atr


def payload(n=16, close=100.0):
    closes = [close] * n
    return {
        "symbol": "TST.TEST",
        "dates": [f"2026-01-{i + 1:02d}" for i in range(n)],
        "ohlc": [[v, v, v - 1, v + 1] for v in closes],
        "PQ": [False] * n,
        "PR": [False] * n,
        "POS": [0] * n,
        "DD": [105.0] * n,
        "EE": [95.0] * n,
        "KK": [95.0] * n,
        "PP": [105.0] * n,
    }


CONTRACT = {"symbol": "TST.TEST", "name": "测试", "category": "测试", "exchange": "TEST"}


class ScreenTests(unittest.TestCase):
    def test_wilder_atr_uses_true_range_and_warmup(self):
        bars = [[10, 10, 9, 11], [10, 12, 11, 13], [12, 11, 10, 12]]
        atr = wilder_atr(bars, window=2)
        self.assertIsNone(atr[0])
        self.assertEqual(atr[1], 2.5)  # (2 + 3) / 2
        self.assertEqual(atr[2], 2.25)  # (2.5 + 2) / 2

    def test_daily_score_uses_inferred_position_entry_without_atr_or_ma_gate(self):
        values = [99.66666666666667] * 5 + [100.0, 120.0]
        ranked = payload(n=7)
        ranked["ohlc"] = [[value, value, value - 1, value + 1] for value in values]
        ranked["POS"][-2:] = [1, 1]

        hit = screen_payload("percent-score", ranked, CONTRACT)["long_trend"][0]
        self.assertIsNotNone(hit["ma7"])
        self.assertAlmostEqual(hit["score"], (120 - 110) / 110 * 100)
        self.assertEqual(hit["score_entry_date"], ranked["dates"][-2])
        self.assertEqual(hit["score_entry_open"], 100.0)
        self.assertEqual(hit["score_entry_source"], "推定持仓起点")
        self.assertEqual(hit["score_center"], 110.0)
        self.assertIsNone(hit["atr14"])

    def test_daily_score_uses_actual_bk_and_sk_opening_prices(self):
        long_d = payload(n=16)
        long_d["POS"][8:] = [1] * 8
        long_d["ohlc"][8] = [100, 101, 99, 102]
        long_d["ohlc"][-1] = [119, 120, 118, 121]
        long_d["signals"] = [{"i": 8, "type": "BK", "price": 99}]
        long_hit = screen_payload("long-entry", long_d, CONTRACT)["long_trend"][0]
        self.assertAlmostEqual(long_hit["score"], (120 - 110) / 110 * 100)
        self.assertEqual(long_hit["score_entry_source"], "实际BK开仓")
        self.assertEqual(long_hit["score_entry_date"], long_d["dates"][8])

        short_d = payload(n=16)
        short_d["POS"][8:] = [-1] * 8
        short_d["ohlc"][8] = [100, 99, 98, 101]
        short_d["ohlc"][-1] = [81, 80, 79, 82]
        short_d["signals"] = [{"i": 8, "type": "SK", "price": 101}]
        short_hit = screen_payload("short-entry", short_d, CONTRACT)["short_trend"][0]
        self.assertAlmostEqual(short_hit["score"], (80 - 90) / 90 * 100)
        self.assertEqual(short_hit["score_entry_source"], "实际SK开仓")

    def test_main_trends_require_only_position(self):
        long_d = payload(close=100)
        long_d["POS"][-1] = 1
        long_d["PR"][-1] = True  # 蓝 K、且收盘落在趋势带外，仍应属于多头趋势。
        long_d["EE"][-1] = 120
        short_d = payload(close=100)
        short_d["POS"][-1] = -1
        short_d["PQ"][-1] = True  # 红 K、且收盘落在趋势带外，仍应属于空头趋势。
        short_d["PP"][-1] = 80

        long_hits = screen_payload("long", long_d, CONTRACT)
        short_hits = screen_payload("short", short_d, CONTRACT)
        self.assertEqual(len(long_hits["long_trend"]), 1)
        self.assertTrue(long_hits["long_trend"][0]["PR"])
        self.assertEqual(len(short_hits["short_trend"]), 1)
        self.assertTrue(short_hits["short_trend"][0]["PQ"])

    def test_transitions_allow_break_anywhere_in_continuous_target_run(self):
        down = payload(n=17)
        down["PQ"][13], down["POS"][13] = True, 1
        down["PR"][14], down["PR"][15], down["PR"][16] = True, True, True
        # 前两根蓝 K 仍在支撑带内，第三根才严格跌破 EE，也应确认多转空。
        down["ohlc"][14] = [98, 98, 97, 99]
        down["ohlc"][15] = [97, 97, 96, 98]
        down["ohlc"][16] = [90, 90, 89, 91]
        down["EE"][14] = 95
        down["EE"][15] = 95
        down["EE"][16] = 95
        down["POS"][16] = -1
        hits = screen_payload("down", down, CONTRACT)
        self.assertEqual(hits["long_to_short"][0]["transition_date"], down["dates"][14])
        self.assertEqual(hits["long_to_short"][0]["transition_close"], 98)
        self.assertEqual(hits["long_to_short"][0]["confirmation_date"], down["dates"][16])
        self.assertEqual(hits["long_to_short"][0]["confirmation_close"], 90)
        self.assertEqual(hits["long_to_short"][0]["score_entry_source"], "转折K替代")
        self.assertEqual(hits["long_to_short"][0]["score_entry_date"], down["dates"][14])
        self.assertAlmostEqual(hits["long_to_short"][0]["score"], (90 - 94) / 94 * 100)
        self.assertEqual(hits["short_trend"][0]["trend_transition_label"], "多转空")

        equality = payload(n=17)
        equality["PQ"][13], equality["POS"][13] = True, 1
        equality["PR"][14], equality["PR"][15], equality["PR"][16] = True, True, True
        equality["ohlc"][14] = [95, 95, 94, 96]
        equality["ohlc"][15] = [95, 95, 94, 96]
        equality["ohlc"][16] = [95, 95, 94, 96]
        equality["EE"][14] = 95
        equality["EE"][15] = 95
        equality["EE"][16] = 95
        self.assertFalse(screen_payload("equal", equality, CONTRACT)["long_to_short"])

        interrupted = payload(n=17)
        interrupted["PQ"][13], interrupted["POS"][13] = True, 1
        interrupted["PR"][14], interrupted["PR"][15], interrupted["PR"][16] = True, True, True
        interrupted["ohlc"][14] = [98, 98, 97, 99]
        interrupted["EE"][14] = 95
        interrupted["PQ"][15] = True
        self.assertFalse(screen_payload("interrupted", interrupted, CONTRACT)["long_to_short"])

    def test_short_to_long_transition_and_warning(self):
        up = payload(n=17)
        up["PR"][13], up["POS"][13] = True, -1
        up["PQ"][14], up["PQ"][15], up["PQ"][16] = True, True, True
        up["ohlc"][14] = [102, 102, 101, 103]
        up["ohlc"][15] = [103, 103, 102, 104]
        up["ohlc"][16] = [110, 110, 109, 111]
        up["PP"][14] = 105
        up["PP"][15] = 105
        up["PP"][16] = 105
        up["POS"][16] = 1
        hits = screen_payload("up", up, CONTRACT)
        self.assertEqual(hits["short_to_long"][0]["transition_date"], up["dates"][14])
        self.assertEqual(hits["short_to_long"][0]["transition_boundary"], "PP")
        self.assertEqual(hits["short_to_long"][0]["confirmation_date"], up["dates"][16])
        self.assertEqual(hits["short_to_long"][0]["score_entry_source"], "转折K替代")
        self.assertEqual(hits["short_to_long"][0]["score_entry_date"], up["dates"][14])
        self.assertAlmostEqual(hits["short_to_long"][0]["score"], (110 - 106) / 106 * 100)
        self.assertEqual(hits["long_trend"][0]["trend_transition_label"], "空转多")

        warning = payload(close=100)
        warning["PQ"][-1], warning["POS"][-1] = True, -1
        warning["KK"][-1], warning["PP"][-1] = 95, 105
        self.assertEqual(len(screen_payload("warning", warning, CONTRACT)["short_to_long_warning"]), 1)

    def test_directional_sorting(self):
        results = {bucket: [] for bucket in BUCKETS}
        results["long_trend"] = [{"score": 0.2}, {"score": 0.8}]
        results["short_trend"] = [{"score": -0.2}, {"score": -1.1}]
        results["short_to_long"] = [{"score": 0.1}, {"score": 0.5}]
        results["long_to_short"] = [{"score": -0.1}, {"score": -0.7}]
        _sort_results(results)
        self.assertEqual([item["score"] for item in results["long_trend"]], [0.8, 0.2])
        self.assertEqual([item["score"] for item in results["short_trend"]], [-1.1, -0.2])
        self.assertEqual([item["score"] for item in results["short_to_long"]], [0.5, 0.1])
        self.assertEqual([item["score"] for item in results["long_to_short"]], [-0.7, -0.1])

    def test_warnings_and_report_rows(self):
        warning = payload(close=100)
        warning["PR"][-1], warning["POS"][-1] = True, 1
        warning["EE"][-1], warning["DD"][-1] = 95, 105
        self.assertEqual(len(screen_payload("warning", warning, CONTRACT)["long_to_short_warning"]), 1)

        report = {"buckets": {bucket: [] for bucket in BUCKETS}}
        report["buckets"]["long_to_short_warning"] = screen_payload("warning", warning, CONTRACT)["long_to_short_warning"]
        rows = flatten_report(report)
        self.assertEqual(rows[0]["bucket"], "long_to_short_warning")
        self.assertEqual(rows[0]["bucket_name"], "多转空预警")
        self.assertIn("score_entry_open", rows[0])
        self.assertIn("score_center", rows[0])

    def test_trend_band_warnings_retain_all_retests_in_latest_nine_bars(self):
        pressure = payload(close=100)
        for index in (7, 14):
            pressure["POS"][index] = -1
            pressure["KK"][index], pressure["PP"][index] = 95, 105
            pressure["ohlc"][index] = [99, 100, 98, 104]
        pressure["POS"][-1] = -1  # 当前仍持空、压力带有效，但自身不是一次回踩。
        pressure["KK"][-1], pressure["PP"][-1] = 95, 105
        pressure["ohlc"][-1] = [100, 100, 99, 106]
        pressure_hits = screen_payload("pressure", pressure, CONTRACT)["short_pressure_warning"]
        self.assertEqual(len(pressure_hits), 1)
        self.assertEqual(pressure_hits[0]["retest_dates"], [pressure["dates"][7], pressure["dates"][14]])
        self.assertEqual(pressure_hits[0]["retest_count"], 2)

        pressure_break = payload(close=106)
        pressure_break["POS"][-1] = -1
        pressure_break["KK"][-1], pressure_break["PP"][-1] = 95, 105
        pressure_break["ohlc"][-1] = [100, 106, 99, 104]
        self.assertFalse(screen_payload("pressure-break", pressure_break, CONTRACT)["short_pressure_warning"])

        pressure_closed_below_band = payload(close=94)
        pressure_closed_below_band["POS"][-1] = -1
        pressure_closed_below_band["KK"][-1], pressure_closed_below_band["PP"][-1] = 95, 105
        pressure_closed_below_band["ohlc"][-1] = [99, 94, 93, 104]
        self.assertTrue(screen_payload("pressure-closed-below-band", pressure_closed_below_band, CONTRACT)["short_pressure_warning"])

        pressure_at_upper_edge = payload(close=105)
        pressure_at_upper_edge["POS"][-1] = -1
        pressure_at_upper_edge["KK"][-1], pressure_at_upper_edge["PP"][-1] = 95, 105
        pressure_at_upper_edge["ohlc"][-1] = [100, 105, 99, 104]
        self.assertFalse(screen_payload("pressure-at-upper-edge", pressure_at_upper_edge, CONTRACT)["short_pressure_warning"])

        support = payload(close=100)
        for index in (7, 14):
            support["POS"][index] = 1
            support["EE"][index], support["DD"][index] = 95, 105
            support["ohlc"][index] = [99, 100, 97, 103]
        support["POS"][-1] = 1  # 当前仍持多、支撑带有效，但自身不是一次回踩。
        support["EE"][-1], support["DD"][-1] = 95, 105
        support["ohlc"][-1] = [100, 100, 94, 101]
        support_hits = screen_payload("support", support, CONTRACT)["long_support_warning"]
        self.assertEqual(len(support_hits), 1)
        self.assertEqual(support_hits[0]["retest_dates"], [support["dates"][7], support["dates"][14]])

        support_at_lower_edge = payload(close=95)
        support_at_lower_edge["POS"][-1] = 1
        support_at_lower_edge["EE"][-1], support_at_lower_edge["DD"][-1] = 95, 105
        support_at_lower_edge["ohlc"][-1] = [96, 95, 95, 100]
        self.assertFalse(screen_payload("support-at-lower-edge", support_at_lower_edge, CONTRACT)["long_support_warning"])

        support_closed_above_band = payload(close=106)
        support_closed_above_band["POS"][-1] = 1
        support_closed_above_band["EE"][-1], support_closed_above_band["DD"][-1] = 95, 105
        support_closed_above_band["ohlc"][-1] = [100, 106, 97, 107]
        self.assertTrue(screen_payload("support-closed-above-band", support_closed_above_band, CONTRACT)["long_support_warning"])

        ended_pressure = payload(close=100)
        ended_pressure["POS"][-2] = -1
        ended_pressure["KK"][-2], ended_pressure["PP"][-2] = 95, 105
        ended_pressure["ohlc"][-2] = [99, 100, 98, 104]
        self.assertFalse(screen_payload("ended-pressure", ended_pressure, CONTRACT)["short_pressure_warning"])

        ended_support = payload(close=100)
        ended_support["POS"][-2] = 1
        ended_support["EE"][-2], ended_support["DD"][-2] = 95, 105
        ended_support["ohlc"][-2] = [99, 100, 97, 103]
        self.assertFalse(screen_payload("ended-support", ended_support, CONTRACT)["long_support_warning"])


if __name__ == "__main__":
    unittest.main()
