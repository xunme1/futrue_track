import copy
import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.api import server
from backend.pipeline.screen import screen_contracts, screen_payload, _sort_results, write_report
from tests.test_screen import payload


DATES = ["2026-08-06", "2026-08-07", "2026-08-10", "2026-08-11",
         "2026-08-12", "2026-08-13", "2026-08-14", "2026-08-17"]


def history(closes, positions=None, dates=None):
    data = payload(len(closes))
    data["dates"] = dates or DATES[-len(closes):]
    data["ohlc"] = [[100, close, min(99, close - 1), max(101, close + 1)] for close in closes]
    data["POS"] = positions if positions is not None else [1] * len(closes)
    return data


def truncate(data, end):
    count = sum(date <= end for date in data["dates"])
    out = {key: value[:count] if isinstance(value, list) and key != "signals" else copy.deepcopy(value)
           for key, value in data.items()}
    out["signals"] = [signal for signal in data.get("signals", []) if signal["i"] < count]
    return out


class TrendRankingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def report(self, data, missing=(), timeframe="1d"):
        contracts = []
        for key, value in data.items():
            contract = {"symbol": f"{key}.TEST", "name": key, "category": "test", "exchange": "TEST"}
            contracts.append(contract)
            (self.root / f"{key}.json").write_text(json.dumps(value), encoding="utf-8")
        contracts.extend({"symbol": f"{key}.TEST", "name": key} for key in missing)
        return screen_contracts(contracts, json_dir=self.root, timeframe=timeframe)

    def test_long_and_short_moves_and_stable_ties(self):
        data = {
            "A": history([100, 105, 120]),
            "B": history([100, 110, 110]),
            "C": history([100, 101, 101]),
            "S": history([100, 95, 80], [-1] * 3),
            "T": history([100, 90, 90], [-1] * 3),
        }
        report = self.report(data)
        for bucket in ("long_trend", "short_trend"):
            first, second = report["buckets"][bucket][:2]
            self.assertEqual((first["rank"], first["previous_rank"], first["rank_change"], first["rank_status"]), (1, 2, 1, "up"))
            self.assertEqual((second["rank_change"], second["rank_status"]), (-1, "down"))
        self.assertEqual(report["buckets"]["long_trend"][2]["rank_status"], "flat")
        tied = self.report({"Z": history([110, 110]), "A": history([110, 110])})
        self.assertEqual([row["key"] for row in tied["buckets"]["long_trend"]], ["Z", "A"])
        self.assertEqual(tied["buckets"]["long_trend"][1]["rank_history"][0]["rank"], 2)

    def test_entries_and_exits_change_full_leaderboard_rank(self):
        report = self.report({
            "steady": history([110, 110, 110]),
            "exit": history([120, 120, 100], [1, 1, 0]),
            "new": history([100, 100, 130], [0, 0, 1]),
        })
        new, steady = report["buckets"]["long_trend"]
        self.assertEqual(new["rank_status"], "new")
        self.assertEqual([point["rank"] for point in new["rank_history"]], [None, None, 1])
        self.assertEqual(steady["rank_status"], "flat")
        exited = self.report({"steady": history([110, 110]), "exit": history([120, 100], [1, 0])})
        self.assertEqual(exited["buckets"]["long_trend"][0]["rank_change"], 1)

    def test_reentry_masks_previous_same_direction_segment(self):
        report = self.report({"A": history([110] * 8, [1, 1, 1, 0, -1, 0, 1, 1])})
        item = report["buckets"]["long_trend"][0]
        self.assertEqual(len(item["rank_history"]), 7)
        self.assertEqual([point["rank"] for point in item["rank_history"]], [None] * 5 + [1, 1])
        self.assertEqual(item["rank_status"], "flat")
        self.assertEqual(report["trend_ranking"]["dates"], DATES[-7:])

    def test_gaps_do_not_carry_forward_or_compare_across_missing_bar(self):
        data = {
            "gap": history([120, 120], dates=[DATES[-3], DATES[-1]]),
            "full": history([110, 110, 110]),
        }
        item = self.report(data)["buckets"]["long_trend"][0]
        self.assertEqual([point["rank"] for point in item["rank_history"]], [1, None, 1])
        self.assertEqual([point["total"] for point in item["rank_history"]], [2, 1, 2])
        self.assertIsNone(item["previous_rank"])
        self.assertEqual(item["rank_status"], "unavailable")

    def test_stale_and_missing_files_excluded_only_from_trend_buckets(self):
        stale = history([100, 100], dates=DATES[-3:-1])
        stale["PR"][-1] = True  # Still qualifies for the legacy conversion warning.
        report = self.report({"fresh": history([110] * 3), "stale": stale}, missing=["missing"])
        self.assertEqual([row["key"] for row in report["buckets"]["long_trend"]], ["fresh"])
        self.assertEqual(report["summary"]["long_trend"], 1)
        self.assertEqual([row["key"] for row in report["buckets"]["long_to_short_warning"]], ["stale"])
        self.assertEqual([row["key"] for row in report["trend_ranking"]["excluded_symbols"]], ["stale", "missing"])
        self.assertEqual(report["trend_ranking"]["as_of"], DATES[-1])

    def test_invalid_scores_stay_last_without_arrow_and_one_bar_is_unavailable(self):
        invalid = history([100, 110])
        invalid["ohlc"][0][0] = None
        report = self.report({"bad": invalid, "good": history([100, 110])})
        item = report["buckets"]["long_trend"][-1]
        self.assertEqual(item["key"], "bad")
        self.assertEqual(item["rank"], 2)
        self.assertIsNone(item["rank_change"])
        self.assertEqual(item["rank_status"], "unavailable")
        self.assertEqual([point["rank"] for point in item["rank_history"]], [None, None])
        single = self.report({"A": history([110])})["buckets"]["long_trend"][0]
        self.assertEqual(len(single["rank_history"]), 1)
        self.assertEqual(single["rank_status"], "unavailable")

    def test_historical_ranks_equal_independently_truncated_screens(self):
        data = {
            "A": history([100, 108, 109, 112, 113, 110, 120, 118]),
            "B": history([100, 105, 108, 111, 115, 117, 118, 119]),
            "C": history([100, 90, 80, 85, 88, 87, 90, 91], [-1] * 8),
        }
        for value in data.values():
            value["signals"] = [{"i": 0, "type": "BK" if value["POS"][0] == 1 else "SK"}]
        # Only a direction-appropriate entry at or before each date may be used.
        data["A"]["signals"].append({"i": 7, "type": "BK"})
        data["A"]["ohlc"][7][0] = 115
        report = self.report(data)
        for date in report["trend_ranking"]["dates"]:
            expected = {"long_trend": [], "short_trend": []}
            for key, value in data.items():
                truncated = truncate(value, date)
                screened = screen_payload(key, truncated, {"symbol": f"{key}.TEST"})
                for bucket in expected:
                    expected[bucket].extend(screened[bucket])
            _sort_results(expected)
            for bucket, items in expected.items():
                mapping = {item["key"]: i for i, item in enumerate(items, 1)}
                for item in report["buckets"][bucket]:
                    point = next(point for point in item["rank_history"] if point["date"] == date)
                    self.assertEqual(point["rank"], mapping[item["key"]])
        original = copy.deepcopy(report)
        data["A"]["ohlc"][-1] = [700, 1000, 600, 1100]
        revised = self.report(data)
        old = {item["key"]: item for item in original["buckets"]["long_trend"]}
        for item in revised["buckets"]["long_trend"]:
            self.assertEqual(item["rank_history"][:-1], old[item["key"]]["rank_history"][:-1])

    def test_api_csv_serialization_and_read_once(self):
        data = {"A": history([100, 110]), "B": history([100, 105])}
        self.report(data)
        contracts = [{"symbol": f"{key}.TEST"} for key in data]
        with patch("builtins.open", wraps=open) as read:
            report = screen_contracts(contracts, json_dir=self.root)
            self.assertEqual(read.call_count, len(data))
        json_path, csv_path = write_report(report, self.root / "output")
        with patch.object(server, "screening_file", return_value=json_path):
            self.assertEqual(server.screening(), report)
        with csv_path.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(json.loads(rows[0]["rank_history"]), report["buckets"]["long_trend"][0]["rank_history"])

    def test_four_hour_reports_and_empty_pool_are_compatible(self):
        value = history([100] * 8)
        value["bar_colors"] = ["red"] * 8
        report = self.report({"A": value}, timeframe="4h")
        self.assertNotIn("trend_ranking", report)
        self.assertNotIn("rank_history", report["buckets"]["long_trend"][0])
        empty = self.report({})
        self.assertIsNone(empty["trend_ranking"]["as_of"])
        self.assertEqual(empty["buckets"]["long_trend"], [])


if __name__ == "__main__":
    unittest.main()
