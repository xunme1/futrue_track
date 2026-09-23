"""日报输入、跨周期状态、历史归档、叙事绑定与API的端到端边界（v6 口径）。"""
import copy
import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from backend.pipeline import scan_report as scan
from backend.pipeline import summary_render as render
from backend.api import server


def inputs(day='2026-09-15', keys=('rb2610',), pos1=1, pos4=0):
    screens, symbols = {}, {}
    for tf, pos in [('1d', pos1), ('4h', pos4)]:
        stamp = day + (' 15:00' if tf == '4h' else '')
        rows = [{'key': k, 'name': k, 'date': stamp, 'close': 101, 'score': 5 if tf == '1d' else 1.5,
                 'DD': 102, 'EE': 100, 'KK': 104, 'PP': 105, 'POS': pos, 'signal_date': stamp} for k in keys]
        buckets = {k: [] for k in scan.BUCKETS}
        if pos:
            buckets['long_trend' if pos == 1 else 'short_trend'] = rows
        symbols[tf] = [dict(r, pos=pos, last_date=stamp,
                       last_signal={'type': {1: 'BK', 0: 'SP', -1: 'SK'}[pos], 'date': stamp}) for r in rows]
        screens[tf] = dict(data_date=day, generated_at=day + 'T16:00:00+08:00', buckets=buckets,
                           summary=scan._summarize({'buckets': buckets}))
    return screens, symbols


class SummaryPipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(scan, 'SNAPSHOT_DIR', self.root / 'snapshots'))
        self.stack.enter_context(patch.object(scan, 'SCAN_DIR', self.root / 'scan'))

    def run_scan(self, data=None, prev4=None, **kwargs):
        screens, symbols = data or inputs()
        keys = [x['key'] for x in symbols['1d']]
        with patch.object(scan, 'load_screening', side_effect=lambda tf: copy.deepcopy(screens[tf])), \
             patch.object(scan, 'load_symbols', side_effect=lambda tf: copy.deepcopy(symbols[tf])), \
             patch.object(scan, 'prev_4h_scores', return_value=dict(prev4 or {})), \
             patch.object(scan, 'load_contracts', return_value=[{'symbol': k, 'name': k} for k in keys]):
            return scan.scan(**kwargs)

    @staticmethod
    def goldman_appendix():
        return {
            'date': '20260915', 'prev_date': '20260914', 'member': '高盛期货',
            'coverage': {'dominant_varieties': 70, 'main_missing': 2, 'sub_missing': 5},
            'data_hash': 'data-hash', 'long_image_hash': 'long-hash',
            'short_image_hash': 'short-hash',
            'long_data_uri': 'data:image/png;base64,bG9uZw==',
            'short_data_uri': 'data:image/png;base64,c2hvcnQ=',
        }

    def test_v6_facts_shape(self):
        f = self.run_scan(prev4={'rb2610': 0.9})
        self.assertEqual(f['scan_version'], 3)
        for key in ('overview', 'signal_actions', 'tiers_long', 'tiers_short',
                    'pool', 'momentum', 'key_levels', 'coverage', 'criteria'):
            self.assertIn(key, f)
        # 1d 多 + 4h 评分 1.5 但不在 4h 多头桶（pos4=0）→ 非龙头；Δ=+0.6 未达新贵 → 回调
        self.assertEqual(f['tiers_long']['lead'], [])
        self.assertEqual(f['tiers_long']['pull'][0]['code'], 'rb')
        self.assertAlmostEqual(f['tiers_long']['pull'][0]['d4h'], 0.6)

    def test_screening_score_fills_native_payload_gap(self):
        """原生 JSON 无评分时，评分取自筛选结果，不补零不抹掉"""
        data = inputs(pos4=1)
        for tf in scan.TIMEFRAMES:
            data[1][tf][0]['score'] = None
        f = self.run_scan(data, prev4={'rb2610': 0.5})
        self.assertEqual(f['tiers_long']['lead'][0]['score_1d'], 5)
        self.assertEqual(f['tiers_long']['lead'][0]['score_4h'], 1.5)

    def test_numeric_symbols_remain_distinct(self):
        keys = ('000016', '000300', '000852', '588000')
        f = self.run_scan(inputs(keys=keys, pos1=-1, pos4=-1))
        rows = [r for t in scan.TIER_ORDER for r in f['tiers_short'][t]]
        self.assertEqual({r['code'] for r in rows}, set(keys))
        self.assertEqual(f['overview']['1d']['short_trend'], 4)
        self.assertTrue(all(r['sector'] == '股指' for r in rows))

    def test_today_signals_listed_for_both_timeframes(self):
        f = self.run_scan()
        by_tf = {(x['tf'], x['signal']) for x in f['signal_actions']}
        self.assertIn(('1d', 'BK'), by_tf)
        self.assertIn(('4h', 'SP'), by_tf)

    def test_generated_time_does_not_change_market_day(self):
        data = inputs()
        data[0]['1d']['generated_at'] = '2026-09-17T18:00:00+08:00'
        f = self.run_scan(data)
        self.assertEqual(f['data_date'], '2026-09-15')

    def test_cli_date_cannot_relabel_current_inputs(self):
        with self.assertRaisesRegex(ValueError, '实际行情日'):
            self.run_scan(data_date='2026-09-17')

    def test_mismatched_timeframes_fail_before_cross_analysis(self):
        data = inputs()
        data[0]['4h']['data_date'] = '2026-09-16'
        with self.assertRaisesRegex(ValueError, '不同步'):
            self.run_scan(data)

    def test_missing_market_date_not_replaced_by_generated_time(self):
        data = inputs()
        del data[0]['1d']['data_date']
        data[0]['1d']['buckets'] = {b: [] for b in scan.BUCKETS}
        with self.assertRaisesRegex(ValueError, '行情日期'):
            self.run_scan(data)

    def test_stale_symbol_marks_unknown_and_provisional(self):
        data = inputs(pos4=1)
        data[1]['4h'][0]['last_date'] = '2026-09-14 15:00'
        f = self.run_scan(data)
        self.assertEqual(f['coverage']['4h']['unknown'], 1)
        row = f['tiers_long']['pull'][0]
        self.assertTrue(row['provisional'])          # 4h 评分未知 → Δ 未知 → 暂定
        self.assertIn('数据不完整', render.fallback_tone(f))

    def test_snapshot_written_and_next_day_uses_it(self):
        first = self.run_scan()
        scan.archive_scan(first)
        self.assertTrue((self.root / 'snapshots/screen_1d_20260915.json').exists())
        repeat = self.run_scan()
        self.assertEqual(first['input_hash'], repeat['input_hash'])
        second = self.run_scan(inputs(day='2026-09-16', pos1=-1))
        self.assertEqual(second['prev_date'], '2026-09-15')
        self.assertEqual(second['overview']['prev_1d']['long_trend'], 1)

    def test_prev4_scores_participate_in_input_hash(self):
        first = self.run_scan(prev4={'rb2610': 0.5})
        second = self.run_scan(prev4={'rb2610': 0.9})
        self.assertNotEqual(first['input_hash'], second['input_hash'])

    def test_invalid_same_day_baseline_rejected(self):
        with self.assertRaisesRegex(ValueError, '严格早于'):
            self.run_scan(prev_date='2026-09-15')

    def test_narrative_date_hash_and_types_validated(self):
        f = self.run_scan()
        for n in [dict(report_date='2000-01-01', input_hash=f['input_hash']),
                  dict(report_date='2026-09-16', input_hash='old'),
                  dict(report_date='2026-09-16', input_hash=f['input_hash'], action_tips='not an array'),
                  dict(report_date='2026-09-16', input_hash=f['input_hash'],
                       judge_cards=[{'title': 't', 'fact': 'f'}]),           # 缺 action
                  dict(report_date='2026-09-16', input_hash=f['input_hash'],
                       section_notes={'9': '旧节号'})]:                         # 非法节键
            with self.assertRaises(ValueError):
                render.render_html(f, n)
        n = dict(report_date='2026-09-16', input_hash=f['input_hash'],
                 section_notes={'pool': '蓄势池点评'},
                 judge_cards=[{'title': '1️⃣ 测试', 'fact': '事实', 'action': '动作'}],
                 one_liner='<script>alert(1)</script>')
        body = render.render_html(f, n)
        self.assertIn('&lt;script&gt;', body)
        self.assertIn('蓄势池点评', body)
        self.assertIn('1️⃣ 测试', body)

    def test_revised_input_invalidates_same_date_narrative(self):
        first = self.run_scan()
        data = inputs()
        data[1]['4h'][0]['close'] = 99
        second = self.run_scan(data)
        self.assertNotEqual(first['input_hash'], second['input_hash'])
        with self.assertRaises(ValueError):
            render.render_html(second, dict(report_date='2026-09-16', input_hash=first['input_hash']))

    def test_old_rules_require_rescan_before_render(self):
        f = self.run_scan()
        f['rules_version'] = 'summary-v2.1'
        with self.assertRaisesRegex(ValueError, '规则版本过旧'):
            render.render_html(f, None)

    def test_old_scan_version_rejected_on_publish(self):
        f = self.run_scan()
        f['scan_version'] = 2
        with self.assertRaisesRegex(ValueError, '重新生成事实'):
            render.publish_report(f, output_dir=self.root)

    def test_explicit_report_date_reaches_body_and_api(self):
        f = self.run_scan()
        out = render.publish_report(f, report_date='2026-09-21', output_dir=self.root)
        body = out.read_text()
        self.assertIn('<em>09-21 作战地图', body)
        self.assertIn('09-21 操作提示', body)
        with patch.object(server, 'REPORTS_DIR', self.root):
            self.assertEqual(server.reports()[0]['data_date'], '2026-09-15')
            self.assertEqual(server.report_detail('2026-09-21')['facts']['input_hash'], f['input_hash'])
            # 归档后修改源扫描，不应影响API返回已发布的事实。
            f['data_date'] = '2026-09-20'
            self.assertEqual(server.report_detail('2026-09-21')['facts']['data_date'], '2026-09-15')

    def test_goldman_appendix_embedded_after_methodology_note(self):
        body = render.render_html(
            self.run_scan(), None, goldman_appendix=self.goldman_appendix()
        )
        self.assertIn('附录 · 高盛主次合约净持仓追踪', body)
        self.assertIn('data:image/png;base64,bG9uZw==', body)
        self.assertLess(body.index('口径说明'), body.index('高盛主次合约净持仓追踪'))
        self.assertLess(body.index('高盛主次合约净持仓追踪'), body.index('<footer>'))

    def test_published_record_archives_goldman_hashes_without_image_payload(self):
        appendix = self.goldman_appendix()
        render.publish_report(
            self.run_scan(), output_dir=self.root, goldman_appendix=appendix
        )
        record = json.loads((self.root / 'daily_summary_2026-09-16.json').read_text())
        addon = record['addons']['goldman_contract_positions']
        self.assertEqual(addon['long_image_hash'], 'long-hash')
        self.assertNotIn('long_data_uri', addon)
        self.assertIn('data:image/png;base64,bG9uZw==',
                      (self.root / 'daily_summary_2026-09-16.html').read_text())

    def test_rerender_preserves_custom_date_and_narrative(self):
        facts = self.run_scan()
        narrative = {
            'report_date': '2026-09-21', 'input_hash': facts['input_hash'],
            'one_liner': '原有叙事保持不变', 'source': '测试叙事',
        }
        render.publish_report(
            facts, narrative, report_date='2026-09-21', output_dir=self.root,
            goldman_appendix={},
        )
        before = json.loads((self.root / 'daily_summary_2026-09-21.json').read_text())
        with patch.object(render, 'load_goldman_appendix', return_value=self.goldman_appendix()):
            out = render.rerender_report_for_data_date('2026-09-15', reports_dir=self.root)
        after = json.loads((self.root / 'daily_summary_2026-09-21.json').read_text())
        self.assertEqual(out.name, 'daily_summary_2026-09-21.html')
        self.assertEqual(after['narrative'], narrative)
        self.assertNotEqual(before['html_hash'], after['html_hash'])
        self.assertIn('addons', after)

    def test_load_goldman_appendix_rejects_mismatched_date(self):
        root = self.root / 'seat'
        root.mkdir()
        (root / 'goldman_contract_positions_20260915.json').write_text(
            json.dumps({'date': '20260914'}), encoding='utf-8')
        (root / 'goldman_contract_long_20260915.png').write_bytes(b'long')
        (root / 'goldman_contract_short_20260915.png').write_bytes(b'short')
        self.assertIsNone(render.load_goldman_appendix('2026-09-15', root))

    def test_load_goldman_appendix_embeds_complete_matching_bundle(self):
        root = self.root / 'seat-complete'
        root.mkdir()
        metadata = {
            'date': '20260915', 'prev_date': '20260914', 'member': '高盛期货',
            'coverage': {'dominant_varieties': 70, 'main_missing': 2, 'sub_missing': 5},
        }
        (root / 'goldman_contract_positions_20260915.json').write_text(
            json.dumps(metadata), encoding='utf-8')
        (root / 'goldman_contract_long_20260915.png').write_bytes(b'long')
        (root / 'goldman_contract_short_20260915.png').write_bytes(b'short')
        appendix = render.load_goldman_appendix('2026-09-15', root)
        self.assertEqual(appendix['date'], '20260915')
        self.assertEqual(appendix['coverage']['main_missing'], 2)
        self.assertEqual(appendix['long_data_uri'], 'data:image/png;base64,bG9uZw==')
        self.assertEqual(len(appendix['short_image_hash']), 64)

    def test_mixed_html_and_record_not_accepted(self):
        out = render.publish_report(self.run_scan(), output_dir=self.root)
        out.write_text('other generation')
        with patch.object(server, 'REPORTS_DIR', self.root):
            self.assertIsNone(server._archived_report('2026-09-16'))

    def test_no_baseline_is_not_market_balance(self):
        self.assertIn('基线不足', render.fallback_tone(self.run_scan()))


if __name__ == '__main__':
    unittest.main()
