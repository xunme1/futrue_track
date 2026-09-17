# -*- coding: utf-8 -*-
"""每日总结扫描器的判据回归测试（不依赖 data/ 产物，用构造数据跑纯函数）。

覆盖方法论 v4 的核心判据：龙头三档、回踩 vs 分歧裁决树、农产品「多头抵抗」4 条硬条件。
任一判据被改动而结果没变 → 这里会红。
"""
import unittest

from backend.pipeline import scan_report as sr


def _row(key, score, close, DD=None, EE=None, pos=1, rank_change=0, sector=None, rank=None):
    return {'key': key, 'name': key, 'sector': sector or sr.sector_of(key),
            'score': score, 'close': close, 'DD': DD, 'EE': EE, 'KK': None, 'PP': None,
            'pos': pos, 'rank': rank, 'rank_change': rank_change, 'buckets': [],
            '_by_bucket': {}, 'last': {}}


class TestHelpers(unittest.TestCase):
    def test_base_of_keeps_case(self):
        """品种码保留原始大小写，才能匹配板块字典里的 FG / RB 混写"""
        self.assertEqual(sr.base_of('rb2610'), 'rb')
        self.assertEqual(sr.base_of('FG601'), 'FG')
        self.assertEqual(sr.base_of('MA610'), 'MA')

    def test_sector_and_type(self):
        self.assertEqual(sr.sector_of('rb2610'), '黑色系')
        self.assertEqual(sr.sector_of('m2701'), '油脂粕')
        self.assertEqual(sr.stype('油脂粕'), '农')
        self.assertEqual(sr.stype('黑色系'), '工')
        self.assertEqual(sr.stype('未知道'), '工')   # 未列出默认工业品


class TestLeaders(unittest.TestCase):
    def test_three_tiers(self):
        P1 = {
            'sc': _row('sc2610', 20.8, 893.0, rank=1),          # 双强：1d≥4.5 且 4h≥1.0
            'xx': _row('xx2601', 12.0, 100.0),                   # 绝对：1d≥10 且 4h>0（弱正）
            'yy': _row('yy2601', 4.0, 100.0),                    # 准：3.0≤1d<4.5 且 4h≥1.0
            'zz': _row('zz2601', 2.0, 100.0),                    # 都不够
        }
        # 4h 代表合约（|score| 最大者命中）
        R4 = {'sc2610_4h': _row('sc2610', 2.0, 893.0),
              'xx2601_4h': _row('xx2601', 0.3, 100.0),
              'yy2601_4h': _row('yy2601', 1.2, 100.0),
              'zz2601_4h': _row('zz2601', 1.2, 100.0)}
        L = sr._leaders(P1, R4)
        self.assertEqual([r['code'] for r in L['dual']], ['sc'])
        self.assertEqual([r['code'] for r in L['absolute']], ['xx'])
        self.assertEqual([r['code'] for r in L['quasi']], ['yy'])


class TestDivergence(unittest.TestCase):
    """裁决树：板块氛围坏 → 4h 已平仓 → 农/工分流"""

    def _setup(self):
        # 芳烃能化（工业品）：bz/eb/MA 仍多头，SH/sp 已开空，v/UR 已离场 → 氛围坏
        P1 = {
            'l': _row('l2701', 5.04, 8540.0, DD=8452.75, EE=8295.67, rank_change=0),
            'bz': _row('bz2610', 9.27, 9057.0, DD=8862.75, EE=8610.67, rank_change=0),
        }
        # 4h：l 转负、bz 仍正，两者均已平仓（不在 4h long_trend 桶）
        R4 = {'l2701': _row('l2701', -0.49, 8540.0, DD=8452.75, EE=8295.67, rank_change=0),
              'bz2610': _row('bz2610', 0.29, 9057.0, DD=8862.75, EE=8610.67, rank_change=0)}
        P1.update({c: _row(c + '2610', -1, 100, pos=-1) for c in ('SH', 'sp')})
        for row in R4.values():
            row['pos'] = 0
        return P1, R4

    def test_industrial_negative_4h_is_divergence(self):
        P1, R4 = self._setup()
        d = sr._divergence(P1, R4, WARN1=set())
        got = {x['code']: x for x in d['items']}
        self.assertEqual(got['l']['verdict'], '分歧')
        self.assertEqual(got['l']['level'], '🟠')      # 日线仍正 → 减半

    def test_industrial_weak_but_positive_is_retest(self):
        """4h 转正是关键分界线：已平仓但 4h 未转负 → 判回踩，不判分歧"""
        P1, R4 = self._setup()
        d = sr._divergence(P1, R4, WARN1=set())
        got = {x['code']: x for x in d['items']}
        self.assertEqual(got['bz']['verdict'], '回踩')

    def test_warning_upgrades_to_red(self):
        """挂日线多头预警 → 直接 🔴，即使日线仍正"""
        P1, R4 = self._setup()
        d = sr._divergence(P1, R4, WARN1={'bz'})
        got = {x['code']: x for x in d['items']}
        self.assertEqual(got['bz']['level'], '🔴')

    def test_agricultural_resistance_all_four(self):
        """农产品 4 条全中 → 多头抵抗（不砍、可低吸）"""
        P1 = {'m': _row('m2701', 3.19, 3385.0, DD=3371.75, EE=3342.33, rank_change=2)}
        R4 = {'m2701': _row('m2701', 0.02, 3385.0, DD=3371.75, EE=3342.33, rank_change=2)}
        P1.update({c: _row(c + '2610', -1, 100, pos=-1) for c in ('y', 'OI')})
        R4['m2701']['pos'] = 0
        d = sr._divergence(P1, R4, WARN1=set())
        self.assertEqual(d['items'][0]['verdict'], '多头抵抗')
        self.assertEqual(d['items'][0]['level'], '🟡')

    def test_agricultural_falls_back_when_condition_missing(self):
        """close < DD（条件 2 不满足）→ 回落到工业品口径，不再判抵抗"""
        P1 = {'m': _row('m2701', 3.19, 3300.0, DD=3371.75, EE=3342.33, rank_change=2)}
        R4 = {'m2701': _row('m2701', 0.02, 3300.0, DD=3371.75, EE=3342.33, rank_change=2)}
        P1.update({c: _row(c + '2610', -1, 100, pos=-1) for c in ('y', 'OI')})
        R4['m2701']['pos'] = 0
        d = sr._divergence(P1, R4, WARN1=set())
        self.assertNotEqual(d['items'][0]['verdict'], '多头抵抗')

    def test_leader_and_holding_4h_are_exempt(self):
        """龙头不判、4h 仍持多不判 —— 防止名单虚增"""
        P1 = {'sc': _row('sc2610', 20.8, 893.0, DD=820.0, EE=783.0)}
        R4 = {'sc2610': _row('sc2610', 5.0, 893.0, DD=820.0, EE=783.0)}
        R4['sc2610']['buckets'] = ['long_trend']
        d = sr._divergence(P1, R4, WARN1=set())
        self.assertEqual(d['items'], [])

    def test_calm_sector_not_evaluated(self):
        """氛围不坏（已平/开空 < 2 只）→ 整块跳过。
        用橡胶系（nr/ru/br 3 只）构造：3 只全部日线持多 → 氛围好。"""
        P1 = {c: _row(f'{c}2610', 5.0, 100.0, DD=95.0, EE=90.0) for c in ('nr', 'ru', 'br')}
        for r in P1.values():
            r['buckets'] = ['long_trend']
        # nr 的 4h 已转负且已平仓 —— 但因为板块氛围好，不应进入名单
        R4 = {f'{c}2610': _row(f'{c}2610', -1.0 if c == 'nr' else 1.0, 100.0, DD=95.0, EE=90.0)
              for c in ('nr', 'ru', 'br')}
        d = sr._divergence(P1, R4, WARN1=set())
        self.assertEqual(d['sectors'], [])
        self.assertEqual(d['items'], [])

    def test_missing_symbols_are_not_counted_as_gone(self):
        """缺失品种不能被伪造成已离场，从而触发板块分歧。"""
        P1 = {'cu': _row('cu2610', 5.0, 100.0, DD=95.0, EE=90.0)}   # 有色其余 9 只无数据
        P1['cu']['buckets'] = ['long_trend']                         # 自身仍在日线多头榜
        # 4h score 0.5 < 1.0 → 不构成龙头，正常进入判定
        R4 = {'cu2610': _row('cu2610', 0.5, 100.0, DD=95.0, EE=90.0)}
        d = sr._divergence(P1, R4, WARN1=set())
        self.assertEqual(d['sectors'], [])
        self.assertEqual(sr.sector_mood(P1, '有色'), ([], []))


class TestTurn(unittest.TestCase):
    def test_verdict_follows_daily_trend(self):
        """铁律：4h 转折一律以日线趋势裁决"""
        scr4 = {'buckets': {'long_to_short': [
            {'key': 'rb2610', 'name': 'rb', 'close': 3000.0, 'score': -1.0,
             'signal_date': '2026-09-15', 'EE': 3100.0}]}, 'summary': {}}
        P1 = {'rb': _row('rb2610', 2.0, 3000.0, EE=3100.0, pos=1)}
        t = sr._turn({'buckets': {}}, scr4, P1)
        self.assertEqual(t['A'][0]['verdict'], '日线仍多')


if __name__ == "__main__":
    unittest.main()
