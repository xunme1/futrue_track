"""事件排序、跨日语义边界、两阶段失败回退等行为回归。"""
import copy
import unittest
from unittest.mock import patch
import json
from tests.test_report_generator import bundle
from backend.pipeline.generate_report import compute_facts, merge_narrative, prompt_package
from backend.pipeline.report_narrator import generate_narrative


class ReportV3Tests(unittest.TestCase):
    def test_pressure_scan_does_not_require_history_bucket(self):
        b = bundle(positions=(-1, -1))
        b['screen_1d']['buckets']['short_trend'][0]['close'] = 104.5
        f = compute_facts(b)
        event = next(e for e in f['event_ledger'] if e['kind'] == 'pressure')
        self.assertIn('维持持空', event['confirmation'])
        self.assertFalse(b['screen_1d']['buckets']['short_pressure_warning'])

    def test_above_pressure_band_not_in_band(self):
        b = bundle(positions=(-1, 0))
        b['screen_1d']['buckets']['short_trend'][0]['close'] = 106
        f = compute_facts(b)
        self.assertFalse(any(e['kind'] == 'pressure' for e in f['event_ledger']))
        self.assertEqual(f['instruments'][0]['bands']['pressure']['position'], '带上')

    def test_warning_followed_by_flat_is_not_short(self):
        old = bundle('2026-09-11', (1, 0))
        old['screen_1d']['buckets']['long_to_short_warning'] = copy.deepcopy(old['screen_1d']['buckets']['long_trend'])
        now = bundle(positions=(0, 0))
        f = compute_facts(now, old)
        self.assertEqual(f['warning_outcomes']['closed_long'], ['rb2610'])
        self.assertEqual(f['daily_actions']['SK'], [])
        self.assertIn('平多', f['focus_events'][0]['label'])

    def test_repair_and_weaken_are_detected_independently_of_risk_grade(self):
        f = compute_facts(bundle(positions=(1, 0)), bundle('2026-09-11', (1, 1)))
        self.assertTrue(any(e['kind'] == 'weaken' for e in f['event_ledger']))
        g = compute_facts(bundle(positions=(1, 1)), bundle('2026-09-11', (1, 0)))
        self.assertTrue(any(e['kind'] == 'repair' for e in g['event_ledger']))

    def test_passive_rank_has_unchanged_price_and_momentum(self):
        old = bundle('2026-09-11', (1, 0))
        now = bundle(positions=(1, 0))
        old['screen_1d']['buckets']['long_trend'][0]['rank'] = 9
        now['screen_1d']['buckets']['long_trend'][0]['rank'] = 3
        f = compute_facts(now, old)
        self.assertIn('被动上移', f['instruments'][0]['rank_explanation'])
        now['screen_1d']['buckets']['long_trend'][0]['score'] = 1.5
        g = compute_facts(now, old)
        self.assertNotIn('被动', g['instruments'][0]['rank_explanation'])

    def test_old_line_crossing_distinct_from_current_line_position(self):
        old = bundle('2026-09-11', (1, 1))
        now = bundle(positions=(1, 1))
        old['screen_1d']['buckets']['long_trend'][0].update(close=101, EE=100)
        now['screen_1d']['buckets']['long_trend'][0].update(close=99, EE=98)
        row = compute_facts(now, old)['instruments'][0]
        self.assertTrue(row['daily_diff']['1d']['crossed_old_EE'])
        self.assertFalse(row['daily']['below_ee'])

    def test_cohort_keeps_observed_exits(self):
        keys = ('rb2610', 'hc2610', 'i2701')
        old = bundle('2026-09-11', (1, 0), keys)
        for r in old['screen_1d']['buckets']['long_trend']:
            r['score_entry_date'] = '2026-09-01'
        now = bundle(positions=(0, 0), keys=keys)
        f = compute_facts(now, old)
        self.assertEqual(len(f['cohort_lifecycle']), 1)
        self.assertEqual(len(f['cohort_lifecycle'][0]['exited_keys']), 3)
        self.assertEqual(f['cohort_lifecycle'][0]['retained_keys'], [])

    def test_histories_include_current_and_change_fingerprint(self):
        b = bundle()
        f = compute_facts(b, bucket_history={'2026-09-11': {'long_trend': 1}})
        g = compute_facts(b, bucket_history={'2026-09-11': {'long_trend': 2}})
        self.assertEqual(f['bucket_trend']['long_trend'][-1], ('2026-09-14', 1))
        self.assertNotEqual(f['input_hash'], g['input_hash'])

    def _narrative(self, f):
        e = f['event_ledger'][0]
        c = dict(id='c1', title='变化需确认', observation='日线出现开多。', interpretation='当前方向具有信号支持。',
                 counter_evidence='尚无后续K线。', confirmation='需要状态延续。', invalidation='后续平多时重评。',
                 missing_data='缺少后续数据。', event_ids=[e['id']], evidence_refs=e['evidence_refs'])
        return dict(report_date=f['report_date'], input_hash=f['input_hash'], one_liner='日线出现开多，仍需后续确认。', claims=[dict(c, id=f'c{i}') for i in range(3)], review_changes=[])

    def test_unknown_reference_and_internal_language_are_rejected(self):
        f = compute_facts(bundle())
        n = self._narrative(f)
        n['claims'][0]['evidence_refs'] = ['invented.level']
        with self.assertRaisesRegex(ValueError, '证据引用'):
            merge_narrative(f, n)
        n = self._narrative(f)
        n['claims'][0]['interpretation'] = 'risk 重点，repaired=false。'
        with self.assertRaisesRegex(ValueError, '内部字段'):
            merge_narrative(f, n)

    def test_two_stage_narration_uses_draft_and_preserves_evidence(self):
        f = compute_facts(bundle())
        n = self._narrative(f)
        with patch('backend.pipeline.report_narrator.chat', return_value=json.dumps(n)) as chat:
            out, refs, failed = generate_narrative(prompt_package(f), 'test', 2, 1000, retries=0, facts=f)
        self.assertEqual(chat.call_count, 2)
        self.assertIn('DRAFT:', chat.call_args.args[0][-1]['content'])
        self.assertFalse(failed)
        self.assertEqual(len(out['claims']), 3)
        self.assertEqual(set(refs), {'c0','c1','c2'})

    def test_editor_failure_does_not_publish_unreviewed_draft(self):
        f = compute_facts(bundle())
        with patch('backend.pipeline.report_narrator.chat', side_effect=[json.dumps(self._narrative(f)), ValueError('截断')]):
            out, _, failed = generate_narrative(prompt_package(f), 'test', 2, 1000, retries=0, facts=f)
        self.assertNotIn('claims', out)
        self.assertIn('规则', out['source'])
        self.assertTrue(failed)


if __name__ == '__main__':
    unittest.main()
