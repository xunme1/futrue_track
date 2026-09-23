"""数据诚信守卫的反例（v6 保留项）：状态不由评分推断，缺证据不补成事实。"""
import unittest
from backend.pipeline import scan_report as scan, summary_render as render
from tests.test_scan_report import _row, _r4


class ReportStateTests(unittest.TestCase):
    def test_negative_momentum_above_support_is_not_break(self):
        """评分偏负不等于破位；破位只看 4h 收盘与 4h EE"""
        state = scan.state4(_row('sc2610', -2, 101, EE=100))
        self.assertEqual(state['tier'], '4h动能偏负')
        self.assertFalse(state['below_EE_4h'])

    def test_positive_momentum_below_support_is_break(self):
        """评分为正也可能收破 EE；破位品种进不了龙头档"""
        state = scan.state4(_row('sc2610', 2, 99, EE=100))
        self.assertEqual(state['tier'], '4h破位')
        P1 = {'sc': _row('sc2610', 12, 110)}
        R4 = {'sc2610': _r4('sc2610', 2, close=99.0, ee=100.0)}
        # 破位不阻止入档（v6 破位只是备注），但备注必须保留
        row = scan._four_tiers(P1, R4, {}, side=1)['lead'][0]
        self.assertTrue(row['breach_4h'])
        self.assertIn('破位', row['reason'])

    def test_missing_price_is_not_no_break(self):
        """缺价格记未知，不补成「未破」"""
        self.assertIsNone(scan.state4(_row('sc2610', 2, 101))['below_EE_4h'])

    def test_bp_is_close_short_not_close_long(self):
        """BP 是平空不是平多"""
        row = dict(_row('m2701', 2, 101, EE=100, pos=0), last={'type': 'BP'})
        self.assertEqual(scan.state4(row)['position_4h'], '平空后空仓')

    def test_missing_scores_not_filled_with_zero(self):
        """未知评分不补零：1d 未知 → 龙头/回调门槛不成立；4h 未知 → Δ 未知"""
        P1 = {'rb': _row('rb2610', None, 100.0)}
        R4 = {'rb2610': _r4('rb2610', 1.5)}
        tiers = scan._four_tiers(P1, R4, {}, side=1)
        self.assertEqual(tiers['lead'], [])
        row = tiers['flat'][0]
        self.assertIsNone(row['score_1d'])
        self.assertTrue(row['provisional'])

    def test_pool_requires_bucket_and_level(self):
        """蓄势池双条件：4h 在桶 且 水平越过 ±1.0；缺一即真·未入档"""
        P1 = {'rb': _row('rb2610', 2.0, 100.0)}
        R4 = {'rb2610': _r4('rb2610', 1.5, in_bucket=False)}   # 掉桶
        tiers = scan._four_tiers(P1, R4, {}, side=1)
        self.assertEqual(scan._pool_rows(tiers['flat'], side=1), [])

    def test_render_html_balanced_tags(self):
        """v6 §4.5：HTML 开闭标签计数必须配对"""
        import re
        P1 = {'rb': _row('rb2610', 5.0, 100.0)}
        R4 = {'rb2610': _r4('rb2610', 1.5)}
        tiers = scan._four_tiers(P1, R4, {'rb2610': 0.5}, side=1)
        f = {'rules_version': render.RULES_VERSION, 'scan_version': 3,
             'data_date': '2026-09-15', 'prev_date': '2026-09-14',
             'created_at': '2026-09-15T18:00:00', 'input_hash': 'x',
             'generated_at': {'1d': '2026-09-15T16:00:00', '4h': '2026-09-15T15:36:00'},
             'overview': {'1d': {'long_trend': 1, 'short_trend': 0, 'long_to_short': 0,
                                 'long_to_short_warning': 0, 'short_to_long': 0,
                                 'short_to_long_warning': 0, 'short_pressure_warning': 0,
                                 'long_support_warning': 0},
                          '4h': {'long_trend': 1, 'short_trend': 0, 'long_to_short': 0,
                                 'long_to_short_warning': 0, 'short_to_long': 0,
                                 'short_to_long_warning': 0, 'short_pressure_warning': 0,
                                 'long_support_warning': 0},
                          'prev_1d': None, 'prev_4d': None, 'prev_4h': None},
             'signal_actions': [], 'tiers_long': tiers,
             'tiers_short': {t: [] for t in scan.TIER_ORDER},
             'pool': {'long': [], 'short': []},
             'momentum': {'accel': [], 'decel': [], 'rank_moves': []},
             'key_levels': [], 'coverage': {}}
        body = render.render_html(f, None, report_date='2026-09-16')
        for tag in ('table', 'tr', 'td', 'th', 'div', 'h2', 'h3', 'ul', 'li', 'ol'):
            self.assertEqual(len(re.findall(rf'<{tag}[\s>]', body)),
                             len(re.findall(rf'</{tag}>', body)), tag)
        self.assertIn('judge-card', body)
        self.assertIn('蓄势池', body)
        self.assertIn('口径说明', body)


if __name__ == '__main__':
    unittest.main()
