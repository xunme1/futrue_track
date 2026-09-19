import tempfile
import unittest
import warnings
from pathlib import Path

import pandas as pd

from backend.pipeline import goldman_contract as gc


def row(day, member="高盛期货", long=0, short=0, long_change=None, short_change=None):
    value = {
        "trade_date": day,
        "member_name": member,
        "total_long": long,
        "total_short": short,
    }
    if long_change is not None:
        value["total_long_change"] = long_change
    if short_change is not None:
        value["total_short_change"] = short_change
    return value


class GoldmanContractTests(unittest.TestCase):
    def test_exact_goldman_match_and_direct_previous_row(self):
        rows = [
            row("20260916", long=100, short=20),
            row("20260917", long=140, short=30, long_change=40, short_change=10),
            row("20260917", member="乾坤期货", long=9999, short=0),
        ]
        value = gc.build_contract_position(rows, "CU2610", "20260917", "20260916")
        self.assertTrue(value["available"])
        self.assertEqual(value["today"], 110)
        self.assertEqual(value["previous"], 80)
        self.assertEqual(value["change"], 30)
        self.assertEqual(value["previous_source"], "previous_row")

    def test_previous_is_reconstructed_from_reported_change(self):
        rows = [row("20260917", long=80, short=150, long_change=-10, short_change=20)]
        value = gc.build_contract_position(rows, "RB2610", "20260917", "20260916")
        self.assertEqual(value["today"], -70)
        self.assertEqual(value["change"], -30)
        self.assertEqual(value["previous"], -40)
        self.assertEqual(value["previous_source"], "reported_change")

    def test_absent_contract_is_not_zero(self):
        value = gc.build_contract_position(
            [row("20260917", member="乾坤期货", long=100)],
            "AL2610", "20260917", "20260916",
        )
        self.assertFalse(value["available"])
        self.assertIsNone(value["today"])

    def test_contract_level_field_names(self):
        """繁微具体合约口径的字段是 long/short（非 total_long/total_short）。"""
        rows = [
            {"trade_date": "20260917", "member_name": "高盛期货",
             "volume": 0, "volume_change": 0,
             "long": 6485, "long_change": 446, "short": 0, "short_change": 0},
            {"trade_date": "20260918", "member_name": "高盛期货",
             "volume": 0, "volume_change": 0,
             "long": 6823, "long_change": 338, "short": 0, "short_change": 0},
        ]
        value = gc.build_contract_position(rows, "CU2611", "20260918", "20260917")
        self.assertTrue(value["available"])
        self.assertEqual(value["today"], 6823)
        self.assertEqual(value["previous"], 6485)
        self.assertEqual(value["change"], 338)
        self.assertEqual(value["previous_source"], "previous_row")

    def test_fetch_deduplicates_contracts_and_isolates_failure(self):
        calls = []

        def query(contract, start, end):
            calls.append(contract)
            if contract == "BAD":
                raise RuntimeError("boom")
            return {"data": {"data": [row(end, long=100, long_change=10)]}}

        dominants = [
            {"symbol": "CU", "main": "CU1", "sub": "SHARED"},
            {"symbol": "AL", "main": "AL1", "sub": "SHARED"},
            {"symbol": "ZN", "main": "BAD", "sub": None},
        ]
        fetched, errors, requested, succeeded = gc.fetch_contracts(
            dominants, "20260917", "20260916", query_fn=query, sleep_sec=0
        )
        self.assertEqual(calls.count("SHARED"), 1)
        self.assertEqual(requested, 4)
        self.assertEqual(succeeded, 3)
        self.assertIn("BAD", errors)
        self.assertFalse(fetched["BAD"]["available"])

    def test_contract_success_response_is_cached_individually(self):
        calls = []

        def query(contract, start, end):
            calls.append(contract)
            return {"data": {"data": [row(end, long=100, long_change=10)]}}

        dominants = [{"symbol": "CU", "main": "CU1", "sub": "CU2"}]
        with tempfile.TemporaryDirectory() as tmp:
            for _ in range(2):
                gc.fetch_contracts(
                    dominants, "20260917", "20260916", query_fn=query,
                    sleep_sec=0, cache_dir=Path(tmp),
                )
        self.assertEqual(calls, ["CU1", "CU2"])

    def test_mismatched_response_code_is_rejected_and_refetched(self):
        """繁微曾出现请求 M2701 返回 JM2701 的错配，校验后必须丢弃并重新请求。"""
        calls = []

        def query(contract, start, end):
            calls.append(contract)
            if contract == "M2701" and calls.count("M2701") == 1:
                return {"data": {"data": [
                    {"code": "JM2701", "trade_date": end, "member_name": "高盛期货",
                     "long": 999, "short": 0},
                ]}}
            if contract == "M2701":
                return {"data": {"data": [
                    {"code": "M2701", "trade_date": end, "member_name": "高盛期货",
                     "long": 100, "short": 0},
                ]}}
            return {"data": {"data": [row(end, long=100, long_change=10)]}}

        dominants = [{"symbol": "M", "main": "M2701", "sub": None},
                     {"symbol": "CU", "main": "CU1", "sub": None}]
        with tempfile.TemporaryDirectory() as tmp:
            fetched, errors, _, _ = gc.fetch_contracts(
                dominants, "20260917", "20260916", query_fn=query,
                sleep_sec=0, cache_dir=Path(tmp),
            )
            self.assertFalse(fetched["M2701"]["available"])
            self.assertIn("错配", errors["M2701"])
            # 错配响应未进缓存，重跑会重新请求并命中正确数据
            fetched2, errors2, _, _ = gc.fetch_contracts(
                dominants, "20260917", "20260916", query_fn=query,
                sleep_sec=0, cache_dir=Path(tmp),
            )
        self.assertEqual(calls.count("M2701"), 2)
        self.assertTrue(fetched2["M2701"]["available"])
        self.assertEqual(fetched2["M2701"]["today"], 100)
        self.assertEqual(errors2, {})

    def test_rq_member_rank_merges_long_and_short_boards(self):
        """米筐持多/持空两榜按 (日期, 会员) 外连接合并，未上榜一侧记 0。"""
        class FakeFutures:
            @staticmethod
            def get_member_rank(contract, trading_date=None, rank_by="volume", **kwargs):
                rows = {
                    "long": [
                        ("2026-09-17", "高盛期货", 1, 6485.0, 446.0),
                        ("2026-09-18", "高盛期货", 1, 6823.0, 338.0),
                        ("2026-09-18", "中信期货", 2, 5000.0, -10.0),
                    ],
                    "short": [
                        ("2026-09-18", "高盛期货", 5, 120.0, 30.0),
                    ],
                }[rank_by]
                return pd.DataFrame(
                    [(d, m, r, v, c) for d, m, r, v, c in rows],
                    columns=["trading_date", "member_name", "rank", "volume", "volume_change"],
                ).set_index("trading_date")

        class FakeRQ:
            futures = FakeFutures()

        old = gc._rq_client
        gc._rq_client = FakeRQ()
        try:
            resp = gc.rq_member_rank("CU2611", "20260917", "20260918")
        finally:
            gc._rq_client = old
        rows = resp["data"]["data"]
        gs = sorted(
            (r for r in rows if r["member_name"] == "高盛期货"),
            key=lambda r: r["trade_date"],
        )
        self.assertEqual(len(gs), 2)
        self.assertEqual(gs[0]["code"], "CU2611")
        # 09-17 只在持多榜：空侧记 0
        self.assertEqual((gs[0]["total_long"], gs[0]["total_short"]), (6823 - 338, 0))
        # 09-18 两榜都在
        self.assertEqual((gs[1]["total_long"], gs[1]["total_short"]), (6823, 120))
        self.assertEqual((gs[1]["total_long_change"], gs[1]["total_short_change"]), (338, 30))
        # 其它会员也保留
        self.assertTrue(any(r["member_name"] == "中信期货" for r in rows))

    def test_all_contract_requests_failing_aborts_addon(self):
        def query(*args):
            raise RuntimeError("down")

        with self.assertRaisesRegex(RuntimeError, "所有具体合约"):
            gc.fetch_contracts(
                [{"symbol": "CU", "main": "CU1", "sub": "CU2"}],
                "20260917", "20260916", query_fn=query, sleep_sec=0,
            )

    def test_dates_are_strict_and_previous_must_be_earlier(self):
        with self.assertRaisesRegex(ValueError, "YYYYMMDD"):
            gc.generate("2026-09-17", "20260916", dominants=[])
        with self.assertRaisesRegex(ValueError, "必须早于"):
            gc.generate("20260917", "20260917", dominants=[])

    def test_ranking_top_n_missing_sub_and_opposite_sub_direction(self):
        def pos(contract, today):
            return {"contract": contract, "available": True, "today": today,
                    "previous": today - 10, "change": 10,
                    "previous_source": "previous_row", "missing_reason": None}

        missing = {"contract": "MISSING", "available": False, "today": None,
                   "previous": None, "change": None, "previous_source": None,
                   "missing_reason": "none"}
        dominants = [
            {"symbol": "CU", "main": "CU1", "sub": "CU2"},
            {"symbol": "RB", "main": "RB1", "sub": "MISSING"},
            {"symbol": "AL", "main": "AL1", "sub": "AL2"},
            {"symbol": "ZN", "main": "NO", "sub": None},
        ]
        fetched = {
            "CU1": pos("CU1", 300), "CU2": pos("CU2", -40),
            "RB1": pos("RB1", 100), "MISSING": missing,
            "AL1": pos("AL1", -250), "AL2": pos("AL2", 25),
            "NO": missing,
        }
        bundle = gc.build_bundle(
            dominants, fetched, {}, "20260917", "20260916", top_n=1
        )
        self.assertEqual([r["symbol"] for r in bundle["rankings"]["long"]], ["CU"])
        self.assertEqual(bundle["rankings"]["long"][0]["sub"]["today"], -40)
        self.assertEqual([r["symbol"] for r in bundle["rankings"]["short"]], ["AL"])
        self.assertEqual(bundle["coverage"]["main_missing"], 1)
        self.assertEqual(bundle["coverage"]["sub_missing"], 2)

    def test_render_chart_writes_png_for_values_and_missing_rows(self):
        item = {
            "symbol": "CU", "name": "铜",
            "main": {"contract": "CU2610", "available": True, "today": 120,
                     "previous": -20, "change": 140},
            "sub": {"contract": "CU2611", "available": False, "today": None,
                    "previous": None, "change": None},
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "chart.png"
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                gc.render_chart([item], "long", "20260917", "20260916", path)
            self.assertTrue(path.exists())
            self.assertGreater(path.stat().st_size, 1000)
            self.assertEqual(path.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")

    def test_file_hash_detects_changed_chart(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "chart.png"
            path.write_bytes(b"first")
            before = gc._file_hash(path)
            path.write_bytes(b"second")
            self.assertNotEqual(before, gc._file_hash(path))


if __name__ == "__main__":
    unittest.main()
