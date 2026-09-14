# -*- coding: utf-8 -*-
"""每日日报事实计算器（报告流水线第 1 步，纯规则、无 LLM）。

读取本地筛选榜单（data/screening、data/4h/screening）与看板产物
（data/json、data/4h/json），按《期货看板每日汇报手册》判据体系计算
结构化事实，写入 data/reports/facts/facts_YYYY-MM-DD.json；同时把当日
screening 快照归档到 data/reports/snapshots/ 供次日 diff 使用。

用法：
    python -m backend.pipeline.report_facts             # 日更后运行
    python -m backend.pipeline.report_facts --force     # 忽略"数据未更新"检查
    python -m backend.pipeline.report_facts --report-date 2026-09-14

判据口径来源：futures-dash-package/README.md 第 5-7、9、13 节。
"""
import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from backend.core.config import DATA_DIR, load_contracts
from backend.core.timeframes import TIMEFRAMES, json_dir, screening_file

BUCKETS = (
    "long_trend", "short_trend", "long_to_short", "long_to_short_warning",
    "short_to_long", "short_to_long_warning",
    "short_pressure_warning", "long_support_warning",
)

# 板块分组字典（手册 §9），键 = 品种代码（大写，不含合约月份）
SECTORS = {
    "黑色系": ["RB", "HC", "I", "JM", "J", "FG", "SA", "SF", "SS"],
    "油脂粕": ["M", "Y", "B", "RM", "A", "OI", "P"],
    "橡胶系": ["NR", "RU", "BR"],
    "芳烃能化": ["BZ", "EB", "EG", "MA", "PX", "L", "PP", "PL", "PF", "PR", "V"],
    "能源链": ["SC", "BU", "FU", "LU", "EC", "PG"],
    "有色": ["CU", "AL", "ZN", "PB", "NI", "SN", "AO", "PS", "SI", "LC", "BC"],
    "贵金属": ["AU", "AG"],
    "股指": ["000016", "000300", "000852", "IM", "588000"],
    "农软": ["CF", "C", "CS", "SR", "AP", "PK", "JD", "LH", "CJ"],
}
SECTOR_OF = {code: sector for sector, codes in SECTORS.items() for code in codes}

# 判据阈值（手册 §5.4 / §6.5 / §7.2）
NEAR_PCT = 1.0          # 收盘价距 EE/DD 差 <1% 视为"贴线"
SCORE_NEAR_ZERO = 0.5   # score 贴零阈值（样本：score +0.25 被定性贴零）
RANK_SIGNIFICANT = 3    # |rank_change| >= 3 视为显著
RE_BK_BARS = 9          # 4h 重新 BK 的有效回望根数（与转折保留口径一致）

REPORTS_DIR = DATA_DIR / "reports"
SNAPSHOT_DIR = REPORTS_DIR / "snapshots"
FACTS_DIR = REPORTS_DIR / "facts"


def _load(path: Path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _dump(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)


def _dedupe_latest(items):
    """4h 桶同 key 可能有多条历史转折，按 signal_date（无则 date）取最新。"""
    out = {}
    for it in items:
        key = it.get("key")
        stamp = it.get("signal_date") or it.get("date") or ""
        if key not in out or stamp >= out[key][0]:
            out[key] = (stamp, it)
    return [v[1] for v in out.values()]


def _product_code(key: str) -> str:
    """bu2610 → BU；SA701 → SA；000016 → 000016；IM2609 → IM。"""
    if key[0].isdigit():
        return key
    i = 0
    while i < len(key) and key[i].isalpha():
        i += 1
    return key[:i].upper()


def _sector_of(key: str):
    return SECTOR_OF.get(_product_code(key))


def _next_weekday(d):
    nxt = d + timedelta(days=1)
    while nxt.weekday() >= 5:
        nxt += timedelta(days=1)
    return nxt


def _shift_date(iso_date: str, days: int) -> str:
    d = datetime.strptime(iso_date[:10], "%Y-%m-%d").date()
    return (d + timedelta(days=days)).isoformat()


def _pct_to(close, level):
    """收盘价相对关键位的距离百分比（正=在线上方）。"""
    if close is None or level in (None, 0):
        return None
    return (close - level) / close * 100


def symbol_states(tf: str):
    """从 data/json 产物提取每品种的权威状态（手册：last_signal 以 symbols 为准）。"""
    states = {}
    for fp in sorted(json_dir(tf).glob("*.json")):
        d = _load(fp)
        dates = d.get("dates") or []
        sigs = d.get("signals") or []
        pos = (d.get("POS") or [0])[-1]
        last_sig, bars_since = None, None
        if sigs:
            s = sigs[-1]
            sig_date = dates[s["i"]] if 0 <= s["i"] < len(dates) else None
            last_sig = {"type": s["type"], "date": sig_date}
            bars_since = len(dates) - 1 - s["i"]
        ohlc = d.get("ohlc") or []
        close = ohlc[-1][1] if ohlc else None  # [开, 收, 低, 高]（ECharts 约定）
        states[fp.stem] = {
            "pos": pos, "last_signal": last_sig, "bars_since": bars_since,
            "close": close, "last_date": dates[-1] if dates else None,
        }
    return states


def build_names(contracts, *screenings):
    names = {e["symbol"].split(".")[0]: e.get("name", "") for e in contracts}
    for scr in screenings:
        for items in (scr.get("buckets") or {}).values():
            for it in items:
                names.setdefault(it.get("key", ""), it.get("name", ""))
    return names


def _brief(it, names, extra=()):
    row = {
        "key": it.get("key"), "name": it.get("name") or names.get(it.get("key"), ""),
        "close": it.get("close"), "score": it.get("score"), "POS": it.get("POS"),
        "DD": it.get("DD"), "EE": it.get("EE"), "KK": it.get("KK"), "PP": it.get("PP"),
        "sector": _sector_of(it.get("key", "")),
    }
    for k in extra:
        row[k] = it.get(k)
    return row


def diff_buckets(current, previous):
    """对比前后两次 screening，输出各桶进出名单。"""
    diff = {}
    for bucket in BUCKETS:
        cur = {it["key"]: it for it in current.get("buckets", {}).get(bucket, [])}
        if previous:
            old = {it["key"]: it for it in previous.get("buckets", {}).get(bucket, [])}
        else:
            old = {}
        diff[bucket] = {
            "count": len(cur), "previous_count": len(old),
            "entered": sorted(k for k in cur if k not in old),
            "left": sorted(k for k in old if k not in cur),
        }
    return diff


def find_previous_snapshot(tf: str, data_date: str):
    """找当前数据日之前的最近一份快照（同日快照不算前日）。"""
    tag = data_date.replace("-", "")
    candidates = sorted(
        p for p in SNAPSHOT_DIR.glob(f"screen_{tf}_*.json")
        if p.stem.rsplit("_", 1)[-1] < tag
    )
    return _load(candidates[-1]) if candidates else None


def classify_verdict_4h(scr_4h, states_1d, states_4h, names, items_1d):
    """4h 转折 × 日线状态裁决（手册 §5.1 铁律：一律以日线趋势为最终裁决）。

    A 档 真·双级别转空：日线已 SK（POS=-1）或 4h 已 SK
    B 档 双级别已离场：日线已 SP（POS=0）且 4h 未 SK —— 等日线 SK 才做空
    C 档 强势回踩：日线仍持多（POS=1）—— 只盯日线 EE
    D 档 空转多：D1 日线也 BK/持多=可跟多；D2 仅 BP=不抢跑；D3 日线仍空=反弹
    E 档 回踩结束：日线持多 + 4h 最近 RE_BK_BARS 根内重新 BK
    """
    verdict = {"A": [], "B": [], "C": [], "D1": [], "D2": [], "D3": [], "E": []}
    for it in _dedupe_latest(scr_4h.get("buckets", {}).get("long_to_short", [])):
        key = it["key"]
        st1 = states_1d.get(key, {})
        pos1d = st1.get("pos", 0)
        sig4h = states_4h.get(key, {}).get("last_signal") or {}
        row = _brief(it, names, extra=("signal_date", "bars_since_signal", "trend_band_date"))
        row["pos_1d"] = pos1d
        row["last_signal_4h"] = sig4h or None
        row["last_signal_1d"] = st1.get("last_signal")
        row["close_1d"] = st1.get("close")
        it1 = items_1d.get(key, {})
        row["score_1d"] = it1.get("score")
        row["EE_1d"] = it1.get("EE")
        row["DD_1d"] = it1.get("DD")
        if pos1d == -1 or sig4h.get("type") == "SK":
            verdict["A"].append(row)
        elif pos1d == 0:
            verdict["B"].append(row)
        else:
            verdict["C"].append(row)
    for it in _dedupe_latest(scr_4h.get("buckets", {}).get("short_to_long", [])):
        key = it["key"]
        st1 = states_1d.get(key, {})
        pos1d = st1.get("pos", 0)
        row = _brief(it, names, extra=("signal_date", "bars_since_signal"))
        row["pos_1d"] = pos1d
        row["last_signal_1d"] = st1.get("last_signal")
        row["last_signal_4h"] = states_4h.get(key, {}).get("last_signal")
        it1 = items_1d.get(key, {})
        row["score_1d"] = it1.get("score")
        row["EE_1d"] = it1.get("EE")
        grade = "D1" if pos1d == 1 else ("D3" if pos1d == -1 else "D2")
        verdict[grade].append(row)
    for key, st in states_4h.items():
        sig = st.get("last_signal") or {}
        if (sig.get("type") == "BK" and st.get("bars_since") is not None
                and st["bars_since"] <= RE_BK_BARS
                and states_1d.get(key, {}).get("pos") == 1):
            verdict["E"].append({
                "key": key, "name": names.get(key, ""),
                "bk_date": sig.get("date"), "bars_since": st["bars_since"],
                "close": st.get("close"), "sector": _sector_of(key),
            })
    for grade in verdict:
        verdict[grade].sort(key=lambda r: (r.get("score") is None, -(abs(r.get("score") or 0))))
    return verdict


def scan_divergence(scr_1d, scr_4h, states_1d, states_4h, names, pool_keys, data_date):
    """分歧严重·特别关注名单（手册 §6 三条判据 + §6.3 评级）。

    口径要点：
    - 只考察合约池内品种（data/json 里残留的旧合约不参与）
    - 日线已确认空头（POS=-1）不属于"分歧"，不计入评级（它们归看空主线）
    - 判据 2 收录全部日线破/贴线未开空品种，用 confirmed 标记是否满足
      "偏弱"要件（score 贴零/转负 或 挂预警）；评级只按 confirmed 计
    - 判据 3 从严：只收 4h 破 DD/EE（贴线不算"破平台"）且 score 为负
    """
    items_1d, items_4h = {}, {}
    for bucket in BUCKETS:
        for it in scr_1d.get("buckets", {}).get(bucket, []):
            items_1d.setdefault(it["key"], it)
        for it in scr_4h.get("buckets", {}).get(bucket, []):
            items_4h.setdefault(it["key"], it)
    warn_1d = {it["key"] for it in scr_1d.get("buckets", {}).get("long_to_short_warning", [])}
    l2s_4h = {it["key"] for it in scr_4h.get("buckets", {}).get("long_to_short", [])}
    l2s_warn_4h = {it["key"] for it in scr_4h.get("buckets", {}).get("long_to_short_warning", [])}

    def break_status(it, allow_near):
        close, dd, ee = it.get("close"), it.get("DD"), it.get("EE")
        if close is None:
            return None
        if ee is not None and close < ee:
            return "破EE"
        if dd is not None and close < dd:
            return "破DD"
        if not allow_near:
            return None
        pct = _pct_to(close, ee)
        if pct is not None and 0 <= pct < NEAR_PCT:
            return "贴EE"
        pct = _pct_to(close, dd)
        if pct is not None and 0 <= pct < NEAR_PCT:
            return "贴DD"
        return None

    # 判据 2：日线偏弱跌破平台（未开空）
    j2 = {}
    for key, it in items_1d.items():
        if key not in pool_keys or states_1d.get(key, {}).get("pos") == -1:
            continue
        hit = break_status(it, allow_near=True)
        if not hit:
            continue
        score = it.get("score")
        weak = score is not None and score < SCORE_NEAR_ZERO
        row = _brief(it, names)
        row["break_status"] = hit
        row["in_warning"] = key in warn_1d
        row["weak"] = weak
        row["confirmed"] = weak or key in warn_1d
        j2[key] = row

    # 判据 3：4h 偏弱跌破平台（从严：只算破 DD/EE）+ 日线仍多
    j3 = {}
    for key, it in items_4h.items():
        if key not in pool_keys or states_1d.get(key, {}).get("pos") != 1:
            continue
        hit = break_status(it, allow_near=False)
        score = it.get("score")
        if hit and score is not None and score < 0:
            row = _brief(it, names, extra=("signal_date",))
            row["break_status"] = hit
            j3[key] = row

    # 判据 1：同板块多只品种 4h 多头结束（尤其偏弱结束），即使日线仍多
    recent_cutoff = _shift_date(data_date, -15)  # 桶外品种以 last_signal 日期定性"近期结束"
    sector_members = defaultdict(list)
    for key in pool_keys:
        sector = _sector_of(key)
        if not sector or states_1d.get(key, {}).get("pos") == -1:
            continue
        st4 = states_4h.get(key, {})
        sig4 = st4.get("last_signal") or {}
        sig4_recent = sig4.get("type") in ("SP", "SK") and \
            (sig4.get("date") or "") >= recent_cutoff
        ended_4h = key in l2s_4h or key in l2s_warn_4h or sig4_recent
        if not ended_4h:
            continue
        it4 = items_4h.get(key, {})
        score4 = it4.get("score")
        sector_members[sector].append({
            "key": key, "name": names.get(key, ""),
            "pos_1d": states_1d.get(key, {}).get("pos"),
            "pos_4h": st4.get("pos"),
            "score_4h": score4,
            "close_4h": it4.get("close"),
            "EE_4h": it4.get("EE"),
            "signal_4h": st4.get("last_signal"),
            "signal_1d": states_1d.get(key, {}).get("last_signal"),
            "weak_4h": (score4 is not None and score4 < SCORE_NEAR_ZERO)
                       or sig4.get("type") == "SK",
            "still_long_1d": states_1d.get(key, {}).get("pos") == 1,
            "note": "桶外品种，仅定性" if key not in items_4h else "",
        })
    j1 = {sector: sorted(m, key=lambda r: (r["score_4h"] is None, r["score_4h"] or 0))
          for sector, m in sorted(sector_members.items()) if len(m) >= 2}

    # 评级（§6.3）：🔴 双命中或日线破生死线（极端偏弱）；🟠 单命中破位；🟡 仅板块联动观察
    j1_keys = {r["key"] for m in j1.values() for r in m}
    rated = {}
    for key in j1_keys | set(j2) | set(j3):
        if states_1d.get(key, {}).get("pos") == -1:
            continue
        h1, h2, h3 = key in j1_keys, key in j2 and j2[key]["confirmed"], key in j3
        it1 = items_1d.get(key, {})
        broke_ee = it1.get("EE") is not None and (it1.get("close") or 1e18) < it1["EE"]
        n = h1 + h2 + h3
        if n >= 2 or broke_ee:
            level = "🔴"
        elif h2 or h3:
            level = "🟠"
        else:
            level = "🟡"
        rated[key] = {
            "key": key, "name": names.get(key, ""), "sector": _sector_of(key),
            "level": level,
            "hits": [s for s, on in (("板块联动", h1), ("日线偏弱破位", h2), ("4h偏弱破位", h3)) if on],
        }
    return {"j1_sector_linkage": j1, "j2_daily_break": sorted(j2.values(), key=lambda r: r["score"] or 0),
            "j3_4h_break": sorted(j3.values(), key=lambda r: r["score"] or 0),
            "rated": sorted(rated.values(), key=lambda r: ("🔴🟠🟡".index(r["level"]), r["key"]))}


def scan_rank_radar(scr_1d, names):
    """动量排名雷达（手册 §7）：|chg|>=3 显著；new 必点名；连续爬升加权。"""
    radar = {"long_risen": [], "long_fallen": [], "short_risen": [], "short_fallen": [],
             "new_entries": []}
    for side, bucket in (("long", "long_trend"), ("short", "short_trend")):
        for it in scr_1d.get("buckets", {}).get(bucket, []):
            rank = it.get("rank")
            if rank is None:
                continue
            hist = it.get("rank_history") or []
            climb = 0
            for a, b in zip(hist[-3:-1], hist[-2:]):
                ra, rb = a.get("rank"), b.get("rank")
                if ra is not None and rb is not None and rb < ra:
                    climb += 1
            row = {
                "key": it["key"], "name": it.get("name") or names.get(it["key"], ""),
                "rank": rank, "previous_rank": it.get("previous_rank"),
                "rank_change": it.get("rank_change"), "rank_status": it.get("rank_status"),
                "rank_history": hist, "score": it.get("score"), "sector": _sector_of(it["key"]),
                "climb_days": climb,
            }
            if it.get("rank_status") == "new":
                radar["new_entries"].append(row)
            chg = it.get("rank_change") or 0
            if abs(chg) >= RANK_SIGNIFICANT:
                radar[f"{side}_{'risen' if chg > 0 else 'fallen'}"].append(row)
    for k in radar:
        radar[k].sort(key=lambda r: -(abs(r.get("rank_change") or 0)))
    return radar


def compute_facts(report_date=None, force=False):
    scr = {tf: _load(screening_file(tf)) for tf in TIMEFRAMES}
    gen_1d = scr["1d"].get("generated_at", "")
    data_date = gen_1d[:10]
    if not data_date:
        raise SystemExit("[错误] 1d screening 缺少 generated_at")

    latest_snap = sorted(SNAPSHOT_DIR.glob("screen_1d_*.json"))
    if latest_snap and not force:
        prev = _load(latest_snap[-1])
        if prev.get("generated_at") == gen_1d:
            print(f"[跳过] 1d 数据未更新（generated_at={gen_1d}），与最近快照一致；"
                  f"周末/节假日不产新报告。", file=sys.stderr)
            return None

    states = {tf: symbol_states(tf) for tf in TIMEFRAMES}
    contracts = load_contracts()
    names = build_names(contracts, scr["1d"], scr["4h"])
    pool_keys = {e["symbol"].split(".")[0] for e in contracts}
    prev_1d = find_previous_snapshot("1d", data_date)
    prev_4h = find_previous_snapshot("4h", (scr["4h"].get("generated_at") or "")[:10] or data_date)

    d1d = diff_buckets(scr["1d"], prev_1d)
    d4h = diff_buckets(scr["4h"], prev_4h)

    # 当日日线交易动作（last_signal 日期 == 1d 数据日，限合约池内）
    actions = defaultdict(list)
    for key in pool_keys:
        st = states["1d"].get(key)
        if not st:
            continue
        sig = st.get("last_signal") or {}
        if sig.get("date") == data_date:
            actions[sig["type"]].append({"key": key, "name": names.get(key, ""),
                                         "sector": _sector_of(key)})

    # 供裁决表查日线字段（EE/score/DD）
    items_1d_map = {}
    for bucket in BUCKETS:
        for it in scr["1d"]["buckets"].get(bucket, []):
            items_1d_map.setdefault(it["key"], it)

    verdict = classify_verdict_4h(scr["4h"], states["1d"], states["4h"], names, items_1d_map)
    divergence = scan_divergence(scr["1d"], scr["4h"], states["1d"], states["4h"],
                                 names, pool_keys, data_date)
    radar = scan_rank_radar(scr["1d"], names)

    # 双级别共振空头（1d 持空 + 4h 持空），供看空主线标记
    short_resonance = sorted(
        it["key"] for it in scr["1d"]["buckets"].get("short_trend", [])
        if states["4h"].get(it["key"], {}).get("pos") == -1)

    # 龙头回踩 / 熊头遇压（附 4h 状态，供模板判断"回踩是否健康/遇压是否有效"）
    items_4h = {}
    for bucket in BUCKETS:
        for it in scr["4h"]["buckets"].get(bucket, []):
            items_4h.setdefault(it["key"], it)

    def _attach_4h(row):
        it4 = items_4h.get(row["key"], {})
        row["pos_4h"] = it4.get("POS")
        row["score_4h"] = it4.get("score")
        return row

    support = [_attach_4h(_brief(it, names, extra=("retest_count", "retest_dates", "score_entry_date")))
               for it in scr["1d"]["buckets"].get("long_support_warning", [])]
    pressure = [_attach_4h(_brief(it, names, extra=("retest_count", "retest_dates", "score_entry_date")))
                for it in scr["1d"]["buckets"].get("short_pressure_warning", [])]
    support.sort(key=lambda r: -(r["score"] or 0))
    pressure.sort(key=lambda r: (r["score"] or 0))

    # 贴线/破线关键位清单（1d 全桶去重）
    key_levels = []
    seen = set()
    for bucket in BUCKETS:
        for it in scr["1d"]["buckets"].get(bucket, []):
            key = it["key"]
            if key in seen:
                continue
            seen.add(key)
            close, ee, dd = it.get("close"), it.get("EE"), it.get("DD")
            pct_ee = _pct_to(close, ee)
            if close is not None and ee is not None and close < ee:
                status = "破EE"
            elif pct_ee is not None and 0 <= pct_ee < NEAR_PCT:
                status = "贴EE"
            elif close is not None and dd is not None and close < dd:
                status = "破DD"
            else:
                continue
            row = _brief(it, names)
            row["status"] = status
            row["pct_to_EE"] = round(pct_ee, 2) if pct_ee is not None else None
            key_levels.append(row)
    key_levels.sort(key=lambda r: (r["status"] != "破EE", abs(r.get("pct_to_EE") or 0)))

    # POS=0 桶外盲点（手册 §6.5 坑：拿不到 4h close/score，只能定性；限合约池内）
    blindspot = []
    for key in pool_keys:
        st = states["1d"].get(key)
        if not st:
            continue
        if st.get("pos") == 0 and key not in seen:
            blindspot.append({
                "key": key, "name": names.get(key, ""), "sector": _sector_of(key),
                "signal_1d": st.get("last_signal"),
                "signal_4h": states["4h"].get(key, {}).get("last_signal"),
                "close_1d": st.get("close"),
            })

    if report_date is None:
        y, m, d_ = map(int, data_date.split("-"))
        report_date = _next_weekday(datetime(y, m, d_).date()).isoformat()

    facts = {
        "facts_version": 1,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "report_date": report_date,
        "header": {
            "data_date_1d": data_date,
            "generated_at_1d": gen_1d,
            "generated_at_4h": scr["4h"].get("generated_at", ""),
            "has_previous": prev_1d is not None,
            "previous_date_1d": (prev_1d or {}).get("generated_at", "")[:10] or None,
        },
        "overview": {"1d": d1d, "4h": d4h},
        "daily_actions": dict(actions),
        "short_resonance": short_resonance,
        "verdict_4h": verdict,
        "divergence": divergence,
        "leader_watch": {"leader_retest": support, "bear_pressure": pressure},
        "rank_radar": radar,
        "key_levels": key_levels,
        "pos_zero_blindspot": sorted(blindspot, key=lambda r: r["key"]),
        "buckets_1d": {b: [_attach_4h(_brief(it, names, extra=("retest_count", "retest_dates",
                                                             "score_entry_date", "rank", "rank_change",
                                                             "rank_status", "previous_rank")))
                           for it in scr["1d"]["buckets"].get(b, [])] for b in BUCKETS},
    }

    # 归档快照（供次日 diff）；报告 JSON 一并落盘
    for tf in TIMEFRAMES:
        tag = (scr[tf].get("generated_at") or "")[:10].replace("-", "")
        if tag:
            _dump(SNAPSHOT_DIR / f"screen_{tf}_{tag}.json", scr[tf])
    _dump(FACTS_DIR / f"facts_{report_date}.json", facts)
    return facts


def main():
    ap = argparse.ArgumentParser(description="每日日报事实计算器")
    ap.add_argument("--report-date", help="报告日期 YYYY-MM-DD（默认=数据日的下一工作日）")
    ap.add_argument("--force", action="store_true", help="忽略数据未更新检查")
    args = ap.parse_args()

    facts = compute_facts(report_date=args.report_date, force=args.force)
    if facts is None:
        return
    o = facts["overview"]
    print(f"[产物] {FACTS_DIR / ('facts_' + facts['report_date'] + '.json')}")
    print(f"数据基准: 1d={facts['header']['generated_at_1d']} 4h={facts['header']['generated_at_4h']}"
          f"（对比前日: {facts['header']['previous_date_1d'] or '无基线'}）")
    print(f"总览 1d: 多{o['1d']['long_trend']['count']}(前{o['1d']['long_trend']['previous_count']})"
          f" 空{o['1d']['short_trend']['count']}(前{o['1d']['short_trend']['previous_count']})"
          f" | 4h: 多{o['4h']['long_trend']['count']} 空{o['4h']['short_trend']['count']}"
          f" 多转空{o['4h']['long_to_short']['count']}")
    v = facts["verdict_4h"]
    print(f"4h 裁决: A共振空{len(v['A'])} B已离场{len(v['B'])} C回踩{len(v['C'])}"
          f" D1/D2/D3={len(v['D1'])}/{len(v['D2'])}/{len(v['D3'])} E重新BK{len(v['E'])}")
    dv = facts["divergence"]
    print(f"分歧名单: 板块联动{len(dv['j1_sector_linkage'])}组 日线破位{len(dv['j2_daily_break'])}"
          f" 4h破位{len(dv['j3_4h_break'])} | 评级 "
          + " ".join(f"{r['level']}{r['key']}" for r in dv["rated"][:12]))
    rr = facts["rank_radar"]
    print(f"排名雷达: 多升{len(rr['long_risen'])} 多降{len(rr['long_fallen'])}"
          f" 空升{len(rr['short_risen'])} 空降{len(rr['short_fallen'])} 新入榜{len(rr['new_entries'])}")


if __name__ == "__main__":
    main()
