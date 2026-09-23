# -*- coding: utf-8 -*-
"""期货看板每日总结 · 事实扫描器（v6 口径，报告流水线第 1 步，纯规则、无 LLM）。

判据口径：《期货看板日报 · 方法论与复制指南 v6》（项目根目录）——
两要素（趋势 × 动量）× 两周期（1d / 4h）：日线定方向，4h 定节奏。
多头侧四档互斥、按优先级取档：🥇绝对龙头 → 🔴危险分歧 → 🚀新贵 → 🟡回调，
空头侧镜像（🐻绝对熊头 / 🔴空头危险分歧 / 📉空头新贵 / 🟡反抽）；
⚪ 未入档中 4h 已强者分流至 🟢 4h 蓄势池。
v5 时代的数据诚信守卫保留：破位与评分独立核验、未知不补零、SP/BP 区分、
close 为空的旧合约过滤、两周期行情日同步校验。见 docs/methodology_v6_review.md。

数据源（本地流水线产物，不依赖 HTTP 看板服务）：
- 当期筛选：data/{,4h/}screening/latest.json
- 权威状态：data/{,4h/}json/*.json（POS / last_signal / 关键位）
- Δ4h 环比：从 data/4h/json/*.json 全量 K 线重算前一交易日 4h score
  （与 screen.py 同口径 (close−MA7)/MA7×100，取日盘收盘 bar，夜盘 23:00 bar 属次日）
- 前日计数基线：data/reports/snapshots/screen_{tf}_{YYYYMMDD}.json

输出：v6 五节调试文本（人类可读）+ 一份结构化 JSON（供渲染器消费），二者同源。

用法：
    python -m backend.pipeline.scan_report
    python -m backend.pipeline.scan_report --data-date 2026-09-15 --prev-date 2026-09-14
    python -m backend.pipeline.scan_report --text-only
"""
import argparse
import json
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from backend.core.config import DATA_DIR, load_contracts
from backend.pipeline.report_store import RULES_VERSION, iso_day, digest, atomic_json, atomic_text
from backend.pipeline.screen import moving_average
from backend.core.timeframes import TIMEFRAMES, json_dir, screening_file

# ---------------------------------------------------------------- 判据常量（v6 §1 阈值表）
LEAD_1D, LEAD_4H = 4.5, 1.0     # 日线 / 4h 动量强门槛
D4H_SIG = 1.0                   # 4h 动量环比显著变化门槛 |Δ4h|
NEW_STRONG_4H = 1.0             # 新贵档前置：4h score 必须越过 ±1.0（与 LEAD_4H 同值）

# 板块定义（仅用于表格分组展示，v6 不再做农/工分流裁决）
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

REPORTS_DIR = DATA_DIR / "reports"
SNAPSHOT_DIR = REPORTS_DIR / "snapshots"
SCAN_DIR = REPORTS_DIR / "scan"

BUCKETS = ("long_trend", "short_trend", "long_to_short", "long_to_short_warning",
           "short_to_long", "short_to_long_warning",
           "short_pressure_warning", "long_support_warning")

# 四档代码 → 两侧档位名（渲染与叙事共用，CSS 语义类同名）
TIER_NAMES = {
    1: {'lead': '🥇 绝对龙头', 'danger': '🔴 危险分歧', 'fresh': '🚀 新贵',
        'pull': '🟡 回调', 'flat': '⚪ 未入档'},
    -1: {'lead': '🐻 绝对熊头', 'danger': '🔴 空头危险分歧', 'fresh': '📉 空头新贵',
         'pull': '🟡 反抽', 'flat': '⚪ 未入档'},
}
TIER_ORDER = ('lead', 'danger', 'fresh', 'pull', 'flat')


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
    code = str(key).split('.')[0]
    return code if code.isdigit() else ''.join(ch for ch in code if ch.isalpha())


def sector_of(key: str) -> str:
    b = base_of(key)
    for s, lst in SECTORS.items():
        if b in lst:
            return s
    return '其他'


# ---------------------------------------------------------------- 数据加载（本地）
def _load(path: Path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _dump(path: Path, payload) -> None:
    atomic_json(path, payload)


def load_screening(tf: str) -> dict:
    """等价 /api/screening[?timeframe=4h]"""
    return _load(screening_file(tf))


def load_symbols(tf: str):
    """权威状态及当前行情；保留最近两次交易信号以复核重新开多。"""
    out = []
    for fp in sorted(json_dir(tf).glob("*.json")):
        d = _load(fp)
        dates = d.get("dates") or []
        signals = [{"type": v["type"], "date": dates[v["i"]]}
                   for v in sorted(d.get("signals") or [], key=lambda v: v["i"])
                   if 0 <= v["i"] < len(dates)]
        row = {"key": fp.stem, "name": d.get("name", ""),
               "last_date": dates[-1] if dates else None,
               "pos": (d.get("POS") or [None])[-1],
               "last_signal": signals[-1] if signals else None, "recent_signals": signals[-2:],
               "close": d["ohlc"][-1][1] if d.get("ohlc") else None}
        row.update({field: (d.get(field) or [None])[-1] for field in ("score", "DD", "EE", "KK", "PP")})
        out.append(row)
    return out


def _close_bar(bar):
    try:
        v = float(bar[1])
        return v if v == v else None
    except (TypeError, ValueError, IndexError):
        return None


def prev_4h_scores(prev_date: str) -> dict:
    """前一交易日 4h score 截面：合约码 → score（与 screen.py 同口径 (close−MA7)/MA7×100）。

    取 prev_date 当天的日盘收盘 bar（时刻 ≤15:30 的最后一根）；夜盘 23:00 bar
    按源标签属当日日历日，但实际是次一交易日夜盘，不参与「前一交易日收盘」。
    当天无日盘 bar 时回退到当天最后一根。数据不足（MA7 预热期、缺价）则不含该键，
    调用方按 None 处理：Δ 栏标「—」，依赖 Δ 的分档暂定并记 provisional。
    """
    out = {}
    for fp in sorted(json_dir('4h').glob("*.json")):
        d = _load(fp)
        dates, ohlc = d.get("dates") or [], d.get("ohlc") or []
        if len(dates) != len(ohlc) or not dates:
            continue
        day_idx = [i for i, t in enumerate(dates) if iso_day(t) == prev_date]
        if not day_idx:
            continue
        day_close = [i for i in day_idx if str(dates[i])[11:16] <= "15:30"]
        idx = day_close[-1] if day_close else day_idx[-1]
        closes = [_close_bar(b) for b in ohlc]
        ma7 = moving_average(closes, 7)[idx]
        close = closes[idx]
        if close is not None and ma7 not in (None, 0):
            out[fp.stem] = (close - ma7) / ma7 * 100
    return out


def market_day(screen):
    """优先使用明确交易日；老筛选文件从最新行情行日期恢复。"""
    explicit = iso_day(screen.get('data_date')) or iso_day((screen.get('trend_ranking') or {}).get('as_of'))
    days = {iso_day(r.get('date')) for rows in screen.get('buckets', {}).values() for r in rows}
    days.discard(None)
    if explicit:
        if days and max(days) > explicit:
            raise ValueError('筛选行情日期晚于声明的数据日')
        return explicit
    if days:
        return max(days)
    raise ValueError('筛选缺少可核验的行情日期；不能使用 generated_at 替代')


def prepare_inputs(scr, sym):
    """固定当前观察池，过期标的保留未知状态，不让旧价位参与新日判断。"""
    contracts = {str(c.get('key') or c['symbol']).split('.')[0]: c for c in load_contracts()}
    notes, days = [], {tf: market_day(scr[tf]) for tf in TIMEFRAMES}
    if len(set(days.values())) != 1:
        raise ValueError(f"两周期行情日不同步：{days}；补齐数据后再生成日报")
    for tf in TIMEFRAMES:
        bykey = {r['key']: r for r in sym[tf]}
        valid = []
        for key, contract in contracts.items():
            row = dict(bykey.get(key) or {}, key=key, name=contract.get('name', key))
            if iso_day(row.get('last_date')) != days[tf]:
                notes.append(f"{tf} {key}: 当日行情缺失，状态与价位按未知处理")
                row = dict(key=key, name=row['name'], pos=None, last_signal=None)
            valid.append(row)
        sym[tf] = valid
        fresh = {r['key'] for r in valid if iso_day(r.get('last_date')) == days[tf]}
        for bucket in BUCKETS:
            if bucket not in scr[tf].get('buckets', {}):
                raise ValueError(f'{tf} 缺少筛选桶 {bucket}')
            scr[tf]['buckets'][bucket] = [r for r in scr[tf]['buckets'][bucket]
                if r['key'] in fresh and iso_day(r.get('date')) == days[tf]]
    return days['1d'], notes


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
        r['recent_signals'] = x.get('recent_signals') or []
        r['last_date'] = x.get('last_date')
        if x.get('name'):
            r['name'] = x['name']
        for field in ('close', 'score', 'DD', 'EE', 'KK', 'PP'):
            if x.get(field) is not None and (field != 'score' or r.get('score') is None):
                r[field] = x[field]
    return rows


def prod_view(rows):
    """品种级视图：保留桶外及未知状态；观察池已在输入层过滤。"""
    out = {}
    for k, r in rows.items():
        b = base_of(k)
        if b not in out or abs(_sc(r)) > abs(_sc(out[b])):
            out[b] = r
    return out


def repr_of(rows, key):
    """按品种取某口径的代表合约（|score| 最大者）"""
    if key in rows:
        return rows[key]
    best = None
    for r in rows.values():
        if base_of(r['key']) != base_of(key):
            continue
        if best is None or abs(_sc(r)) > abs(_sc(best)):
            best = r
    return best or {}


# ---------------------------------------------------------------- 事实提取
def _row(r):
    """结构化一行（供 JSON）"""
    return {
        'key': r.get('key'), 'code': base_of(r.get('key', '')), 'name': r.get('name', ''),
        'sector': r.get('sector'),
        'score': r.get('score'), 'close': r.get('close'),
        'DD': r.get('DD'), 'EE': r.get('EE'), 'KK': r.get('KK'), 'PP': r.get('PP'),
        'rank': r.get('rank'), 'rank_change': r.get('rank_change'),
        'rank_status': r.get('rank_status'), 'rank_history': r.get('rank_history'),
        'pos': r.get('pos'), 'last_signal': r.get('last') or None,
    }


def state4(r):
    """持仓、价位、评分独立核验；事件仅由最新交易信号解释。"""
    pos, score, close, ee = (r.get(k) for k in ('pos', 'score', 'close', 'EE'))
    last = r.get('last') or {}
    below = close < ee if close is not None and ee is not None and ee > 0 else None
    position = {1: '持多', -1: '持空', 0: '空仓'}.get(pos, '持仓未知')
    if pos == 0:
        position = {'SP': '平多后空仓', 'BP': '平空后空仓'}.get(last.get('type'), '空仓（来源未核验）')
    if pos not in (-1, 0, 1):
        tier = '4h未知'
    elif pos == -1:
        tier = '4h持空'
    elif pos == 0:
        tier = '4h空仓'
    elif below is True:
        tier = '4h破位'
    elif below is None or score is None:
        tier = '4h未知'
    elif score < 0:
        tier = '4h动能偏负'
    elif score >= LEAD_4H:
        tier = '4h强势'
    else:
        tier = '4h弱正'
    price_state = '收破4h EE' if below is True else ('未破4h EE' if below is False else '4h价位未知')
    return dict(tier=tier, position_4h=position, pos_4h=pos, score_4h=score,
                close_4h=close, EE_4h=ee, below_EE_4h=below,
                gap_to_EE_4h=_gap_pct(close, ee), last_signal_4h=last,
                state_4h=position + ' · ' + price_state,
                in_long_trend_4h=pos == 1)


def scan(data_date=None, prev_date=None):
    scr = {tf: load_screening(tf) for tf in TIMEFRAMES}
    sym = {tf: load_symbols(tf) for tf in TIMEFRAMES}

    # 读取一次并携带该批输入直到归档，防止计算和保存时混入另一次更新。
    raw_scr = json.loads(json.dumps(scr))
    actual_day, quality_notes = prepare_inputs(scr, sym)
    if data_date and data_date != actual_day:
        raise ValueError(f'指定数据日 {data_date} 与实际行情日 {actual_day} 不符')
    data_date = actual_day
    gen = {tf: scr[tf].get('generated_at', '') for tf in TIMEFRAMES}
    prev_date = prev_date or infer_prev_date(data_date)
    if not iso_day(prev_date) or prev_date >= data_date:
        raise ValueError('历史基线日期必须严格早于当前行情日')

    R = {tf: build_rows(scr[tf], sym[tf]) for tf in TIMEFRAMES}
    P = {tf: prod_view(R[tf]) for tf in TIMEFRAMES}
    prev = {tf: load_prev_screening(tf, prev_date) for tf in TIMEFRAMES}
    prev4 = prev_4h_scores(prev_date)
    if not prev4:
        quality_notes.append(f"缺少 {prev_date} 的 4h 历史数据，Δ4h 环比全部记为未知，分档暂定")

    facts = {
        'scan_version': 3, 'rules_version': RULES_VERSION,
        'quality_notes': quality_notes,
        'created_at': datetime.now().isoformat(timespec='seconds'),
        'data_date': data_date,
        'prev_date': prev_date,
        'generated_at': gen,
        'criteria': {'LEAD_1D': LEAD_1D, 'LEAD_4H': LEAD_4H,
                     'D4H_SIG': D4H_SIG, 'NEW_STRONG_4H': NEW_STRONG_4H},
    }

    # 头部总览（多空计数与前日基线）
    facts['overview'] = {
        '1d': current_counts(scr['1d'], P['1d']),
        '4h': current_counts(scr['4h'], P['4h']),
        'prev_1d': (prev['1d'] or {}).get('summary') if prev['1d'] else None,
        'prev_4h': (prev['4h'] or {}).get('summary') if prev['4h'] else None,
    }

    # 0️⃣ 当日信号动作（1d 与 4h 的 BK/SP/SK/BP 全列不省略）
    facts['signal_actions'] = [
        {'tf': tf, 'key': x['key'], 'code': base_of(x['key']), 'name': x.get('name', ''),
         'signal': (x.get('last_signal') or {}).get('type'), 'pos': x.get('pos')}
        for tf in TIMEFRAMES for x in sym[tf]
        if (x.get('last_signal') or {}).get('date')
        and iso_day((x.get('last_signal') or {}).get('date')) == data_date
        and x.get('pos') is not None]

    # 二 / 三：多空四档（互斥，按优先级取档）；⚪ 中 4h 强者分流至蓄势池
    facts['tiers_long'] = _four_tiers(P['1d'], R['4h'], prev4, side=1)
    facts['tiers_short'] = _four_tiers(P['1d'], R['4h'], prev4, side=-1)
    facts['pool'] = {
        'long': _pool_rows(facts['tiers_long']['flat'], side=1),
        'short': _pool_rows(facts['tiers_short']['flat'], side=-1),
    }

    # 四：动量异动榜（Δ4h 环比 + 排名异动 |Δrank|≥3）
    facts['momentum'] = _momentum(P['4h'], P['1d'], R['4h'], prev4, scr['1d'],
                                  facts['tiers_long'], facts['tiers_short'])

    # 操作提示素材：关键位紧贴度
    facts['key_levels'] = _key_levels(P['1d'])

    facts['coverage'] = {tf: {'known': sum(r.get('pos') in (-1, 0, 1) for r in P[tf].values()),
                              'unknown': sum(r.get('pos') not in (-1, 0, 1) for r in P[tf].values())} for tf in TIMEFRAMES}
    for tf in TIMEFRAMES:
        missing = [r['key'] for r in P[tf].values() if r.get('pos') is not None and r.get('score') is None]
        facts['coverage'][tf]['missing_score'] = len(missing)
        if missing:
            quality_notes.append(f"{tf}: {len(missing)} 个合约缺少可用动量评分，评分条件记为未知，持仓与价位独立核验：" + '、'.join(missing))
    no_d4 = [r['code'] for t in (facts['tiers_long'], facts['tiers_short'])
             for tier in TIER_ORDER for r in t[tier] if r.get('d4h') is None]
    if no_d4:
        quality_notes.append(f"Δ4h 未知（{prev_date} 4h 评分缺失），相关分档暂定：" + '、'.join(sorted(set(no_d4))))
    facts['input_hash'] = digest({'rules': RULES_VERSION, 'screen': raw_scr, 'symbols': sym,
                                  'previous': prev, 'prev_4h_scores': prev4,
                                  'data_date': data_date, 'prev_date': prev_date})
    facts['input_snapshot'] = {'screen': scr, 'symbols': sym}
    return facts


def current_counts(screen, products):
    counts = _summarize(screen)
    counts['long_trend'] = sum(r.get('pos') == 1 for r in products.values())
    counts['short_trend'] = sum(r.get('pos') == -1 for r in products.values())
    return counts


def archive_scan(facts):
    day = facts['data_date'].replace('-', '')
    text = render_text(facts)  # 所有渲染校验先完成，再发布快照与扫描产物。
    for tf in TIMEFRAMES:
        screen = dict(facts['input_snapshot']['screen'][tf], summary=facts['overview'][tf])
        _dump(SNAPSHOT_DIR / f'screen_{tf}_{day}.json', screen)
        _dump(SNAPSHOT_DIR / f'symbols_{tf}_{day}.json', facts['input_snapshot']['symbols'][tf])
    _dump(SCAN_DIR / f"scan_{facts['data_date']}.json", facts)
    atomic_text(SCAN_DIR / f"scan_{facts['data_date']}.txt", text)


def _summarize(scr):
    return {b: len(scr['buckets'].get(b, [])) for b in BUCKETS}


# ---------------------------------------------------------------- v6 四档分类
def _classify(s1, s4, in_bucket, d4, side):
    """四档互斥判定（v6 §2，按优先级取档）。side=1 多头侧 / -1 空头侧（镜像取反）。

    返回档位代码：lead / danger / fresh / pull / flat。
    Δ4h 缺失（None）时 danger/fresh 无法确认，按当日状态落入 pull/flat，
    由调用方记 provisional（报告加 ※）。未知 score 不补零，门槛条件直接不成立。
    """
    if side == -1:
        s1 = -s1 if s1 is not None else None
        s4 = -s4 if s4 is not None else None
        d4 = -d4 if d4 is not None else None
    if s1 is not None and s1 >= LEAD_1D and in_bucket and s4 is not None and s4 >= LEAD_4H:
        return 'lead'
    if d4 is not None and d4 <= -D4H_SIG and (not in_bucket or (s4 is not None and s4 < 0)):
        return 'danger'
    if d4 is not None and d4 >= D4H_SIG and s4 is not None and s4 > NEW_STRONG_4H:
        return 'fresh'
    if s1 is not None and s1 >= LEAD_1D and (d4 is None or d4 > -D4H_SIG):
        return 'pull'
    return 'flat'


def _tier_reason(tier, side, s1, s4, d4, in_bucket, breach, provisional):
    """一行一句话理由（规则版；LLM 叙事可另写点评，事实以此为准）。"""
    mark = '※' if provisional else ''
    weak = ('掉出多头桶' if side == 1 else '掉出空头桶') if not in_bucket else \
        ('4h 评分转负' if side == 1 else '4h 评分转正')
    if tier == 'lead':
        txt = (f"日线与 4h 双强（1d {s1:.2f} ≥ {LEAD_1D}，4h {s4:.2f} ≥ {LEAD_4H}），核心仓拿住"
               if side == 1 else
               f"日线与 4h 双空（1d {s1:.2f} ≤ −{LEAD_1D}，4h {s4:.2f} ≤ −{LEAD_4H}），空单拿住")
    elif tier == 'danger':
        txt = (f"4h 转弱（{weak}）且 Δ4h {d4:+.2f} ≤ −{D4H_SIG}，减仓/撤"
               if side == 1 else
               f"4h 转强（{weak}）且 Δ4h {d4:+.2f} ≥ +{D4H_SIG}，空单减/撤防反转")
    elif tier == 'fresh':
        txt = (f"4h 环比 {d4:+.2f} 且 4h {s4:.2f} > {NEW_STRONG_4H}，新主线候选，关注/试多"
               if side == 1 else
               f"4h 环比 {d4:+.2f} 且 4h {s4:.2f} < −{NEW_STRONG_4H}，新空主线候选，可跟空")
    elif tier == 'pull':
        txt = (f"日线 {s1:.2f} ≥ {LEAD_1D}，4h 环比未显著恶化，持有不砍"
               if side == 1 else
               f"日线 {s1:.2f} ≤ −{LEAD_1D}，4h 环比未显著回升，空单持有别抄底")
    else:
        txt = "日线持{}但不满足四档任一条".format('多' if side == 1 else '空')
    if breach is True:
        txt += '；⚠️4h 收破 EE（破位）' if side == 1 else '；⚠️4h 上破 PP（破位）'
    return mark + txt


def _four_tiers(P1, R4, prev4, side):
    """某一侧的四档分档结果：{tier: [行]}，行内含 Δ4h、排名轨迹与一句话理由。"""
    bucket = 'long_trend' if side == 1 else 'short_trend'
    tiers = {t: [] for t in TIER_ORDER}
    for b, r in P1.items():
        if r.get('pos') != side:
            continue
        r4 = repr_of(R4, r['key'])
        s1, s4 = r.get('score'), r4.get('score')
        in_bucket = bucket in (r4.get('buckets') or [])
        prev = prev4.get(r4.get('key'))
        d4 = None if (s4 is None or prev is None) else s4 - prev
        st = state4(r4)
        # 破位备注：多头侧 = 4h 收破 EE；空头侧镜像 = 4h 上破 PP（v6 §2 状态备注）。
        c4, pp4 = st['close_4h'], r4.get('PP')
        breach = st['below_EE_4h'] if side == 1 else (
            c4 > pp4 if c4 is not None and pp4 is not None and pp4 > 0 else None)
        tier = _classify(s1, s4, in_bucket, d4, side)
        d = _row(r)
        d.update({
            'tier': tier, 'tier_name': TIER_NAMES[side][tier], 'side': side,
            'score_1d': s1, 'score_4h': s4, 'd4h': d4, 'prev_score_4h': prev,
            'provisional': d4 is None,
            'key_4h': r4.get('key'), 'pos_4h': st['pos_4h'],
            'in_bucket_4h': in_bucket, 'breach_4h': breach,
            'close_4h': st['close_4h'], 'EE_4h': st['EE_4h'], 'PP_4h': pp4,
            'reason': _tier_reason(tier, side, s1, s4, d4, in_bucket,
                                   breach, d4 is None),
        })
        tiers[tier].append(d)
    for t in tiers:
        tiers[t].sort(key=lambda x: (_sc({'score': x['score_1d']}) * -side))
    return tiers


def _pool_rows(flat_rows, side):
    """🟢 4h 蓄势池：⚪ 未入档中 4h 已在趋势桶且 4h score 越过强门槛者（v6 §2 补充）。

    蓄势而非启动：4h 水平够，日线趋势/评分这一侧还没确认。
    """
    pool = []
    for r in flat_rows:
        s4 = r.get('score_4h')
        if not r.get('in_bucket_4h') or s4 is None:
            continue
        if side == 1 and s4 <= NEW_STRONG_4H:
            continue
        if side == -1 and s4 >= -NEW_STRONG_4H:
            continue
        s1 = r.get('score_1d')
        reason = (f"4h 已在多头桶且 4h {s4:.2f} > {NEW_STRONG_4H}，日线 {s1 if s1 is None else f'{s1:.2f}'} 未达 {LEAD_1D}"
                  if side == 1 else
                  f"4h 已在空头桶且 4h {s4:.2f} < −{NEW_STRONG_4H}，日线 {s1 if s1 is None else f'{s1:.2f}'} 未达 −{LEAD_1D}")
        upgrade = (f"日线评分 ≥ {LEAD_1D} 或日线 BK 确认后提级进实档；4h 掉出多头桶即出池"
                   if side == 1 else
                   f"日线评分 ≤ −{LEAD_1D} 或日线 SK 确认后提级进实档；4h 掉出空头桶即出池")
        pool.append({'key': r['key'], 'code': r['code'], 'name': r['name'],
                     'sector': r['sector'], 'side': side,
                     'score_1d': s1, 'score_4h': s4, 'd4h': r.get('d4h'),
                     'provisional': r.get('provisional'),
                     'reason': reason, 'upgrade': upgrade})
    pool.sort(key=lambda x: -abs(x['score_4h']))
    return pool


def _momentum(P4, P1, R4, prev4, scr1, tiers_long, tiers_short):
    """动量异动榜：Δ4h 环比变化最大者，多向加速 / 空向失速各取前 8，附档位交叉印证。"""
    tier_map = {}
    for t in TIER_ORDER:
        for r in tiers_long[t]:
            tier_map[r['code']] = TIER_NAMES[1][t]
        for r in tiers_short[t]:
            tier_map[r['code']] = TIER_NAMES[-1][t]
    items = []
    for b, r in P4.items():
        s4 = r.get('score')
        prev = prev4.get(r['key'])
        if s4 is None or prev is None:
            continue
        p1 = P1.get(b) or {}
        items.append({'key': r['key'], 'code': b, 'name': r.get('name', ''),
                      'sector': r.get('sector'), 'score_4h': s4, 'd4h': s4 - prev,
                      'pos_1d': p1.get('pos'), 'score_1d': p1.get('score'),
                      'tier': tier_map.get(b)})
    accel = sorted((i for i in items if i['d4h'] >= D4H_SIG), key=lambda x: -x['d4h'])[:8]
    decel = sorted((i for i in items if i['d4h'] <= -D4H_SIG), key=lambda x: x['d4h'])[:8]

    rank_moves = []
    for side_bucket, side_name in (('long_trend', '多头榜'), ('short_trend', '空头榜')):
        for it in scr1['buckets'].get(side_bucket, []):
            if it.get('close') is None:
                continue
            chg = it.get('rank_change')
            if isinstance(chg, int) and not isinstance(chg, bool) and abs(chg) >= 3:
                rank_moves.append({'key': it['key'], 'code': base_of(it['key']),
                                   'name': it.get('name', ''), 'side': side_name,
                                   'rank': it.get('rank'), 'rank_change': chg,
                                   'score': it.get('score')})
    rank_moves.sort(key=lambda x: -abs(x['rank_change']))
    return {'accel': accel, 'decel': decel, 'rank_moves': rank_moves,
            'basis': 'Δ4h = 今日 4h 评分 − 前一交易日 4h 评分（日盘收盘口径）'}


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


# ---------------------------------------------------------------- 文本渲染（v6 五节调试文本）
def render_text(f: dict) -> str:
    L = []
    W = "="
    data_date, prev_date = f['data_date'], f['prev_date']

    L.append(W * 112)
    L.append(f"【头部】数据基准 {data_date} ｜ 对比基准 {prev_date}")
    L.append(f"  1d generated_at: {f['generated_at']['1d']}")
    L.append(f"  1d: " + json.dumps(f['overview']['1d'], ensure_ascii=False))
    L.append(f"  4h generated_at: {f['generated_at']['4h']}")
    L.append(f"  4h: " + json.dumps(f['overview']['4h'], ensure_ascii=False))
    for tf in TIMEFRAMES:
        p = f['overview'].get(f'prev_{tf}')
        L.append(f"  {tf} {prev_date}: " + (json.dumps(p, ensure_ascii=False) if p else "无归档（首日运行正常）"))

    L.append("\n" + W * 112)
    L.append(f"【一·0️⃣】当日信号动作（{data_date}，1d 与 4h 全列）")
    if f['signal_actions']:
        for x in f['signal_actions']:
            L.append(f"  {x['tf']}  {x['code']:7s}{x['name']:12s}{x['signal']}  pos={x['pos']}")
    else:
        L.append("  无")

    for side, label, tiers in ((1, '二·多头阵营', f['tiers_long']), (-1, '三·空头阵营', f['tiers_short'])):
        L.append("\n" + W * 112)
        L.append(f"【{label} · 四档表】阈值：1d≥{LEAD_1D} ｜ 4h≥{LEAD_4H} ｜ |Δ4h|≥{D4H_SIG}")
        for t in TIER_ORDER:
            rows = tiers[t]
            L.append(f"\n  ── {TIER_NAMES[side][t]}（{len(rows)} 只）")
            for r in rows:
                hs = " ".join(str(x.get('rank')) if x.get('rank') is not None else '-'
                              for x in (r.get('rank_history') or []))
                L.append(f"  {r['code']:7s}{r['name']:12s}1d={_f2(r['score_1d']):>7} 4h={_f2(r['score_4h']):>7} "
                         f"Δ4h={_f2(r['d4h']):>7} rank轨迹[{hs}]")
                L.append(f"      → {r['reason']}")
        pool = f['pool']['long' if side == 1 else 'short']
        L.append(f"\n  ── 🟢 4h 蓄势池（{len(pool)} 只）")
        for r in pool:
            L.append(f"  {r['code']:7s}{r['name']:12s}4h={_f2(r['score_4h']):>7} Δ4h={_f2(r['d4h']):>7} "
                     f"1d={_f2(r['score_1d']):>7}")
            L.append(f"      入池：{r['reason']} ｜ 提级：{r['upgrade']}")

    L.append("\n" + W * 112)
    L.append(f"【四】动量异动榜（Δ4h 环比 ≥ ±{D4H_SIG}，各取前 8）")
    L.append("  ── 多向加速")
    for i in f['momentum']['accel']:
        L.append(f"  {i['code']:7s}{i['name']:12s}Δ4h={i['d4h']:+.2f} 4h={_f2(i['score_4h'])} 档={i.get('tier') or '—'}")
    L.append("  ── 空向失速")
    for i in f['momentum']['decel']:
        L.append(f"  {i['code']:7s}{i['name']:12s}Δ4h={i['d4h']:+.2f} 4h={_f2(i['score_4h'])} 档={i.get('tier') or '—'}")
    L.append("  ── 排名异动（|Δrank|≥3）")
    for i in f['momentum']['rank_moves']:
        L.append(f"  {i['side']} {i['code']:7s}{i['name']:12s}rank={i['rank']} ({i['rank_change']:+d})")

    L.append("\n" + W * 112)
    L.append("【五】操作提示素材：关键位紧贴度（生死线排序）")
    for x in f['key_levels']:
        L.append(f"  {x['label']}  {x['code']:6s}{x['name']:12s} {x['gap_pct']:+.2f}%")

    if f['quality_notes']:
        L.append("\n" + W * 112)
        L.append("【数据质量备注】")
        for n in f['quality_notes']:
            L.append(f"  - {n}")
    L.append("\n" + W * 112)
    L.append("扫描完成。下一步：summary_narrator 叙事 / summary_render 渲染（v6 五节结构）。")
    return "\n".join(L)


# ---------------------------------------------------------------- CLI
def main():
    ap = argparse.ArgumentParser(description="期货看板每日总结 · 事实扫描器（v6 口径，本地数据版）")
    ap.add_argument("--data-date", help="数据日期 YYYY-MM-DD（必须与实际筛选行情日期一致）")
    ap.add_argument("--prev-date", help="上一交易日 YYYY-MM-DD（默认取快照中最近一份）")
    ap.add_argument("--text-only", action="store_true", help="只打印文本，不落盘")
    args = ap.parse_args()

    f = scan(data_date=args.data_date, prev_date=args.prev_date)

    if args.text_only:
        print(render_text(f))
        return

    SCAN_DIR.mkdir(parents=True, exist_ok=True)
    tag = f['data_date']
    archive_scan(f)
    txt = render_text(f)
    print(txt)
    print(f"\n[产物] {SCAN_DIR / f'scan_{tag}.json'}")
    print(f"[产物] {SCAN_DIR / f'scan_{tag}.txt'}")


if __name__ == "__main__":
    main()
