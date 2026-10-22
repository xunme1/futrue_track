# -*- coding: utf-8 -*-
"""v6 四档分类的判据回归测试（不依赖 data/ 产物，用构造数据跑纯函数）。

覆盖：四档优先级互斥、阈值边界（4.5 / ±1.0）、新贵 4h 强势前置、
危险分歧的「4h 偏弱 + Δ≤−1」双条件、回调的 Δ>−1 条件、空头镜像、
Δ4h 缺失 → provisional、蓄势池分流、前一交易日 4h score 重算口径。
任一判据被改动而结果没变 → 这里会红。
"""
import json
import copy
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.pipeline import scan_report as sr


def _row(key, score, close, DD=None, EE=None, pos=1, rank_change=0, sector=None, rank=None):
    return {'key': key, 'name': key, 'sector': sector or sr.sector_of(key),
            'score': score, 'close': close, 'DD': DD, 'EE': EE, 'KK': None, 'PP': None,
            'pos': pos, 'rank': rank, 'rank_change': rank_change, 'buckets': [],
            '_by_bucket': {}, 'last': {}}


def _r4(key, score, pos=1, in_bucket=True, close=100.0, ee=90.0, pp=110.0):
    side_bucket = 'long_trend' if pos == 1 else ('short_trend' if pos == -1 else None)
    return {'key': key, 'name': key, 'sector': sr.sector_of(key), 'score': score,
            'close': close, 'DD': None, 'EE': ee, 'KK': None, 'PP': pp,
            'pos': pos, 'buckets': [side_bucket] if (in_bucket and side_bucket) else [],
            '_by_bucket': {}, 'last': {}, 'recent_signals': []}


class TestHelpers(unittest.TestCase):
    def test_base_of_keeps_case(self):
        """品种码保留原始大小写，才能匹配板块字典里的 FG / RB 混写"""
        self.assertEqual(sr.base_of('rb2610'), 'rb')
        self.assertEqual(sr.base_of('FG601'), 'FG')
        self.assertEqual(sr.base_of('MA610'), 'MA')

    def test_sector_of(self):
        self.assertEqual(sr.sector_of('rb2610'), '黑色系')
        self.assertEqual(sr.sector_of('m2701'), '油脂粕')
        self.assertEqual(sr.sector_of('xx2601'), '其他')


class TestClassify(unittest.TestCase):
    """_classify(s1, s4, in_bucket, d4, side)：v6 §2 优先级与阈值边界。"""

    def test_lead_requires_all_four_conditions(self):
        self.assertEqual(sr._classify(4.5, 1.0, True, 0.0, 1), 'lead')   # 边界值恰好入档
        self.assertEqual(sr._classify(4.49, 1.0, True, 0.0, 1), 'flat')  # 日线不够
        self.assertEqual(sr._classify(4.5, 0.99, True, 0.0, 1), 'pull')  # 日线强但 4h 弱正 → 回调（sc 式）
        self.assertEqual(sr._classify(4.5, 1.0, False, 0.0, 1), 'pull')  # 掉桶但 Δ 未崩 → 回调

    def test_lead_beats_fresh_by_priority(self):
        """同时满足龙头与新贵 → 取龙头（互斥，优先级 1 先命中）"""
        self.assertEqual(sr._classify(6.0, 2.0, True, 2.0, 1), 'lead')

    def test_danger_needs_weak_4h_and_delta(self):
        self.assertEqual(sr._classify(5.0, -0.5, False, -1.0, 1), 'danger')  # Δ 边界
        self.assertEqual(sr._classify(5.0, -0.5, True, -1.5, 1), 'danger')   # 在桶但评分转负
        self.assertEqual(sr._classify(5.0, 0.5, True, -0.99, 1), 'pull')     # Δ 未达门槛
        # sc 式边缘：Δ 崩了但 4h 不弱（在桶且评分>0）→ 不构成危险分歧，也不满足回调 → ⚪
        self.assertEqual(sr._classify(20.8, 0.32, True, -5.97, 1), 'flat')

    def test_fresh_requires_4h_strong_precondition(self):
        """v6.1：Δ≥+1 但 4h≤1.0 是负区回抽，不入新贵"""
        self.assertEqual(sr._classify(2.0, 1.5, True, 1.0, 1), 'fresh')    # Δ 边界
        self.assertEqual(sr._classify(2.0, 1.0, True, 2.0, 1), 'flat')     # 4h 恰为 1.0，不超 NEW_STRONG
        self.assertEqual(sr._classify(2.0, -0.5, False, 1.5, 1), 'flat')   # 负区回抽
        # 文档实例：fu 4h −0.86 / Δ+2.35、pg 4h −1.16 / Δ+1.76 → 不是新贵；1d≥4.5 降级为回调
        self.assertEqual(sr._classify(10.85, -0.86, False, 2.35, 1), 'pull')
        self.assertEqual(sr._classify(9.95, -1.16, False, 1.76, 1), 'pull')

    def test_pullback_requires_delta_not_collapsed(self):
        self.assertEqual(sr._classify(6.0, 0.5, True, 0.3, 1), 'pull')
        self.assertEqual(sr._classify(6.0, 0.5, True, None, 1), 'pull')    # Δ 缺失暂定回调
        self.assertEqual(sr._classify(6.0, 0.5, True, -1.0, 1), 'flat')    # Δ 崩但 4h 不弱 → ⚪

    def test_delta_none_disables_delta_gated_tiers(self):
        """Δ4h 未知：危险分歧/新贵无法确认，按当日状态暂定"""
        self.assertEqual(sr._classify(5.0, -0.5, False, None, 1), 'pull')
        self.assertEqual(sr._classify(2.0, 1.5, True, None, 1), 'flat')
        self.assertEqual(sr._classify(2.0, 0.5, True, None, 1), 'flat')

    def test_none_scores_fail_thresholds_not_fabricated(self):
        self.assertEqual(sr._classify(None, 1.5, True, 1.5, 1), 'fresh')   # 日线未知仍可入新贵
        self.assertEqual(sr._classify(None, 0.5, True, 0.3, 1), 'flat')
        # 4h 未知：龙头不满足；回调只看日线与 Δ（Δ 缺失不挡回调）→ 暂定回调
        self.assertEqual(sr._classify(5.0, None, True, None, 1), 'pull')

    def test_short_side_mirror(self):
        C = sr._classify
        self.assertEqual(C(-4.5, -1.0, True, 0.0, -1), 'lead')     # 绝对熊头边界
        self.assertEqual(C(-4.49, -1.0, True, 0.0, -1), 'flat')
        self.assertEqual(C(-5.0, 0.5, False, 1.0, -1), 'danger')   # 空头危险分歧
        self.assertEqual(C(-5.0, 0.5, True, 0.99, -1), 'pull')     # Δ 未达 → 反抽
        self.assertEqual(C(-2.0, -1.5, True, -1.0, -1), 'fresh')   # 空头新贵
        self.assertEqual(C(-2.0, 0.5, True, -1.5, -1), 'flat')     # 正区恶化 ≠ 空头新贵
        self.assertEqual(C(-6.0, -0.5, True, 0.3, -1), 'pull')     # 反抽
        self.assertEqual(C(-6.0, -0.5, True, 1.0, -1), 'flat')     # Δ 显著回升但 4h 未转强 → ⚪


class TestFourTiers(unittest.TestCase):
    def _run(self, P1, R4, prev4, side=1):
        return sr._four_tiers(P1, R4, prev4, side)

    def test_rows_carry_delta_and_provisional(self):
        P1 = {'rb': _row('rb2610', 5.0, 100.0)}
        R4 = {'rb2610': _r4('rb2610', 1.5)}
        tiers = self._run(P1, R4, {'rb2610': 0.5})
        row = tiers['lead'][0]
        self.assertEqual(row['d4h'], 1.0)
        self.assertFalse(row['provisional'])
        # 前日评分缺失 → Δ 未知 → provisional
        tiers2 = self._run(P1, R4, {})
        self.assertIsNone(tiers2['lead'][0]['d4h'])
        self.assertTrue(tiers2['lead'][0]['provisional'])
        self.assertTrue(tiers2['lead'][0]['reason'].startswith('※'))

    def test_breach_remark_mirrors_by_side(self):
        """破位备注：多头侧 4h 收破 EE；空头侧 4h 上破 PP"""
        P1 = {'rb': _row('rb2610', 6.0, 100.0)}
        R4 = {'rb2610': _r4('rb2610', 0.5, close=85.0, ee=90.0)}   # 收破 EE
        row = self._run(P1, R4, {}, side=1)['pull'][0]
        self.assertTrue(row['breach_4h'])
        self.assertIn('收破 EE', row['reason'])

        P1s = {'cu': _row('cu2610', -6.0, 100.0, pos=-1)}
        R4s = {'cu2610': _r4('cu2610', -0.5, pos=-1, close=115.0, pp=110.0)}  # 上破 PP
        rows = self._run(P1s, R4s, {}, side=-1)['pull'][0]
        self.assertTrue(rows['breach_4h'])
        self.assertIn('上破 PP', rows['reason'])
        # 空头侧收破 EE 不算破位备注（方向无关）
        R4s['cu2610']['close'] = 85.0
        rows = self._run(P1s, R4s, {}, side=-1)['pull'][0]
        self.assertFalse(rows['breach_4h'])

    def test_bucket_membership_gates_lead(self):
        P1 = {'rb': _row('rb2610', 5.0, 100.0)}
        R4 = {'rb2610': _r4('rb2610', 1.5, in_bucket=False)}       # 评分够但掉桶
        tiers = self._run(P1, R4, {'rb2610': 1.0})
        self.assertEqual(tiers['lead'], [])
        self.assertEqual(tiers['pull'][0]['code'], 'rb')           # Δ=−0.5 未崩 → 回调


class TestPool(unittest.TestCase):
    def test_pool_diverts_strong_4h_from_flat(self):
        """⚪ 中 4h 在桶且 >1.0 者入蓄势池；水平不足者真·未入档（v6 实例：pb/AP 水平不足）"""
        P1 = {'TA': _row('TA601', 4.19, 100.0),    # ⚪：日线 <4.5、Δ 平缓
              'pb': _row('pb2610', 2.0, 100.0)}    # ⚪ 且 4h 弱
        R4 = {'TA601': _r4('TA601', 1.28), 'pb2610': _r4('pb2610', 0.55)}
        tiers = sr._four_tiers(P1, R4, {'TA601': 0.63, 'pb2610': 0.2}, side=1)
        pool = sr._pool_rows(tiers['flat'], side=1)
        self.assertEqual([r['code'] for r in pool], ['TA'])
        self.assertIn('提级', pool[0]['upgrade'])
        self.assertIn('未达', pool[0]['reason'])

    def test_pool_short_mirror(self):
        P1 = {'SA': _row('SA601', -2.79, 100.0, pos=-1)}
        R4 = {'SA601': _r4('SA601', -1.76, pos=-1)}
        tiers = sr._four_tiers(P1, R4, {'SA601': -1.0}, side=-1)
        pool = sr._pool_rows(tiers['flat'], side=-1)
        self.assertEqual([r['code'] for r in pool], ['SA'])
        # 4h 掉出空头桶 → 出池
        R4['SA601']['buckets'] = []
        tiers = sr._four_tiers(P1, R4, {'SA601': -1.0}, side=-1)
        self.assertEqual(sr._pool_rows(tiers['flat'], side=-1), [])


class TestPrev4hScores(unittest.TestCase):
    """前一交易日 4h score 重算：取日盘收盘 bar（15:xx），不取夜盘 23:00。"""

    def _payload(self, tmpdir, key='rb2610'):
        # 12 根 bar：保证 09-14 的 15:00 bar 越过 MA7 预热期（≥7 根）。
        # 09-14 日盘收 100，夜盘 23:00 收 200（应被忽略，不计入 09-14 收盘）。
        dates = ['2026-09-08 15:00', '2026-09-09 11:30', '2026-09-09 15:00', '2026-09-09 23:00',
                 '2026-09-10 11:30', '2026-09-10 15:00', '2026-09-11 15:00', '2026-09-11 23:00',
                 '2026-09-14 11:30', '2026-09-14 15:00', '2026-09-14 23:00',
                 '2026-09-15 15:00']
        closes = [100, 100, 100, 100, 100, 100, 100, 100, 100, 100, 200, 100]
        payload = {'dates': dates, 'ohlc': [[c, c, c, c] for c in closes]}
        (tmpdir / f'{key}.json').write_text(json.dumps(payload), encoding='utf-8')

    def test_picks_day_close_not_night_bar(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            self._payload(Path(td))
            with patch.object(sr, 'json_dir', lambda tf: Path(td)):
                scores = sr.prev_4h_scores('2026-09-14')
            self.assertAlmostEqual(scores['rb2610'], 0.0)  # 100 vs MA7(全100)=100
            # 若误取 23:00 夜盘 bar（收 200），score 会显著为正
            self.assertNotAlmostEqual(scores['rb2610'], (200 - 800 / 7) / (800 / 7) * 100)

    def test_missing_day_returns_no_entry(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            self._payload(Path(td))
            with patch.object(sr, 'json_dir', lambda tf: Path(td)):
                self.assertEqual(sr.prev_4h_scores('2026-09-10'), {})


class TestMomentum(unittest.TestCase):
    def test_accel_decel_and_tier_cross_reference(self):
        P4 = {'eg': _row('eg2601', 3.18, 100.0), 'sc': _row('sc2610', 0.32, 100.0),
              'xx': _row('xx2601', 0.5, 100.0)}
        P1 = {'eg': _row('eg2601', 9.56, 100.0), 'sc': _row('sc2610', 20.8, 100.0)}
        prev4 = {'eg2601': -0.73, 'sc2610': 6.29, 'xx2601': 0.45}
        scr1 = {'buckets': {'long_trend': [
            {'key': 'eg2601', 'name': 'eg', 'close': 100.0, 'score': 9.56,
             'rank': 5, 'rank_change': 4}]}}
        tiers_long = {t: [] for t in sr.TIER_ORDER}
        tiers_long['lead'] = [{'code': 'eg'}]
        tiers_short = {t: [] for t in sr.TIER_ORDER}
        m = sr._momentum(P4, P1, {}, prev4, scr1, tiers_long, tiers_short)
        self.assertEqual([i['code'] for i in m['accel']], ['eg'])
        self.assertEqual([i['code'] for i in m['decel']], ['sc'])
        self.assertIn('绝对龙头', m['accel'][0]['tier'])
        self.assertEqual(m['rank_moves'][0]['code'], 'eg')
        # Δ 不足 ±1 的不上榜
        self.assertNotIn('xx', [i['code'] for i in m['accel'] + m['decel']])


class TestRelay(unittest.TestCase):
    """_relay：三·补2 趋势强中继倒N（v6.4）纯分数跨档筛选与边界。"""

    def _call(self, p4, p1, prev4, tl=None, ts=None):
        tl = tl if tl is not None else {t: [] for t in sr.TIER_ORDER}
        ts = ts if ts is not None else {t: [] for t in sr.TIER_ORDER}
        return sr._relay(p4, p1, prev4, tl, ts)

    def test_short_weak_rebound_included(self):
        """空头侧：1d < −2、4h < −1、0 < Δ4h < +2 → 弱反抽入选"""
        P4 = {'ap': _row('AP601', -4.16, 100.0)}
        P1 = {'ap': _row('AP601', -6.08, 100.0)}
        out = self._call(P4, P1, {'AP601': -5.98})   # Δ4h = +1.82
        self.assertEqual([r['code'] for r in out['short']['rows']], ['ap'])
        self.assertEqual(out['long']['rows'], [])
        self.assertIn('弱反抽', out['short']['rows'][0]['reason'])

    def test_strict_boundaries_excluded(self):
        """边界全为严格不等号：s1=−2、s4=−1、Δ=0、Δ=+2 均不入选"""
        cases = [
            (-2.0, -4.0, -5.0),   # s1 恰为 −2
            (-6.0, -1.0, -2.0),   # s4 恰为 −1
            (-6.0, -4.0, -4.0),   # Δ4h = 0
            (-6.0, -4.0, -6.0),   # Δ4h = +2 到上界
        ]
        for i, (s1, s4, prev) in enumerate(cases):
            key = f'x{i}2601'
            out = self._call({f'x{i}': _row(key, s4, 100.0)},
                             {f'x{i}': _row(key, s1, 100.0)}, {key: prev})
            self.assertEqual(out['short']['rows'], [], f'case {i} 不应入选')

    def test_long_mirror_and_near(self):
        """多头侧镜像入选；Δ4h 越过窗口上界（如 +5.53）→ 进 near 供脚注"""
        P4 = {'fu': _row('fu2601', 2.5, 100.0), 'LC': _row('LC2601', -3.8, 100.0)}
        P1 = {'fu': _row('fu2601', 3.0, 100.0), 'LC': _row('LC2601', -5.0, 100.0)}
        prev4 = {'fu2601': 4.0, 'LC2601': -9.33}      # fu Δ=−1.5 入选；LC Δ=+5.53 明显反抽
        out = self._call(P4, P1, prev4)
        self.assertEqual([r['code'] for r in out['long']['rows']], ['fu'])
        self.assertEqual(out['short']['rows'], [])
        self.assertEqual([r['code'] for r in out['short']['near']], ['LC'])

    def test_long_boundary_excluded(self):
        """多头侧 Δ=0 或 Δ=−2 到界均不入选"""
        for prev, tag in ((2.5, 'Δ=0'), (4.5, 'Δ=−2')):
            out = self._call({'fu': _row('fu2601', 2.5, 100.0)},
                             {'fu': _row('fu2601', 3.0, 100.0)}, {'fu2601': prev})
            self.assertEqual(out['long']['rows'], [], tag)

    def test_missing_delta_neither_row_nor_near(self):
        """Δ4h 缺失：不入选也不进 near（unknown 不补零）"""
        out = self._call({'ap': _row('AP601', -4.16, 100.0)},
                         {'ap': _row('AP601', -6.08, 100.0)}, {})
        self.assertEqual(out['short']['rows'], [])
        self.assertEqual(out['short']['near'], [])

    def test_tier_cross_reference_and_sort(self):
        """当前档位跨档引用；行按 |日线 score| 降序"""
        P4 = {'ap': _row('AP601', -4.0, 100.0), 'jd': _row('jd2601', -1.5, 100.0)}
        P1 = {'ap': _row('AP601', -6.0, 100.0), 'jd': _row('jd2601', -2.3, 100.0)}
        ts = {t: [] for t in sr.TIER_ORDER}
        ts['lead'] = [{'code': 'ap'}]
        out = self._call(P4, P1, {'AP601': -5.0, 'jd2601': -2.6}, ts=ts)
        self.assertEqual([r['code'] for r in out['short']['rows']], ['ap', 'jd'])
        self.assertIn('绝对熊头', out['short']['rows'][0]['tier'])
        self.assertIsNone(out['short']['rows'][1]['tier'])

    def test_render_missing_relay_key(self):
        """旧扫描产物无 relay 键时渲染不炸，空侧脚注给出原因"""
        from backend.pipeline import summary_render as srdr
        html = srdr.render_html(
            {'rules_version': 'summary-v3.0', 'data_date': '2026-09-15',
             'prev_date': '2026-09-14', 'generated_at': {'1d': '2026-09-15 16:00:00',
             '4h': '2026-09-15 15:40:00'}, 'created_at': '2026-09-15 16:05:00',
             'overview': {'1d': {'long_trend': 0, 'short_trend': 0, 'long_to_short': 0},
                          '4h': {'long_trend': 0, 'short_trend': 0, 'long_to_short': 0}},
             'signal_actions': [], 'tiers_long': {t: [] for t in sr.TIER_ORDER},
             'tiers_short': {t: [] for t in sr.TIER_ORDER}, 'pool': {'long': [], 'short': []},
             'momentum': {'accel': [], 'decel': [], 'rank_moves': []},
             'key_levels': [], 'coverage': {}, 'quality_notes': []},
            None, '2026-09-16')
        self.assertIn('三·补2', html)
        self.assertIn('本期无', html)


class TestOptionsRebound(unittest.TestCase):
    def call(self, s1=-6.0, s4=0.5, previous=-1.0, pos=0):
        return sr._options_rebound(
            {'rb': _r4('rb2610', s4, pos=pos, in_bucket=False)},
            {'rb': _row('rb2610', s1, 100, pos=pos)},
            {'rb2610': previous}, {}, {})['rows']

    def test_positive_zero_and_negative_4h_allowed_without_position_gate(self):
        for s4 in (-5.0, 0.0, 0.5, 1.199):
            for pos in (-1, 0, 1):
                with self.subTest(s4=s4, pos=pos):
                    rows = self.call(s4=s4, previous=s4 - 2.5, pos=pos)
                    self.assertEqual(len(rows), 1)
                    self.assertAlmostEqual(rows[0]['d4h'], 2.5)
                    self.assertEqual(rows[0]['prev_score_4h'], s4 - 2.5)

    def test_strict_boundaries_and_outside_values_excluded(self):
        for s1, s4, previous in (
                (-4.5, 0.5, -1), (-4.49, 0.5, -1),
                (-6, 1.2, 0), (-6, 1.3, 0),
                (-6, 0.5, 0.5), (-6, 0.5, 0.6),
                (-6, 0.5, -2.5), (-6, 0.5, -2.6)):
            with self.subTest(s1=s1, s4=s4, previous=previous):
                self.assertEqual(self.call(s1, s4, previous), [])
        self.assertEqual(len(self.call(-4.501, 1.199, 1.198)), 1)

    def test_missing_invalid_and_nonfinite_scores_excluded(self):
        for value in (None, float('nan'), float('inf'), float('-inf'), True, '0.5'):
            for index in range(3):
                args = [-6.0, 0.5, -1.0]
                args[index] = value
                with self.subTest(value=value, index=index):
                    self.assertEqual(self.call(*args), [])
        self.assertEqual(sr._options_rebound(
            {'rb': _r4('rb2610', 0.5)}, {}, {'rb2610': -1}, {}, {})['rows'], [])
        self.assertEqual(sr._options_rebound(
            {'rb': _r4('rb2610', 0.5)}, {'rb': _row('rb2610', -6, 100)},
            {}, {}, {})['rows'], [])

    def test_sort_cross_reference_and_inputs_unchanged(self):
        p4 = {code: _r4(code + '2610', 0.5) for code in ('rb', 'cu', 'al')}
        p1 = {code: _row(code + '2610', score, 100)
              for code, score in (('rb', -6), ('cu', -8), ('al', -8))}
        previous = {r['key']: -1 for r in p4.values()}
        tiers = {'danger': [{'code': 'rb'}]}
        arguments = (p4, p1, previous, {}, tiers)
        before = copy.deepcopy(arguments)
        rows = sr._options_rebound(*arguments)['rows']
        self.assertEqual([r['code'] for r in rows], ['al', 'cu', 'rb'])
        self.assertEqual(rows[2]['tier'], sr.TIER_NAMES[-1]['danger'])
        self.assertIsNone(rows[0]['tier'])
        self.assertEqual(arguments, before)

    def test_render_escapes_text_and_preserves_existing_style(self):
        from backend.pipeline import summary_render as render
        row = self.call()[0]
        row.update(name='<b>品种</b>', reason='<script>bad</script>', tier='<i>档位</i>')
        body = render.s3d_options_rebound({'options_rebound': {'rows': [row]}})
        self.assertIn('<div class="card">', body)
        self.assertIn('<div class="tbwrap"><table>', body)
        self.assertIn('日线 score &lt; -4.5 且 4h score &lt; 1.2 且 0 &lt; Δ4h &lt; +3', body)
        self.assertIn('反抽中级别关键时刻，顺大逆小势必上期权。', body)
        self.assertIn('&lt;script&gt;bad&lt;/script&gt;', body)
        self.assertIn('&lt;b&gt;品种&lt;/b&gt;', body)
        self.assertIn('&lt;i&gt;档位&lt;/i&gt;', body)
        self.assertNotIn('<script>', body)

    def test_empty_results_distinguished_from_old_archive_without_scan(self):
        from backend.pipeline import summary_render as render
        body = render.s3d_options_rebound({'options_rebound': {'rows': []}})
        self.assertIn('本期 0 只', body)
        self.assertIn('本期无符合条件的品种', body)
        self.assertEqual(render.s3d_options_rebound({}), '')


if __name__ == "__main__":
    unittest.main()
