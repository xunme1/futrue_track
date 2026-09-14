# -*- coding: utf-8 -*-
"""每日日报合成渲染器（报告流水线第 2 步）。

读取 report_facts 产出的事实 JSON，叠加可选的叙事 JSON
（data/reports/narrative/narrative_YYYY-MM-DD.json，由 OpenClaw 龙虾 agent
按 docs/report_contract.md 契约撰写；缺失时以规则化模板兜底），渲染归档：

    data/reports/daily_report_YYYY-MM-DD.html   浅色主题单文件（内联 CSS）
    data/reports/daily_report_YYYY-MM-DD.json   facts + narrative 合并归档

模板结构对齐原版日报标准（futures-dash-package/sample-report 的九段式）：
表格骨架与"结论/定性"列由规则自动生成，叙事 JSON 只负责点评段落。

用法：
    python -m backend.pipeline.report_render                     # 渲染最新一份 facts
    python -m backend.pipeline.report_render --date 2026-09-14   # 指定报告日
"""
import argparse
import html
import json
from datetime import datetime
from pathlib import Path

from backend.core.config import DATA_DIR

REPORTS_DIR = DATA_DIR / "reports"
FACTS_DIR = REPORTS_DIR / "facts"
NARRATIVE_DIR = REPORTS_DIR / "narrative"

BUCKET_LABELS = {
    "long_trend": "多头", "short_trend": "空头", "long_to_short": "多转空",
    "long_to_short_warning": "多转空预警", "short_to_long": "空转多",
    "short_to_long_warning": "空转多预警",
    "short_pressure_warning": "遇压", "long_support_warning": "回踩",
}
OVERVIEW_ORDER = ["long_trend", "short_trend", "long_to_short", "long_to_short_warning",
                  "short_to_long", "short_pressure_warning", "long_support_warning"]

DASHBOARD_URL = "http://110.42.220.207:8000/"


def _esc(s) -> str:
    return html.escape("" if s is None else str(s))


def _num(v, nd=2):
    if v is None:
        return "—"
    return f"{v:.{nd}f}"


def _md(date_str) -> str:
    """2026-09-14 / 2026-09-14 15:00 → 09-14"""
    s = str(date_str or "")
    return s[5:10] if len(s) >= 10 else s


def _sig(sig) -> str:
    """{type,date} → 'SK @09-14'"""
    if not sig:
        return "—"
    return f"{sig.get('type', '?')} @{_md(sig.get('date'))}"


def _names(rows, maxn=0) -> str:
    items = [f"{r.get('name') or ''} {r.get('key') or ''}".strip() for r in rows]
    if maxn and len(items) > maxn:
        return "、".join(items[:maxn]) + f" 等 {len(items)} 只"
    return "、".join(items)


def _table(headers, rows) -> str:
    head = "".join(f"<th>{_esc(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def _note_or_fallback(narrative, field, fallback_html) -> str:
    text = (narrative or {}).get(field)
    if isinstance(text, str) and text.strip():
        return f"<p class='note'>{_esc(text)}</p>"
    return fallback_html


def _by_sector(rows) -> str:
    """把品种列表按板块聚合：黑色系（螺纹 rb、热卷 hc）。"""
    groups = {}
    for r in rows:
        groups.setdefault(r.get("sector") or "其他", []).append(r.get("name") or r.get("key"))
    return "；".join(f"{s}（{'、'.join(v)}）" for s, v in groups.items())


# ---------- 兜底叙事（narrative 缺失时的规则化直述） ----------

def _day_tone(facts):
    """从两口径计数变化给当日定性（有前日基线时才输出）。"""
    if not facts["header"].get("has_previous"):
        return None
    o1, o4 = facts["overview"]["1d"], facts["overview"]["4h"]
    d_long = o1["long_trend"]["count"] - o1["long_trend"]["previous_count"]
    d_short = o1["short_trend"]["count"] - o1["short_trend"]["previous_count"]
    l4, s4 = o4["long_trend"]["count"], o4["short_trend"]["count"]
    acts = facts.get("daily_actions") or {}
    if d_short >= 3 and s4 > l4:
        return f"空头扩散日（日线空头 +{d_short}，4h 空头 {s4} 反超多头 {l4}）"
    if len(acts.get("SP", [])) >= 5 and not acts.get("SK"):
        return f"多头撤退日（日线 {len(acts['SP'])} 只集体平多，未见反手开空）"
    if d_long >= 3 and l4 > s4:
        return f"多头进攻日（日线多头 +{d_long}，4h 多 {l4} : 空 {s4}）"
    if d_short >= 3:
        return f"空头增强日（日线空头 +{d_short}）"
    if d_long >= 3:
        return f"多头增强日（日线多头 +{d_long}）"
    return "多空均衡日（两口径计数无显著变化）"


def fallback_one_liner(facts) -> str:
    h = facts["header"]
    o1, o4 = facts["overview"]["1d"], facts["overview"]["4h"]
    tone = _day_tone(facts)
    parts = []
    if tone:
        parts.append(f"<b>{_esc(tone)}</b>")
    acts = facts.get("daily_actions") or {}
    if acts.get("SK"):
        parts.append(f"日线 <b>{len(acts['SK'])} 只新开空</b>（{_esc(_names(acts['SK'], 10))}）")
    if acts.get("SP"):
        parts.append(f"<b>{len(acts['SP'])} 只平多</b>（{_esc(_names(acts['SP'], 10))}）")
    parts.append(f"4h 多 {o4['long_trend']['count']} / 空 {o4['short_trend']['count']}"
                 f"（前日 {o4['long_trend']['previous_count']}/{o4['short_trend']['previous_count']}）")
    dv = facts["divergence"]["rated"]
    red = [r for r in dv if r["level"] == "🔴"]
    if red:
        parts.append(f"⭐ 分歧 🔴 {len(red)} 只：{_esc(_names(red, 8))}")
    if not parts:
        parts.append(f"数据基准 {_esc(h['data_date_1d'])} 收盘。")
    return "。".join(parts) + "。"


def fallback_core_judgments(facts):
    out = []
    acts = facts.get("daily_actions") or {}
    o4 = facts["overview"]["4h"]
    tone = _day_tone(facts)
    if tone:
        out.append(("当日定性", f"{tone}。"))
    if acts.get("SK"):
        reso = [r["name"] for r in acts["SK"] if r["key"] in facts.get("short_resonance", [])]
        out.append((f"日线 {len(acts['SK'])} 只新开空（SK）⚠️",
                    f"{_by_sector(acts['SK'])}。其中 4h 同步持空的双级共振：{'、'.join(reso) or '无'}。"))
    if acts.get("SP"):
        out.append((f"日线 {len(acts['SP'])} 只平多离场（SP）",
                    f"{_by_sector(acts['SP'])}。若前日在预警名单内 = 预警兑现，不是洗盘。"))
    if acts.get("BK"):
        out.append((f"日线 {len(acts['BK'])} 只新开多（BK）", f"{_names(acts['BK'])}。"))
    v = facts["verdict_4h"]
    out.append(("4h 转折裁决",
                f"多转空 {o4['long_to_short']['count']} 只：A 双级共振空 {len(v['A'])}（{_names(v['A'], 6)}）、"
                f"B 已离场 {len(v['B'])}（不追空，等日线 SK）、C 回踩 {len(v['C'])}（只盯日线 EE）；"
                f"空转多 {len(v['D1']) + len(v['D2']) + len(v['D3'])} 只；E 重新 BK {len(v['E'])} 只（{_names(v['E'], 4)}）。"))
    dv = facts["divergence"]
    red = [r for r in dv["rated"] if r["level"] == "🔴"]
    if red:
        sectors = sorted({r.get("sector") or "其他" for r in red})
        out.append((f"⭐ 分歧严重 🔴 {len(red)} 只",
                    f"集中在 {'、'.join(sectors)}：{_names(red, 8)} —— 多单不持有 / 先撤；修复信号 = 4h 重新 BK。"))
    warn = facts["buckets_1d"]["long_to_short_warning"]
    if warn:
        out.append((f"日线多转空预警 {len(warn)} 只",
                    f"{_names(warn, 8)}：收盘破 EE 即离场，预警兑现勿当洗盘。"))
    return out


def fallback_tips(facts):
    tips = []
    dv = facts["divergence"]["rated"]
    red_sectors = {}
    for r in dv:
        if r["level"] == "🔴":
            red_sectors.setdefault(r.get("sector") or "其他", []).append(r)
    for sector, rows in red_sectors.items():
        tips.append(f"{sector}：{_names(rows)} 命中 {'＋'.join(rows[0]['hits'])} 等判据，多单不持有 / 先撤。")
    for r in dv:
        if r["level"] == "🟠":
            tips.append(f"{r['name']} {r['key']}：{'＋'.join(r['hits'])}，多单减半，破 EE 走。")
    for row in facts["key_levels"][:3]:
        tips.append(f"{row['name']} {row['key']}：{row['status']}（收 {_num(row.get('close'))} / EE {_num(row.get('EE'))}），多单警戒线。")
    for row in facts["verdict_4h"]["C"][:3]:
        tips.append(f"{row['name']} {row['key']}：4h 多转空但日线仍多 = 回踩，盯日线 EE {_num(row.get('EE_1d'))}，不做空。")
    for row in facts["verdict_4h"]["E"][:2]:
        tips.append(f"{row['name']} {row['key']}：4h {_md(row.get('bk_date'))} 重新 BK，回踩结束信号。")
    for row in facts["leader_watch"]["bear_pressure"][:2]:
        tips.append(f"{row['name']} {row['key']}：熊头遇压，反弹进 {_num(row.get('KK'))}-{_num(row.get('PP'))} 带可加空。")
    if not facts["buckets_1d"]["short_to_long"]:
        tips.append("全场日线空转多为 0，不要抄底。")
    return tips[:9]


# ---------- 各段渲染 ----------

def render_header(facts, narrative) -> str:
    h = facts["header"]
    rd = facts["report_date"]
    o1, o4 = facts["overview"]["1d"], facts["overview"]["4h"]
    prev_txt = (f"{_md(h['previous_date_1d'])}（1d {o1['long_trend']['previous_count']} 多 / "
                f"{o1['short_trend']['previous_count']} 空；4h {o4['long_trend']['previous_count']} 多 / "
                f"{o4['short_trend']['previous_count']} 空）") if h.get("previous_date_1d") else "无（首次建基线）"

    def cell(d):
        cur, prev = d["count"], d["previous_count"]
        return f"<b>{prev} → {cur}</b>" if (prev or cur) and prev != cur else str(cur)

    rows = []
    for tf, label in (("1d", "1d 日线"), ("4h", "4h")):
        rows.append([f"<b>{label}</b>"] + [cell(facts["overview"][tf][b]) for b in OVERVIEW_ORDER])
    overview = _table(["口径"] + [BUCKET_LABELS[b] for b in OVERVIEW_ORDER], rows)

    one_liner = (narrative or {}).get("one_liner")
    one_html = f"<b>{_esc(one_liner)}</b>" if one_liner else fallback_one_liner(facts)
    return f"""
<div class="banner">
  <h1>期货看板每日总结 · {_esc(rd[5:])} 作战地图</h1>
  <div class="meta">
    <span><b>数据基准</b>：{_esc(h['data_date_1d'])} 收盘 — 1d {_esc(h['generated_at_1d'][11:16])} / 4h {_esc(h['generated_at_4h'][11:16])} 生成</span>
    <span><b>对比基准</b>：{prev_txt}</span>
    <span><b>看板</b>：{DASHBOARD_URL}</span>
    <span><b>本次运行</b>：{_esc(facts.get('created_at', '')[:16].replace('T', ' '))} 生成（报告日期 = 下一交易日 {_esc(rd)}）</span>
  </div>
</div>
{overview}
<blockquote>📌 一句话：{one_html}</blockquote>"""


def render_core(facts, narrative) -> str:
    cj = (narrative or {}).get("core_judgments")
    nums = "1️⃣2️⃣3️⃣4️⃣5️⃣6️⃣7️⃣"
    if isinstance(cj, list) and cj:
        cards = "".join(
            f"<div class='card'><div class='card-title'>{nums[i]} {_esc(c.get('title') if isinstance(c, dict) else '')}</div>"
            f"<div class='card-body'>{_esc(c.get('body') if isinstance(c, dict) else c)}</div></div>"
            for i, c in enumerate(cj))
        title = f"一、核心判断（{len(cj)} 条）"
    else:
        fb = fallback_core_judgments(facts)
        cards = "".join(
            f"<div class='card'><div class='card-title'>{nums[i]} {_esc(t)}</div>"
            f"<div class='card-body'>{_esc(b)}</div></div>" for i, (t, b) in enumerate(fb))
        title = f"一、核心判断（{len(fb)} 条 · 规则直述版）"
    return f"<h2>{title}</h2><div class='cards'>{cards}</div>"


def render_divergence(facts, narrative) -> str:
    dv = facts["divergence"]
    notes = (narrative or {}).get("divergence_notes") or {}
    data_date = facts["header"]["data_date_1d"]
    parts = ["<h2>二、⭐ 分歧严重 · 特别关注名单（三判据）</h2>",
             "<p class='muted'>判据 1：同板块多只 4h 多头结束且偏弱（即使日线仍多）→ 板块级分歧；"
             "判据 2：日线偏弱跌破平台（即使未开空）→ 特别关注；"
             "判据 3：4h 偏弱破平台 ＋ 板块集体偏弱（日线仍多）→ 板块级分歧。"
             "🔴 多单不持有/先撤；🟠 减半、破 EE 走；🟡 只盯不做。修复信号 = 4h 重新 BK。</p>"]

    rated = dv["rated"]
    if rated:
        hot = [r for r in rated if r["level"] in ("🔴", "🟠")]
        calm = [r for r in rated if r["level"] == "🟡"]
        if hot:
            rows = [[r["level"], _esc(r["name"]), _esc(r["key"]), _esc(r.get("sector") or "—"),
                     _esc("＋".join(r["hits"]))] for r in hot]
            parts.append(_table(["评级", "品种", "代码", "板块", "命中判据"], rows))
        if calm:
            parts.append(f"<p class='muted'>🟡 观察（只盯不做，{len(calm)} 只）：{_esc(_names(calm, 20))}</p>")

    # A 档 · 板块联动（品种 | 日线信号 | 4h 信号 | 偏弱结束？ | 关键位）
    if dv["j1_sector_linkage"]:
        parts.append("<h3>A 档 · 板块联动</h3>")
        for sector, members in dv["j1_sector_linkage"].items():
            weak_n = sum(1 for m in members if m.get("weak_4h"))
            parts.append(f"<h4>{_esc(sector)}：{len(members)} 只 4h 多头结束（偏弱 {weak_n} 只）</h4>")
            note = notes.get(sector) or notes.get(f"A:{sector}")
            if note:
                parts.append(f"<p class='note'>{_esc(note)}</p>")
            rows = []
            for m in members:
                c4, ee4 = m.get("close_4h"), m.get("EE_4h")
                if c4 is not None and ee4 is not None and c4 < ee4:
                    lv = f"收 {_num(c4)} ＜ 4h EE {_num(ee4)} <b>已破</b>"
                elif (m.get("signal_4h") or {}).get("type") == "SK":
                    lv = "<b>板块领先开空</b>"
                elif m.get("note"):
                    lv = "桶外品种，仅定性"
                else:
                    lv = "—"
                rows.append([f"<b>{_esc(m['name'])}</b>", _esc(_sig(m.get("signal_1d"))),
                             _esc(_sig(m.get("signal_4h"))),
                             ("✅ 偏弱 −" if m.get("weak_4h") else "⚪ 中性") +
                             (_esc(_num(m.get("score_4h"))) if m.get("score_4h") is not None else ""),
                             lv])
            parts.append(_table(["品种", "日线信号", "4h 信号", "偏弱结束？", "关键位"], rows))

    # B 档 · 日线偏弱跌破平台（品种 | 日线破位情况 | 偏弱证据 | 评级）
    if dv["j2_daily_break"]:
        parts.append("<h3>B 档 · 日线偏弱跌破平台（未开空）→ 特别关注 ⚠️</h3>")
        if notes.get("B"):
            parts.append(f"<p class='note'>{_esc(notes['B'])}</p>")
        level_of = {r["key"]: r["level"] for r in rated}
        rows = []
        for r in dv["j2_daily_break"]:
            close, dd, ee = r.get("close"), r.get("DD"), r.get("EE")
            hit = r["break_status"]
            base = dd if "DD" in hit else ee
            brk = f"收 {_num(close)} <b>{hit} {_num(base)}</b>"
            if hit == "破DD" and ee is not None and close is not None and close >= ee:
                brk += f"，贴 EE {_num(ee)}（差 {(close - ee) / close * 100:.1f}%）"
            elif "贴" in hit:
                brk += f"（差 {abs(close - base) / close * 100:.1f}%）"
            ev = []
            if r.get("score") is not None:
                ev.append(f"score {_num(r['score'])}")
            if r.get("in_warning"):
                ev.append("<b>已挂日线预警</b>")
            if r.get("weak"):
                ev.append("偏弱")
            rows.append([f"<b>{_esc(r['name'])}</b>", brk, _esc("；".join(ev) if ev else "—").replace("&lt;b&gt;", "<b>"),
                         level_of.get(r["key"], "🟡") + (" 确认" if r.get("confirmed") else " 观察")])
        parts.append(_table(["品种", "日线破位情况", "偏弱证据", "评级"], rows))

    # C 档 · 4h 偏弱破平台 + 板块集体偏弱（板块 | 品种明细 | 日线状态 | 评级）
    if dv["j3_4h_break"]:
        parts.append("<h3>C 档 · 4h 偏弱跌破平台 ＋ 板块集体偏弱（日线仍多）→ 板块级分歧 ⚠️</h3>")
        if notes.get("C"):
            parts.append(f"<p class='note'>{_esc(notes['C'])}</p>")
        level_of = {r["key"]: r["level"] for r in rated}
        sectors = {}
        for r in dv["j3_4h_break"]:
            sectors.setdefault(r.get("sector") or "其他", []).append(r)
        rows = []
        for sector, members in sectors.items():
            detail = "／ ".join(f"<b>{_esc(m['name'])}</b> {_num(m.get('score'))}（{_esc(m['break_status'])}）"
                               for m in members)
            lv = "🔴" if any(level_of.get(m["key"]) == "🔴" for m in members) else "🟠"
            rows.append([f"<b>{_esc(sector)}</b>", detail, "日线仍多", lv])
        parts.append(_table(["板块", "品种（4h score ／ 破位情况）", "日线状态", "评级"], rows))
    return "".join(parts)


def _long_tiers(items, warn_keys):
    """看多主线梯队划分（对齐原版：龙头/中军稳/中军弱/橡胶系/逆势新增/农产品/其他）。"""
    tiers = {"🐲 龙头": [], "中军（稳）": [], "中军（弱）": [], "橡胶系": [],
             "逆势新增": [], "农产品": [], "其他": []}
    for r in items:
        sector, score = r.get("sector"), r.get("score") or 0
        entry = r.get("score_entry_date") or ""
        weak_4h = r.get("pos_4h") != 1
        if score >= 8:
            tiers["🐲 龙头"].append(r)
        elif sector == "橡胶系":
            tiers["橡胶系"].append(r)
        elif sector in ("油脂粕", "农软"):
            tiers["农产品"].append(r)
        elif entry >= "2026-09-07":
            tiers["逆势新增"].append(r)
        elif r["key"] in warn_keys or weak_4h:
            tiers["中军（弱）"].append(r)
        elif sector in ("能源链", "芳烃能化", "有色", "贵金属", "股指"):
            tiers["中军（稳）"].append(r)
        else:
            tiers["其他"].append(r)
    return tiers


def render_long(facts, narrative) -> str:
    items = facts["buckets_1d"]["long_trend"]
    warn_keys = {r["key"] for r in facts["buckets_1d"]["long_to_short_warning"]}
    tiers = _long_tiers(items, warn_keys)
    tier_status = {
        "🐲 龙头": "4h 持多、回踩未破 → 回踩即低吸",
        "中军（稳）": "日线仍多、4h 持多 → 持有",
        "中军（弱）": "4h 预警/已平多 → 回踩升级，盯日线 EE",
        "橡胶系": "4h 集体偏弱 → 最脆，盯日线 EE",
        "逆势新增": "新近开多 → 弱多观察",
        "农产品": "4h 偏弱 → 盯日线 EE 定去留",
        "其他": "中性观察",
    }
    parts = [f"<h2>三、看多主线（日线多头 {len(items)} 只）</h2>",
             _note_or_fallback(narrative, "long_notes", "")]
    rows = []
    for tier, group in tiers.items():
        if not group:
            continue
        group = sorted(group, key=lambda r: -(r.get("score") or 0))
        names_txt = "／ ".join(f"<b>{_esc(r['key'])} {_esc(r['name'])}</b> {_num(r.get('score'))}"
                              for r in group)
        weak4 = sum(1 for r in group if r.get("pos_4h") != 1)
        status = tier_status[tier]
        if weak4:
            status += f"（4h 转弱 {weak4}/{len(group)}）"
        rows.append([tier, names_txt, _esc(status)])
    parts.append(_table(["梯队", "品种（日线 score）", "状态"], rows))
    return "".join(parts)


def render_short(facts, narrative) -> str:
    items = sorted(facts["buckets_1d"]["short_trend"], key=lambda r: (r.get("score") or 0))
    data_date = facts["header"]["data_date_1d"]
    resonance = set(facts.get("short_resonance") or [])
    pressure = {r["key"]: r for r in facts["leader_watch"]["bear_pressure"]}
    parts = [f"<h2>四、看空主线（日线空头 {len(items)} 只）</h2>",
             _note_or_fallback(narrative, "short_notes", "")]
    rows = []
    for r in items:
        key = r["key"]
        entry = r.get("score_entry_date") or ""
        st = [f"{_md(entry)} 空至今"]
        if key in pressure:
            st.append(f"遇压 {pressure[key].get('retest_count') or 0} 次")
        if entry == data_date:
            kind = "🔴 新熊·双级共振" if key in resonance else "🔴 新熊·4h 未确认"
        elif key in resonance:
            kind = "🟢 老熊·双空持有"
        else:
            kind = "⚪ 4h 未同步，动能待确认"
        rows.append([f"<b>{_esc(r['name'])} {_num(r.get('score'))}</b>", _esc("；".join(st)), _esc(kind)])
    parts.append(_table(["品种（score）", "状态", "性质"], rows))
    return "".join(parts)


def render_verdict(facts, narrative) -> str:
    v = facts["verdict_4h"]
    data_date = facts["header"]["data_date_1d"]
    warn_keys = {r["key"] for r in facts["buckets_1d"]["long_to_short_warning"]}
    parts = ["<h2>五、阶段性转折要注意（4h 转折 × 日线裁决）</h2>",
             _note_or_fallback(narrative, "transition_notes",
                               "<p class='muted'>铁律：4h 转折一律以日线趋势为最终裁决，不能直接当转势。</p>")]

    if v["A"]:
        parts.append(f"<h3>A. 真·双级别转空（4h ＋ 1d 共振）→ 可跟空 ✅ · {len(v['A'])} 只</h3>")
        rows = []
        for r in v["A"]:
            s1, s4 = r.get("last_signal_1d") or {}, r.get("last_signal_4h") or {}
            if s1.get("date") == data_date and (s4.get("date") or "").startswith(data_date):
                note = "当日双级新开，信号最新最干净"
            elif (s4.get("date") or "") > (s1.get("date") or ""):
                note = "老空重新加速"
            else:
                note = "—"
            rows.append([f"<b>{_esc(r['name'])}</b>", _esc(_sig(s1)), _esc(_sig(s4)), _esc(note)])
        parts.append(_table(["品种", "1d 信号", "4h 信号", "说明"], rows))

    if v["B"]:
        parts.append(f"<h3>B. 4h 已平多 ＋ 日线已平多（POS=0）→ 双级别已离场，趋势偏空 🚨 · {len(v['B'])} 只</h3>")
        rows = []
        for r in v["B"]:
            close1, ee1 = r.get("close_1d"), r.get("EE_1d")
            if close1 is not None and ee1 is not None:
                ee_txt = f"{_num(ee1)}（收 {_num(close1)}，<b>已跌破</b>）" if close1 < ee1 else f"{_num(ee1)}（收 {_num(close1)}）"
            else:
                ee_txt = "—"
            rows.append([f"<b>{_esc(r['name'])}</b>", _esc(_sig(r.get("last_signal_1d"))),
                         _esc(_sig(r.get("last_signal_4h"))), ee_txt])
        parts.append(_table(["品种", "1d 信号", "4h 信号", "日线 EE（生死线）"], rows))
        parts.append("<p class='muted'>B 组 = 日线多头逻辑已破坏。不是回踩，是离场。等日线正式 SK 才谈做空，别在反弹里抢。</p>")

    if v["C"]:
        parts.append(f"<h3>C. 4h 平多但日线仍持多（= 强势回踩，非做空）→ 只盯日线 EE 👀 · {len(v['C'])} 只</h3>")
        rows = []
        for r in v["C"]:
            st = f"多 {_num(r.get('score_1d'))}"
            if r["key"] in warn_keys:
                st += "（<b>已挂日线预警</b>）"
            close1, ee1 = r.get("close_1d"), r.get("EE_1d")
            ee_txt = f"<b>{_num(ee1)}</b>（收 {_num(close1)}"
            if close1 is not None and ee1 is not None:
                gap = (close1 - ee1) / close1 * 100
                ee_txt += f"，差 {gap:.1f}%{' 🚨' if gap < 1 else ''}）" if gap >= 0 else "，<b>已跌破</b>）"
            else:
                ee_txt += "）"
            rows.append([f"<b>{_esc(r['name'])}</b>", _esc(_md(r.get("signal_date"))), st, ee_txt])
        parts.append(_table(["品种", "4h 转折时点", "日线状态", "日线 EE"], rows))
        parts.append("<p class='muted'>C 组是「回踩」不是「转空」；已挂日线预警的破 EE 立刻走。</p>")

    d_all = [("D1 · 可跟多（日线也 BK/持多）", v["D1"]), ("D2 · 仅 BP 未确认（勿抢跑）", v["D2"]),
             ("D3 · 日线仍空的反弹（不抢跑）", v["D3"])]
    if any(g for _, g in d_all):
        for title, group in d_all:
            if group:
                txt = "、".join(f"<b>{_esc(r['name'])}</b>（4h {_esc(_md(r.get('signal_date')))} 转多，日线 {_esc(_sig(r.get('last_signal_1d')))}）"
                               for r in group)
                parts.append(f"<h4>{_esc(title)}</h4><p>{txt}</p>")
    else:
        parts.append("<h3>D. 4h 空转多：<b>0 只</b> ❌</h3>"
                     "<p class='muted'>全线没有任何 4h 翻多信号 —— 不要抢反弹。</p>")

    if v["E"]:
        parts.append(f"<h3>E. 4h 重新开多（回踩结束样板）✅ · {len(v['E'])} 只</h3><ul>"
                     + "".join(f"<li><b>{_esc(r['name'])} {_esc(r['key'])}</b>：4h {_esc(_md(r.get('bk_date')))} 重新 BK ＋ 日线持多</li>"
                               for r in v["E"]) + "</ul>")
    return "".join(parts)


def render_leader_watch(facts, narrative) -> str:
    lw = facts["leader_watch"]
    data_date = facts["header"]["data_date_1d"]

    def is_hot(r):
        dates = r.get("retest_dates") or []
        return (r.get("score") or 0) >= 5 or (dates and dates[-1] == data_date)

    leaders = [r for r in lw["leader_retest"] if is_hot(r)]
    leaders_rest = [r for r in lw["leader_retest"] if not is_hot(r)]
    parts = ["<h2>六、龙头回踩（重点 👀）</h2>",
             "<p class='muted'>规则：日线趋势完好 ＋ 回踩不破 = 持有/低吸；跌破日线 EE 才谈离场；4h 重新 BK = 回踩结束信号。</p>",
             _note_or_fallback(narrative, "leader_notes", "")]
    if leaders:
        rows = []
        for r in leaders:
            close, dd, ee, pp = r.get("close"), r.get("DD"), r.get("EE"), r.get("PP")
            retest = f"1d 回踩 {r.get('retest_count') or 0} 次（{_esc('、'.join(_md(x) for x in (r.get('retest_dates') or [])[-3:]))}）"
            pos4 = r.get("pos_4h")
            retest += "，4h 持多" if pos4 == 1 else ("，<b>4h 已平多</b>" if pos4 == 0 else "，4h 持空")
            lv = f"收 {_num(close)}"
            if pp is not None and close is not None and close > pp:
                lv += f" ＞ PP {_num(pp)}"
                concl = "✅ 收在 PP 上方，回踩即低吸"
            elif dd is not None and close is not None and close < dd:
                lv += f" ＜ DD {_num(dd)} <b>已破平台</b>"
                concl = "🚨 破 DD，盯 EE 定去留"
            else:
                lv += f"（DD {_num(dd)} / EE {_num(ee)}）"
                concl = "✅ 线上，持有" if pos4 == 1 else "⚠️ 4h 转弱，盯日线 EE"
            rows.append([f"<b>{_esc(r['name'])}</b>", retest, lv, concl])
        parts.append(_table(["品种", "回踩情况", "关键位", "结论"], rows))
    if leaders_rest:
        parts.append(f"<p class='muted'>其余回踩多头 {len(leaders_rest)} 只（非龙头且当日未触线）：{_esc(_names(leaders_rest, 20))}</p>")

    bears = [r for r in lw["bear_pressure"] if is_hot(r) or (r.get("score") or 0) <= -1.5]
    bears_rest = [r for r in lw["bear_pressure"] if r not in bears]
    parts.append("<h2>七、熊头遇压（重点 👀）</h2>")
    parts.append("<p class='muted'>规则：熊头反弹到压力带 = 空单持有/加空区；站上 PP 防反抽；跌破前低才是加速。</p>")
    parts.append(_note_or_fallback(narrative, "pressure_notes", ""))
    if bears:
        rows = []
        for r in bears:
            close, kk, pp = r.get("close"), r.get("KK"), r.get("PP")
            band = f"{_num(kk)} – {_num(pp)}（收 {_num(close)}）"
            if close is not None and kk is not None and close < kk:
                concl = "✅ 收在 KK 下方，空头顺畅"
            elif close is not None and pp is not None and close > pp:
                concl = "⚠️ 站上 PP，减仓防反抽"
            else:
                concl = "🔴 正在压力带内 = 加空区"
            rows.append([f"<b>{_esc(r['name'])}</b>", f"{r.get('retest_count') or 0} 次（{_esc('、'.join(_md(x) for x in (r.get('retest_dates') or [])[-3:]))}）",
                         band, concl])
        parts.append(_table(["品种", "触压次数", "压力带（KK–PP）", "结论"], rows))
    if bears_rest:
        parts.append(f"<p class='muted'>其余遇压空头 {len(bears_rest)} 只：{_esc(_names(bears_rest, 20))}</p>")
    return "".join(parts)


def render_tips(facts, narrative) -> str:
    tips = (narrative or {}).get("action_tips")
    if not (isinstance(tips, list) and tips):
        tips = fallback_tips(facts)
    lis = "".join(f"<li>{_esc(t)}</li>" for t in tips)
    return f"<h2>八、{_esc(facts['report_date'][5:])} 操作提示（最简版）</h2><ol class='tips'>{lis}</ol>"


def render_radar(facts, narrative) -> str:
    rr = facts["rank_radar"]
    parts = ["<h2>九、动量排名雷达 · 新贵与掉队（补充信号 👀）</h2>",
             "<p class='muted'>|排名变化| ≥ 3 视为显著；连续爬升权重更高；新入榜必点名。"
             "排名是独立于 score 的第二证据源，需与信号交叉印证（✅ = 已互证）。</p>",
             _note_or_fallback(narrative, "rank_notes", "")]

    # 交叉印证索引
    cross = {}
    for r in facts["verdict_4h"]["E"]:
        cross[r["key"]] = "4h 重新 BK ✅"
    for r in facts["divergence"]["j3_4h_break"]:
        cross.setdefault(r["key"], "4h 偏弱破位 ✅")
    for r in facts["divergence"]["j2_daily_break"]:
        cross.setdefault(r["key"], "日线破位/贴线 ⚠️")
    for r in facts["buckets_1d"]["long_to_short_warning"]:
        cross.setdefault(r["key"], "日线预警 ⚠️")
    for r in (facts.get("daily_actions") or {}).get("SK", []):
        cross.setdefault(r["key"], "当日开空 ✅")
    for r in facts["leader_watch"]["leader_retest"]:
        cross.setdefault(r["key"], f"回踩 {r.get('retest_count') or 0} 次未破")

    def radar_rows(rows_data, sign):
        rows = []
        for r in rows_data:
            hist = "→".join(str(h.get("rank")) for h in (r.get("rank_history") or [])[-7:])
            chg = r.get("rank_change") or 0
            qual = []
            if (r.get("climb_days") or 0) >= 2:
                qual.append(f"连升 {r['climb_days']} 日")
            if r.get("rank_status") == "new":
                qual.append("新入榜")
            rows.append([f"<b>{_esc(r['name'])}</b>",
                         f"#{r.get('previous_rank') or '—'} → <b>#{r.get('rank')}</b>（{sign}{abs(chg)}）",
                         _esc(hist), _esc(cross.get(r["key"], "—")), _esc("；".join(qual))])
        return _table(["品种", "排名变化", "7 日轨迹", "交叉印证", "定性"], rows)

    for title, data, sign in (("🚀 多头榜新贵", rr["long_risen"], "+"), ("📉 多头榜掉队", rr["long_fallen"], "−"),
                              ("🆙 空头榜新贵（排名上升）", rr["short_risen"], "+"),
                              ("📉 空头榜掉队", rr["short_fallen"], "−")):
        if data:
            parts.append(f"<h3>{title}</h3>" + radar_rows(data, sign))
    if rr["new_entries"]:
        parts.append("<h3>🆕 新入榜</h3>"
                     + _table(["品种", "代码", "名次", "板块", "交叉印证"],
                              [[_esc(r["name"]), _esc(r["key"]), str(r.get("rank")),
                                _esc(r.get("sector") or "—"), _esc(cross.get(r["key"], "—"))]
                               for r in rr["new_entries"]]))
    return "".join(parts)


def render_footer(facts) -> str:
    blind = facts.get("pos_zero_blindspot") or []
    blind_txt = ""
    if blind:
        blind_txt = ("<p class='muted'>POS=0 桶外品种（4h close/score 缺失，仅定性）："
                     + _esc("、".join(f"{r['name']} {r['key']}" for r in blind[:20]))
                     + ("……" if len(blind) > 20 else "") + "</p>")
    return f"""<h2>口径说明</h2>
<ul class='muted'>
<li>1d POS=1 多头 / POS=−1 空头；多转空 = 红转蓝（确认 = 开空 SK）；空转多 = 蓝转红（确认 = 开多 BK）；回踩支撑 = 9 根内触碰 DD/EE，遇压 = 9 根内触碰 KK/PP；<b>4h 转折一律以日线趋势为最终裁决</b>。</li>
<li>EE = 多头生死线（跌破 = 多头逻辑破坏）；DD = 平台下沿；KK/PP = 空头压力带（反弹进带 = 加空区，站上 PP 防反抽）。</li>
<li>数据源：{DASHBOARD_URL} 期货筛选看板（米筐 RQData 240 分钟 K 线 + 日线）。facts 生成于 {_esc(facts.get('created_at'))}。</li>
</ul>{blind_txt}"""


CSS = """
:root { --ink:#1f2933; --muted:#6b7280; --line:#e5e7eb; --bg:#f6f7f9; }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--ink);
  font:14px/1.7 -apple-system, "PingFang SC", "Noto Sans CJK SC", "Microsoft YaHei", sans-serif; }
.wrap { max-width: 1080px; margin: 0 auto; padding: 24px 20px 60px; }
.banner { background:#1f2933; color:#f9fafb; border-radius:12px; padding:22px 26px; margin-bottom:18px; }
.banner h1 { margin:0 0 10px; font-size:22px; }
.banner .meta { display:flex; flex-direction:column; gap:4px; color:#cbd2d9; font-size:13px; }
blockquote { margin:12px 0; background:#fff8e6; border-left:4px solid #f0b429; border-radius:6px;
  padding:12px 16px; }
h2 { font-size:18px; margin:30px 0 10px; padding-left:10px; border-left:5px solid #f0b429; }
h3 { font-size:15px; margin:18px 0 8px; }
h4 { font-size:14px; margin:12px 0 6px; color:#374151; }
table { width:100%; border-collapse:collapse; background:#fff; border-radius:8px; overflow:hidden;
  margin:8px 0 4px; font-size:13px; }
th, td { border-bottom:1px solid var(--line); padding:7px 10px; text-align:left; }
th { background:#eef1f4; color:#374151; font-weight:600; white-space:nowrap; }
tr:last-child td { border-bottom:none; }
.cards { display:grid; gap:10px; }
.card { background:#fff; border:1px solid var(--line); border-radius:10px; padding:12px 16px; }
.card-title { font-weight:700; margin-bottom:4px; }
.muted { color:var(--muted); font-size:12.5px; }
.note { background:#eef6ff; border:1px solid #c5dcf5; border-radius:8px; padding:10px 14px; }
ol.tips li { margin:4px 0; }
"""


def render_html(facts, narrative) -> str:
    body = "".join([
        render_header(facts, narrative),
        render_core(facts, narrative),
        render_divergence(facts, narrative),
        render_long(facts, narrative),
        render_short(facts, narrative),
        render_verdict(facts, narrative),
        render_leader_watch(facts, narrative),
        render_tips(facts, narrative),
        render_radar(facts, narrative),
        render_footer(facts),
    ])
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>期货看板每日总结 · {_esc(facts['report_date'])} 作战地图</title>
<style>{CSS}</style></head>
<body><div class="wrap">{body}</div></body></html>"""


def _load(path: Path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main():
    ap = argparse.ArgumentParser(description="每日日报合成渲染器")
    ap.add_argument("--date", help="报告日期 YYYY-MM-DD（默认取最新 facts）")
    args = ap.parse_args()

    if args.date:
        facts_file = FACTS_DIR / f"facts_{args.date}.json"
    else:
        candidates = sorted(FACTS_DIR.glob("facts_*.json"))
        if not candidates:
            raise SystemExit("[错误] 无 facts 产物，先运行 python -m backend.pipeline.report_facts")
        facts_file = candidates[-1]
    if not facts_file.exists():
        raise SystemExit(f"[错误] 找不到 {facts_file}")
    facts = _load(facts_file)
    report_date = facts["report_date"]

    narrative_file = NARRATIVE_DIR / f"narrative_{report_date}.json"
    narrative = None
    if narrative_file.exists():
        narrative = _load(narrative_file)
        print(f"[叙事] 采用 {narrative_file}")
    else:
        print(f"[叙事] 无 narrative_{report_date}.json，使用规则化模板兜底")

    out_json = REPORTS_DIR / f"daily_report_{report_date}.json"
    out_html = REPORTS_DIR / f"daily_report_{report_date}.html"
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump({"report_date": report_date, "facts": facts, "narrative": narrative},
                  f, ensure_ascii=False, indent=1)
    with open(out_html, "w", encoding="utf-8") as f:
        f.write(render_html(facts, narrative))
    print(f"[产物] {out_html}")
    print(f"[产物] {out_json}")


if __name__ == "__main__":
    main()
