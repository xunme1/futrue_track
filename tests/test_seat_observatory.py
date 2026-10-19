"""Production-template promotion, cache migration and archive compatibility."""
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from backend.api import server
from backend.pipeline import goldman_contract, seat_daily, seat_html, seat_observatory as report
from backend.pipeline.report_store import atomic_json, atomic_text, digest
from tests.test_seat_html import position_rows


class ObservatoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.date = "20260918"
        self.universe = [{"symbol": "MA", "name": "甲醇", "main": "MA2701", "sub": "MA2703"}]
        position_rows().to_csv(self.root / f"seat_data_{self.date}.csv", index=False)
        atomic_json(self.root / f"dominant_{self.date}.json", self.universe)

    def facts(self):
        return report.build_facts(position_rows(), self.date, "20260917", universe=self.universe,
                                  market_context={"MA": {"available": True, "contract": "MA2701"}})

    def publish(self, facts):
        signals = report.build_signals(facts)
        return report.publish_report(facts, report.fallback_narrative(facts, signals), signals,
                                     directory=self.root, html2canvas_source="window.html2canvas=()=>{};")

    def test_old_entry_only_forwards_to_production_renderer(self):
        self.assertIs(seat_html.render_html, report.render_html)
        self.assertIs(seat_html.main, report.main)
        self.assertFalse(hasattr(seat_html, "CSS"))
        self.assertFalse(hasattr(seat_html, "JS"))

    def test_offline_rebuild_recomputes_members_and_retains_verified_market(self):
        old = self.facts()
        old["groups"]["major"]["varieties"][0]["net_today"] = 999999
        self.publish(old)
        raw = self.root / "goldman_contract_cache" / self.date
        raw.mkdir(parents=True)
        atomic_json(goldman_contract._contract_cache_path(raw, "MA2701"),
                    {"data": {"data": position_rows().to_dict("records")}})
        with patch.object(report.requests, "get", side_effect=AssertionError("must stay offline")):
            facts = report.build_cached_facts(self.date, self.root)
        self.assertEqual(facts["groups"]["major"]["members"],
                         ["中信期货", "国泰君安", "东证期货", "永安期货"])
        self.assertEqual(facts["groups"]["major"]["varieties"][0]["net_today"], 8000)
        self.assertEqual(facts["market_context"], old["market_context"])
        contracts = facts["contract_positions"]
        self.assertEqual(contracts["coverage"]["cached_responses"], 1)
        self.assertEqual(contracts["coverage"]["request_errors"], 1)
        self.assertEqual(contracts["groups"]["major"]["varieties"][0]["main"]["today"], 8000)
        self.assertIsNone(contracts["groups"]["major"]["varieties"][0]["sub"]["today"])

    def test_cached_rebuild_rejects_corrupt_or_mismatched_archive(self):
        path, record_path = self.publish(self.facts())
        atomic_text(path, "corrupted")
        with self.assertRaisesRegex(ValueError, "校验失败"):
            report.build_cached_facts(self.date, self.root)
        self.publish(self.facts())
        record = json.loads(record_path.read_text())
        record["facts"]["prev_date"] = "20260916"
        atomic_json(record_path, record)
        with self.assertRaisesRegex(ValueError, "日期"):
            report.build_cached_facts(self.date, self.root)

    def test_missing_contracts_keeps_base_report_and_unknown_prices(self):
        facts = report.build_cached_facts(self.date, self.root)
        self.assertIsNone(facts["contract_positions"])
        self.assertEqual(facts["market_context"], {})
        html, _ = self.publish(facts)
        body = html.read_text()
        self.assertIn("暂无主次合约披露数据", body)
        self.assertIn('"contract_positions":null', body)
        self.assertIn("F.groups[k].members", body)

    def test_json_payload_escapes_script_breakouts_without_changing_data(self):
        facts = self.facts()
        hostile = '</script><img src=x onerror=alert(1)> @@HTML2CANVAS@@'
        facts["universe"][0]["name"] = hostile
        path, _ = self.publish(facts)
        body = path.read_text()
        payload = re.search(r'<script id="dataset" type="application/json">(.*?)</script>',
                            body, re.DOTALL).group(1)
        self.assertEqual(json.loads(payload)["facts"]["universe"][0]["name"], hostile)
        self.assertNotIn('</script><img', body)
        self.assertNotIn('<script src=', body)

    def test_publishing_new_date_preserves_old_archive_and_api_validation(self):
        old_body = "<html>legacy archive</html>"
        atomic_text(self.root / "seat_report_20260917.html", old_body)
        atomic_text(self.root / "seat_data_20260917.csv", "trade_date\n20260917\n")
        atomic_json(self.root / "seat_report_20260917.json",
                    {"data_date": "20260917", "html_hash": digest(old_body)})
        path, _ = self.publish(self.facts())
        with patch.object(server, "SEAT_DIR", self.root):
            items = server.seat_list()
            self.assertEqual([item["date"] for item in items], ["20260918", "20260917"])
            self.assertTrue(all(item["has_html"] for item in items))
            self.assertEqual(Path(server.seat_html(self.date).path), path)
            self.assertEqual(Path(server.seat_html("20260917").path).read_text(), old_body)
            atomic_text(path, "tampered")
            self.assertFalse(server.seat_list()[0]["has_html"])
            with self.assertRaises(server.HTTPException):
                server.seat_html(self.date)

    def test_daily_uses_new_renderer_and_uploads_only_newly_published_html(self):
        with patch.object(seat_daily, "SEAT_DIR", self.root), \
                patch.object(seat_daily, "setup_font"), patch.object(seat_daily, "render"), \
                patch("backend.pipeline.goldman_contract.generate"), \
                patch("backend.pipeline.summary_render.rerender_report_for_data_date"), \
                patch("backend.pipeline.dominant_fetch.load_or_fetch", return_value=(self.universe, None)), \
                patch.object(report, "fetch_market_context", return_value={}), \
                patch.object(report, "load_contract_positions", return_value=None), \
                patch.object(report, "load_goldman_contract", return_value=None), \
                patch.object(report, "publish_report") as publish, \
                patch("backend.pipeline.oss_upload.upload_seat_report") as upload:
            path = self.root / f"seat_report_{self.date}.html"
            publish.return_value = (path, path.with_suffix(".json"))
            seat_daily.main(["--date", self.date, "--no-report"])
            publish.assert_called_once()
            upload.assert_called_once_with(self.date, html_path=path)
            upload.reset_mock()
            publish.side_effect = RuntimeError("render failure")
            seat_daily.main(["--date", self.date, "--no-report"])
            upload.assert_not_called()


if __name__ == "__main__":
    unittest.main()
