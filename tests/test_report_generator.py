"""日报关键口径回归；全部使用小型合成快照，不依赖行情服务。"""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from backend.pipeline.generate_report import (
    BUCKETS, compute_facts, data_day, distance, fallback_narrative, main,
    make_sections, merge_narrative, next_report_day, prompt_package,
    render_html, render_markdown,
)


def bundle(day="2026-09-14", positions=(1, 0), keys=("rb2610",)):
    result = {"contracts": [{"key": k, "name": "测试" + k} for k in keys]}
    for tf, pos in zip(("1d", "4h"), positions):
        rows = {b: [] for b in BUCKETS}
        symbols = []
        for key in keys:
            stamp = day if tf == "1d" else day + " 15:00"
            signal = {1: "BK", 0: "SP", -1: "SK"}[pos]
            row = {"key": key, "name": key, "date": stamp, "POS": pos, "close": 101,
                   "score": 0.2, "DD": 102, "EE": 100, "KK": 104, "PP": 105}
            bucket = "long_trend" if pos == 1 else "short_trend" if pos == -1 else "long_to_short"
            if pos == 0:
                row["signal_date"] = stamp
            rows[bucket].append(row)
            symbols.append({"key": key, "last_date": stamp, "pos": pos, "last_signal": {"type": signal, "date": stamp}})
        result[f"screen_{tf}"] = {"timeframe": tf, "data_date": day, "generated_at": day + "T17:30:00+08:00", "buckets": rows,
                                 "summary": {b: len(rs) for b, rs in rows.items()}}
        result[f"symbols_{tf}"] = symbols
    return result


class ReportGeneratorTests(unittest.TestCase):
    def test_daily_neutral_four_hour_short_is_not_resonance(self):
        f = compute_facts(bundle(positions=(0, -1)))
        self.assertEqual(f["instruments"][0]["verdict"], "日线观望 / 4h 持空")

    def test_daily_long_four_hour_short_is_divergence(self):
        self.assertIn("分歧", compute_facts(bundle(positions=(1, -1)))["instruments"][0]["verdict"])

    def test_both_short_required(self):
        self.assertEqual(compute_facts(bundle(positions=(-1, -1)))["instruments"][0]["verdict"], "双级别持空")

    def test_no_history_not_everything_new(self):
        f = compute_facts(bundle())
        v = f["overview"]["1d"]["long_trend"]
        self.assertIsNone(v["previous_count"])
        self.assertEqual(v["entered"], [])
        self.assertIn("无基线", render_markdown(f, fallback_narrative(f)))

    def test_data_day_not_generation_day(self):
        b = bundle("2026-09-11")
        b["screen_1d"]["generated_at"] = "2026-09-13T12:00:00+08:00"
        self.assertEqual(data_day(b["screen_1d"]), "2026-09-11")
        self.assertEqual(compute_facts(b)["report_date"], "2026-09-14")

    def test_stale_four_hour_disables_cross_period(self):
        b = bundle(positions=(1, 1))
        old = bundle("2026-09-11", positions=(1, 1))
        b["screen_4h"], b["symbols_4h"] = old["screen_4h"], old["symbols_4h"]
        f = compute_facts(b)
        self.assertEqual(f["instruments"][0]["verdict"], "跨周期待核验")
        self.assertFalse(f["header"]["synchronized"])

    def test_missing_authority_not_neutral(self):
        b = bundle()
        b["symbols_1d"] = []
        f = compute_facts(b)
        self.assertIsNone(f["instruments"][0]["daily"]["pos"])
        self.assertEqual(f["daily_actions"]["BK"], [])

    def test_missing_historical_state_disables_delta(self):
        before = bundle("2026-09-11")
        before["symbols_1d"] = []
        f = compute_facts(bundle(), before)
        self.assertIsNone(f["overview"]["1d"]["long_trend"]["previous_count"])

    def test_missing_bucket_is_error_not_zero(self):
        b = bundle()
        del b["screen_1d"]["buckets"]["long_trend"]
        with self.assertRaisesRegex(ValueError, "缺少分桶"):
            compute_facts(b)

    def test_repair_requires_history(self):
        current = bundle(positions=(1, 1))
        self.assertFalse(compute_facts(current)["instruments"][0]["repaired"])
        old = bundle("2026-09-11")
        self.assertTrue(compute_facts(current, old)["instruments"][0]["repaired"])

    def test_four_hour_break_without_peers_not_sector_risk(self):
        b = bundle()
        h = b["screen_4h"]["buckets"]["long_to_short"][0]
        h.update(score=-2, close=99)
        f = compute_facts(b)
        self.assertNotIn("4h破位且板块偏弱", f["instruments"][0]["hits"])

    def test_two_contracts_of_same_product_not_sector_peers(self):
        b = bundle(keys=("rb2610", "rb2701"))
        for h in b["screen_4h"]["buckets"]["long_to_short"]:
            h.update(score=-2, close=99)
        self.assertFalse(compute_facts(b)["sectors"][0]["linked"])

    def test_opposite_historical_transition_removed(self):
        b = bundle(positions=(1, 1))
        base = copy.deepcopy(b["screen_4h"]["buckets"]["long_trend"][0])
        base["signal_date"] = "2026-09-11 15:00"
        b["screen_4h"]["buckets"]["long_to_short"] = [base]
        f = compute_facts(b)
        self.assertNotIn("long_to_short", f["instruments"][0]["four_hour"]["memberships"])

    def test_signal_timestamp_not_transition_timestamp(self):
        b = bundle()
        b["screen_4h"]["buckets"]["long_to_short"][0]["signal_date"] = "2026-09-11 11:30"
        row = compute_facts(b)["instruments"][0]["four_hour"]
        self.assertEqual(row["last_signal"]["date"], "2026-09-14 15:00")
        self.assertEqual(row["events"]["long_to_short"], "2026-09-11 11:30")

    def test_missing_levels_do_not_become_zero(self):
        b = bundle()
        del b["screen_1d"]["buckets"]["long_trend"][0]["EE"]
        f = compute_facts(b)
        self.assertIsNone(f["instruments"][0]["daily"]["ee_distance"])
        self.assertIn("EE 缺失", f["instruments"][0]["condition"])

    def test_percentage_denominator_is_level(self):
        self.assertAlmostEqual(distance(110, 100), 10)
        self.assertIsNone(distance(0, 0))

    def test_rank_streak_stops_at_reversal(self):
        b = bundle()
        row = b["screen_1d"]["buckets"]["long_trend"][0]
        row.update(rank=4, previous_rank=10, rank_change=6, rank_history=[{"rank": n} for n in [19, 21, 14, 14, 12, 10, 4]])
        self.assertEqual(compute_facts(b)["rank_radar"][0]["streak"], 3)

    def test_calendar_and_explicit_date(self):
        self.assertEqual(next_report_day("2026-09-30", calendar=["2026-09-30", "2026-10-08"])[0], "2026-10-08")
        with self.assertRaises(ValueError):
            next_report_day("2026-09-30", calendar=["2026-09-30"])
        with self.assertRaises(ValueError):
            next_report_day("2026-09-30", explicit="2026-09-29")

    def test_narrative_hash_and_html_safety(self):
        f = compute_facts(bundle())
        with self.assertRaisesRegex(ValueError, "input_hash"):
            merge_narrative(f, {"report_date": f["report_date"], "input_hash": "stale"})
        n = {"report_date": f["report_date"], "input_hash": f["input_hash"], "sections": {"rank": "<script>alert(1)</script>"}}
        with self.assertRaisesRegex(ValueError, "纯文本"):
            merge_narrative(f, n)
        f["instruments"][0]["name"] = "<img src=x onerror=alert(1)>"
        self.assertNotIn("<img src=x", render_html(f, fallback_narrative(f)))

    def test_all_sections_share_same_columns_in_both_outputs(self):
        f = compute_facts(bundle())
        n = fallback_narrative(f)
        for s in make_sections(f, n):
            self.assertTrue(all(len(r) == len(s["headers"]) for r in s["rows"]), s["id"])
            self.assertIn(s["title"], render_markdown(f, n))
        self.assertEqual(set(prompt_package(f)["stages"]), {"analysis", "editor", "schema"})

    def test_cli_repeat_retains_baseline_and_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, b in (("latest", bundle()), ("previous", bundle("2026-09-11"))):
                directory = root / name
                directory.mkdir()
                for key, value in b.items():
                    filename = "contracts.json" if key == "contracts" else key + "_now.json"
                    (directory / filename).write_text(json.dumps(value), encoding="utf-8")
            args = ["--input-dir", str(root / "latest"), "--output-dir", str(root / "out")]
            main([*args, "--previous-dir", str(root / "previous")])
            file = root / "out/daily_report_2026-09-15.json"
            first = json.loads(file.read_text())["facts"]
            main(args)
            second = json.loads(file.read_text())["facts"]
            self.assertEqual(first["input_hash"], second["input_hash"])
            self.assertEqual(second["header"]["previous_date_1d"], "2026-09-11")
            self.assertTrue(file.with_suffix(".html").exists())
            self.assertTrue(file.with_suffix(".md").exists())
    def test_cohort_groups_by_entry_date(self):
        b = bundle(keys=("a2601", "b2601", "c2601"), positions=(1, 1))
        rows = b["screen_1d"]["buckets"]["long_trend"]
        for r in rows:
            r["score_entry_date"] = "2026-08-31"
        rows[0]["close"] = 99   # 破 EE 100 → 受压且风险升为重点
        rows[1]["close"] = 103  # 距 EE 约 3%，不贴线
        rows[2]["close"] = 103
        f = compute_facts(b)
        self.assertEqual(len(f["cohorts"]), 1)
        c = f["cohorts"][0]
        self.assertEqual((c["entry_date"], c["side"], c["size"]), ("2026-08-31", "多头", 3))
        self.assertEqual(c["stressed"], 1)
        self.assertEqual(c["stressed_keys"], ["a2601"])
        self.assertEqual(c["flagged"], 1)
        # 同批不足 3 只不成批
        small = bundle(keys=("a2601", "b2601"), positions=(1, 1))
        for r in small["screen_1d"]["buckets"]["long_trend"]:
            r["score_entry_date"] = "2026-08-31"
        self.assertEqual(compute_facts(small)["cohorts"], [])

    def test_board_trend_from_rank_totals(self):
        b = bundle()
        b["screen_1d"]["buckets"]["long_trend"][0]["rank_history"] = [
            {"date": "2026-09-11", "rank": 1, "total": 40}, {"date": "2026-09-14", "rank": 1, "total": 35}]
        f = compute_facts(b)
        self.assertEqual(f["board_trend"]["dates"], ["2026-09-11", "2026-09-14"])
        self.assertEqual(f["board_trend"]["long_trend"], [40, 35])
        self.assertIsNone(f["board_trend"]["short_trend"])
        # 行间 total 不一致则整体弃用，不拼接错误口径
        b2 = bundle(keys=("a2601", "b2601"), positions=(1, 1))
        b2["screen_1d"]["buckets"]["long_trend"][0]["rank_history"] = [{"date": "2026-09-11", "rank": 1, "total": 40}]
        b2["screen_1d"]["buckets"]["long_trend"][1]["rank_history"] = [{"date": "2026-09-11", "rank": 2, "total": 41}]
        f2 = compute_facts(b2)
        self.assertIsNone(f2["board_trend"]["long_trend"])
        self.assertTrue(any("不一致" in n for n in f2["quality_notes"]))

    def test_bucket_history_seeded_and_accumulated(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, b in (("latest", bundle()), ("previous", bundle("2026-09-11"))):
                directory = root / name
                directory.mkdir()
                for key, value in b.items():
                    filename = "contracts.json" if key == "contracts" else key + "_now.json"
                    (directory / filename).write_text(json.dumps(value), encoding="utf-8")
            args = ["--input-dir", str(root / "latest"), "--output-dir", str(root / "out")]
            main([*args, "--previous-dir", str(root / "previous")])
            main(args)
            facts = json.loads((root / "out/daily_report_2026-09-15.json").read_text())["facts"]
            self.assertEqual(facts["bucket_trend"]["long_trend"], [["2026-09-11", 1], ["2026-09-14", 1]])
            history = json.loads((root / "out/counts_history.json").read_text())["1d"]
            self.assertEqual(sorted(history), ["2026-09-11", "2026-09-14"])
            self.assertEqual(history["2026-09-14"]["long_trend"], 1)


if __name__ == "__main__":
    unittest.main()
