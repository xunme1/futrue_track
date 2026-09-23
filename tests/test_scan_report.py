# -*- coding: utf-8 -*-
"""v6 四档分类的判据回归测试（不依赖 data/ 产物，用构造数据跑纯函数）。

覆盖：四档优先级互斥、阈值边界（4.5 / ±1.0）、新贵 4h 强势前置、
危险分歧的「4h 偏弱 + Δ≤−1」双条件、回调的 Δ>−1 条件、空头镜像、
Δ4h 缺失 → provisional、蓄势池分流、前一交易日 4h score 重算口径。
任一判据被改动而结果没变 → 这里会红。
"""
import json
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


if __name__ == "__main__":
    unittest.main()
