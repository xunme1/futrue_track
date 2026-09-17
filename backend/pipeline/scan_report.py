# -*- coding: utf-8 -*-
"""期货看板每日总结 · 事实扫描器（报告流水线第 1 步，纯规则、无 LLM）。

判据口径来源：资料库《期货看板日报 · 方法论与复制指南》（v4 判据体系），
移植自 daily_scan.py，数据源改为读取本地流水线产物，不再依赖 HTTP 看板服务。

与 daily_scan.py 的差异（仅数据层与输出层，判据完全一致）：
- 数据源：/api/screening[?timeframe=4h] → data/{,4h/}screening/latest.json
          /api/symbols[?timeframe=4h]   → data/{,4h/}json/*.json（POS / last_signal）
          前日归档 screen_{tf}_{YYYYMMDD}.json → data/reports/snapshots/
- 日期：TODAY / PREV 由 CLI 传入（默认取 screening 的 generated_at 日期）
- 输出：12 段文本（人类可读，调试用）+ 一份结构化 JSON（供渲染器消费），二者同源

用法：
    python -m backend.pipeline.scan_report
    python -m backend.pipeline.scan_report --data-date 2026-09-15 --prev-date 2026-09-14
    python -m backend.pipeline.scan_report --text-only
"""
import argparse
import io
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from backend.core.config import DATA_DIR
from backend.core.timeframes import TIMEFRAMES, json_dir, screening_file

# ---------------------------------------------------------------- 判据常量
# 龙头门槛（方法论 §2.3）
LEAD_1D, LEAD_4H = 4.5, 1.0
ABSO_1D = 10.0          # 日线绝对龙头门槛
QUASI_1D = 3.0          # 准龙头下沿；也是【五】分档的参与门槛
TIER_4H = -0.5          # 4h 微负 / 破位分界（农产品抵抗条件 3）

# 板块定义（方法论 §9；TYPE 未列出的默认「工」）
SECTORS = {
    '黑色系': ['rb', 'hc', 'i', 'j', 'jm', 'FG', 'SA', 'SF', 'SM', 'ss', 'SI'],
    '有色': ['cu', 'al', 'zn', 'pb', 'ni', 'sn', 'ao', 'PS', 'LC', 'bc'],
    '芳烃能化': ['bz', 'eb', 'eg', 'MA', 'PX', 'l', 'pp', 'PL', 'PF', 'PR', 'v', 'UR', 'SH', 'sp'],
    '能源链': ['sc', 'bu', 'fu', 'lu', 'ec', 'pg'],
    '橡胶系': ['nr', 'ru', 'br'],
    '贵金属': ['au', 'ag'],
    '股指': ['000016', '000300', '000852', 'IM', '588000'],
    '油脂粕': ['m', 'y', 'b', 'RM', 'a', 'OI', 'p'],
    '农软': ['CF', 'c', 'cs', 'SR', 'AP', 'PK', 'jd', 'lh', 'CJ'],
}
TYPE = {'油脂粕': '农', '农软': '农'}

REPORTS_DIR = DATA_DIR / "reports"
SNAPSHOT_DIR = REPORTS_DIR / "snapshots"
SCAN_DIR = REPORTS_DIR / "scan"

BUCKETS = ("long_trend", "short_trend", "long_to_short", "long_to_short_warning",
           "short_to_long", "short_to_long_warning",
           "short_pressure_warning", "long_support_warning")


# ---------------------------------------------------------------- 工具
def _f2(v):
    return f"{v:.2f}" if isinstance(v, (int, float)) else "None"


def _sc(r):
    """score，None → 0"""
    return (r or {}).get('score') or 0


def _gap_pct(close, ref):
    """收盘价相对参考位的偏离百分比（正 = 在线上方）"""
    return (close - ref) / ref * 100 if (close and ref) else None


def base_of(key: str) -> str:
    """合约码 → 品种码。保留原始大小写（FG601→FG，rb2610→rb），与板块字典匹配。"""
    return ''.join(ch for ch in key if ch.isalpha())


def sector_of(key: str) -> str:
    b = base_of(key)
    for s, lst in SECTORS.items():
        if b in lst:
            return s
    return '其他'


def stype(sector: str) -> str:
    return TYPE.get(sector, '工')


# ---------------------------------------------------------------- 数据加载（本地）
def _load(path: Path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _dump(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)


def load_screening(tf: str) -> dict:
    """等价 /api/screening[?timeframe=4h]"""
    return _load(screening_file(tf))


def load_symbols(tf: str):
    """等价 /api/symbols[?timeframe=4h]：key / pos / last_signal"""
    out = []
    for fp in sorted(json_dir(tf).glob("*.json")):
        d = _load(fp)
        sigs = d.get("signals") or []
        dates = d.get("dates") or []
        last_sig = None
        if sigs:
            s = sigs[-1]
            i = s["i"]
            last_sig = {"type": s["type"], "date": dates[i] if 0 <= i < len(dates) else None}
        out.append({
            "key": fp.stem,
            "pos": (d.get("POS") or [0])[-1],
            "last_signal": last_sig,
        })
    return out


def load_prev_screening(tf: str, prev_date: str):
    """前一日归档快照；缺失返回 None（首日运行正常）"""
    fp = SNAPSHOT_DIR / f"screen_{tf}_{prev_date.replace('-', '')}.json"
    return _load(fp) if fp.exists() else None


# ---------------------------------------------------------------- 索引构建
def build_rows(scr: dict, sym):
    """合约级索引。过滤 close is None 的旧合约（关键：否则虚增计数）。"""
    rows = {}
    for bk, items in scr['buckets'].items():
        for it in items:
            if it.get('close') is None:
                continue
            k = it['key']
            r = rows.setdefault(k, {'key': k, 'name': it.get('name', ''),
                                    'sector': sector_of(k), 'buckets': [], '_by_bucket': {}})
            r['name'] = it.get('name', '')
            if bk not in r['buckets']:
                r['buckets'].append(bk)
            r['_by_bucket'][bk] = it
            for f in ['close', 'score', 'DD', 'EE', 'KK', 'PP', 'signal_date',
                      'retest_count', 'retest_dates', 'rank', 'previous_rank',
                      'rank_change', 'rank_status', 'rank_history',
                      'bars_since_signal', 'stars', 'POS']:
                if it.get(f) is not None and f not in r:
                    r[f] = it.get(f)
    for x in sym:
        k = x['key']
        r = rows.setdefault(k, {'key': k, 'name': '', 'sector': sector_of(k),
                                'buckets': [], '_by_bucket': {}})
        r['pos'] = x.get('pos')
        r['last'] = x.get('last_signal') or {}
    return rows


def prod_view(rows):
    """品种级视图：同品种多合约取 |score| 最大者；无行情（close=None）一律剔除。"""
    out = {}
    for k, r in rows.items():
        if r.get('close') is None:
            continue
        b = base_of(k)
        if b not in out or abs(_sc(r)) > abs(_sc(out[b])):
            out[b] = r
    return out


def repr_of(rows, key):
    """按品种取某口径的代表合约（|score| 最大者）"""
    best = None
    for r in rows.values():
        if base_of(r['key']) != base_of(key):
            continue
        if best is None or abs(_sc(r)) > abs(_sc(best)):
            best = r
    return best or {}


def sector_mood(P1, sector):
    """板块氛围：返回 (已开空品种, 已离场品种)。已离场 = 不在日线多头榜。"""
    short_, gone_ = [], []
    for b in SECTORS.get(sector, []):
        r = P1.get(b)
        if r is None:
            gone_.append(b)                      # 不在日线任何桶
        elif r.get('pos') == -1:
            short_.append(b)
        elif 'long_trend' not in (r.get('buckets') or []):
            gone_.append(b)                      # 已平多未开空
    return short_, gone_


# ---------------------------------------------------------------- 事实提取
def _row(r):
    """结构化一行（供 JSON）"""
    return {
        'key': r.get('key'), 'code': base_of(r.get('key', '')), 'name': r.get('name', ''),
        'sector': r.get('sector'), 'stype': stype(r.get('sector')),
        'score': r.get('score'), 'close': r.get('close'),
        'DD': r.get('DD'), 'EE': r.get('EE'), 'KK': r.get('KK'), 'PP': r.get('PP'),
        'rank': r.get('rank'), 'rank_change': r.get('rank_change'),
        'rank_status': r.get('rank_status'), 'rank_history': r.get('rank_history'),
        'pos': r.get('pos'), 'last_signal': r.get('last') or None,
        'retest_count': r.get('retest_count'), 'retest_dates': r.get('retest_dates'),
    }


def scan(data_date=None, prev_date=None):
    scr = {tf: load_screening(tf) for tf in TIMEFRAMES}
    sym = {tf: load_symbols(tf) for tf in TIMEFRAMES}

    gen = {tf: scr[tf].get('generated_at', '') for tf in TIMEFRAMES}
    if not data_date:
        data_date = gen['1d'][:10]
    if not data_date:
        raise SystemExit("[错误] 1d screening 缺少 generated_at，请用 --data-date 指定")
    if not prev_date:
        prev_date = infer_prev_date(data_date)

    R = {tf: build_rows(scr[tf], sym[tf]) for tf in TIMEFRAMES}
    P = {tf: prod_view(R[tf]) for tf in TIMEFRAMES}
    prev = {tf: load_prev_screening(tf, prev_date) for tf in TIMEFRAMES}

    # 日线多头预警集合（品种码）
    WARN1 = {base_of(x['key']) for x in scr['1d']['buckets'].get('long_to_short_warning', [])
             if x.get('close') is not None}

    facts = {
        'scan_version': 1,
        'created_at': datetime.now().isoformat(timespec='seconds'),
        'data_date': data_date,
        'prev_date': prev_date,
        'generated_at': gen,
        'criteria': {'LEAD_1D': LEAD_1D, 'LEAD_4H': LEAD_4H, 'ABSO_1D': ABSO_1D,
                     'QUASI_1D': QUASI_1D, 'TIER_4H': TIER_4H},
    }

    # 【一】总览
    facts['overview'] = {
        '1d': scr['1d'].get('summary') or _summarize(scr['1d']),
        '4h': scr['4h'].get('summary') or _summarize(scr['4h']),
        'prev_1d': (prev['1d'] or {}).get('summary') if prev['1d'] else None,
        'prev_4h': (prev['4h'] or {}).get('summary') if prev['4h'] else None,
    }

    # 【二】【三】多空持仓
    # 多头按 score 降序（强的在前），空头按 score 升序（最负的在前）
    facts['long_positions'] = _by_sector([_row(r) for r in P['1d'].values()
                                          if r.get('pos') == 1], desc=True)
    facts['short_positions'] = _by_sector([_row(r) for r in P['1d'].values()
                                           if r.get('pos') == -1], desc=False)

    # 【四】龙头三档
    facts['leaders'] = _leaders(P['1d'], R['4h'])

    # 【五】日线多头 × 4h 分档
    facts['long_4h_tiers'] = _long_4h_tiers(P['1d'], R['4h'])

    # 【六】分歧判定（农/工分流）
    facts['divergence'] = _divergence(P['1d'], R['4h'], WARN1)

    # 【七】阶段性转折
    facts['turn'] = _turn(scr['1d'], scr['4h'], P['1d'])

    # 【八】龙头回踩 / 【九】熊头遇压
    facts['leader_retest'] = _attach_4h(
        [_row_bucket(it) for it in
         sorted(scr['1d']['buckets'].get('long_support_warning', []),
                key=lambda x: -(x.get('score') or 0))
         if it.get('close') is not None], R['4h'])
    facts['bear_pressure'] = _attach_4h(
        [_row_bucket(it) for it in
         sorted(scr['1d']['buckets'].get('short_pressure_warning', []),
                key=lambda x: (x.get('score') or 0))
         if it.get('close') is not None], R['4h'])

    # 【十】排名雷达
    facts['rank_radar'] = _rank_radar(scr['1d'])

    # 【十一】今日新信号
    facts['new_signals'] = {
        tf: [{'key': x['key'], 'code': base_of(x['key']), 'pos': x.get('pos'),
              'signal': (x.get('last_signal') or {}).get('type')}
             for x in sym[tf] if (x.get('last_signal') or {}).get('date') == data_date]
        for tf in TIMEFRAMES
    }

    # 【十二】关键位紧贴度
    facts['key_levels'] = _key_levels(P['1d'])

    return facts


def _summarize(scr):
    return {b: len(scr['buckets'].get(b, [])) for b in BUCKETS}


def _by_sector(rows, desc=True):
    """按板块分组。desc=True 按 score 降序（多头），False 升序（空头：最负在前）。"""
    out = defaultdict(list)
    for r in rows:
        out[r['sector']].append(r)
    if desc:
        return {s: sorted(v, key=lambda x: -_sc(x)) for s, v in out.items()}
    return {s: sorted(v, key=lambda x: _sc(x)) for s, v in out.items()}


def _row_bucket(it):
    """桶条目 → 结构化（保留 retest_dates 等）"""
    return {
        'key': it.get('key'), 'code': base_of(it.get('key', '')),
        'name': it.get('name', ''), 'sector': sector_of(it.get('key', '')),
        'score': it.get('score'), 'close': it.get('close'),
        'DD': it.get('DD'), 'EE': it.get('EE'), 'KK': it.get('KK'), 'PP': it.get('PP'),
        'rank': it.get('rank'), 'rank_change': it.get('rank_change'),
        'rank_status': it.get('rank_status'), 'rank_history': it.get('rank_history'),
        'retest_count': it.get('retest_count'), 'retest_dates': it.get('retest_dates'),
        'signal_date': it.get('signal_date'),
    }


def _leaders(P1, R4):
    dual, abso, quasi = [], [], []
    for b, r in P1.items():
        if r.get('pos') != 1:
            continue
        r4 = repr_of(R4, b)
        s1, s4 = _sc(r), _sc(r4)
        if s1 >= LEAD_1D and s4 >= LEAD_4H:
            dual.append((b, r, r4))
        elif s1 >= ABSO_1D and s4 > 0:
            abso.append((b, r, r4))
        if QUASI_1D <= s1 < LEAD_1D and s4 >= LEAD_4H:
            quasi.append((b, r, r4))
    dual.sort(key=lambda x: -_sc(x[1]))
    abso.sort(key=lambda x: -_sc(x[1]))
    quasi.sort(key=lambda x: -_sc(x[1]))
    return {
        'dual': [_leader_row(b, r, r4) for b, r, r4 in dual],
        'absolute': [_leader_row(b, r, r4) for b, r, r4 in abso],
        'quasi': [_leader_row(b, r, r4) for b, r, r4 in quasi],
    }


def _leader_row(b, r, r4):
    d = _row(r)
    d.update({'score_4h': r4.get('score'), 'key_4h': r4.get('key')})
    return d


def _long_4h_tiers(P1, R4):
    """【五】日线多头（score≥QUASI_1D）× 4h 状态分档"""
    tiers = {'4h强势': [], '4h贴零': [], '4h微负': [], '4h破位': []}
    for b, r in sorted(P1.items(), key=lambda x: -_sc(x[1])):
        if r.get('pos') != 1 or _sc(r) < QUASI_1D:
            continue
        s4 = _sc(repr_of(R4, b))
        if s4 >= LEAD_4H:
            tag = '4h强势'
        elif s4 >= 0:
            tag = '4h贴零'
        elif s4 >= TIER_4H:
            tag = '4h微负'
        else:
            tag = '4h破位'
        r4 = repr_of(R4, b)
        d = _row(r)
        d.update({'score_4h': s4, 'tier': tag,
                  'in_long_trend_4h': 'long_trend' in (r4.get('buckets') or []),
                  'pos_4h': r4.get('pos'),
                  'gap_to_EE': _gap_pct(r.get('close'), r.get('EE'))})
        tiers[tag].append(d)
    return tiers


def _divergence(P1, R4, WARN1):
    """【六】分歧判定：工业品判分歧 / 农产品判「多头抵抗」"""
    sectors, items = [], []
    for s in SECTORS:
        short_, gone_ = sector_mood(P1, s)
        if len(short_) + len(gone_) < 2:
            continue
        hits = []
        for b in SECTORS[s]:
            r = P1.get(b)
            if not r or r.get('pos') != 1:
                continue                                  # 只判日线仍多头
            r4 = repr_of(R4, b)
            s1, s4 = _sc(r), _sc(r4)
            if s1 >= LEAD_1D and s4 >= LEAD_4H:
                continue                                  # 龙头不判
            if 'long_trend' in (r4.get('buckets') or []):
                continue                                  # 4h 仍持多不判
            hits.append((b, r, r4))
        if not hits:
            continue
        sectors.append({'sector': s, 'stype': stype(s),
                        'short': short_, 'gone': gone_,
                        'mood_n': len(short_) + len(gone_)})
        for b, r, r4 in sorted(hits, key=lambda x: _sc(x[1])):
            s1, s4 = _sc(r), _sc(r4)
            close, DD, EE = r.get('close'), r.get('DD'), r.get('EE')
            chg = r.get('rank_change')
            c2 = (s1 > 0 and close is not None and DD is not None and close >= DD)
            c3 = (s4 >= TIER_4H)
            c4 = (isinstance(chg, int) and not isinstance(chg, bool) and chg >= 0)
            warn = b in WARN1
            weak = s4 < 0
            d = _row(r)
            d.update({'score_4h': s4, 'in_warning': warn,
                      'c2': c2, 'c3': c3, 'c4': c4,
                      'gap_to_EE': _gap_pct(close, EE), 'sector_name': s})
            if stype(s) == '农':
                if c2 and c3 and c4:
                    d['verdict'], d['level'] = '多头抵抗', '🟡'
                else:
                    d['verdict'], d['level'] = '未全中(回落工业品口径)', None
                    d = _industrial_grade(d, s1, s4, warn, weak)
            else:
                d = _industrial_grade(d, s1, s4, warn, weak)
            items.append(d)
    return {'sectors': sectors, 'items': items}


def _attach_4h(rows, R4):
    """给日线桶条目补 4h 状态（score / pos / 是否仍在 4h 多头桶），供渲染分档用。
    仅写 JSON，不影响 12 段文本输出。"""
    for r in rows:
        r4 = repr_of(R4, r.get('key', ''))
        r['score_4h'] = r4.get('score')
        r['pos_4h'] = r4.get('pos')
        r['in_long_trend_4h'] = 'long_trend' in (r4.get('buckets') or [])
        r['key_4h'] = r4.get('key')
    return rows


def _industrial_grade(d, s1, s4, warn, weak):
    """工业品口径：4h 转负或挂预警 → 分歧；否则按回踩"""
    if weak or warn:
        d['verdict'] = '分歧'
        d['level'] = '🔴' if (s1 < 0 or warn) else '🟠'
        why = []
        if s4 < 0:
            why.append(f"4h转负({_f2(s4)})")
        if warn:
            why.append("挂日线预警")
        if s1 < 0:
            why.append(f"日线转负({_f2(s1)})")
        d['why'] = why
    else:
        d['verdict'], d['level'] = '回踩', '✅'
        d['why'] = []
    return d


def _turn(scr1, scr4, P1):
    """【七】阶段性转折：A 4h 多转空 × 日线裁决 / B 反向信号"""
    rows4 = {}
    for it in scr4['buckets'].get('long_to_short', []):
        if it.get('close') is None:
            continue
        k, sd = it['key'], it.get('signal_date') or ''
        if k in rows4 and rows4[k][0] >= sd:
            continue
        rows4[k] = (sd, it)
    a = []
    for k, (sd, it) in sorted(rows4.items(), key=lambda x: (x[1][1].get('score') or 0)):
        b = base_of(k)
        r1 = P1.get(b, {})
        p1 = r1.get('pos')
        if p1 == -1:
            v = '共振空'
        elif p1 == 0:
            v = '已离场'
        elif p1 == 1:
            v = '日线仍多'
        else:
            v = '?'
        d = _row_bucket(it)
        d.update({'pos_1d': p1, 'verdict': v, 'EE_1d': r1.get('EE'), 'score_1d': r1.get('score')})
        a.append(d)
    b_list = []
    for it in scr4['buckets'].get('short_to_long', []):
        d = _row_bucket(it)
        d['pos_1d'] = P1.get(base_of(it.get('key', '')), {}).get('pos')
        b_list.append(d)
    warn = {}
    for nm, arr in (('4h短转长预警', scr4['buckets'].get('short_to_long_warning', [])),
                    ('1d短转长预警', scr1['buckets'].get('short_to_long_warning', []))):
        warn[nm] = [{'code': base_of(x.get('key', '')), 'name': x.get('name', ''),
                     'signal_date': x.get('signal_date'), 'score': x.get('score')} for x in arr]
    return {'A': a, 'B': b_list, 'warnings': warn}


def _rank_radar(scr1):
    out = {}
    for side in ('long_trend', 'short_trend'):
        items = sorted([x for x in scr1['buckets'].get(side, []) if x.get('close') is not None],
                       key=lambda x: (x.get('rank') or 999))
        rows = []
        for it in items:
            chg = it.get('rank_change')
            d = _row_bucket(it)
            d['risen'] = isinstance(chg, int) and not isinstance(chg, bool) and chg >= 3
            d['fallen'] = isinstance(chg, int) and not isinstance(chg, bool) and chg <= -3
            rows.append(d)
        out[side] = rows
    return out


def _key_levels(P1, top=15):
    arr = []
    for b, r in P1.items():
        c, ee, kk = r.get('close'), r.get('EE'), r.get('KK')
        if r.get('pos') == 1 and c and ee:
            arr.append((abs(_gap_pct(c, ee) or 0), b, r.get('name', ''), _gap_pct(c, ee), '多头距EE'))
        if r.get('pos') == -1 and c and kk:
            arr.append((abs(_gap_pct(c, kk) or 0), b, r.get('name', ''), _gap_pct(c, kk), '空头距KK'))
    arr.sort()
    return [{'code': b, 'name': nm, 'gap_pct': gp, 'label': lab} for _, b, nm, gp, lab in arr[:top]]


def infer_prev_date(data_date: str) -> str:
    """默认上一交易日：取快照中早于 data_date 的最新一份"""
    tag = data_date.replace('-', '')
    cands = sorted(p.stem.rsplit('_', 1)[-1] for p in SNAPSHOT_DIR.glob("screen_1d_*.json")
                   if p.stem.rsplit('_', 1)[-1] < tag)
    if not cands:
        d = datetime.strptime(data_date, "%Y-%m-%d").date() - timedelta(days=1)
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        return d.isoformat()
    s = cands[-1]
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}"


# ---------------------------------------------------------------- 文本渲染（12 段）
def render_text(f: dict) -> str:
    L = []
    W = "="
    data_date, prev_date = f['data_date'], f['prev_date']

    L.append(W * 112)
    L.append(f"【一】总览计数 + 与前日对比        数据日期 {data_date}   对比 {prev_date}")
    L.append(f"  1d generated_at: {f['generated_at']['1d']}")
    L.append(f"  1d: " + json.dumps(f['overview']['1d'], ensure_ascii=False))
    L.append(f"  4h generated_at: {f['generated_at']['4h']}")
    L.append(f"  4h: " + json.dumps(f['overview']['4h'], ensure_ascii=False))
    for tf in TIMEFRAMES:
        p = f['overview'].get(f'prev_{tf}')
        L.append(f"  {tf} {prev_date}: " + (json.dumps(p, ensure_ascii=False) if p else "无归档（首日运行正常）"))

    L.append("\n" + W * 112)
    L.append("【二】日线多头持仓（POS=1）按板块")
    for s in _ordered_sectors(f['long_positions']):
        rows = f['long_positions'][s]
        L.append(f"\n  ◆ {s}({stype(s)}) n={len(rows)}: " + ", ".join(f"{r['key']}={_f2(r['score'])}" for r in rows))
        for r in rows:
            ls = r.get('last_signal') or {}
            L.append(f"      {r['key']:8s}{r['name']:12s}sc={_f2(r['score']):>7} close={str(r['close']):<10} "
                     f"DD={_f2(r['DD'])} EE={_f2(r['EE'])} KK={r['KK']} PP={r['PP']} "
                     f"rank={r['rank']}({r['rank_change']}) {ls.get('type')}@{ls.get('date')}")

    L.append("\n" + W * 112)
    L.append("【三】日线空头持仓（POS=-1）按板块")
    for s in _ordered_sectors(f['short_positions']):
        rows = f['short_positions'][s]
        L.append(f"\n  ◆ {s}({stype(s)}) n={len(rows)}: " + ", ".join(f"{r['key']}={_f2(r['score'])}" for r in rows))
        for r in rows:
            ls = r.get('last_signal') or {}
            L.append(f"      {r['key']:8s}{r['name']:12s}sc={_f2(r['score']):>7} close={str(r['close']):<10} "
                     f"KK={r['KK']} PP={r['PP']} 触压={r['retest_count']}次 "
                     f"rank={r['rank']}({r['rank_change']}) {ls.get('type')}@{ls.get('date')}")

    c = f['criteria']
    L.append("\n" + W * 112)
    L.append("【四】趋势与龙头")
    L.append(f"  定义：🥇双强龙头 = 日线≥{c['LEAD_1D']} 且 4h≥{c['LEAD_4H']}；"
             f"🥇日线绝对龙头 = 日线≥{c['ABSO_1D']:g} 且 4h>0；🥈准龙头 = 日线 {c['QUASI_1D']:.1f}~{c['LEAD_1D']} 且 4h≥{c['LEAD_4H']}")
    for r in f['leaders']['dual']:
        L.append(f"  🥇 {r['code']:7s}{r['name']:12s}1d={_f2(r['score']):>7} 4h={_f2(r['score_4h']):>7} "
                 f"rank={r['rank']}({_sign(r['rank_change'])}) {r.get('rank_status')}")
    L.append(f"  ── 双强龙头 共 {len(f['leaders']['dual'])} 只")
    for r in f['leaders']['absolute']:
        L.append(f"  🥇★{r['code']:7s}{r['name']:12s}1d={_f2(r['score']):>7} 4h={_f2(r['score_4h']):>7} "
                 f"rank={r['rank']}({_sign(r['rank_change'])}) ← 日线绝对龙头（4h 仅弱正但从未转负）")
    L.append("  🥈 准龙头：")
    for r in f['leaders']['quasi']:
        L.append(f"      {r['code']:7s}{r['name']:12s}1d={_f2(r['score']):>7} 4h={_f2(r['score_4h']):>7} "
                 f"rank={r['rank']}({r['rank_change']})")

    L.append("\n" + W * 112)
    L.append("【五】日线多头 × 4h 状态分档（中性描述，结论交第六段）")
    TAG = {'4h强势': '✅ 4h强势', '4h贴零': '🟡 4h贴零（正值但弱）',
           '4h微负': '🟠 4h微负', '4h破位': '🚨 4h破位'}
    flat = []
    for tag, rows in f['long_4h_tiers'].items():
        flat.extend((tag, r) for r in rows)
    for tag, r in sorted(flat, key=lambda x: -_sc(x[1])):
        L.append(f"  {r['code']:7s}{r['name']:12s}1d={_f2(r['score']):>7} 4h={_f2(r['score_4h']):>7} "
                 f"close={str(r['close']):<10}DD={_f2(r['DD'])} EE={_f2(r['EE'])} "
                 f"距EE={_f2(r['gap_to_EE'])}% rank={r['rank']}({r['rank_change']})  {TAG[tag]}")

    L.append("\n" + W * 112)
    L.append("【六】分歧判定 ★核心★ —— 工业品判分歧 / 农产品判「多头抵抗」")
    L.append("  工业品分歧 = ① 板块氛围坏 ② 4h 已平仓（不在 4h 多头榜） ③ 4h 转负 或 挂日线多头预警")
    L.append(f"  农产品抵抗 = ① 板块氛围坏 ② 日线 score>0 且 close≥DD ③ 4h ≥ {c['TIER_4H']} ④ rank_change ≥ 0（4 条全中）")
    L.append("-" * 112)
    by_sector = defaultdict(list)
    for it in f['divergence']['items']:
        by_sector[it['sector_name']].append(it)
    for s in f['divergence']['sectors']:
        name = s['sector']
        L.append(f"\n  ◆ {name}（{s['stype']}品） 氛围坏：已开空 {len(s['short'])} 只 {s['short']} ｜ 已离场 {len(s['gone'])} 只 {s['gone']}")
        for it in sorted(by_sector.get(name, []), key=lambda x: _sc(x)):
            if s['stype'] == '农' and it['verdict'] == '多头抵抗':
                L.append(f"     🟡 {it['code']:6s}{it['name']:12s}1d={_f2(it['score']):>7} 4h={_f2(it['score_4h']):>7} "
                         f"close={it['close']} DD={_f2(it['DD'])} EE={_f2(it['EE'])} rank={it['rank']}({it['rank_change']})")
                L.append(f"        → ✅ 4/4 全中 → 【多头抵抗】按回踩关注（不砍、可低吸）；日线破 EE={_f2(it['EE'])} 才算失败")
            elif s['stype'] == '农':
                L.append(f"     ❌ {it['code']:6s}{it['name']:12s}1d={_f2(it['score']):>7} 4h={_f2(it['score_4h']):>7} "
                         f"条件2={it['c2']} 条件3={it['c3']} 条件4={it['c4']} → 未全中，回落到工业品口径")
            elif it['verdict'] == '分歧':
                L.append(f"     {it['level']} {it['code']:6s}{it['name']:12s}1d={_f2(it['score']):>7} 4h={_f2(it['score_4h']):>7} "
                         f"距EE={_f2(it['gap_to_EE'])}% rank={it['rank']}({it['rank_change']}) → 分歧（{'；'.join(it['why'])}）")
            else:
                L.append(f"     ✅ {it['code']:6s}{it['name']:12s}1d={_f2(it['score']):>7} 4h={_f2(it['score_4h']):>7} "
                         f"→ 4h 未明显偏弱（≥0）→ 按【回踩】处理")

    L.append("\n" + W * 112)
    L.append("【七】阶段性转折")
    L.append("  A. 4h 多转空 → 日线裁决（🟥共振空 / ⬜已离场 / 🟨日线仍多=回踩）")
    MARK = {'共振空': '🟥共振空', '已离场': '⬜已离场', '日线仍多': '🟨日线仍多', '?': '?'}
    for it in f['turn']['A']:
        L.append(f"     {it['code']:6s}{it['name']:12s}4h={_f2(it['score']):>7} sig={it.get('signal_date')} | "
                 f"1dPOS={it['pos_1d']} 1dEE={it['EE_1d']} → {MARK.get(it['verdict'], it['verdict'])}")
    L.append("\n  B. 4h 空转多 / 短转长预警（反向信号，多为假转折）")
    for it in f['turn']['B']:
        L.append(f"     {it['code']:6s}{it['name']:12s}sig={it.get('signal_date')} 4h={_f2(it['score'])} 1dPOS={it['pos_1d']}")
    for nm, arr in f['turn']['warnings'].items():
        L.append(f"     {nm}: " + (", ".join(f"{x['code']}({x['signal_date']},sc={_f2(x['score'])})" for x in arr) or "无"))

    L.append("\n" + W * 112)
    L.append(f"【八】龙头回踩（long_support_warning）  今日新触 = {data_date}")
    for it in f['leader_retest']:
        rd = it.get('retest_dates') or []
        fresh = '★今日新触' if data_date in rd else ''
        L.append(f"  {it['code']:6s}{it['name']:12s}sc={_f2(it['score']):>7} close={it['close']} PP={it['PP']} "
                 f"EE={it['EE']} 回踩={it['retest_count']}次 {rd} {fresh}")

    L.append("\n" + W * 112)
    L.append(f"【九】熊头遇压（short_pressure_warning）  今日新触 = {data_date}")
    for it in f['bear_pressure']:
        rd = it.get('retest_dates') or []
        fresh = '★今日新触' if data_date in rd else ''
        L.append(f"  {it['code']:6s}{it['name']:12s}sc={_f2(it['score']):>7} close={it['close']} KK={it['KK']} "
                 f"PP={it['PP']} 距KK={_f2(_gap_pct(it['close'], it['KK']))}% 触压={it['retest_count']}次 {rd} {fresh}")

    L.append("\n" + W * 112)
    L.append("【十】动量排名雷达（1d 多/空榜）  显著阈值 |chg|>=3，NEW=新入榜")
    for side, lab in (('long_trend', '多头榜'), ('short_trend', '空头榜')):
        items = f['rank_radar'].get(side, [])
        L.append(f"\n  --- {lab} n={len(items)} ---")
        for it in items:
            chg = it.get('rank_change')
            mk = '  <<< 新贵' if it['risen'] else ('  <<< 掉队' if it['fallen'] else '')
            hs = " ".join(str(x.get('rank')) if x.get('rank') is not None else '-'
                          for x in (it.get('rank_history') or []))
            L.append(f"    #{it['rank']:<4}{it['code']:7s}{it['name']:12s}sc={_f2(it['score']):>7} "
                     f"chg={_sign(chg)} {it.get('rank_status', '')}{mk}")
            L.append(f"         7日轨迹: {hs}")

    L.append("\n" + W * 112)
    L.append(f"【十一】今日（{data_date}）出新信号")
    for tf in TIMEFRAMES:
        t = f['new_signals'][tf]
        L.append(f"  {tf}: " + (", ".join(f"{x['code']}(pos={x['pos']},{x['signal']})" for x in t) if t else "无"))

    L.append("\n" + W * 112)
    L.append("【十二】关键位紧贴度（生死线排序）—— 收盘距日线 EE 最近的多头 + 距 KK 最近的空头")
    for x in f['key_levels']:
        L.append(f"  {x['label']}  {x['code']:6s}{x['name']:12s} {x['gap_pct']:+.2f}%")

    L.append("\n" + W * 112)
    L.append("扫描完成。下一步：按「判据体系」把上述素材写成日报（9 节结构见方法论文档）。")
    return "\n".join(L)


def _sign(chg):
    return f"{chg:+d}" if isinstance(chg, int) and not isinstance(chg, bool) else " NEW"


def _ordered_sectors(mapping):
    """只输出 SECTORS 内定义的板块，顺序同 SECTORS 定义（与 daily_scan 版式一致）。
    未在板块字典内的品种（如 TA）仍留在 JSON 里，仅不出现在【二】【三】文本段。"""
    return [s for s in SECTORS if s in mapping]


# ---------------------------------------------------------------- CLI
def main():
    ap = argparse.ArgumentParser(description="期货看板每日总结 · 事实扫描器（本地数据版）")
    ap.add_argument("--data-date", help="数据日期 YYYY-MM-DD（默认取 1d screening 的 generated_at）")
    ap.add_argument("--prev-date", help="上一交易日 YYYY-MM-DD（默认取快照中最近一份）")
    ap.add_argument("--text-only", action="store_true", help="只打印文本，不落盘")
    args = ap.parse_args()

    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    f = scan(data_date=args.data_date, prev_date=args.prev_date)

    if args.text_only:
        print(render_text(f))
        return

    SCAN_DIR.mkdir(parents=True, exist_ok=True)
    tag = f['data_date']
    _dump(SCAN_DIR / f"scan_{tag}.json", f)
    txt = render_text(f)
    (SCAN_DIR / f"scan_{tag}.txt").write_text(txt, encoding="utf-8")
    print(txt)
    print(f"\n[产物] {SCAN_DIR / f'scan_{tag}.json'}")
    print(f"[产物] {SCAN_DIR / f'scan_{tag}.txt'}")


if __name__ == "__main__":
    main()
