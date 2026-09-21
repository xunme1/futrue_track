import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from backend.api import server
from backend.pipeline.report_store import digest
from backend.pipeline import seat_fetch
from backend.pipeline import seat_html


GROUP_MEMBERS = {
    "goldman": ["高盛期货"],
    "major": ["国泰君安", "中信期货", "永安期货"],
    "retail": ["东方财富", "徽商期货"],
}


def position_rows(symbol="MA", missing_prev_member=None):
    days = [f"202609{day:02d}" for day in range(7, 19)]
    rows = []
    for index, day in enumerate(days):
        for group, members in GROUP_MEMBERS.items():
            for member in members:
                if day == "20260917" and member == missing_prev_member:
                    continue
                if group in ("goldman", "major"):
                    long, short = 1000 + index * 100, 100
                else:
                    long, short = 100, 900 + index * 80
                rows.append({
                    "trade_date": day, "member_name": member,
                    "total_long": long, "total_short": short,
                    "symbol": symbol, "source": "finoview",
                })
        # 乾坤必须完全不影响高盛。
        rows.append({
            "trade_date": day, "member_name": "乾坤期货",
            "total_long": 999999, "total_short": 0,
            "symbol": symbol, "source": "finoview",
        })
    return pd.DataFrame(rows)


class SeatFetchTests(unittest.TestCase):
    def test_commodity_universe_excludes_financial_futures(self):
        rows = seat_fetch.commodity_universe("20260918", dominants=[
            {"symbol": "M", "main": "M1"},
            {"symbol": "IF", "main": "IF1"},
            {"symbol": "T", "main": "T1"},
            {"symbol": "AO", "main": "AO1"},
        ])
        self.assertEqual([row["symbol"] for row in rows], ["AO", "M"])

    def test_ricequant_is_the_only_source(self):
        class Futures:
            @staticmethod
            def get_member_rank(symbol, rank_by, start_date, end_date):
                return pd.DataFrame([{
                    "trading_date": "2026-09-18", "member_name": "高盛期货",
                    "volume": 20 if rank_by == "long" else 5, "volume_change": 2,
                }]).set_index("trading_date")

        class RQ:
            futures = Futures()

        rows, meta = seat_fetch.fetch_symbol("AO", "20260901", "20260918", RQ())
        self.assertEqual(meta["source"], "ricequant")
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["total_long"], rows[0]["total_short"]), (20, 5))

    def test_daike_suffix_is_normalized_to_company_name(self):
        class Futures:
            @staticmethod
            def get_member_rank(symbol, rank_by, start_date, end_date):
                return pd.DataFrame([{
                    "trading_date": "2026-09-18", "member_name": "徽商期货（代客）",
                    "volume": 7, "volume_change": 1,
                }]).set_index("trading_date")

        class RQ:
            futures = Futures()

        rows, meta = seat_fetch.fetch_symbol("SA", "20260901", "20260918", RQ())
        self.assertEqual(meta["source"], "ricequant")
        self.assertEqual({row["member_name"] for row in rows}, {"徽商期货"})

    def test_per_symbol_cache_skips_second_request(self):
        calls = []

        class Futures:
            @staticmethod
            def get_member_rank(symbol, rank_by, start_date, end_date):
                calls.append((symbol, rank_by))
                return pd.DataFrame([{
                    "trading_date": "2026-09-18", "member_name": "高盛期货",
                    "volume": 1, "volume_change": 0,
                }]).set_index("trading_date")

        class RQ:
            futures = Futures()

        with tempfile.TemporaryDirectory() as tmp:
            for _ in range(2):
                seat_fetch.fetch_all(
                    "20260901", "20260918", symbols=["M"], directory=tmp,
                    rq=RQ(), sleep_sec=0,
                )
        self.assertEqual(sorted(set(calls)), [("M", "long"), ("M", "short")])

    def test_failed_symbol_is_retried_instead_of_cached(self):
        calls = []

        class Futures:
            @staticmethod
            def get_member_rank(symbol, *args, **kwargs):
                calls.append(symbol)
                return pd.DataFrame()

        class RQ:
            futures = Futures()

        with tempfile.TemporaryDirectory() as tmp:
            for _ in range(2):
                seat_fetch.fetch_all(
                    "20260901", "20260918", symbols=["EC"], directory=tmp,
                    rq=RQ(), sleep_sec=0,
                )
            self.assertFalse((Path(tmp) / "EC.json").exists())
        self.assertEqual(calls, ["EC", "EC", "EC", "EC"])


class SeatFactsTests(unittest.TestCase):
    def facts(self, frame=None):
        return seat_html.build_facts(
            frame if frame is not None else position_rows(),
            "20260918", "20260917",
            universe=[{"symbol": "MA", "name": "甲醇", "main": "MA2701"}],
            source_manifest={"MA": {"source": "finoview"}},
            market_context={"MA": {
                "contract": "MA2701", "available": True,
                "price_return_pct": 1.2, "oi_change": 5000, "oi_change_pct": 2.0,
            }},
        )

    def test_exact_goldman_excludes_qiankun_and_builds_history(self):
        facts = self.facts()
        row = facts["groups"]["goldman"]["varieties"][0]
        expected = (1000 + 11 * 100) - 100
        self.assertEqual(row["net_today"], expected)
        self.assertNotEqual(row["net_today"], expected + 999999)
        self.assertEqual(row["history_valid_days"], 12)
        self.assertIsNotNone(row["position_pct_20d"])

    def test_partial_previous_day_counts_missing_member_as_zero(self):
        facts = self.facts(position_rows(missing_prev_member="永安期货"))
        row = facts["groups"]["major"]["varieties"][0]
        self.assertTrue(row["available"])
        # 昨日永安未披露按零计：净仓=国泰君安+中信期货两家（各 2000-100）
        self.assertEqual(row["net_prev"], 3800)
        self.assertEqual(row["prev_missing_members"], ["永安期货"])
        self.assertTrue(row["prev_partial"])
        self.assertFalse(row["prev_complete"])
        self.assertIsNotNone(row["net_change"])
        self.assertIsNotNone(row["action"])

    def test_partial_today_disclosure_marks_missing_members(self):
        frame = position_rows()
        frame = frame[~((frame["trade_date"] == "20260918")
                        & (frame["member_name"] == "徽商期货"))]
        facts = self.facts(frame)
        row = facts["groups"]["retail"]["varieties"][0]
        self.assertTrue(row["available"])
        self.assertTrue(row["partial"])
        self.assertFalse(row["today_complete"])
        self.assertEqual(row["missing_members"], ["徽商期货"])
        # 仅东方财富有披露：100 - (900 + 11*80)
        self.assertEqual(row["net_today"], -1680)

    def test_zero_position_disclosure_counts_as_undisclosed(self):
        frame = position_rows()
        mask = ((frame["trade_date"] == "20260918")
                & (frame["member_name"] == "高盛期货"))
        frame.loc[mask, "total_long"] = 0
        frame.loc[mask, "total_short"] = 0
        facts = self.facts(frame)
        row = facts["groups"]["goldman"]["varieties"][0]
        self.assertFalse(row["available"])
        self.assertIn("无有效披露", row["missing_reason"])

    def test_mixed_source_symbol_is_rejected_instead_of_silently_combined(self):
        frame = position_rows()
        frame.loc[frame.index[0], "source"] = "ricequant"
        facts = seat_html.build_facts(
            frame, "20260918", "20260917",
            universe=[{"symbol": "MA", "name": "甲醇"}],
            source_manifest={"MA": {"source": "finoview"}},
        )
        row = facts["groups"]["goldman"]["varieties"][0]
        self.assertEqual(row["source"], "mixed-invalid")
        self.assertFalse(row["available"])
        self.assertIn("拒绝计算", row["missing_reason"])

    def test_cross_group_signal_has_evidence_and_price_confirmation(self):
        facts = self.facts()
        signals = seat_html.build_signals(facts)
        self.assertEqual(signals[0]["category"], "institution_retail_divergence")
        self.assertEqual(signals[0]["evidence_id"], "institution_retail_divergence:MA")
        self.assertIn("价格同向", signals[0]["observation"])

    def test_narrative_rejects_unknown_evidence(self):
        facts = self.facts()
        signals = seat_html.build_signals(facts)
        narrative = seat_html.fallback_narrative(facts, signals)
        narrative["overall_summary"]["evidence_ids"] = ["made-up"]
        with self.assertRaisesRegex(ValueError, "证据"):
            seat_html.validate_narrative(narrative, signals)

    def test_narrative_rejects_wrong_direction_and_unknown_product(self):
        facts = self.facts()
        signals = seat_html.build_signals(facts)
        evidence_id = signals[0]["evidence_id"]
        narrative = seat_html.fallback_narrative(facts, signals)
        evidence_ids = narrative["overall_summary"]["evidence_ids"]
        self.assertIn(evidence_id, evidence_ids)
        narrative["overall_summary"] = {
            "text": "甲醇 MA 的高盛净空，与输入证据中的实际席位方向明确相反。",
            "evidence_ids": evidence_ids,
        }
        with self.assertRaisesRegex(ValueError, "方向"):
            seat_html.validate_narrative(narrative, signals)
        narrative["overall_summary"] = {
            "text": "甲醇 MA 的高盛翻空，但该动作与输入证据中的实际变化相反。",
            "evidence_ids": evidence_ids,
        }
        with self.assertRaisesRegex(ValueError, "动作"):
            seat_html.validate_narrative(narrative, signals)
        narrative["overall_summary"] = {
            "text": "不存在的新品种 ZZ 出现值得关注的席位变化，需要进一步核对。",
            "evidence_ids": evidence_ids,
        }
        with self.assertRaisesRegex(ValueError, "品种"):
            seat_html.validate_narrative(narrative, signals)

    def test_fallback_narrative_satisfies_strict_evidence_schema(self):
        facts = self.facts()
        signals = seat_html.build_signals(facts)
        narrative = seat_html.fallback_narrative(facts, signals)
        self.assertIs(seat_html.validate_narrative(narrative, signals), narrative)
        self.assertTrue(all(
            note["evidence_ids"] for note in narrative["group_notes"].values()
        ))

    def test_model_validation_failure_uses_deterministic_fallback(self):
        facts = self.facts()
        signals = seat_html.build_signals(facts)
        calls = []

        class Response:
            status_code = 200

            @staticmethod
            def json():
                return {"choices": [{"message": {"content": "{}"}}]}

        def invalid_call(*args):
            calls.append(args[1])
            return Response()

        narrative = seat_html.generate_narrative(
            facts, signals, "test-key", call_fn=invalid_call,
        )
        self.assertEqual(len(calls), 4)
        self.assertTrue(narrative["meta"]["validated"])
        self.assertIn("校验失败", narrative["meta"]["fallback_reason"])

    def test_market_context_uses_same_contract_with_single_contract_frame(self):
        class RQ:
            @staticmethod
            def get_price(contracts, **kwargs):
                self = pd.DataFrame([
                    {"date": "2026-09-17", "close": 2000, "open_interest": 10000},
                    {"date": "2026-09-18", "close": 2040, "open_interest": 10500},
                ])
                return self.set_index("date")

        context = seat_html.fetch_market_context(
            [{"symbol": "MA", "main": "MA2701"}],
            "20260918", "20260917", rq=RQ(),
        )
        self.assertEqual(context["MA"]["contract"], "MA2701")
        self.assertEqual(context["MA"]["price_return_pct"], 2.0)
        self.assertEqual(context["MA"]["oi_change"], 500)

    def test_html_has_three_sections_controls_and_offline_export(self):
        facts = self.facts()
        signals = seat_html.build_signals(facts)
        narrative = seat_html.fallback_narrative(facts, signals)
        body = seat_html.render_html(
            facts, narrative, signals,
            "window.html2canvas=async function(){return {toBlob:function(){}}};",
        )
        self.assertLess(body.index("高盛席位"), body.index("主力席位"))
        self.assertLess(body.index("主力席位"), body.index("散户席位"))
        self.assertIn('state={limit:10,selected:[],query:\'\'}', body)
        self.assertIn("最多同时选择 20 个品种", body)
        self.assertIn("导出 PNG", body)
        self.assertIn("html2canvas", body)
        self.assertIn("未披露按零计", body)
        self.assertIn("overviewStrip", body)
        self.assertIn(".badge.long", body)

    def test_publish_archives_matching_html_hash(self):
        facts = self.facts()
        signals = seat_html.build_signals(facts)
        narrative = seat_html.fallback_narrative(facts, signals)
        with tempfile.TemporaryDirectory() as tmp:
            html_path, json_path = seat_html.publish_report(
                facts, narrative, signals, directory=tmp,
                html2canvas_source="window.html2canvas=function(){};",
            )
            record = json.loads(json_path.read_text())
            self.assertEqual(record["html_hash"], digest(html_path.read_text()))
            self.assertEqual(record["data_date"], "20260918")


class SeatApiTests(unittest.TestCase):
    def test_html_report_is_listed_and_served_with_hash_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "seat_data_20260918.csv").write_text("trade_date\n20260918\n")
            body = "<html>seat</html>"
            (root / "seat_report_20260918.html").write_text(body)
            (root / "seat_report_20260918.json").write_text(json.dumps({
                "data_date": "20260918", "html_hash": digest(body),
                "summary": "今日摘要", "generated_at": "2026-09-18T18:05:00+08:00",
            }))
            with patch.object(server, "SEAT_DIR", root):
                rows = server.seat_list()
                response = server.seat_html("20260918")
            self.assertTrue(rows[0]["has_html"])
            self.assertEqual(rows[0]["summary"], "今日摘要")
            self.assertEqual(Path(response.path).name, "seat_report_20260918.html")


if __name__ == "__main__":
    unittest.main()
