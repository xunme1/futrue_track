# -*- coding: utf-8 -*-
"""期货看板每日总结 · HTML 渲染器（v6 五节结构，报告流水线第 2 步）。

读取 scan_report 产出的扫描 JSON（data/reports/scan/scan_YYYY-MM-DD.json），
叠加可选叙事 JSON（data/reports/narrative/narrative_YYYY-MM-DD.json，由 LLM 按
docs/summary_contract.md 撰写；缺字段时以规则化模板兜底），渲染单文件 HTML：

    data/reports/daily_summary_YYYY-MM-DD.html

结构对齐《期货看板日报 · 方法论与复制指南 v6》§3 五节固定模板：
头部 → 一·核心判断（当日信号动作 + judge-card）→ 二·多头四档表 →
三·空头镜像四档表 → 三补·🟢4h 蓄势池 → 四·动量异动榜 → 五·操作提示 → 尾·口径说明。
视觉类名按 v6 §3.1 固定：.header/.oneline/.card/.judge-grid/.judge-card/.note/
表格/.lead .pull .danger .bear .fresh .flat/.foot。

用法：
    python -m backend.pipeline.summary_render
    python -m backend.pipeline.summary_render --date 2026-09-15
"""
import argparse
import base64
import hashlib
import html
import json
from datetime import datetime, timedelta
from pathlib import Path

from backend.core.config import DATA_DIR
from backend.pipeline.report_store import RULES_VERSION, atomic_json, atomic_text, iso_day, digest
from backend.pipeline.scan_report import TIER_NAMES, TIER_ORDER

REPORTS_DIR = DATA_DIR / "reports"
SCAN_DIR = REPORTS_DIR / "scan"
NARRATIVE_DIR = REPORTS_DIR / "narrative"
SEAT_DIR = DATA_DIR / "seat"

DASHBOARD_URL = "http://110.42.220.207:8000/"

WEEKDAY_CN = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")

# 版本行（v6 §3.1 头部必含）：写清当期版本号与本期生效的规则变更。
VERSION_NOTE = ("方法论 v6.2（四档互斥：龙头 → 危险分歧 → 新贵 → 回调，空头镜像）｜"
                "本期生效：🚀新贵 4h 强势前置（4h 评分须 > 1.0，负区回抽不算新贵）＋ 🟢 4h 蓄势池"
                f"（实现规则版本 {RULES_VERSION}）")


def _esc(s) -> str:
    return html.escape("" if s is None else str(s))


def _n(v, nd=2):
    if v is None:
        return "—"
    return f"{v:.{nd}f}"


def _md(date_str) -> str:
    s = str(date_str or "")
    return s[5:10] if len(s) >= 10 else s


def _wd(date_str) -> str:
    try:
        return WEEKDAY_CN[datetime.strptime(str(date_str)[:10], "%Y-%m-%d").weekday()]
    except Exception:
        return ""


def _next_weekday(d):
    nxt = d + timedelta(days=1)
    while nxt.weekday() >= 5:
        nxt += timedelta(days=1)
    return nxt


def _sign(chg):
    return f"{chg:+d}" if isinstance(chg, int) and not isinstance(chg, bool) else "NEW"


def _name(r, maxn=0):
    """渲染一个品种：名称 + 代码"""
    items = [f"{r.get('name') or ''} {r.get('code') or r.get('key') or ''}".strip()]
    return items[0]


def _names(rows, maxn=0) -> str:
    items = [_name(r) for r in rows]
    if maxn and len(items) > maxn:
        return "、".join(items[:maxn]) + f" 等 {len(items)} 只"
    return "、".join(items)


def _sc(r):
    return (r or {}).get('score') or 0


def _gap(close, ref):
    return (close - ref) / ref * 100 if (close and ref) else None


def _acts_1d(f):
    return [x for x in f.get('signal_actions', []) if x.get('tf') == '1d']


# ---------------------------------------------------------------- 兜底叙事
def fallback_tone(f):
    """从可核验的两口径计数变化给当日定性。"""
    if any(v.get('unknown', 0) for v in f.get('coverage', {}).values()):
        return '数据不完整 · 暂停方向定性'
    o, p = f['overview']['1d'], f['overview'].get('prev_1d')
    d_long = o['long_trend'] - (p or {}).get('long_trend', o['long_trend'])
    d_short = o['short_trend'] - (p or {}).get('short_trend', o['short_trend'])
    acts = _acts_1d(f)
    n_bk = sum(1 for x in acts if x.get('signal') == 'BK')
    n_sk = sum(1 for x in acts if x.get('signal') == 'SK')
    n_sp = sum(1 for x in acts if x.get('signal') == 'SP')
    if p is None:
        return '基线不足 · 观察当日状态'
    if d_long >= 2 and n_bk:
        return "多头反攻日"
    if n_sp >= 3 and not n_sk:
        return "多头撤退日"
    if d_short >= 3 and n_sk:
        return "空头扩散日"
    if d_short >= 2:
        return "空头增强日"
    if d_long >= 2:
        return "多头增强日"
    return "多空均衡日"


def fallback_one_liner(f, tone):
    dd = _md(f['data_date'])
    o, p = f['overview']['1d'], f['overview'].get('prev_1d') or {}
    o4 = f['overview']['4h']
    acts = _acts_1d(f)
    seg = []
    for sig, verb in (('BK', '新开多'), ('SK', '新开空'), ('SP', '平多离场'), ('BP', '平空离场')):
        rows = [x for x in acts if x.get('signal') == sig]
        if rows:
            seg.append(f"{len(rows)} 只{verb}（{_names(rows, 6)}）")
    lead = len(f.get('tiers_long', {}).get('lead', []))
    bear = len(f.get('tiers_short', {}).get('lead', []))
    head = f"{dd} 是{tone}"
    if seg:
        head += " —— 日线 " + "、".join(seg)
    return (head + f"。绝对龙头 {lead} 只 / 绝对熊头 {bear} 只；"
            f"日线多空 {o['long_trend']} : {o['short_trend']}"
            f"（前 {(p or {}).get('long_trend', '—')} : {(p or {}).get('short_trend', '—')}），"
            f"4h 多空 {o4['long_trend']} : {o4['short_trend']}。")


def fallback_cautions(f):
    """规则生成的「别误读」提示：只在数据支持时输出（纯文本，渲染层负责转义）。"""
    out = []
    prov = [r['code'] for side in ('tiers_long', 'tiers_short')
            for t in TIER_ORDER for r in f.get(side, {}).get(t, []) if r.get('provisional')]
    if prov:
        out.append({'title': 'Δ4h 缺失的分档是暂定',
                    'body': f"{'、'.join(sorted(set(prov)))} 缺少前一交易日 4h 评分，"
                            f"依赖环比的档位（危险分歧/新贵）无法确认，当前档位加 ※ 暂定。"})
    pool = f.get('pool', {})
    n_pool = len(pool.get('long', [])) + len(pool.get('short', []))
    if n_pool:
        out.append({'title': '蓄势池不是已启动信号',
                    'body': f"🟢 蓄势池 {n_pool} 只是「4h 已强、日线未确认」的观察名单，"
                            f"日线评分越槛或日线开多/开空确认后才提级进实档。"})
    accel_neg = [i for i in f.get('momentum', {}).get('accel', [])
                 if i.get('score_4h') is not None and i['score_4h'] <= 1.0]
    if accel_neg:
        out.append({'title': '负值区环比改善 ≠ 新贵',
                    'body': f"{_names(accel_neg, 6)} 4h 环比改善但 4h 评分未越过 +1.0，"
                            f"属空头力竭回抽，不是多头进攻，不入新贵档。"})
    return out


def fallback_judge_cards(f):
    """规则版核心判断卡：每张 = title / fact / action。"""
    cards = []
    tl, ts = f.get('tiers_long', {}), f.get('tiers_short', {})
    lead, bear = tl.get('lead', []), ts.get('lead', [])
    if lead or bear:
        cards.append({
            'title': f"1️⃣🥇 双强阵营：龙头 {len(lead)} 只 / 熊头 {len(bear)} 只",
            'fact': f"绝对龙头：{_names(lead, 8) or '无'}；绝对熊头：{_names(bear, 8) or '无'}。",
            'action': "龙头拿住核心仓、熊头空单拿住；双周期双强是最高权重信号。",
        })
    danger = tl.get('danger', []) + ts.get('danger', [])
    if danger:
        cards.append({
            'title': f"2️⃣🔴 危险分歧 {len(danger)} 只",
            'fact': "；".join(f"{_name(x)}（{_esc_free(x['reason'])}）" for x in danger[:6]),
            'action': "危险分歧 = 4h 转弱且环比显著恶化：多单减仓/撤，空单侧镜像防反转。",
        })
    fresh = tl.get('fresh', []) + ts.get('fresh', [])
    if fresh:
        cards.append({
            'title': f"3️⃣🚀 新贵 {len(fresh)} 只",
            'fact': "、".join(_name(x) for x in fresh[:8]),
            'action': "4h 环比与绝对水平双确认的新主线候选，可关注/试仓。",
        })
    pool = f.get('pool', {})
    pool_all = pool.get('long', []) + pool.get('short', [])
    if pool_all:
        cards.append({
            'title': f"4️⃣🟢 蓄势池 {len(pool_all)} 只",
            'fact': "、".join(_name(x) for x in pool_all[:8]),
            'action': "4h 已强、日线未确认：小仓试多/观察，日线越槛提级，4h 掉桶出池。",
        })
    acts = f.get('signal_actions', [])
    if acts:
        by_sig = {}
        for x in acts:
            by_sig.setdefault(x['signal'], []).append(x)
        fact = "；".join(f"{sig} {len(rows)} 只（{_names(rows, 6)}）" for sig, rows in by_sig.items())
        cards.append({'title': f"5️⃣📋 当日信号动作 {len(acts)} 条", 'fact': fact,
                      'action': "新开仓信号核对关键位后执行；平仓信号优先兑现。"})
    return cards[:5]


def _esc_free(s):
    return str(s).lstrip('※')


def fallback_tips(f, report_date=None):
    tips = []
    tl, ts = f.get('tiers_long', {}), f.get('tiers_short', {})
    for x in tl.get('danger', []):
        tips.append(f"⚠️ {_name(x)}：{_esc_free(x['reason'])}。")
    for x in ts.get('danger', []):
        tips.append(f"⚠️ {_name(x)}：{_esc_free(x['reason'])}。")
    lead, bear = tl.get('lead', []), ts.get('lead', [])
    if lead:
        tips.append(f"绝对龙头 {_names(lead, 8)}：双周期双强，核心仓拿住，回调不破 4h 趋势不减。")
    if bear:
        tips.append(f"绝对熊头 {_names(bear, 8)}：双周期双空，空单拿住，反弹不加多。")
    fresh = tl.get('fresh', []) + ts.get('fresh', [])
    if fresh:
        tips.append(f"新贵 {_names(fresh, 8)}：新主线候选，小仓试，4h 评分跌回 ±1.0 内即撤。")
    pool_all = f.get('pool', {}).get('long', []) + f.get('pool', {}).get('short', [])
    if pool_all:
        tips.append(f"蓄势池 {_names(pool_all, 8)}：观察为主，日线确认后再提级加仓。")
    for x in f.get('key_levels', [])[:2]:
        if x['label'] == '多头距EE':
            tips.append(f"{x['name']} {x['code']}：距日线 EE 仅 {x['gap_pct']:+.2f}%，多单警戒线。")
        else:
            tips.append(f"{x['name']} {x['code']}：距日线 KK 仅 {x['gap_pct']:+.2f}%，空单警戒线。")
    review = lead[:1] + tl.get('danger', [])[:1] + tl.get('fresh', [])[:1] + pool_all[:1]
    if review:
        tips.append("明日复核重点：" + "、".join(_name(x) for x in review)
                    + " —— 复核 4h 桶归属与 Δ4h 环比是否延续。")
    return tips[:8]


# ---------------------------------------------------------------- 组件
def _chg_chip(label, cur, prev):
    if prev is None:
        return f'<span class="chip">{label} <b class="cnum">{cur}</b></span>'
    cls = "green" if cur > prev else ("red" if cur < prev else "")
    return (f'<span class="chip {cls}">{label} <b class="cnum">{prev}</b> '
            f'<span class="arrow">→</span> <b class="cnum">{cur}</b></span>')


def _table(headers, rows):
    head = "".join(f"<th>{_esc(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f'<div class="tbwrap"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def _section(n, title, body, note=None):
    note_html = f'<div class="note">{_esc(note)}</div>' if note else ""
    return f'<div class="card"><h2><span class="n">{_esc(str(n))}</span>{_esc(title)}</h2>{note_html}{body}</div>'


def _badge(tier, side):
    """档位徽章：六色语义类 .lead .pull .danger .bear .fresh .flat。"""
    cls = {'lead': 'lead' if side == 1 else 'bear'}.get(tier, tier)
    return f'<span class="bdg2 {cls}">{_esc(TIER_NAMES[side][tier])}</span>'


def _rank_track(r):
    hist = r.get('rank_history') or []
    if not hist:
        return '<span class="mut">—</span>'
    return '<span class="mut">' + _esc(" ".join(
        str(x.get('rank')) if x.get('rank') is not None else '-' for x in hist[-7:])) + '</span>'


def _d4h_cell(r):
    if r.get('d4h') is None:
        return '<span class="mut">—※</span>'
    v = r['d4h']
    cls = 'up' if v > 0 else ('dn' if v < 0 else 'mut')
    return f'<span class="{cls}">{v:+.2f}</span>'


def _tier_rows(tiers, side):
    """四档表行：品种 ｜ 日线 score ｜ 4h score ｜ Δ4h ｜ 排名轨迹(7日) ｜ 档位 ｜ 一句话。"""
    rows = []
    for t in TIER_ORDER:
        for r in tiers.get(t, []):
            s4 = r.get('score_4h')
            s4_cls = 'up' if (s4 or 0) > 0 else ('dn' if (s4 or 0) < 0 else 'mut')
            rows.append([
                f"<b>{_esc(r['name'])}</b> <span class='mut'>{_esc(r['code'])}</span>",
                f'<span class="nowrap">{_n(r.get("score_1d"))}</span>',
                f'<span class="{s4_cls}">{_n(s4)}</span>',
                _d4h_cell(r),
                _rank_track(r),
                _badge(t, side),
                _esc(r.get('reason')),
            ])
    return rows


# ---------------------------------------------------------------- 各节
def render_header(f, narrative, report_date):
    dd, prev = f['data_date'], f['prev_date']
    o, p = f['overview']['1d'], f['overview'].get('prev_1d') or {}
    o4, p4 = f['overview']['4h'], f['overview'].get('prev_4h') or {}
    tone = (narrative or {}).get('tone') or fallback_tone(f)
    tone_cls = "green" if ('多' in tone and '空' not in tone.replace('多空', '')) else "amber"

    chips = "".join([
        _chg_chip('1d 多头', o['long_trend'], p.get('long_trend')),
        _chg_chip('1d 空头', o['short_trend'], p.get('short_trend')),
        _chg_chip('4h 多头', o4['long_trend'], p4.get('long_trend')),
        _chg_chip('4h 空头', o4['short_trend'], p4.get('short_trend')),
        _chg_chip('4h 多转空', o4['long_to_short'], p4.get('long_to_short')),
    ])
    return f"""
<header>
  <div class="hd-top">
    <span class="tag">Futures Dashboard · Daily</span>
    <span class="chip {tone_cls}">{_esc(tone)}</span>
    <span class="chip">日线多空 <b>{o['long_trend']} : {o['short_trend']}</b></span>
    <span class="chip">4h 多空 <b>{o4['long_trend']} : {o4['short_trend']}</b></span>
  </div>
  <h1>期货看板每日总结 · <em>{_esc(_md(report_date))} 作战地图</em></h1>
  <div class="hd-sub">
    <span>数据基准：<b>{_esc(dd)}（{_esc(_wd(dd))}）收盘</b>（1d {_esc(f['generated_at']['1d'][11:16])} / 4h {_esc(f['generated_at']['4h'][11:16])} 生成）</span>
    <span>对比基准：<b>{_esc(_md(prev))}</b>（1d {p.get('long_trend', '—')} 多 / {p.get('short_trend', '—')} 空；4h {p4.get('long_trend', '—')} 多 / {p4.get('short_trend', '—')} 空）</span>
    <span>看板：<b>{_esc(DASHBOARD_URL.replace('http://', '').rstrip('/'))}</b></span>
    <span>本次运行：<b>{_esc(f.get('created_at', ''))}</b></span>
  </div>
  <div class="hd-sub" style="margin-top:8px"><span>版本：<b>{_esc(VERSION_NOTE)}</b></span></div>
  <div class="hd-sub" style="margin-top:10px">{chips}</div>
</header>"""


def render_oneline(f, narrative):
    tone = (narrative or {}).get('tone') or fallback_tone(f)
    one = (narrative or {}).get('one_liner') or fallback_one_liner(f, tone)
    return f'<div class="oneline"><b>一句话：{_esc(one)}</b></div>'


def render_cautions(f, narrative):
    items = (narrative or {}).get('cautions')
    if not items:
        items = fallback_cautions(f)
    wrap = ('<div class="oneline" style="border-left-color:var(--amber);'
            'background:linear-gradient(135deg,#fffaf0,#fff);border-color:#fce3b4">')
    out = []
    for t in items:
        if isinstance(t, dict):
            title, body = t.get('title', ''), t.get('body', '')
            inner = (f'⚠️ <b style="color:var(--amber)">{_esc(title)}</b>：{_esc(body)}'
                     if title else _esc(body))
        else:
            inner = _esc(t)
        out.append(wrap + inner + '</div>')
    return "".join(out)


def s1_core(f, narrative, notes):
    """一、核心判断：0️⃣ 当日信号动作（全列不省略）+ judge-card 栅格。"""
    acts = f.get('signal_actions', [])
    SIG_LABEL = {'BK': '开多', 'SK': '开空', 'SP': '平多', 'BP': '平空'}
    lines = []
    for tf, label in (('1d', '日线'), ('4h', '4小时')):
        rows = [x for x in acts if x.get('tf') == tf]
        txt = ("、".join(f"{_esc(x['name'])} {_esc(x['code'])}（{_esc(x['signal'])} "
                         f"{SIG_LABEL.get(x['signal'], x['signal'])}）" for x in rows) or "无")
        lines.append(f'<div class="ovrow"><span>{label}信号</span><span style="font-weight:400;text-align:right">{txt}</span></div>')
    sig_block = ('<div class="sigblock"><div class="ot">0️⃣ 当日信号动作（必读 · 全列不省略）</div>'
                 + "".join(lines) + '</div>')

    cards = (narrative or {}).get('judge_cards') or fallback_judge_cards(f)
    card_html = []
    for i, c in enumerate(cards):
        title = str(c.get('title', ''))
        cls = 'j-red' if any(k in title for k in ('🔴', '危险', '撤')) else \
              'j-green' if any(k in title for k in ('🟢', '蓄势', '龙头', '双强')) else 'j-gold'
        card_html.append(
            f'<div class="judge-card {cls}"><div class="jt">{_esc(title)}</div>'
            f'<div class="jf"><span class="jl">事实</span>{_esc(c.get("fact"))}</div>'
            f'<div class="ja"><span class="jl">动作</span><b>{_esc(c.get("action"))}</b></div></div>')
    body = sig_block + (f'<div class="judge-grid">{"".join(card_html)}</div>' if card_html else '')
    return _section('一', '核心判断', body, notes.get('1'))


def s2_long(f, notes):
    tiers = f.get('tiers_long', {})
    rows = _tier_rows(tiers, side=1)
    flat = tiers.get('flat', [])
    to_pool = {r['code'] for r in f.get('pool', {}).get('long', [])}
    summary = (f'<p>未入档 {len(flat)} 只'
               + (f'（其中 {("、".join(sorted(to_pool)))} 转入 🟢 蓄势池，见下节）' if to_pool else '')
               + '。</p>')
    broken = [r for t in TIER_ORDER for r in tiers.get(t, []) if r.get('breach_4h') is True]
    warn = (f'<div class="note">⚠️ 特别警示：{_esc(_names(broken, 10))} 4h 收破 EE（破位），'
            f'档位之外叠加价格风险，多单贴线品种优先复核。</div>') if broken else ''
    body = (_table(['品种', '日线 score', '4h score', 'Δ4h', '排名轨迹(7日)', '档位', '一句话'], rows)
            if rows else '<p>当日无日线持多品种。</p>')
    return _section('二', '多头阵营 · 四档表', body + summary + warn, notes.get('2'))


def s3_short(f, notes):
    tiers = f.get('tiers_short', {})
    rows = _tier_rows(tiers, side=-1)
    flat = tiers.get('flat', [])
    to_pool = {r['code'] for r in f.get('pool', {}).get('short', [])}
    summary = (f'<p>未入档 {len(flat)} 只'
               + (f'（其中 {("、".join(sorted(to_pool)))} 转入 🟢 蓄势池，见下节）' if to_pool else '')
               + '。</p>')
    warn = ('<div class="note">提示：空头榜排名上升 = 相对名次的塌陷假象，'
            '不代表资金流入或主动走强；出榜以持仓变化为准。</div>')
    broken = [r for t in TIER_ORDER for r in tiers.get(t, []) if r.get('breach_4h') is True]
    if broken:
        warn += (f'<div class="note">⚠️ 特别警示：{_esc(_names(broken, 10))} 4h 上破 PP（破位），'
                 f'空单侧价格风险优先复核。</div>')
    body = (_table(['品种', '日线 score', '4h score', 'Δ4h', '排名轨迹(7日)', '档位', '一句话'], rows)
            if rows else '<p>当日无日线持空品种。</p>')
    return _section('三', '空头阵营 · 镜像四档表', body + summary + warn, notes.get('3'))


def s3b_pool(f, notes):
    pool = f.get('pool', {})
    long_rows, short_rows = pool.get('long', []), pool.get('short', [])
    if not long_rows and not short_rows:
        return _section('三·补', '🟢 4h 蓄势池', '<p>本期无。</p>', notes.get('pool'))

    def rows_of(items, side):
        return [[f"<b>{_esc(r['name'])}</b> <span class='mut'>{_esc(r['code'])}</span>",
                 f'<span class="{"up" if side == 1 else "dn"}">{_n(r.get("score_4h"))}</span>',
                 _d4h_cell(r),
                 _n(r.get('score_1d')),
                 _esc(r.get('reason')),
                 _esc(r.get('upgrade'))] for r in items]

    parts = []
    if long_rows:
        parts.append(f'<h3>多头蓄势（{len(long_rows)} 只）</h3>'
                     + _table(['品种', '4h score', 'Δ4h', '日线 score', '入池理由', '提级条件'],
                              rows_of(long_rows, 1)))
    if short_rows:
        parts.append(f'<h3>空头蓄势（{len(short_rows)} 只）</h3>'
                     + _table(['品种', '4h score', 'Δ4h', '日线 score', '入池理由', '提级条件'],
                              rows_of(short_rows, -1)))
    parts.append('<div class="note">蓄势池 = 四档之外的补充标记：4h 已在趋势桶且评分越过 ±1.0，'
                 '但日线这一侧还没确认。小仓试/观察；4h 掉出趋势桶即出池。'
                 '未入池的 ⚪ 未入档品种属真·未入档，4h 水平不足。</div>')
    return _section('三·补', '🟢 4h 蓄势池', "".join(parts), notes.get('pool'))


def s4_momentum(f, notes):
    m = f.get('momentum', {})

    def rows_of(items, side_label):
        return [[f"<b>{_esc(i['name'])}</b> <span class='mut'>{_esc(i['code'])}</span>",
                 f'<span class="{"up" if i["d4h"] > 0 else "dn"}">{i["d4h"]:+.2f}</span>',
                 _n(i.get('score_4h')),
                 _esc(i.get('tier') or '（非持仓侧）'),
                 _esc({1: '日线持多', -1: '日线持空', 0: '日线空仓'}.get(i.get('pos_1d'), '日线未知'))]
                for i in items]

    parts = []
    accel, decel = m.get('accel', []), m.get('decel', [])
    if accel:
        parts.append(f'<h3>🚀 多向加速（Δ4h ≥ +1.0 · 前 {len(accel)}）</h3>'
                     + _table(['品种', 'Δ4h', '4h score', '当前档位（交叉印证）', '日线方向'],
                              rows_of(accel, '多')))
    if decel:
        parts.append(f'<h3>📉 空向失速（Δ4h ≤ −1.0 · 前 {len(decel)}）</h3>'
                     + _table(['品种', 'Δ4h', '4h score', '当前档位（交叉印证）', '日线方向'],
                              rows_of(decel, '空')))
    rm = m.get('rank_moves', [])
    if rm:
        parts.append('<h3>排名异动（|Δrank| ≥ 3）</h3>'
                     + _table(['榜单', '品种', '排名', '变化', '日线 score'],
                              [[_esc(x['side']),
                                f"<b>{_esc(x['name'])}</b> <span class='mut'>{_esc(x['code'])}</span>",
                                f"#{x.get('rank') or '—'}", _sign(x.get('rank_change')),
                                _n(x.get('score'))] for x in rm]))
    if not parts:
        parts.append('<p>当日无显著动量异动。</p>')
    return _section('四', '动量异动榜', "".join(parts), notes.get('4'))


def s5_tips(f, narrative, report_date=None):
    tips = (narrative or {}).get('action_tips') or fallback_tips(f, report_date)
    lis = "".join(f"<li>{_esc(t)}</li>" for t in tips)
    return _section('五', f"{_esc(_md(report_date or next_report_date(f)))} 操作提示",
                    f'<ol class="oplist">{lis}</ol>')


def methodology_note(f):
    """尾部口径说明：固定文案（v6 §3 末节）。"""
    c = f.get('criteria', {})
    lead_1d, lead_4h = c.get('LEAD_1D', 4.5), c.get('LEAD_4H', 1.0)
    d4h_sig, new_strong = c.get('D4H_SIG', 1.0), c.get('NEW_STRONG_4H', 1.0)
    return _section('尾', '口径说明', f"""
<ul>
<li><b>四档定义（多头侧，空头镜像）</b>：🥇绝对龙头 = 日线多 ＋ 日线评分 ≥ {lead_1d} ＋ 4h 在多头桶 ＋ 4h 评分 ≥ {lead_4h}；
🔴危险分歧 = 日线多 ＋ 4h 偏弱（掉出多头桶或 4h 评分 &lt; 0）＋ Δ4h ≤ −{d4h_sig}；
🚀新贵 = 日线多 ＋ Δ4h ≥ +{d4h_sig} ＋ 4h 评分 &gt; {new_strong}（负值区回抽不算新贵）；
🟡回调 = 日线多 ＋ 日线评分 ≥ {lead_1d} ＋ Δ4h &gt; −{d4h_sig}。四档互斥，一只品种一天只属于一个档，按优先级取档。</li>
<li><b>🟢 4h 蓄势池</b>：⚪ 未入档中 4h 已在趋势桶且 4h 评分越过 ±{new_strong} 者；蓄势而非启动，日线确认后提级，4h 掉桶出池。</li>
<li><b>Δ4h 口径</b>：Δ4h = 今日日盘收盘 4h 评分 − 前一交易日日盘收盘 4h 评分（4h 评分 = 收盘对 MA7 偏离%）。前一交易日数据缺失时 Δ 栏标「—」，依赖环比的档位暂定并加 ※。</li>
<li><b>破位备注</b>：多头侧 = 4h 收盘跌破 4h EE；空头侧 = 4h 收盘上破 4h PP。仅作备注用词，不单独设档；4h 评分转正/转负不等于重新入桶。</li>
<li><b>塌陷假象</b>：空头榜排名上升是评分派生的相对名次变化，不能解释为资金流入或主动走强。</li>
<li><b>增仓预留</b>：第三要素「增减仓」数据源暂未接入；接入后日线增仓 = 对当侧趋势的增强确认，只作加减分项，不改变四档定义。</li>
<li><b>数据源</b>：{_esc(DASHBOARD_URL)}（扫描生成于 {_esc(f.get('created_at'))}）。报告日默认下一工作日，未内置节假日日历。</li>
</ul>""")


def load_goldman_appendix(data_date, directory=None):
    """Load a complete same-day Goldman chart bundle for single-file embedding."""
    tag = str(data_date).replace('-', '')
    root = Path(directory) if directory is not None else SEAT_DIR
    json_path = root / f'goldman_contract_positions_{tag}.json'
    long_path = root / f'goldman_contract_long_{tag}.png'
    short_path = root / f'goldman_contract_short_{tag}.png'
    if not all(path.exists() for path in (json_path, long_path, short_path)):
        return None
    try:
        metadata = json.loads(json_path.read_text(encoding='utf-8'))
        if metadata.get('date') != tag:
            return None
        long_bytes, short_bytes = long_path.read_bytes(), short_path.read_bytes()
        if not long_bytes or not short_bytes:
            return None
    except (OSError, ValueError, TypeError):
        return None
    return {
        'date': tag,
        'prev_date': metadata.get('prev_date'),
        'member': metadata.get('member'),
        'coverage': metadata.get('coverage') or {},
        'data_hash': digest(metadata),
        'long_image_hash': hashlib.sha256(long_bytes).hexdigest(),
        'short_image_hash': hashlib.sha256(short_bytes).hexdigest(),
        'long_data_uri': 'data:image/png;base64,' + base64.b64encode(long_bytes).decode('ascii'),
        'short_data_uri': 'data:image/png;base64,' + base64.b64encode(short_bytes).decode('ascii'),
    }


def render_goldman_appendix(appendix):
    if not appendix:
        return ''
    coverage = appendix.get('coverage') or {}
    caption = (
        f"数据日 {_esc(appendix.get('date'))}，对比 {_esc(appendix.get('prev_date'))}；"
        f"动态主次合约品种 {coverage.get('dominant_varieties', '—')} 个，"
        f"主力缺失 {coverage.get('main_missing', '—')} 个，"
        f"次主力缺失 {coverage.get('sub_missing', '—')} 个。"
    )
    return (
        '<div class="card goldman-appendix">'
        '<h2><span>附录 · 高盛主次合约净持仓追踪</span></h2>'
        f'<p>{caption}</p>'
        '<h3>主力合约净多排名</h3>'
        f'<img src="{appendix["long_data_uri"]}" alt="高盛主次合约净多持仓排名图">'
        '<h3>主力合约净空排名</h3>'
        f'<img src="{appendix["short_data_uri"]}" alt="高盛主次合约净空持仓排名图">'
        '<p class="mut">口径为交易所会员持仓前20名披露数据；未披露不代表真实持仓为零。</p>'
        '</div>'
    )


def next_report_date(f):
    d = datetime.strptime(f['data_date'][:10], "%Y-%m-%d").date()
    return _next_weekday(d).isoformat()


# ---------------------------------------------------------------- 样式（v6 §3.1 固定类名）
CSS = """
  :root{
    --bg:#f4f6f9; --card:#ffffff; --line:#e6e9ef;
    --ink:#1c2430; --ink2:#5b6675; --ink3:#8a94a3;
    --red:#d92d20; --red-d:#b42318; --red-bg:#fef2f1;
    --green:#16855a; --green-bg:#edfdf4;
    --amber:#b45309; --amber-bg:#fffaf0;
    --blue:#1d4ed8; --blue-bg:#eff5ff;
    --gold:#a16207; --gold-bg:#fffbeb;
    --navy:#25456f;
  }
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;background:var(--bg);color:var(--ink);line-height:1.65;font-size:15px;-webkit-font-smoothing:antialiased}
  .wrap{max-width:1060px;margin:0 auto;padding:18px 16px 60px}

  header{background:linear-gradient(135deg,#22406a 0%,#2c5386 55%,#37649e 100%);color:#fff;border-radius:16px;padding:26px 28px 22px;position:relative;overflow:hidden}
  header::after{content:"";position:absolute;right:-70px;top:-70px;width:250px;height:250px;border-radius:50%;background:radial-gradient(circle,rgba(217,45,32,.22),transparent 70%)}
  header::before{content:"";position:absolute;right:80px;bottom:-100px;width:230px;height:230px;border-radius:50%;background:radial-gradient(circle,rgba(255,255,255,.07),transparent 70%)}
  .hd-top{display:flex;flex-wrap:wrap;gap:10px;align-items:center;position:relative;z-index:1}
  .tag{font-size:11.5px;letter-spacing:.12em;color:#a9c0dd;text-transform:uppercase}
  h1{font-size:25px;font-weight:800;margin:4px 0 2px;letter-spacing:.4px}
  h1 em{font-style:normal;color:#ffa79c}
  .hd-sub{font-size:13px;color:#cddbec;display:flex;flex-wrap:wrap;gap:6px 14px;margin-top:6px;position:relative;z-index:1}
  .hd-sub b{color:#fff}
  .chip{display:inline-flex;align-items:center;gap:5px;background:rgba(255,255,255,.13);border:1px solid rgba(255,255,255,.22);border-radius:999px;padding:3px 11px;font-size:12.5px;color:#eef4fb;white-space:nowrap}
  .chip.red{background:rgba(217,45,32,.3);border-color:rgba(255,140,130,.45)}
  .chip.green{background:rgba(22,133,90,.34);border-color:rgba(120,231,183,.45)}
  .chip.amber{background:rgba(245,158,11,.24);border-color:rgba(255,205,100,.5)}
  .chip b{color:#fff}
  .cnum{font-weight:800;font-size:14px}
  .chip .arrow{color:#ffb3a8;font-weight:700}

  .card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:20px 22px;margin-top:16px;box-shadow:0 1px 3px rgba(28,36,48,.05)}
  h2{font-size:19px;font-weight:800;color:var(--navy);margin-bottom:14px;padding-bottom:9px;border-bottom:2px solid var(--line);display:flex;align-items:center;gap:8px;flex-wrap:wrap}
  h2 .n{display:inline-flex;align-items:center;justify-content:center;min-width:26px;height:26px;padding:0 6px;border-radius:8px;background:var(--navy);color:#fff;font-size:14px;font-weight:800;flex:0 0 auto}
  h3{font-size:15.5px;font-weight:800;margin:18px 0 9px;color:var(--ink);display:flex;align-items:center;gap:7px}
  h3:first-of-type{margin-top:4px}
  p{font-size:14.5px;color:var(--ink2);margin-bottom:9px}
  p b,li b{color:var(--ink)}
  ul{margin:0 0 10px 20px}
  li{font-size:14.3px;color:var(--ink2);margin-bottom:5px}
  .hl{color:var(--red-d);font-weight:800}
  .hlg{color:var(--green);font-weight:800}
  .hla{color:var(--amber);font-weight:800}

  .tbwrap{overflow-x:auto;margin:10px 0 4px;-webkit-overflow-scrolling:touch}
  table{width:100%;border-collapse:collapse;font-size:13.4px;min-width:560px}
  th{background:#f0f3f8;color:var(--navy);font-weight:700;text-align:left;padding:9px 10px;border-bottom:2px solid #dde3ec;white-space:nowrap;font-size:12.8px}
  td{padding:8px 10px;border-bottom:1px solid var(--line);color:var(--ink2);vertical-align:top}
  tr:last-child td{border-bottom:none}
  tbody tr:hover{background:#fafbfd}
  td b{color:var(--ink)}
  .up{color:var(--red);font-weight:800}
  .dn{color:var(--green);font-weight:800}
  .warn{color:var(--amber);font-weight:800}
  .mut{color:var(--ink3)}
  .nowrap{white-space:nowrap}

  .bdg2{display:inline-block;padding:1.5px 8px;border-radius:999px;font-size:11.8px;font-weight:800;white-space:nowrap}
  .bdg2.lead{background:var(--gold-bg);color:var(--gold);border:1px solid #fde68a}
  .bdg2.pull{background:#fffbeb;color:#a16207;border:1px solid #fde68a}
  .bdg2.danger{background:var(--red-bg);color:var(--red-d);border:1px solid #fbd5d1}
  .bdg2.bear{background:#f3e8ff;color:#7c3aed;border:1px solid #ddd0f5}
  .bdg2.fresh{background:var(--green-bg);color:var(--green);border:1px solid #c7f0dd}
  .bdg2.flat{background:#f1f3f7;color:var(--ink3);border:1px solid #e2e6ee}

  .oneline{background:linear-gradient(135deg,#fff8f0,#fff);border:1px solid #fde68a;border-left:5px solid var(--gold);border-radius:12px;padding:15px 18px;margin-top:14px;font-size:14.6px;color:var(--ink);line-height:1.75}
  .oneline b{color:var(--gold)}

  .note{background:#fffbeb;border:1px solid #fde68a;border-radius:10px;padding:11px 14px;font-size:13.2px;color:var(--ink2);margin:10px 0}
  .note b{color:var(--ink)}

  .sigblock{background:#f8fafc;border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin-bottom:14px}
  .sigblock .ot{font-size:13px;font-weight:800;color:var(--navy);margin-bottom:9px;letter-spacing:.3px}
  .ovrow{display:flex;justify-content:space-between;align-items:baseline;gap:12px;font-size:13.4px;padding:4px 0;border-bottom:1px dashed #eef1f6}
  .ovrow:last-child{border-bottom:none}
  .ovrow span:first-child{color:var(--ink3);flex:0 0 auto}
  .ovrow span:last-child{color:var(--ink)}

  .judge-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:12px}
  .judge-card{background:var(--card);border:1px solid var(--line);border-left-width:5px;border-radius:12px;padding:14px 16px}
  .judge-card.j-red{border-left-color:var(--red)}
  .judge-card.j-gold{border-left-color:var(--gold)}
  .judge-card.j-green{border-left-color:var(--green)}
  .judge-card .jt{font-size:14.5px;font-weight:800;color:var(--ink);margin-bottom:8px}
  .judge-card .jf,.judge-card .ja{font-size:13.4px;color:var(--ink2);margin-bottom:6px;line-height:1.7}
  .judge-card .jl{display:inline-block;background:#f0f3f8;color:var(--navy);border-radius:6px;padding:0 7px;font-size:11.5px;font-weight:800;margin-right:7px}

  .oplist{counter-reset:o;list-style:none;margin:0}
  .oplist li{counter-increment:o;position:relative;padding:9px 0 9px 38px;border-bottom:1px dashed #eef1f6;font-size:14.2px;color:var(--ink2);line-height:1.7}
  .oplist li:last-child{border-bottom:none}
  .oplist li::before{content:counter(o);position:absolute;left:0;top:9px;width:24px;height:24px;border-radius:7px;background:var(--navy);color:#fff;font-size:12.5px;font-weight:800;display:flex;align-items:center;justify-content:center}

  .goldman-appendix img{display:block;width:100%;height:auto;margin:10px auto 20px;border-radius:10px;background:#0e1e33}
  .goldman-appendix h3{margin-top:18px}

  footer,.foot{margin-top:22px;padding:14px 16px;background:#eef1f6;border-radius:12px;font-size:12.6px;color:var(--ink3);line-height:1.7}

  @media(max-width:820px){
    .judge-grid{grid-template-columns:1fr}
    h1{font-size:21px}
    .card{padding:16px 15px}
    header{padding:20px 18px 18px}
  }
"""


def render_footer(f):
    return (f"<footer>口径：日线定趋势、4h 定节奏；趋势 × 动量两要素，四档互斥。"
            f"持仓、价格与评分分开核验；SP平多、BP平空；破位备注：多头侧 4h 收破 EE、空头侧 4h 上破 PP；评分偏负不等于破位。报告日默认下一工作日，未内置节假日日历。"
            f"数据源：{_esc(DASHBOARD_URL)} —— 扫描生成于 {_esc(f.get('created_at'))}，"
            f"数据基准 {_esc(f['data_date'])}，对比 {_esc(f['prev_date'])}。</footer>")


def render_html(f, narrative, report_date=None, goldman_appendix=None) -> str:
    if f.get('rules_version') != RULES_VERSION:
        raise ValueError('扫描规则版本过旧，请先重新运行 scan_report，再按新事实撰写叙事')
    report_date = validate_report_date(f, report_date or next_report_date(f))
    validate_narrative(f, narrative, report_date)
    notes = (narrative or {}).get('section_notes') or {}
    body = "".join([
        render_header(f, narrative, report_date),
        '<div class="note">' + _esc(f.get('narrative_status') or ((narrative.get('source') or '外部叙事') + ' · 已绑定本次事实' if narrative else '规则版 · 未使用模型叙事')) + '</div>',
        '<div class="note">' + _esc('；'.join(f.get('quality_notes') or [])) + '</div>' if f.get('quality_notes') else '',
        render_oneline(f, narrative),
        render_cautions(f, narrative),
        s1_core(f, narrative, notes),
        s2_long(f, notes),
        s3_short(f, notes),
        s3b_pool(f, notes),
        s4_momentum(f, notes),
        s5_tips(f, narrative, report_date),
        methodology_note(f),
        render_goldman_appendix(goldman_appendix),
        render_footer(f),
    ])
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>期货看板每日总结 · {_esc(report_date)} 作战地图</title>
<style>{CSS}</style></head>
<body><div class="wrap">{body}</div></body></html>"""


def validate_report_date(facts, report_date):
    if iso_day(report_date) != report_date or report_date <= facts['data_date']:
        raise ValueError('报告日必须是晚于数据日的有效日期')
    return report_date


# 叙事 section_notes 的合法节键（v6：一=核心判断走 judge_cards，五=操作提示走 action_tips）
NOTE_KEYS = {'1', '2', '3', 'pool', '4'}


def validate_narrative(facts, narrative, report_date):
    if narrative is None:
        return
    if not isinstance(narrative, dict):
        raise ValueError('叙事必须是JSON对象')
    if narrative.get('report_date') != report_date or narrative.get('input_hash') != facts.get('input_hash') or not facts.get('input_hash'):
        raise ValueError('叙事日期或输入指纹不匹配，需依据当前扫描事实重写')

    def text(value):
        if not isinstance(value, str) or not value.strip() or len(value) > 4000:
            raise ValueError('叙事字段必须为非空纯文本，最长4000字')

    for field in ('tone', 'one_liner', 'source'):
        if field in narrative:
            text(narrative[field])
    notes = narrative.get('section_notes', {})
    if not isinstance(notes, dict) or set(notes) - NOTE_KEYS:
        raise ValueError('section_notes 必须使用 v6 节键：1 / 2 / 3 / pool / 4')
    for value in notes.values():
        text(value)
    cards = narrative.get('judge_cards', [])
    if not isinstance(cards, list):
        raise ValueError('judge_cards 必须是数组')
    for card in cards:
        if not isinstance(card, dict):
            raise ValueError('judge_cards 元素必须是对象')
        for field in ('title', 'fact', 'action'):
            text(card.get(field))
    for field in ('action_tips', 'cautions'):
        values = narrative.get(field, [])
        if not isinstance(values, list):
            raise ValueError(field + ' 必须是数组')
        for value in values:
            if field == 'cautions' and isinstance(value, dict):
                text(value.get('title'))
                text(value.get('body'))
            else:
                text(value)


def publish_report(facts, narrative=None, report_date=None, output_dir=None,
                   goldman_appendix=None):
    if facts.get('scan_version') != 3 or not facts.get('input_hash'):
        raise ValueError('请先用新版 scan_report 重新生成事实')
    root = Path(output_dir) if output_dir is not None else REPORTS_DIR
    day = validate_report_date(facts, report_date or next_report_date(facts))
    appendix = goldman_appendix
    if appendix is None and output_dir is None:
        appendix = load_goldman_appendix(facts['data_date'])
    body = render_html(facts, narrative, day, appendix)
    # 单文件原子替换，最后发布同批成品记录；API只读取归档副本，不再猜测数据日。
    atomic_text(root / f'daily_summary_{day}.html', body)
    record = {
        'report_date': day, 'data_date': facts['data_date'], 'input_hash': facts['input_hash'],
        'html_hash': digest(body), 'facts': facts, 'narrative': narrative,
        'one_liner': (narrative or {}).get('one_liner') or fallback_one_liner(facts, fallback_tone(facts))}
    if appendix:
        record['addons'] = {'goldman_contract_positions': {
            key: value for key, value in appendix.items() if not key.endswith('_data_uri')
        }}
    atomic_json(root / f'daily_summary_{day}.json', record)
    return root / f'daily_summary_{day}.html'


def rerender_report_for_data_date(data_date, reports_dir=None):
    """Re-render an already-published report while preserving its bound inputs.

    The seat job runs after the ordinary report.  Reading the archived record,
    rather than today's mutable scan/narrative files, preserves a custom report
    date and the exact facts/narrative that were previously published.
    """
    root = Path(reports_dir) if reports_dir is not None else REPORTS_DIR
    candidates = []
    for path in sorted(root.glob('daily_summary_*.json'), reverse=True):
        try:
            record = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError, TypeError):
            continue
        if record.get('data_date') == data_date and isinstance(record.get('facts'), dict):
            candidates.append(record)
    if not candidates:
        return None
    record = candidates[0]
    appendix = load_goldman_appendix(data_date)
    return publish_report(
        record['facts'], record.get('narrative'), record.get('report_date'), output_dir=root,
        goldman_appendix=appendix or {},
    )


def main():
    ap = argparse.ArgumentParser(description="期货看板每日总结 · HTML 渲染器（v6 五节结构）")
    ap.add_argument("--date", help="数据日期 YYYY-MM-DD（默认取最新扫描产物）")
    ap.add_argument("--report-date", help="报告日期 YYYY-MM-DD（默认=数据日的下一工作日）")
    args = ap.parse_args()

    if args.date:
        scan_file = SCAN_DIR / f"scan_{args.date}.json"
    else:
        cands = sorted(SCAN_DIR.glob("scan_*.json"))
        if not cands:
            raise SystemExit("[错误] 无扫描产物，先运行 python -m backend.pipeline.scan_report")
        scan_file = cands[-1]
    if not scan_file.exists():
        raise SystemExit(f"[错误] 找不到 {scan_file}")
    with open(scan_file, encoding="utf-8") as fp:
        f = json.load(fp)

    report_date = args.report_date or next_report_date(f)
    narrative_file = NARRATIVE_DIR / f"narrative_{report_date}.json"
    narrative = None
    if narrative_file.exists():
        try:
            with open(narrative_file, encoding="utf-8") as fp:
                narrative = json.load(fp)
            validate_narrative(f, narrative, report_date)
        except ValueError as exc:
            narrative = None
            f['narrative_status'] = f'规则版 · 未采用旧叙事：{exc}'
            print('[叙事] ' + f['narrative_status'])
        else:
            print(f"[叙事] 采用 {narrative_file}")
    else:
        print(f"[叙事] 无 narrative_{report_date}.json，使用规则化模板兜底")

    out = publish_report(f, narrative, report_date)
    print(f"[产物] {out}")


if __name__ == "__main__":
    main()
