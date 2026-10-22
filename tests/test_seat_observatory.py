"""Production-template promotion, cache migration and archive compatibility."""
import json
from pathlib import Path
import re
import shutil
import subprocess
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

    def run_dashboard_js(self, assertions):
        """Execute the generated report's real JS, without browser event wiring."""
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is needed to execute dashboard filter tests")
        path, _ = self.publish(self.facts())
        body = path.read_text()
        payload = re.search(r'<script id="dataset" type="application/json">(.*?)</script>',
                            body, re.DOTALL).group(1)
        script = re.search(r"<script>\s*('use strict';.*?)</script>", body, re.DOTALL).group(1)
        script = script.split("document.addEventListener('click',", 1)[0]
        harness = """
const {readFileSync}=require('node:fs'),vm=require('node:vm');
const input=JSON.parse(readFileSync(0,'utf8')),elements=new Map();
const document={getElementById(id){
  if(!elements.has(id))elements.set(id,{innerHTML:'',textContent:'',setAttribute(){}});
  return elements.get(id);
}};
document.getElementById('dataset').textContent=input.payload;
vm.runInNewContext(input.script,{document,assert:require('node:assert/strict'),
  localStorage:{getItem(){return null;}}},{timeout:5000});
"""
        result = subprocess.run([node, "-e", harness], input=json.dumps({
            "payload": payload, "script": script + "\n" + assertions,
        }), text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_missing_goldman_filter_requires_disclosed_opposite_nonzero_positions(self):
        self.run_dashboard_js("""
const a=row('goldman','MA'),b=row('major','MA'),c=row('retail','MA');
// Goldman disclosure, major disclosure/net, retail disclosure/net, expected.
const cases=[
  [false,true,100,true,-80,true], [false,true,-100,true,80,true],
  [false,true,100,true,80,false], [false,true,-100,true,-80,false],
  [false,false,100,true,-80,false], [false,true,100,false,-80,false],
  [false,true,0,true,-80,false], [false,true,100,true,0,false],
  [true,true,100,true,-80,false],
  [false,true,null,true,-80,false], [false,true,100,true,null,false],
  [false,true,NaN,true,-80,false], [false,true,Infinity,true,-80,false],
];
for(const [ga,ma,m,ra,r,expected] of cases){
  Object.assign(a,{available:ga,net_today:0});
  Object.assign(b,{available:ma,net_today:m});
  Object.assign(c,{available:ra,net_today:r});
  assert.equal(reverseNoGoldman('MA'),expected,JSON.stringify([ga,ma,m,ra,r]));
}
Object.assign(b,{available:true,net_today:100,partial:true});
Object.assign(c,{available:true,net_today:-80,partial:true});
assert.equal(reverseNoGoldman('MA'),true,'partial disclosure remains eligible');
delete index.goldman.MA;
assert.equal(reverseNoGoldman('MA'),true,'absent Goldman row is undisclosed');
delete index.major.MA;
assert.equal(reverseNoGoldman('MA'),false,'absent major row is ineligible');
""")

    def test_original_filters_remain_unchanged_and_new_filter_combines_with_search(self):
        self.run_dashboard_js(r"""
const a=row('goldman','MA'),b=row('major','MA'),c=row('retail','MA');
Object.assign(a,{available:true,net_today:50});
Object.assign(b,{available:true,net_today:100});
Object.assign(c,{available:true,net_today:-80});
for(const filter of ['all','same','reverse']){state.filter=filter;assert.equal(matches('MA'),true);}
state.filter='stars';assert.equal(matches('MA'),false);
stars.add('MA');assert.equal(matches('MA'),true);
state.filter='reverse-no-goldman';assert.equal(matches('MA'),false);
a.available=false;a.net_today=null;
assert.equal(matches('MA'),true);
assert.equal(same('MA'),false);assert.equal(reverse('MA'),false);
for(const query of ['甲醇','ma','MA2701','ma2703']){
  state.query=query;assert.equal(matches('MA'),true,query);
}
state.query='铜';assert.equal(matches('MA'),false);
state.query='';renderFilters();
const filters=$('filters').innerHTML;
assert.equal((filters.match(/class="chip"/g)||[]).length,5);
assert.match(filters,/data-filter="reverse-no-goldman" aria-pressed="true">机构散户反向（高盛未披露）<span class="count">1<\/span>/);
assert.match(filters,/data-filter="reverse" aria-pressed="false">机构散户反向<span class="count">0<\/span>/);
assert.match(directionFlag('MA'),/高盛未披露/);
a.available=true;a.net_today=50;
assert.equal(directionFlag('MA'),'<span class="flag reverse">机构散户反向</span>');
c.net_today=80;
assert.equal(directionFlag('MA'),'<span class="flag">机构同向</span>');
""")

    def test_new_filter_applies_to_overview_comparison_and_contracts(self):
        self.run_dashboard_js("""
Object.assign(row('goldman','MA'),{available:false,net_today:null});
Object.assign(row('major','MA'),{available:true,net_today:100});
Object.assign(row('retail','MA'),{available:true,net_today:-80});
state.filter='reverse-no-goldman';
renderOverview();assert.match($('seat-grid').innerHTML,/data-open="MA"/);
renderCompare();assert.match($('matrix-wrap').innerHTML,/高盛未披露/);
F.contract_positions={groups:{major:{coverage:{},varieties:[{symbol:'MA',
  main:{contract:'MA2701',available:true,today:100,previous:90,change:10,members:[]},
  sub:{contract:'MA2703',available:false,members:[]}}]}}};
state.contractGroup='major';state.view='contracts';state.contractMode='table';
renderContracts();assert.match($('contract-content').innerHTML,/MA2701/);
row('goldman','MA').available=true;
renderOverview();assert.doesNotMatch($('seat-grid').innerHTML,/data-open="MA"/);
renderCompare();assert.doesNotMatch($('matrix-wrap').innerHTML,/data-open="MA"/);
renderContracts();assert.doesNotMatch($('contract-content').innerHTML,/MA2701/);
""")

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
