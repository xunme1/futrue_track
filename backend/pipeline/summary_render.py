# -*- coding: utf-8 -*-
"""期货看板每日总结 · HTML 渲染器（报告流水线第 2 步）。

读取 scan_report 产出的扫描 JSON（data/reports/scan/scan_YYYY-MM-DD.json），
叠加可选叙事 JSON（data/reports/narrative/narrative_YYYY-MM-DD.json，由 LLM 按
docs/summary_contract.md 撰写；缺字段时以规则化模板兜底），渲染单文件 HTML：

    data/reports/daily_summary_YYYY-MM-DD.html

版式与配色对齐资料库成品《期货看板每日总结》（9 节结构 + 浅色卡片）。

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

REPORTS_DIR = DATA_DIR / "reports"
SCAN_DIR = REPORTS_DIR / "scan"
NARRATIVE_DIR = REPORTS_DIR / "narrative"
SEAT_DIR = DATA_DIR / "seat"

DASHBOARD_URL = "http://110.42.220.207:8000/"

WEEKDAY_CN = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


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


# ---------------------------------------------------------------- 兜底叙事
def fallback_tone(f):
    """从可核验的两口径计数变化给当日定性。"""
    if any(v.get('unknown', 0) for v in f.get('coverage', {}).values()):
        return '数据不完整 · 暂停方向定性'
    o, p = f['overview']['1d'], f['overview'].get('prev_1d')
    o4 = f['overview']['4h']
    d_long = o['long_trend'] - (p or {}).get('long_trend', o['long_trend'])
    d_short = o['short_trend'] - (p or {}).get('short_trend', o['short_trend'])
    acts = f.get('new_signals', {}).get('1d', [])
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
    acts = f.get('new_signals', {}).get('1d', [])
    seg = []
    for sig, verb in (('BK', '新开多'), ('SK', '新开空'), ('SP', '平多离场'), ('BP', '平空离场')):
        rows = [x for x in acts if x.get('signal') == sig]
        if rows:
            seg.append(f"{len(rows)} 只{verb}（{_names(rows, 6)}）")
    head = f"{dd} 是{tone}"
    if seg:
        head += " —— 日线 " + "、".join(seg)
    return (head + f"。日线多空 {o['long_trend']} : {o['short_trend']}"
            f"（前 {(p or {}).get('long_trend', '—')} : {(p or {}).get('short_trend', '—')}），"
            f"4h 多空 {o4['long_trend']} : {o4['short_trend']}。")


def fallback_cautions(f):
    """规则生成的「别误读」提示：只在数据支持时输出。

    返回 [{'title','body'}]，两者均为纯文本 —— 渲染层负责拼装 HTML 并转义，
    避免规则串里夹标签被二次转义（历史版本就栽在这里）。
    """
    out = []
    o4, p4 = f['overview']['4h'], f['overview'].get('prev_4h') or {}
    n_sl, p_sl = o4.get('short_to_long', 0), p4.get('short_to_long')
    if p_sl is not None and n_sl > p_sl:
        real = [x for x in f['turn']['B'] if x.get('current_long_4h')]
        out.append({
            'title': f"别误读「4h 空转多 {p_sl}→{n_sl}」",
            'body': (f"其中当前4h持多且最近信号为 BK的只有 {len(real)} 只"
                     + (f"（{_names(real, 6)}）" if real else "")
                     + "，其余仅是蓝转红候选，不构成翻多。"),
        })
    red = [x for x in f['divergence']['items'] if x.get('level') == '🔴']
    if red:
        out.append({
            'title': f"分歧名单 {len(red)} 只已到 🔴 级（{_names(red, 8)}）",
            'body': "多单不持有 / 先撤；修复信号 = 4h 重新 BK。",
        })
    return out


def fallback_tips(f):
    tips = []
    for x in f['divergence']['items']:
        if x.get('level') == '🔴':
            tips.append(f"{_name(x)}：{'＋'.join(x.get('why') or ['分歧'])} → 多单不持有 / 先撤。")
        elif x.get('level') == '🟠':
            tips.append(f"{_name(x)}：{'＋'.join(x.get('why') or ['分歧'])} → 多单减半，破 EE {_n(x.get('EE'))} 走。")
        elif x.get('verdict') == '多头抵抗':
            tips.append(f"{_name(x)}（农产品）：四项证据齐全 → 抵抗候选，等待新的交易信号确认；日线参考 EE {_n(x.get('EE'))} 才算失败。")
    for x in f['turn']['A'][:3]:
        if x.get('verdict') == '共振空':
            tips.append(f"{_name(x)}：4h + 日线双级共振空 → 可跟空。")
        elif x.get('verdict') == '日线仍多':
            tips.append(f"{_name(x)}：4h短周期变化但日线仍多，需核验当前状态，盯日线 EE {_n(x.get('EE_1d'))}，不做空。")
    for x in f['bear_pressure'][:2]:
        tips.append(f"{_name(x)}：熊头遇压，反弹进 {_n(x.get('KK'))}–{_n(x.get('PP'))} 压力带可加空。")
    for x in f['key_levels'][:2]:
        if x['label'] == '多头距EE':
            tips.append(f"{x['name']} {x['code']}：距日线 EE 仅 {x['gap_pct']:+.2f}%，多单警戒线。")
    return tips[:9]


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
    if n >= 4:
        return (f'<details class="card fold-section"><summary>'
                f'<h2><span class="n">{n}</span><span>{title}</span></h2>'
                '<span class="fold-label" aria-hidden="true"><span class="fold-show">展开</span>'
                '<span class="fold-hide">收起</span><span class="fold-arrow">⌄</span></span>'
                f'</summary><div class="fold-body">{note_html}{body}</div></details>')
    return f'<div class="card"><h2><span class="n">{n}</span>{title}</h2>{note_html}{body}</div>'


def _grp(rows, by_sector=True):
    if not rows:
        return "—"
    if not by_sector:
        return "、".join(_name(r) for r in rows)
    g = {}
    for r in rows:
        g.setdefault(r.get('sector') or '其他', []).append(r)
    return "；".join(f"{s}（{'、'.join(_name(x) for x in v)}）" for s, v in g.items())


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
  </div>
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


def s1_overview(f, notes):
    o, p = f['overview']['1d'], f['overview'].get('prev_1d') or {}
    o4, p4 = f['overview']['4h'], f['overview'].get('prev_4h') or {}
    rows = [
        ('日线多头', o['long_trend'], p.get('long_trend')),
        ('日线空头', o['short_trend'], p.get('short_trend')),
        ('日线多转空', o['long_to_short'], p.get('long_to_short')),
        ('日线多转空预警', o['long_to_short_warning'], p.get('long_to_short_warning')),
        ('日线空转多', o['short_to_long'], p.get('short_to_long')),
        ('日线回踩', o['long_support_warning'], p.get('long_support_warning')),
        ('日线遇压', o['short_pressure_warning'], p.get('short_pressure_warning')),
    ]
    left = "".join(
        f'<div class="ovrow"><span>{_esc(t)}</span><span>{cur}'
        + (f' <span class="mut">（前 {pv}）</span>' if pv is not None and pv != cur else '')
        + '</span></div>' for t, cur, pv in rows)
    rows4 = [
        ('4h 多头', o4['long_trend'], p4.get('long_trend')),
        ('4h 空头', o4['short_trend'], p4.get('short_trend')),
        ('4h 多转空', o4['long_to_short'], p4.get('long_to_short')),
        ('4h 空转多', o4['short_to_long'], p4.get('short_to_long')),
    ]
    right = "".join(
        f'<div class="ovrow"><span>{_esc(t)}</span><span>{cur}'
        + (f' <span class="mut">（前 {pv}）</span>' if pv is not None and pv != cur else '')
        + '</span></div>' for t, cur, pv in rows4)
    acts = f.get('new_signals', {}).get('1d', [])
    act_txt = "、".join(f"{x['code']}({x['signal']})" for x in acts) or "无"
    body = (f'<div class="ov"><div class="ovc"><div class="ot">日线口径</div>{left}</div>'
            f'<div class="ovc"><div class="ot">4 小时口径</div>{right}</div></div>'
            f'<p style="margin-top:12px">当日日线新信号：<b>{_esc(act_txt)}</b></p>')
    body += '<p>当日4小时新信号：<b>' + _esc('、'.join(x['code'] + '(' + str(x['signal']) + ')' for x in f.get('new_signals', {}).get('4h', [])) or '无') + '</b></p>'
    return _section(1, "两口径总览", body, notes.get('1'))


def s2_leaders(f, notes):
    L = f['leaders']
    tiers = f['long_4h_tiers']
    all_rows = [r for rows in tiers.values() for r in rows]
    ended = [r for r in all_rows if r.get('pos_4h') in (0, -1)]
    holding = [r for r in all_rows if r.get('pos_4h') == 1
               and r.get('below_EE_4h') is False and r.get('score_4h') is not None
               and r['score_4h'] < 1]
    broken = [r for r in all_rows if r.get('pos_4h') == 1 and r.get('below_EE_4h') is True]

    def leader_rows(rows):
        return [[f"<b>{_esc(r['name'])}</b>", _esc(r['code']), _n(r['score']),
                 _n(r.get('score_4h')), f"#{r.get('rank') or '—'}", _sign(r.get('rank_change'))]
                for r in rows]

    parts = []
    if L['dual']:
        parts.append(f"<h3>🥇 双强龙头（{len(L['dual'])} 只）—— 日线强 ＋ 4h 强，最该拿住的一批</h3>"
                     + _table(['品种', '代码', '日线', '4h', '排名', '变化'], leader_rows(L['dual'])))
    if L['absolute']:
        parts.append(f"<h3>🥇 日线绝对龙头（{len(L['absolute'])} 只）—— 日线极强，4h 只弱正</h3>"
                     + _table(['品种', '代码', '日线', '4h', '排名', '变化'], leader_rows(L['absolute'])))
    if L['quasi']:
        parts.append(f"<h3>🥈 准龙头（{len(L['quasi'])} 只）</h3>"
                     + _table(['品种', '代码', '日线', '4h', '排名', '变化'], leader_rows(L['quasi'])))
    if holding:
        parts.append(f"<h3>🟡 回踩持有多头（{len(holding)} 只）—— 4h仍持多且未破4h EE，评分偏弱需观察</h3>"
                     + _table(['品种', '代码', '日线', '4h', '距 EE', '状态'],
                              [[f"<b>{_esc(r['name'])}</b>", _esc(r['code']), _n(r['score']),
                                _n(r.get('score_4h')), f"{_n(r.get('gap_to_EE'))}%", _esc(r['tier'])]
                               for r in holding]))
    for label, data in [('⚠️ 4h 多头已结束（空仓或持空，未重新 BK）', ended),
                        ('🚨 4h 仍持多但收破支撑', broken),
                        ('⚪ 4h数据不足 · 已知状态仍保留', tiers.get('4h未知', []))]:
        if data:
            parts.append(f'<h3>{label}（{len(data)} 只）</h3>' + _state_table(data))
    if not parts:
        parts.append("<p>当日无满足龙头门槛的品种。</p>")
    return _section(2, "📈 趋势与龙头（权重最高 · 重点看）", "".join(parts), notes.get('2'))


def _state_table(rows):
    return _table(['品种', '日线评分', '日线DD / EE', '4h评分', '4h状态', '4h收盘 / EE', '最近4h交易信号'],
                  [[f"<b>{_esc(_name(r))}</b>", _n(r.get('score')), f"{_n(r.get('DD'))} / {_n(r.get('EE'))}", _n(r.get('score_4h')),
                    _esc(r.get('state_4h')), f"{_n(r.get('close_4h'))} / {_n(r.get('EE_4h'))}",
                    _esc((r.get('last_signal_4h') or {}).get('type', '—')) + ' · ' +
                    _esc((r.get('last_signal_4h') or {}).get('date', '—'))] for r in rows])


def s3_retest(f, notes):
    rows = f['leader_retest']
    groups = {}
    for r in rows:
        if r.get('pos_4h') is None or r.get('below_EE_4h') is None:
            label = '⚪ 证据不足（暂停修复判断）'
        elif r.get('below_EE_4h') is True:
            label = '🚨 4h收盘跌破4h EE'
        elif r.get('repaired'):
            label = '✅ 已核验平多后重新开多（仍需延续）'
        elif r.get('reopened_long'):
            label = '🟡 本期新开多（未满足回踩修复全部证据）'
        elif r.get('pos_4h') == 1:
            label = '🟡 持续持多（不等于本期重新开多）'
        else:
            label = '⚠️ 4h非多（尚未重新BK）'
        groups.setdefault(label, []).append(r)
    priority = ['🚨', '✅', '🟡', '⚠️', '⚪']
    body = ''.join(f'<h3>{label}（{len(data)} 只）</h3>' + _state_table(data)
                   for label, data in sorted(groups.items(), key=lambda kv: next(i for i, p in enumerate(priority) if kv[0].startswith(p))))
    body = '<p>日线DD/EE与4h收盘/EE分列展示；历史回踩记录不代表当前仍在支撑带。评分偏负与收破EE分别判断。</p>' + body
    return _section(3, '🔁 龙头回踩与4h状态核验', body, notes.get('3'))


def s4_divergence(f, notes):
    items = f['divergence']['items']
    resist = [x for x in items if x.get('verdict') == '多头抵抗']
    red = [x for x in items if x.get('level') == '🔴']
    orange = [x for x in items if x.get('level') == '🟠']
    back = [x for x in items if x.get('verdict') in ('单只预警', '待核验')]
    parts = []
    if f['divergence']['sectors']:
        rows = [[f"<b>{_esc(s['sector'])}</b>", _esc(s['stype']),
                 f"{len(s['short'])} 只", "、".join(s['short']) or "—",
                 f"{len(s['gone'])} 只", "、".join(s['gone']) or "—"]
                for s in f['divergence']['sectors']]
        parts.append("<h3>板块当前状态（持空 / 空仓 ≥ 2 只；不是近5日事件）</h3>"
                     + _table(['板块', '口径', '当前持空', '明细', '当前空仓', '明细'], rows))
    if resist:
        parts.append("<h3>🟡 农产品「多头抵抗」判定 —— 4 条全中才算</h3>"
                     + _table(['品种', '代码', '日线', '4h', '收盘', 'DD', 'EE', '排名'],
                              [[f"<b>{_esc(x['name'])}</b>", _esc(x['code']), _n(x['score']),
                                _n(x.get('score_4h')), _n(x.get('close')), _n(x.get('DD')),
                                _n(x.get('EE')), f"#{x.get('rank') or '—'}（{_sign(x.get('rank_change'))}）"]
                               for x in resist])
                     + "<p>抵抗候选须同时满足：日线评分正且收盘≥DD；4h为SP后空仓、未破4h EE且评分≥−0.5；排名不下降；板块当前弱背景。候选不等于新的开多信号。</p>")
    if red or orange:
        rows = [[x.get('level', ''), f"<b>{_esc(x['name'])}</b>", _esc(x['code']),
                 _esc(x.get('sector_name') or x.get('sector')), _n(x['score']), _n(x.get('score_4h')),
                 _esc("；".join(x.get('why') or [])) or "—"]
                for x in red + orange]
        parts.append(f"<h3>🔴 分歧名单（含未满足抵抗条件的农产品 · {len(red) + len(orange)} 条）</h3>"
                     + _table(['评级', '品种', '代码', '板块', '日线', '4h', '依据'], rows)
                     + "<p>🔴 多单不持有 / 先撤；🟠 多单减半，破 EE 走；修复信号 = 4h 重新 BK。</p>")
    if back:
        parts.append(f"<h3>⚠️ 单只预警 / 待核验（未满足全部条件或证据不足）（{len(back)} 只）</h3>"
                     + _state_table(back))
    if not parts:
        parts.append("<p>当日无板块级分歧。</p>")
    return _section(4, "⭐ 分歧严重 · 特别关注名单（简化版）", "".join(parts), notes.get('4'))


def _short_nature(r, data_date):
    last = r.get('last_signal') or {}
    tags = ['当日新开空'] if last.get('type') == 'SK' and iso_day(last.get('date')) == data_date else ['持续持空']
    if r.get('score') is None:
        tags.append('评分未知')
    elif r['score'] > 0:
        tags.append('评分已正，不等于出榜')
    return ' · '.join(tags)


def s5_short(f, notes):
    rows = [r for sec in f['short_positions'].values() for r in sec]
    rows.sort(key=lambda x: _sc(x))
    body = _table(['品种', '代码', '板块', '日线', '性质', '收盘', 'KK', 'PP', '触压'],
                  [[f"<b>{_esc(r['name'])}</b>", _esc(r['code']), _esc(r.get('sector')),
                    _n(r['score']), _esc(_short_nature(r, f['data_date'])), _n(r.get('close')), _n(r.get('KK')), _n(r.get('PP')),
                    f"{r.get('retest_count') or 0} 次"] for r in rows])
    return _section(5, f"看空主线（日线空头 {len(rows)} 只）", body, notes.get('5'))


def s6_turn(f, notes):
    A = f['turn']['A']
    MARK = {'共振空': ('🔴', '可跟空（4h + 日线双级共振）'),
            '已离场': ('⬜', '当前空仓，等待新的日线交易信号'),
            '日线仍多': ('🟨', '日线仍多，短周期转折需核验'),
            '日空 / 4h观望': ('⬜', '日线持空、4h观望，尚非双级共振'),
            '状态待核验': ('⚪', '状态不足，暂不判断共振')}
    parts = []
    if A:
        rows = [[MARK.get(x['verdict'], ('⚪', ''))[0], f"<b>{_esc(x['name'])}</b>", _esc(x['code']),
                 _n(x['score']), _esc(_md(x.get('signal_date'))), _n(x.get('score_1d')),
                 _n(x.get('EE_1d')), _esc(MARK.get(x['verdict'], ('', '—'))[1])]
                for x in A]
        parts.append("<h3>A. 4h 多转空 → 日线裁决</h3>"
                     + _table(['', '品种', '代码', '4h', '信号日', '日线', '日线 EE', '动作'], rows))
    B = f['turn']['B']
    if B:
        rows = [[f"<b>{_esc(x['name'])}</b>", _esc(x['code']), _n(x['score']),
                 _esc(_md(x.get('signal_date'))), _esc(x.get('pos_1d')), _esc(x.get('pos_4h'))] for x in B]
        parts.append("<h3>B. 反向事件（历史转折需与当前4h持仓分别核验）</h3>"
                     + _table(['品种', '代码', '4h', '信号日', '日线持仓', '4h持仓'], rows))
    for nm, arr in f['turn']['warnings'].items():
        if arr:
            parts.append(f"<p>{_esc(nm)}：{_esc(_names(arr, 20))}</p>")
    if not parts:
        parts.append("<p>当日无 4h 转折信号。</p>")
    return _section(6, "阶段性转折（简化版 · 只留两条主线 + 一个反向段）",
                    "".join(parts), notes.get('6'))


def s7_pressure(f, notes):
    rows = f['bear_pressure']
    if not rows:
        return _section(7, "熊头遇压（重点 👀）", "<p>当日无熊头触压品种。</p>", notes.get('7'))
    body = _table(['品种', '代码', '日线', '4h', '收盘', 'KK', 'PP', '触压', '距 KK'],
                  [[f"<b>{_esc(r['name'])}</b>", _esc(r['code']), _n(r['score']),
                    _n(r.get('score_4h')), _n(r.get('close')), _n(r.get('KK')),
                    _n(r.get('PP')), f"{r.get('retest_count') or 0} 次",
                    f"{_n(_gap(r.get('close'), r.get('KK')))}%"] for r in rows])
    return _section(7, f"熊头遇压（重点 👀 · {len(rows)} 只）", body, notes.get('7'))


def s8_tips(f, narrative, report_date=None):
    tips = (narrative or {}).get('action_tips') or fallback_tips(f)
    lis = "".join(f"<li>{_esc(t)}</li>" for t in tips)
    return _section(8, f"{_esc(_md(report_date or next_report_date(f)))} 操作提示（最简版）",
                    f'<ol class="oplist">{lis}</ol>')


def s9_radar(f, notes):
    parts = []
    for side, lab, mark in (('long_trend', '多头榜', '🚀'), ('short_trend', '空头榜', '🆙')):
        rows = f['rank_radar'].get(side, [])
        risen = [r for r in rows if r.get('risen')]
        fallen = [r for r in rows if r.get('fallen')]
        new = [r for r in rows if r.get('rank_status') == 'new']
        for sub, data in ((f"{mark} 新贵（排名显著上升）", risen),
                          ("📉 掉队（排名显著下滑）", fallen),
                          ("🆕 新入榜", new)):
            if not data:
                continue
            parts.append(f"<h3>{lab} · {sub}（{len(data)} 只）</h3>"
                         + _table(['品种', '代码', '日线', '排名', '变化', '7 日轨迹', '解读边界'],
                                  [[f"<b>{_esc(r['name'])}</b>", _esc(r['code']), _n(r['score']),
                                    f"#{r.get('rank') or '—'}", _sign(r.get('rank_change')),
                                    _esc(" ".join(str(x.get('rank')) if x.get('rank') is not None else '-'
                                                  for x in (r.get('rank_history') or [])[-7:])), _esc(r.get('rank_note'))]
                                   for r in data]))
    if not parts:
        parts.append("<p>当日无显著排名变动（|变化| ≥ 3 视为显著）。</p>")
    return _section(9, "动量排名雷达 · 新贵与掉队（补充信号 👀）", "".join(parts), notes.get('9'))


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


def _sc(r):
    return (r or {}).get('score') or 0


def _gap(close, ref):
    return (close - ref) / ref * 100 if (close and ref) else None


def next_report_date(f):
    d = datetime.strptime(f['data_date'][:10], "%Y-%m-%d").date()
    return _next_weekday(d).isoformat()


# ---------------------------------------------------------------- 样式（对齐资料库成品）
CSS = """
  :root{
    --bg:#f4f6f9; --card:#ffffff; --line:#e6e9ef;
    --ink:#1c2430; --ink2:#5b6675; --ink3:#8a94a3;
    --red:#d92d20; --red-d:#b42318; --red-bg:#fef2f1;
    --green:#16855a; --green-bg:#edfdf4;
    --amber:#b45309; --amber-bg:#fffaf0;
    --blue:#1d4ed8; --blue-bg:#eff5ff;
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
  h2 .n{display:inline-flex;align-items:center;justify-content:center;width:26px;height:26px;border-radius:8px;background:var(--navy);color:#fff;font-size:14px;font-weight:800;flex:0 0 auto}
  .fold-section{padding:0;overflow:hidden}
  .fold-section>summary{display:flex;align-items:center;justify-content:space-between;gap:14px;padding:18px 22px;cursor:pointer;list-style:none}
  .fold-section>summary::-webkit-details-marker{display:none}
  .fold-section>summary:hover{background:#f8fafc}
  .fold-section>summary:focus-visible{outline:3px solid var(--blue);outline-offset:-3px;border-radius:13px}
  .fold-section>summary h2{margin:0;padding:0;border:0;flex:1;min-width:0;flex-wrap:nowrap;align-items:flex-start;font-size:17px}
  .fold-section>summary .n{margin-top:1px}
  .fold-label{display:inline-flex;align-items:center;gap:8px;flex-shrink:0;font-size:12px;font-weight:600;color:var(--blue)}
  .fold-arrow{font-size:19px;line-height:1;display:inline-block}
  .fold-hide,.fold-section[open] .fold-show{display:none}
  .fold-section[open] .fold-hide{display:inline}
  .fold-section[open] .fold-arrow{transform:rotate(180deg)}
  .fold-section[open]>summary{border-bottom:1px solid var(--line)}
  .fold-body{padding:14px 22px 20px}
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

  .bdg{display:inline-block;padding:1.5px 8px;border-radius:999px;font-size:11.8px;font-weight:800;white-space:nowrap}
  .bdg.r{background:var(--red-bg);color:var(--red-d);border:1px solid #fbd5d1}
  .bdg.g{background:var(--green-bg);color:var(--green);border:1px solid #c7f0dd}
  .bdg.a{background:var(--amber-bg);color:var(--amber);border:1px solid #fce3b4}
  .bdg.y{background:#fffbeb;color:#a16207;border:1px solid #fde68a}
  .bdg.b{background:var(--blue-bg);color:var(--blue);border:1px solid #cddffb}
  .bdg.n{background:#f1f3f7;color:var(--ink3);border:1px solid #e2e6ee}

  .ov{display:grid;grid-template-columns:repeat(2,1fr);gap:12px;margin-top:12px}
  .ovc{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
  .ovc .ot{font-size:13px;font-weight:800;color:var(--navy);margin-bottom:9px;letter-spacing:.3px}
  .ovrow{display:flex;justify-content:space-between;align-items:center;font-size:13.4px;padding:4px 0;border-bottom:1px dashed #eef1f6}
  .ovrow:last-child{border-bottom:none}
  .ovrow span:first-child{color:var(--ink3)}
  .ovrow span:last-child{font-weight:800;color:var(--ink)}

  .oneline{background:linear-gradient(135deg,#fff8f7,#fff);border:1px solid #fbd5d1;border-left:5px solid var(--red);border-radius:12px;padding:15px 18px;margin-top:14px;font-size:14.6px;color:var(--ink);line-height:1.75}
  .oneline b{color:var(--red-d)}

  .note{background:#f8fafc;border:1px dashed #d8dfe9;border-radius:10px;padding:11px 14px;font-size:13.2px;color:var(--ink2);margin:10px 0}
  .note b{color:var(--ink)}

  .oplist{counter-reset:o;list-style:none;margin:0}
  .oplist li{counter-increment:o;position:relative;padding:9px 0 9px 38px;border-bottom:1px dashed #eef1f6;font-size:14.2px;color:var(--ink2);line-height:1.7}
  .oplist li:last-child{border-bottom:none}
  .oplist li::before{content:counter(o);position:absolute;left:0;top:9px;width:24px;height:24px;border-radius:7px;background:var(--navy);color:#fff;font-size:12.5px;font-weight:800;display:flex;align-items:center;justify-content:center}

  .goldman-appendix img{display:block;width:100%;height:auto;margin:10px auto 20px;border-radius:10px;background:#0e1e33}
  .goldman-appendix h3{margin-top:18px}

  footer{margin-top:22px;padding:14px 16px;background:#eef1f6;border-radius:12px;font-size:12.6px;color:var(--ink3);line-height:1.7}

  @media(max-width:820px){
    .ov{grid-template-columns:1fr}
    h1{font-size:21px}
    .card{padding:16px 15px}
    .fold-section{padding:0}
    .fold-section>summary{padding:16px 15px;gap:10px}
    .fold-section>summary h2{font-size:16px}
    .fold-body{padding:12px 15px 16px}
    header{padding:20px 18px 18px}
  }
"""


def render_footer(f):
    return (f"<footer>口径：日线定趋势、4h 定节奏、板块定氛围、排名定动能。"
            f"持仓、价格与评分分开核验；SP平多、BP平空；4h破位仅指4h收盘低于4h EE，评分偏负不等于破位；回踩修复需核验本期平多后重新开多；当前持多不等于本期修复。报告日默认下一工作日，未内置节假日日历。"
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
        s1_overview(f, notes),
        s2_leaders(f, notes),
        s3_retest(f, notes),
        s4_divergence(f, notes),
        s5_short(f, notes),
        s6_turn(f, notes),
        s7_pressure(f, notes),
        s8_tips(f, narrative, report_date),
        s9_radar(f, notes),
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
    if not isinstance(notes, dict) or set(notes) - set('123456789'):
        raise ValueError('section_notes 必须使用1至9节号')
    for value in notes.values():
        text(value)
    for field in ('action_tips', 'cautions'):
        values = narrative.get(field, [])
        if not isinstance(values, list):
            raise ValueError(field + ' 必须是数组')
        for value in values:
            if field == 'cautions' and isinstance(value, dict):
                text(value.get('title')); text(value.get('body'))
            else:
                text(value)


def publish_report(facts, narrative=None, report_date=None, output_dir=None,
                   goldman_appendix=None):
    if facts.get('scan_version') != 2 or not facts.get('input_hash'):
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
    ap = argparse.ArgumentParser(description="期货看板每日总结 · HTML 渲染器")
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
