"""v5纠偏的反例：状态不由评分推断，缺证据不补成事实。"""
import unittest
from backend.pipeline import scan_report as scan, summary_render as render
from tests.test_scan_report import _row


class ReportStateTests(unittest.TestCase):
    def test_negative_momentum_above_support_is_not_break(self):
        state = scan.state4(_row('sc2610', -2, 101, EE=100))
        self.assertEqual(state['tier'], '4h动能偏负')
        self.assertFalse(state['below_EE_4h'])

    def test_positive_momentum_below_support_is_break(self):
        state = scan.state4(_row('sc2610', 2, 99, EE=100))
        self.assertEqual(state['tier'], '4h破位')
        self.assertFalse(scan._leaders({'sc':_row('sc2610',12,110)},
                         {'sc2610':_row('sc2610',2,99,EE=100)})['dual'])

    def test_same_timeframe_price_and_equality(self):
        p={'sc':_row('sc2610',12,80,EE=90)}
        four={'sc2610':_row('sc2610',-1,100,EE=100)}
        row=scan._long_4h_tiers(p,four)['4h动能偏负'][0]
        self.assertFalse(row['below_EE_4h'])
        self.assertEqual(row['EE_4h'],100)

    def test_missing_price_is_not_no_break(self):
        self.assertIsNone(scan.state4(_row('sc2610',2,101))['below_EE_4h'])

    def test_bp_is_close_short_not_close_long(self):
        row=dict(_row('m2701',2,101,EE=100,pos=0),last={'type':'BP'})
        self.assertEqual(scan.state4(row)['position_4h'],'平空后空仓')

    def test_flat_high_score_remains_in_ended_table(self):
        p={'bc':_row('bc2609',-0.1,101,EE=100)}
        four={'bc2609':dict(_row('bc2609',2,101,EE=100,pos=0),last={'type':'SP'})}
        tiers=scan._long_4h_tiers(p,four)
        self.assertEqual(tiers['4h空仓'][0]['code'],'bc')
        html=render.s2_leaders(dict(leaders=dict(dual=[],absolute=[],quasi=[]),long_4h_tiers=tiers),{})
        self.assertIn('平多后空仓',html)

    def market(self):
        p={'m':_row('m2701',3,101,DD=100,EE=99),
           'y':_row('y2701',-1,99,pos=-1),'OI':_row('OI611',-1,99,pos=-1)}
        four={'m2701':dict(_row('m2701',0.1,101,EE=100,pos=0),last={'type':'SP'})}
        return p,four

    def test_resistance_rejects_short_bp_and_break(self):
        for changes in [dict(pos=-1),dict(last={'type':'BP'}),dict(close=99)]:
            p,four=self.market();four['m2701'].update(changes)
            self.assertNotEqual(scan._divergence(p,four,set())['items'][0]['verdict'],'多头抵抗')

    def test_weak_daily_positive_four_hour_still_divergence(self):
        p={'bc':_row('bc2609',3,101,DD=100,EE=99),
           'cu':_row('cu2609',-1,99,pos=-1),'al':_row('al2610',-1,99,pos=-1)}
        four={'bc2609':_row('bc2609',2,101,EE=100,pos=0)}
        self.assertEqual(scan._divergence(p,four,set())['items'][0]['verdict'],'分歧')

    def test_broken_reopened_position_is_not_repaired(self):
        r=dict(_row('eg2610',2,99,EE=100),last={'type':'BK','date':'2026-09-15'},
               recent_signals=[{'type':'SP','date':'2026-09-14'},{'type':'BK','date':'2026-09-15'}])
        result=scan._attach_4h([dict(key='eg2610',retest_dates=['2026-09-14'])],{'eg2610':r},'2026-09-14')[0]
        self.assertTrue(result['reopened_long'])
        self.assertFalse(result['repaired'])

    def test_positive_short_score_does_not_imply_bucket_exit(self):
        row=dict(key='c2701',score=1,close=101,rank=1,rank_change=4)
        result=scan._rank_radar({'buckets':{'short_trend':[row]}})['short_trend'][0]
        self.assertTrue(result['risen'])
        self.assertIn('不代表自动出榜',result['rank_note'])


if __name__ == '__main__':
    unittest.main()
