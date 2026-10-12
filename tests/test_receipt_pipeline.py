"""仓单抓取、历史补跑及 HTML 缺失值回归测试；不连接外部接口。"""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from backend.pipeline import receipt_daily, receipt_fetch, receipt_html
from backend.pipeline.receipt_core import HISTORY_COLUMNS
from backend.pipeline.report_store import digest
from tests.test_receipt_core import make_hist


def receipt_rows(symbol, dates, values=None):
    frame = make_hist(values or [100] * len(dates), exchange="DCE")
    frame["symbol"] = symbol
    frame["date"] = dates
    return frame


class ReceiptPipelineTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="receipt-test-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.history = self.root / "history"
        self.history.mkdir()
        for module in (receipt_fetch, receipt_html):
            for name, value in (("HISTORY_DIR", self.history), ("RECEIPT_DIR", self.root)):
                patcher = patch.object(module, name, value)
                patcher.start()
                self.addCleanup(patcher.stop)
        patcher = patch.object(receipt_fetch, "commodity_symbols", return_value=["M", "EC"])
        self.symbols = patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(receipt_fetch, "multiplier_map", return_value={"M": 10})
        self.multipliers = patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(receipt_html, "contract_map", return_value={})
        patcher.start()
        self.addCleanup(patcher.stop)

    def seed(self):
        receipt_rows("M", ["20261009", "20261012"], [100, 200]).to_csv(
            self.history / "M.csv", index=False)

    @staticmethod
    def response(rq, symbols, start, target):
        if "M" in symbols:
            return receipt_rows("M", [target])
        return pd.DataFrame(columns=HISTORY_COLUMNS)

    def status(self):
        return json.loads((self.root / "fetch_status.json").read_text())

    def test_new_symbol_backfill_does_not_expand_existing_requests(self):
        self.seed()
        with patch.object(receipt_fetch, "fetch_frame", side_effect=self.response) as fetch:
            receipt_fetch.run(date="20261012", rq=object())
        calls = [(c.args[1], c.args[2], c.args[3]) for c in fetch.call_args_list]
        self.assertEqual(calls, [(["EC"], "20210101", "20261012"),
                                 (["M"], "20260922", "20261012")])
        self.assertEqual(self.status()["EC"]["status"], "no_data")

    def test_no_data_symbol_retries_incrementally_and_can_recover(self):
        self.seed()
        with patch.object(receipt_fetch, "fetch_frame", side_effect=self.response) as fetch:
            receipt_fetch.run(date="20261012", rq=object())
            fetch.reset_mock()
            receipt_fetch.run(date="20261013", rq=object())
            self.assertEqual(len(fetch.call_args_list), 1)
            self.assertEqual(fetch.call_args.args[1:], (["M", "EC"], "20260923", "20261013"))
        self.assertEqual(self.status()["EC"]["checked_from"], "20210101")
        self.assertEqual(self.status()["EC"]["checked_through"], "20261013")
        recovered = pd.concat([receipt_rows(s, ["20261014"]) for s in ("M", "EC")])
        with patch.object(receipt_fetch, "fetch_frame", return_value=recovered):
            receipt_fetch.run(date="20261014", rq=object())
        self.assertTrue((self.history / "EC.csv").exists())
        self.assertEqual(self.status()["EC"]["status"], "available")

    def test_all_empty_responses_are_cached_without_fake_history(self):
        self.symbols.return_value = ["EC"]
        with patch.object(receipt_fetch, "fetch_frame", side_effect=self.response) as fetch:
            self.assertEqual(receipt_fetch.run(date="20261012", rq=object()), (None, None))
            self.assertEqual(receipt_fetch.run(date="20261013", rq=object()), (None, None))
        self.assertEqual([c.args[2] for c in fetch.call_args_list], ["20210101", "20260923"])
        self.assertEqual(list(self.history.glob("*.csv")), [])

    def test_failed_request_is_not_cached_as_no_data(self):
        with patch.object(receipt_fetch, "fetch_frame", side_effect=RuntimeError("offline")):
            with self.assertRaises(RuntimeError):
                receipt_fetch.run(date="20261012", rq=object())
        self.assertFalse((self.root / "fetch_status.json").exists())

    def test_force_and_backfill_can_retry_missing_history(self):
        self.seed()
        with patch.object(receipt_fetch, "fetch_frame", side_effect=self.response) as fetch:
            receipt_fetch.run(date="20261012", rq=object())
            fetch.reset_mock()
            receipt_fetch.run(date="20261012", force=True, rq=object())
            self.assertIn((["EC"], "20210101"),
                          [(c.args[1], c.args[2]) for c in fetch.call_args_list])
            self.assertTrue(self.multipliers.call_args.kwargs["force"])
            fetch.reset_mock()
            receipt_fetch.run(date="20261012", backfill=True, rq=object())
            self.assertEqual(fetch.call_args.args[1:], (["M", "EC"], "20210101", "20261012"))

    def test_explicit_start_does_not_claim_full_history_checked(self):
        self.seed()
        with patch.object(receipt_fetch, "fetch_frame", side_effect=self.response) as fetch:
            receipt_fetch.run(date="20261012", start="20261001", rq=object())
            self.assertEqual(fetch.call_args.args[2], "20261001")
            fetch.reset_mock()
            receipt_fetch.run(date="20261013", rq=object())
            self.assertIn((["EC"], "20210101"),
                          [(c.args[1], c.args[2]) for c in fetch.call_args_list])

    def test_snapshot_and_facts_exclude_future_rows(self):
        self.seed()
        receipt_rows("EC", ["20261012"]).to_csv(self.history / "EC.csv", index=False)
        date, snapshot = receipt_fetch.snapshot(as_of="20261009")
        self.assertEqual(date, "20261009")
        self.assertEqual(pd.read_csv(snapshot)["on_warrant"].tolist(), [100])
        facts = receipt_html.build_facts(as_of="20261009")
        self.assertEqual(facts["data_date"], "20261009")
        self.assertEqual([s["symbol"] for s in facts["symbols"]], ["M"])
        self.assertEqual(facts["symbols"][0]["lots"], 100)
        self.assertEqual(facts["symbols"][0]["series"]["daily"], [["20261009", 100, None]])
        self.assertEqual(receipt_fetch.snapshot(as_of="20261008"), (None, None))
        self.assertEqual(receipt_html.build_facts(as_of="20261008")["symbols"], [])

    def test_daily_backrun_preserves_newer_archive(self):
        self.seed()
        newer = receipt_html.publish_report(receipt_html.build_facts())
        original = [p.read_bytes() for p in newer]
        with patch.object(receipt_fetch, "fetch_frame", side_effect=self.response), \
                patch.object(receipt_daily, "fetch_run", side_effect=lambda **kw: receipt_fetch.run(rq=object(), **kw)):
            receipt_daily.main(["--date", "20261009"])
        self.assertEqual([p.read_bytes() for p in newer], original)
        record = json.loads((self.root / "receipt_report_20261009.json").read_text())
        body = (self.root / "receipt_report_20261009.html").read_text()
        self.assertEqual(record["data_date"], "20261009")
        self.assertEqual(record["facts"]["as_of"], "20261009")
        self.assertEqual(record["html_hash"], digest(body))
        self.assertEqual(record["input_hash"], digest(record["facts"]))

    def test_daily_unpublished_target_uses_previous_data_day(self):
        self.seed()
        with patch.object(receipt_fetch, "fetch_frame", return_value=receipt_rows("M", ["20261012"])), \
                patch.object(receipt_daily, "fetch_run", side_effect=lambda **kw: receipt_fetch.run(rq=object(), **kw)):
            receipt_daily.main(["--date", "20261013"])
        record = json.loads((self.root / "receipt_report_20261012.json").read_text())
        self.assertEqual(record["facts"]["as_of"], "20261012")
        self.assertFalse((self.root / "receipt_report_20261013.html").exists())


@unittest.skipUnless(shutil.which("node"), "需要 Node 验证报告内嵌 JavaScript")
class ReceiptChartTests(unittest.TestCase):
    def run_js(self, assertions):
        # 执行完整报告 JS，最小 DOM 替身仅承接渲染和事件注册。
        setup = """
const assert=require('node:assert/strict');
const elements=new Map();
global.document={getElementById(id){
 if(!elements.has(id))elements.set(id,{textContent:'',innerHTML:'',addEventListener(){}});
 return elements.get(id);
},querySelectorAll(){return []}};
document.getElementById('report-data').textContent=JSON.stringify({facts:{symbols:[],data_date:'20261009'}});
"""
        result = subprocess.run([shutil.which("node")], input=setup + receipt_html.JS + assertions,
                                text=True, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_forecast_and_total_keep_gaps_without_connecting_across_them(self):
        self.run_js("""
const s={series:{ef:[['20261005',100,80],['20261006',100,80],['20261007',100,null],['20261008',100,0],['20261009',100,0]]}};
const original=lineChart;lineChart=spec=>spec;
const spec=chartEf(s);
assert.deepEqual(spec.series[0].pts.map(p=>p[1]),[180,180,null,100,100]);
assert.deepEqual(spec.series[2].pts.map(p=>p[1]),[80,80,null,0,0]);
lineChart=original;
const svg=chartEf(s);
const paths=[...svg.matchAll(/<path d="([^"]*)"[^>]*stroke="([^"]+)"/g)];
for(const color of ['#26a67b','#8ab4d8']){
 const d=paths.find(m=>m[2]===color)[1];
 assert.equal((d.match(/M/g)||[]).length,2);
 assert.equal((d.match(/L/g)||[]).length,2);
}
assert.ok(!svg.includes('NaN'));
""")

    def test_missing_prices_do_not_create_a_fictitious_right_axis(self):
        self.run_js("""
lineChart=spec=>spec;
const spec=chartDual({series:{daily:Array.from({length:10},(_,i)=>['20261009',i,null])}});
assert.equal(spec.series.length,1);
assert.equal(spec.y2,undefined);
""")

    def test_isolated_reported_zero_is_visible(self):
        self.run_js("""
const svg=lineChart({series:[{pts:[[0,null],[1,0],[2,null]],color:'#26a67b'}]});
assert.equal((svg.match(/<circle /g)||[]).length,1);
assert.ok(!svg.includes('NaN'));
""")

    def test_card_explicitly_marks_missing_same_day_prices(self):
        self.run_js("""
const s={symbol:'M',name:'豆粕',exchange:'DCE',unit:'手',date:'20261009',signals:[],
 change_1d:null,change_5d:null,change_5d_pct:null,change_20d_pct:null,
 price_missing_reason:'缺少仓单日 20261009 的行情，价格分位及虚实比不可用',
 series:{daily:[['20261009',100,null]],changes:[],seasonal:{2026:[[282,100]]},ef:null}};
assert.ok(cardHtml(s).includes(s.price_missing_reason));
""")


if __name__ == "__main__":
    unittest.main()
