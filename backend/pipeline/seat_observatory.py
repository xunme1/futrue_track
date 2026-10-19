# -*- coding: utf-8 -*-
"""席位观察室正式生成器：事实、受约束叙事、单文件看板与日期归档。"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import html
import json
import math
import os
from pathlib import Path
import re

import pandas as pd
import requests

from backend.pipeline.report_store import atomic_json, atomic_text, digest
from backend.pipeline.seat_core import CN_NAME, MEMBER_FAMILIES, SEAT_DIR, prev_trade_date, read_seat_csv
from backend.pipeline.seat_fetch import FINANCIAL_SYMBOLS


REPORT_SCHEMA_VERSION = 3
RENDERER_VERSION = "seat-observatory-v1"
PROMPT_VERSION = "seat-narrative-v2"
DEFAULT_LIMIT = 10
MAX_SELECTION = 20
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
HTML2CANVAS_URL = "https://cdn.jsdelivr.net/npm/html2canvas@1.4.1/dist/html2canvas.min.js"
HTML2CANVAS_SHA256 = "e87e550794322e574a1fda0c1549a3c70dae5a93d9113417a429016838eab8cb"

GROUP_SPECS = {
    "goldman": {
        "code": "Q", "name": "高盛席位", "short_name": "高盛",
        "families": MEMBER_FAMILIES["Q"],
    },
    "major": {
        "code": "Z", "name": "主力席位", "short_name": "主力",
        "families": MEMBER_FAMILIES["Z"],
    },
    "retail": {
        "code": "R", "name": "散户席位", "short_name": "散户",
        "families": MEMBER_FAMILIES["R"],
    },
}


def _number(value):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return 0.0
    return result if math.isfinite(result) else 0.0


def _sign(value):
    if value is None or value == 0:
        return 0
    return 1 if value > 0 else -1


def _direction(value):
    return "偏多" if value > 0 else ("偏空" if value < 0 else "中性")


def _action(today, previous):
    if previous is None:
        return None
    if today > 0:
        if previous <= 0:
            return "翻多"
        return "加多" if abs(today) >= abs(previous) else "减多"
    if today < 0:
        if previous >= 0:
            return "翻空"
        return "加空" if abs(today) >= abs(previous) else "减空"
    return "回到中性"


def _percentile(values, current, absolute=False):
    if current is None or len(values) < 10:
        return None
    sample = [abs(value) for value in values] if absolute else list(values)
    target = abs(current) if absolute else current
    return round(sum(value <= target for value in sample) / len(sample), 2)


def _family_snapshot(day_frame, families):
    """组净仓：已披露成员按实际持仓合计，未披露成员按零计并返回其索引。

    只有零持仓披露（两侧均为 0）视为未披露；全部成员未披露时返回 None。
    """
    if day_frame.empty:
        return None, list(range(len(families)))
    selected, missing = [], []
    for index, aliases in enumerate(families):
        part = day_frame[day_frame["member_name"].isin(set(aliases))]
        if part.empty:
            missing.append(index)
        else:
            selected.append(part)
    if not selected:
        return None, missing
    rows = pd.concat(selected, ignore_index=True)
    long_sum = rows["total_long"].map(_number).sum()
    short_sum = rows["total_short"].map(_number).sum()
    if not long_sum and not short_sum:
        return None, missing
    return float(long_sum - short_sum), missing


def _source_for(symbol_frame, symbol, source_manifest):
    meta = (source_manifest or {}).get(symbol) or {}
    values = []
    if "source" in symbol_frame.columns:
        values = [str(value) for value in symbol_frame["source"].dropna().unique()]
        if len(values) > 1:
            return "mixed-invalid"
    if meta.get("source"):
        if values and values[0] != meta["source"]:
            return "mixed-invalid"
        return meta["source"]
    if symbol in (source_manifest or {}):
        return "unavailable"
    if symbol_frame.empty:
        return "unavailable"
    if len(values) == 1:
        return values[0]
    return "finoview-legacy"


def _source_detail(symbol_frame, symbol, source_manifest):
    meta = dict((source_manifest or {}).get(symbol) or {})
    meta["source"] = _source_for(symbol_frame, symbol, source_manifest)
    return meta


def _build_group_row(symbol, name, symbol_frame, spec, dates, trade_date, prev_date,
                     source, market):
    snapshots = {}
    missing = {}
    for day in dates:
        net, missing_families = _family_snapshot(
            symbol_frame[symbol_frame["trade_date"] == day], spec["families"]
        )
        snapshots[day] = net
        missing[day] = missing_families
    today = snapshots.get(trade_date)
    previous = snapshots.get(prev_date)
    valid_history = [snapshots[day] for day in dates[-20:] if snapshots.get(day) is not None]
    daily_changes = []
    for before, after in zip(dates, dates[1:]):
        if snapshots.get(before) is not None and snapshots.get(after) is not None:
            daily_changes.append(snapshots[after] - snapshots[before])
    change = today - previous if today is not None and previous is not None else None
    streak = 0
    if today is not None and _sign(today):
        direction = _sign(today)
        for day in reversed(dates):
            value = snapshots.get(day)
            if value is None or _sign(value) != direction:
                break
            streak += 1
    recent = [snapshots.get(day) for day in dates[-3:]]
    position_trend = None
    if (len(recent) == 3 and all(value is not None and _sign(value) == _sign(today)
                                 for value in recent) and _sign(today)):
        magnitudes = [abs(value) for value in recent]
        if magnitudes[0] < magnitudes[1] < magnitudes[2]:
            position_trend = "连续增强"
        elif magnitudes[0] > magnitudes[1] > magnitudes[2]:
            position_trend = "连续减弱"
    denominator = max([abs(value) for value in valid_history] + [1.0])
    missing_today = missing.get(trade_date) or []
    missing_prev = missing.get(prev_date) or []
    missing_today_names = [spec["families"][index][0] for index in missing_today]
    missing_prev_names = [spec["families"][index][0] for index in missing_prev]
    return {
        "symbol": symbol,
        "name": name,
        "source": source,
        "available": today is not None,
        "missing_reason": (
            None if today is not None
            else "当日组内成员均无有效披露" if symbol_frame.shape[0] else "品种数据不可用"
        ),
        "partial": today is not None and bool(missing_today),
        "missing_family_count": len(missing_today),
        "missing_members": missing_today_names,
        "today_complete": today is not None and not missing_today,
        "prev_complete": previous is not None and not missing_prev,
        "prev_partial": previous is not None and bool(missing_prev),
        "prev_missing_members": missing_prev_names,
        "comparison_note": (
            None if previous is not None
            else "昨日组内成员均无有效披露"
        ),
        "net_today": round(today) if today is not None else None,
        "net_prev": round(previous) if previous is not None else None,
        "net_change": round(change) if change is not None else None,
        "direction": _direction(today) if today is not None else None,
        "action": _action(today, previous) if today is not None else None,
        "position_pct_20d": _percentile(valid_history, today),
        "change_pct_20d": _percentile(daily_changes[-20:], change, absolute=True),
        "direction_streak": streak if today is not None else None,
        "position_trend": position_trend,
        "history_valid_days": len(valid_history),
        "relative_today": round(today / denominator, 4) if today is not None else None,
        "relative_prev": round(previous / denominator, 4) if previous is not None else None,
        "prev_comparable": previous is not None,
        "market": market,
    }


def fetch_market_context(dominants, trade_date, prev_date, rq=None):
    """Compare the same target-day dominant contract on both dates."""
    mapping = {
        str(row.get("symbol") or "").upper(): str(row.get("main") or "")
        for row in dominants
        if str(row.get("symbol") or "").upper() not in FINANCIAL_SYMBOLS
        and row.get("main")
    }
    contracts = sorted(set(mapping.values()))
    result = {symbol: {"contract": contract, "available": False}
              for symbol, contract in mapping.items()}
    if not contracts:
        return result
    try:
        if rq is None:
            from backend.pipeline.goldman_contract import _rq
            rq = _rq()
        frame = rq.get_price(
            contracts, start_date=prev_date, end_date=trade_date,
            frequency="1d", fields=["close", "open_interest"],
        )
    except Exception as exc:  # noqa: BLE001 - context is optional
        for value in result.values():
            value["missing_reason"] = f"{type(exc).__name__}: {exc}"
        return result
    if frame is None or len(frame) == 0:
        return result
    reset = frame.reset_index()
    # rqdatac 多合约返回 (order_book_id, date) 索引；单合约版本有时只保留
    # date 索引。两种结构都归一成相同列，避免小商品池烟雾测试误判缺失。
    if "order_book_id" not in reset.columns and len(contracts) == 1:
        reset["order_book_id"] = contracts[0]
    if "date" not in reset.columns:
        date_columns = [column for column in reset.columns
                        if "date" in str(column).lower()]
        if not date_columns:
            for value in result.values():
                value["missing_reason"] = "行情响应缺少日期列"
            return result
        reset = reset.rename(columns={date_columns[0]: "date"})
    reset["date_key"] = reset["date"].astype(str).str[:10].str.replace("-", "", regex=False)
    for symbol, contract in mapping.items():
        part = reset[reset["order_book_id"].astype(str) == contract]
        previous = part[part["date_key"] == prev_date]
        today = part[part["date_key"] == trade_date]
        if previous.empty or today.empty:
            result[symbol]["missing_reason"] = "主力合约今昨日行情不完整"
            continue
        p, t = previous.iloc[-1], today.iloc[-1]
        close_p, close_t = _number(p.get("close")), _number(t.get("close"))
        oi_p, oi_t = _number(p.get("open_interest")), _number(t.get("open_interest"))
        if not close_p:
            result[symbol]["missing_reason"] = "前收盘无效"
            continue
        result[symbol] = {
            "contract": contract,
            "available": True,
            "price_return_pct": round((close_t / close_p - 1) * 100, 2),
            "oi_change": round(oi_t - oi_p),
            "oi_change_pct": round((oi_t / oi_p - 1) * 100, 2) if oi_p else None,
        }
    return result


def build_facts(df, trade_date, prev_date, universe=None, source_manifest=None,
                market_context=None, goldman_contract=None, contract_positions=None):
    """Build the complete auditable fact package used by HTML and the LLM."""
    frame = df.copy()
    frame["trade_date"] = frame["trade_date"].astype(str).str.replace("-", "", regex=False)
    frame["symbol"] = frame["symbol"].astype(str).str.upper()
    frame["member_name"] = frame["member_name"].astype(str).str.strip()
    for column in ("total_long", "total_short"):
        if column not in frame:
            frame[column] = 0
    if universe is None:
        universe = [{"symbol": value} for value in sorted(frame["symbol"].unique())]
    universe = [dict(row) for row in universe
                if str(row.get("symbol") or "").upper() not in FINANCIAL_SYMBOLS]
    market_context = market_context or {}
    dates = sorted(day for day in frame["trade_date"].unique() if day <= trade_date)
    groups = {}
    for group_key, spec in GROUP_SPECS.items():
        varieties = []
        for item in universe:
            symbol = str(item.get("symbol") or "").upper()
            symbol_frame = frame[frame["symbol"] == symbol]
            source = _source_for(symbol_frame, symbol, source_manifest)
            name = item.get("name") or CN_NAME.get(symbol, symbol)
            group_row = _build_group_row(
                symbol, name, symbol_frame, spec, dates, trade_date, prev_date,
                source, market_context.get(symbol),
            )
            if source == "mixed-invalid":
                group_row.update({
                    "available": False,
                    "missing_reason": "检测到同一品种跨源混合，已拒绝计算",
                    "partial": False,
                    "today_complete": False,
                    "prev_complete": False,
                    "prev_partial": False,
                    "net_today": None,
                    "net_prev": None,
                    "net_change": None,
                    "direction": None,
                    "action": None,
                    "position_pct_20d": None,
                    "change_pct_20d": None,
                    "direction_streak": None,
                    "position_trend": None,
                    "relative_today": None,
                    "relative_prev": None,
                    "prev_comparable": False,
                })
            varieties.append(group_row)
        available = [row for row in varieties if row["available"]]
        groups[group_key] = {
            "code": spec["code"],
            "name": spec["name"],
            "short_name": spec["short_name"],
            "members": [family[0] for family in spec["families"]],
            "varieties": varieties,
            "coverage": {
                "available": len(available),
                "missing": len(varieties) - len(available),
                "long": sum(row["net_today"] > 0 for row in available),
                "short": sum(row["net_today"] < 0 for row in available),
                "neutral": sum(row["net_today"] == 0 for row in available),
                "prev_comparable": sum(row["prev_comparable"] for row in available),
            },
        }
    sources = {}
    source_details = {}
    for row in universe:
        symbol = str(row.get("symbol") or "").upper()
        symbol_frame = frame[frame["symbol"] == symbol]
        detail = _source_detail(symbol_frame, symbol, source_manifest)
        source = detail["source"]
        sources[source] = sources.get(source, 0) + 1
        source_details[symbol] = detail
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "data_date": trade_date,
        "prev_date": prev_date,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "basis": "交易所会员持买/持卖前20名披露口径；未披露不代表真实持仓为零",
        "universe": [{
            "symbol": str(row.get("symbol") or "").upper(),
            "name": row.get("name") or CN_NAME.get(str(row.get("symbol") or "").upper(),
                                                     str(row.get("symbol") or "").upper()),
            "main": row.get("main"), "sub": row.get("sub"),
        } for row in universe],
        "source_coverage": sources,
        "source_details": source_details,
        "groups": groups,
        "market_context": market_context,
        "goldman_contract": goldman_contract,
        "contract_positions": contract_positions,
    }


def build_signals(facts):
    """Create deterministic, evidence-addressable narrative candidates."""
    maps = {
        key: {row["symbol"]: row for row in value["varieties"]}
        for key, value in facts["groups"].items()
    }
    names = {row["symbol"]: row["name"] for row in facts["universe"]}
    signals = []
    for symbol in sorted(names):
        rows = {key: maps[key].get(symbol) for key in GROUP_SPECS}
        eligible = all(row and row["available"] and row["prev_comparable"] for row in rows.values())
        if not eligible:
            continue
        signs = {key: _sign(row["net_today"]) for key, row in rows.items()}
        if 0 in signs.values():
            continue
        position_extreme = max(
            abs(2 * row["position_pct_20d"] - 1)
            for row in rows.values() if row["position_pct_20d"] is not None
        ) if any(row["position_pct_20d"] is not None for row in rows.values()) else 0
        change_strength = max(
            (row["change_pct_20d"] or 0) for row in rows.values()
        )
        persistence = min(max((row["direction_streak"] or 0) for row in rows.values()) / 5, 1)
        category, cross_value = None, 0
        if signs["goldman"] == signs["major"] != signs["retail"]:
            category, cross_value = "institution_retail_divergence", 1.0
        elif len(set(signs.values())) == 1 and position_extreme >= 0.8:
            category, cross_value = "crowded_consensus", 0.7
        elif any(row["action"] in ("翻多", "翻空") for row in rows.values()):
            category, cross_value = "flip", 0.6
        elif change_strength >= 0.8:
            category, cross_value = "change_extreme", 0.3
        elif any(row["position_trend"] for row in rows.values()):
            category, cross_value = "persistence", 0.3
        elif len(set(signs.values())) == 1:
            category, cross_value = "consensus", 0.4
        if not category:
            continue
        market = facts["market_context"].get(symbol) or {}
        changes = [row["net_change"] for row in rows.values() if row["net_change"] is not None]
        impulse = sum(changes)
        price = market.get("price_return_pct") if market.get("available") else None
        oi = market.get("oi_change") if market.get("available") else None
        if price is None:
            context_value, context_label = 0, "价格/OI缺失"
        elif _sign(impulse) == _sign(price) and _sign(price):
            context_value = 1.0 if oi is not None and oi > 0 else 0.7
            context_label = "席位变化与价格同向" + ("且OI增加" if oi is not None and oi > 0 else "")
        elif _sign(impulse) and _sign(price):
            context_value = 0.6 if oi is not None and oi > 0 else 0.4
            context_label = "席位变化与价格背离" + ("且OI增加" if oi is not None and oi > 0 else "")
        else:
            context_value, context_label = 0.3, "价格变化接近中性"
        score = round(
            0.35 * cross_value + 0.25 * change_strength + 0.20 * position_extreme
            + 0.10 * persistence + 0.10 * context_value, 4
        )
        direction_text = "净多" if signs["goldman"] > 0 else "净空"
        if category == "institution_retail_divergence":
            observation = (
                f"{names[symbol]} {symbol}：高盛与主力均为{direction_text}，"
                f"散户方向相反；{context_label}。"
            )
        elif category == "crowded_consensus":
            observation = f"{names[symbol]} {symbol}：三组方向一致且至少一组处于20日极值；{context_label}。"
        elif category == "flip":
            flipped = "、".join(GROUP_SPECS[key]["short_name"] for key, row in rows.items()
                                if row["action"] in ("翻多", "翻空"))
            observation = f"{names[symbol]} {symbol}：{flipped}出现净仓翻向；{context_label}。"
        elif category == "change_extreme":
            strong = "、".join(
                GROUP_SPECS[key]["short_name"] for key, row in rows.items()
                if (row["change_pct_20d"] or 0) >= 0.8
            )
            observation = f"{names[symbol]} {symbol}：{strong}净仓变化强度处于20日高位；{context_label}。"
        elif category == "persistence":
            trends = "、".join(
                f"{GROUP_SPECS[key]['short_name']}{row['position_trend']}"
                for key, row in rows.items() if row["position_trend"]
            )
            observation = f"{names[symbol]} {symbol}：{trends}；{context_label}。"
        else:
            observation = f"{names[symbol]} {symbol}：三组净仓方向一致；{context_label}。"
        signals.append({
            "evidence_id": f"{category}:{symbol}",
            "scope": "variety",
            "symbol": symbol,
            "name": names[symbol],
            "category": category,
            "groups": list(GROUP_SPECS),
            "score": score,
            "observation": observation,
            "market_context": context_label,
            "facts": {
                key: {
                    "direction": row["direction"],
                    "action": row["action"],
                    "net_today": row["net_today"],
                    "net_change": row["net_change"],
                    "position_pct_20d": row["position_pct_20d"],
                    "change_pct_20d": row["change_pct_20d"],
                    "direction_streak": row["direction_streak"],
                    "position_trend": row["position_trend"],
                }
                for key, row in rows.items()
            },
        })
    signals.sort(key=lambda row: (-row["score"], row["symbol"]))
    for group_key, group in facts["groups"].items():
        top_rows = sorted(
            (row for row in group["varieties"] if row["available"]),
            key=lambda row: abs(row["net_today"]), reverse=True,
        )[:3]
        signals.append({
            "evidence_id": f"group_snapshot:{group_key}",
            "scope": "group",
            "group": group_key,
            "category": "group_snapshot",
            "groups": [group_key],
            "score": 0,
            "observation": _fallback_group_note(facts, group_key),
            "facts": {
                "coverage": group["coverage"],
                "top_positions": [{
                    "symbol": row["symbol"],
                    "name": row["name"],
                    "net_today": row["net_today"],
                    "direction": row["direction"],
                    "action": row["action"],
                } for row in top_rows],
            },
        })
    return signals


SYSTEM_PROMPT = """你是期货席位持仓日报编辑。只使用用户提供、带 evidence_id 的事实写作。
必须区分客观观察、可能解释和风险，不得把席位方向写成确定性价格预测；不得声称散户、
机构或高盛具有固定胜率；不得把未披露仓位当作零；不得把会员代客持仓写成会员自营观点。
只输出符合指定结构的 JSON，不输出 Markdown 或额外说明。"""


def _fallback_group_note(facts, group_key):
    group = facts["groups"][group_key]
    coverage = group["coverage"]
    rows = sorted(
        (row for row in group["varieties"] if row["available"]),
        key=lambda row: abs(row["net_today"]), reverse=True,
    )
    names = "、".join(f"{row['name']} {row['symbol']}" for row in rows[:3]) or "无完整披露品种"
    return (
        f"本组有披露 {coverage['available']} 个品种（成员未披露按零计），净多 {coverage['long']}、"
        f"净空 {coverage['short']}；按净仓绝对值靠前的是 {names}。"
        "方向仅描述已披露持仓，不代表后续价格判断。"
    )


def fallback_narrative(facts, signals, reason="规则模板"):
    selected = [signal for signal in signals if signal.get("scope") == "variety"][:3]
    snapshots = {signal.get("group"): signal for signal in signals
                 if signal.get("scope") == "group"}
    if selected:
        summary = " ".join(signal["observation"] for signal in selected)
        evidence = [signal["evidence_id"] for signal in selected]
    else:
        summary = "今日没有同时满足三组今昨日完整披露条件的高置信差异信号，优先查看各组持仓事实。"
        evidence = [signal["evidence_id"] for signal in list(snapshots.values())[:2]]
    if len(evidence) < 2:
        evidence.extend(
            signal["evidence_id"] for signal in snapshots.values()
            if signal["evidence_id"] not in evidence
        )
        evidence = evidence[:2]
    return {
        "overall_summary": {"text": summary, "evidence_ids": evidence},
        "group_notes": {
            key: {
                "text": _fallback_group_note(facts, key),
                "evidence_ids": ([snapshots[key]["evidence_id"]] if key in snapshots else []),
            }
            for key in GROUP_SPECS
        },
        "focus_signal_ids": [signal["evidence_id"] for signal in selected[:5]],
        "risk_notes": [
            "会员持仓仅覆盖交易所前20名披露范围，未披露不代表真实仓位为零。",
            "会员席位可能包含代客、套保与跨期组合，净仓差异不构成确定性交易信号。",
        ],
        "meta": {
            "model": None,
            "prompt_version": PROMPT_VERSION,
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "validated": True,
            "input_hash": digest({"data_date": facts["data_date"], "signals": signals}),
            "fallback_reason": reason,
        },
    }


def _text(value, minimum=1, maximum=1000):
    return isinstance(value, str) and minimum <= len(value.strip()) <= maximum


def validate_narrative(value, signals):
    if not isinstance(value, dict):
        raise ValueError("叙事必须是 JSON 对象")
    evidence = {signal["evidence_id"] for signal in signals}
    overall = value.get("overall_summary")
    if not isinstance(overall, dict) or not _text(overall.get("text"), 20, 700):
        raise ValueError("overall_summary.text 无效")
    overall_ids = overall.get("evidence_ids")
    if not isinstance(overall_ids, list) or len(overall_ids) > 5 or set(overall_ids) - evidence:
        raise ValueError("overall_summary 证据编号无效")
    if signals and len(overall_ids) < min(2, len(signals)):
        raise ValueError("overall_summary 至少需要 2 个有效证据（仅有一个候选时为 1 个）")
    notes = value.get("group_notes")
    if not isinstance(notes, dict) or set(notes) != set(GROUP_SPECS):
        raise ValueError("group_notes 必须完整包含三组")
    for key, note in notes.items():
        if not isinstance(note, dict) or not _text(note.get("text"), 20, 500):
            raise ValueError(f"{key} 注释无效")
        ids = note.get("evidence_ids")
        if not isinstance(ids, list) or len(ids) > 3 or set(ids) - evidence:
            raise ValueError(f"{key} 证据编号无效")
        if signals and not ids:
            raise ValueError(f"{key} 至少需要 1 个有效证据")
    focus = value.get("focus_signal_ids")
    if not isinstance(focus, list) or len(focus) > 5 or set(focus) - evidence:
        raise ValueError("focus_signal_ids 无效")
    risks = value.get("risk_notes")
    if not isinstance(risks, list) or len(risks) != 2 or not all(_text(x, 10, 300) for x in risks):
        raise ValueError("risk_notes 必须是两条有效文本")
    joined = json.dumps(value, ensure_ascii=False)
    for phrase in ("固定胜率", "必然上涨", "必然下跌", "稳赢"):
        if phrase in joined:
            raise ValueError(f"包含禁止表述: {phrase}")
    _validate_text_claims(value, signals)
    return value


def _validate_text_claims(value, signals):
    """Reject explicit product/group direction claims that contradict evidence.

    Prose cannot be proved exhaustively, but explicit ``品种 + 席位 + 方向/动作``
    statements can and should be checked before publication.
    """
    by_id = {signal["evidence_id"]: signal for signal in signals}
    texts = [(value["overall_summary"]["text"], value["overall_summary"]["evidence_ids"])]
    texts.extend((note["text"], note["evidence_ids"])
                 for note in value["group_notes"].values())
    group_words = {
        "goldman": ("高盛",),
        "major": ("主力", "机构"),
        "retail": ("散户",),
    }
    direction_words = {"偏多": "偏多", "净多": "偏多", "偏空": "偏空", "净空": "偏空"}
    action_words = ("翻多", "翻空", "加多", "减多", "加空", "减空", "回到中性")
    for text_value, evidence_ids in texts:
        cited = [by_id[item] for item in evidence_ids]
        allowed_symbols = set()
        allowed_name_tokens = set()
        for item in cited:
            if item.get("symbol"):
                allowed_symbols.add(item["symbol"])
                allowed_name_tokens.update(re.findall(r"[A-Z]{1,4}", item.get("name") or ""))
            for row in item.get("facts", {}).get("top_positions", []):
                allowed_symbols.add(row["symbol"])
                allowed_name_tokens.update(re.findall(r"[A-Z]{1,4}", row.get("name") or ""))
        symbol_tokens = set(re.findall(r"(?<![A-Z])[A-Z]{1,3}(?![A-Z])", text_value))
        unexpected = symbol_tokens - allowed_symbols - allowed_name_tokens - {"OI", "Q", "Z", "R"}
        if unexpected:
            raise ValueError(f"正文引用了不存在或未列证据的品种: {sorted(unexpected)}")
        product_signals = [signal for signal in signals if signal.get("scope") == "variety"]
        for signal in product_signals:
            if ((re.search(rf"(?<![A-Z]){re.escape(signal['symbol'])}(?![A-Z])", text_value)
                 or signal["name"] in text_value)
                    and signal["symbol"] not in allowed_symbols):
                raise ValueError(f"正文引用了未列证据的品种: {signal['symbol']}")
        for sentence in re.split(r"[。；;！!？?]", text_value):
            matching = [item for item in cited if item.get("scope") == "variety"
                        if (re.search(rf"(?<![A-Z]){re.escape(item['symbol'])}(?![A-Z])", sentence)
                            or item["name"] in sentence)]
            if len(matching) != 1:
                continue
            fact = matching[0]["facts"]
            for group_key, aliases in group_words.items():
                for word, normalized in direction_words.items():
                    claimed = any(re.search(
                        rf"{re.escape(alias)}.{{0,10}}{re.escape(word)}", sentence
                    ) for alias in aliases)
                    if claimed and fact[group_key]["direction"] != normalized:
                        raise ValueError(
                            f"{matching[0]['symbol']} {group_key} 方向与证据不符"
                        )
                for word in action_words:
                    claimed = any(re.search(
                        rf"{re.escape(alias)}.{{0,10}}{re.escape(word)}", sentence
                    ) for alias in aliases)
                    if claimed and fact[group_key]["action"] != word:
                        raise ValueError(
                            f"{matching[0]['symbol']} {group_key} 动作与证据不符"
                        )


def _parse_json_text(text):
    text = str(text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I)
    return json.loads(text)


def _call_deepseek(api_key, model, messages, max_tokens):
    return requests.post(
        DEEPSEEK_URL,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "messages": messages,
            "temperature": 0.1,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        },
        timeout=120,
    )


def generate_narrative(facts, signals, api_key, call_fn=_call_deepseek):
    """Generate validated prose; one repair attempt per model, then fallback."""
    prompt_signals = (
        [signal for signal in signals if signal.get("scope") == "variety"][:17]
        + [signal for signal in signals if signal.get("scope") == "group"]
    )
    input_payload = {
        "date": facts["data_date"],
        "prev_date": facts["prev_date"],
        "method": {
            "position_basis": facts["basis"],
            "score_note": "score只用于选择素材，不是交易预测",
        },
        "group_coverage": {
            key: value["coverage"] for key, value in facts["groups"].items()
        },
        "signals": prompt_signals,
        "output_schema": {
            "overall_summary": {"text": "中文结论", "evidence_ids": ["有效ID，2至5个"]},
            "group_notes": {
                key: {"text": "该组事实注释", "evidence_ids": ["有效ID，最多3个"]}
                for key in GROUP_SPECS
            },
            "focus_signal_ids": ["有效ID，最多5个"],
            "risk_notes": ["风险1", "风险2"],
        },
    }
    base = json.dumps(input_payload, ensure_ascii=False, separators=(",", ":"))
    last_error = None
    for model, max_tokens in (("deepseek-flash", 16000), ("deepseek-chat", 4000)):
        repair = ""
        for attempt in range(2):
            user = "根据以下证据编写席位日报叙事。不得添加输入之外的数字或品种。\n" + base + repair
            try:
                response = call_fn(api_key, model, [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user},
                ], max_tokens)
                if response.status_code != 200:
                    raise RuntimeError(f"HTTP {response.status_code}: {response.text[:200]}")
                parsed = _parse_json_text(response.json()["choices"][0]["message"]["content"])
                narrative = validate_narrative(parsed, prompt_signals)
                narrative["meta"] = {
                    "model": model,
                    "prompt_version": PROMPT_VERSION,
                    "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "validated": True,
                    "input_hash": digest(input_payload),
                    "attempt": attempt + 1,
                }
                return narrative
            except Exception as exc:  # noqa: BLE001 - validation failure gets one repair
                last_error = f"{type(exc).__name__}: {exc}"
                repair = f"\n上次输出未通过校验：{last_error}。请仅返回修正后的 JSON。"
    return fallback_narrative(facts, signals, reason=f"模型不可用或校验失败: {last_error}")


def load_goldman_contract(date, directory=None):
    root = Path(directory) if directory else SEAT_DIR
    path = root / f"goldman_contract_positions_{date}.json"
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return value if value.get("date") == date else None


def load_contract_positions(date, directory=None):
    """Load the auditable all-seat main/sub contract bundle for one date."""
    root = Path(directory) if directory else SEAT_DIR
    path = root / f"seat_contract_positions_{date}.json"
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    groups = value.get("groups") if isinstance(value, dict) else None
    if not isinstance(value, dict) or value.get("date") != date or not isinstance(groups, dict):
        return None
    if any(
        not isinstance(groups.get(key), dict)
        or groups[key].get("members") != [family[0] for family in spec["families"]]
        for key, spec in GROUP_SPECS.items()
    ):
        print(f"[警告] {path.name} 的席位成员口径已过期，请重算主次合约包；本次不载入旧包")
        return None
    return value


def load_html2canvas_source(directory=None):
    """Cache and verify html2canvas; reports embed the verified bytes."""
    root = Path(directory) if directory else SEAT_DIR / "vendor"
    path = root / "html2canvas-1.4.1.min.js"
    source = None
    if path.exists():
        source = path.read_bytes()
    if source is None or hashlib.sha256(source).hexdigest() != HTML2CANVAS_SHA256:
        response = requests.get(HTML2CANVAS_URL, timeout=30)
        response.raise_for_status()
        source = response.content
        if hashlib.sha256(source).hexdigest() != HTML2CANVAS_SHA256:
            raise RuntimeError("html2canvas 下载内容哈希不匹配")
        atomic_text(path, source.decode("utf-8"))
    return source.decode("utf-8")


def render_html(facts, narrative, signals, html2canvas_source):
    """Render the production observatory, independently of the frozen preview."""
    payload = json.dumps(
        {"facts": facts, "narrative": narrative, "signals": signals},
        ensure_ascii=False, separators=(",", ":"), allow_nan=False,
    ).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    template = (Path(__file__).parent / "templates" / "seat_observatory.html").read_text(encoding="utf-8")
    replacements = {
        "TITLE": html.escape(f"席位观察室 · {facts['data_date']}"),
        "DATA": payload,
        "HTML2CANVAS": html2canvas_source.replace("</script", "<\\/script"),
    }
    # One pass: user-controlled text cannot introduce additional substitutions.
    return re.sub(r"@@(TITLE|DATA|HTML2CANVAS)@@", lambda m: replacements[m[1]], template)


def publish_report(facts, narrative, signals, directory=None, html2canvas_source=None):
    root = Path(directory) if directory else SEAT_DIR
    root.mkdir(parents=True, exist_ok=True)
    if html2canvas_source is None:
        html2canvas_source = load_html2canvas_source()
    body = render_html(facts, narrative, signals, html2canvas_source)
    html_path = root / f"seat_report_{facts['data_date']}.html"
    json_path = root / f"seat_report_{facts['data_date']}.json"
    atomic_text(html_path, body)
    record = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "renderer_version": RENDERER_VERSION,
        "data_date": facts["data_date"],
        "prev_date": facts["prev_date"],
        "generated_at": facts["generated_at"],
        "input_hash": digest(facts),
        "html_hash": digest(body),
        "summary": narrative["overall_summary"]["text"],
        "facts": facts,
        "signals": signals,
        "narrative": narrative,
    }
    atomic_json(json_path, record)
    return html_path, json_path


def build_cached_facts(date, directory=None):
    """Recompute groups from raw caches without requesting market data or AI.

    Used when promoting a new renderer. Price/OI can only come from a verified
    same-date archive; old narratives and old member totals are never reused.
    """
    datetime.strptime(date, "%Y%m%d")
    root = Path(directory) if directory else SEAT_DIR
    frame = read_seat_csv(root / f"seat_data_{date}.csv")
    if date not in set(frame["trade_date"].astype(str)):
        raise ValueError(f"缓存中没有目标数据日 {date}")
    previous = prev_trade_date(frame, date)
    if not previous:
        raise ValueError("缺少前一交易日")
    archived = {}
    record_path = root / f"seat_report_{date}.json"
    if record_path.exists():
        record = json.loads(record_path.read_text(encoding="utf-8"))
        body = (root / f"seat_report_{date}.html").read_text(encoding="utf-8")
        if record.get("data_date") != date or record.get("html_hash") != digest(body):
            raise ValueError("旧归档校验失败，拒绝复用其行情快照")
        archived = record.get("facts") or {}
        if archived.get("data_date") != date or archived.get("prev_date") != previous:
            raise ValueError("旧归档事实日期与原始缓存不一致")
    dominant_path = root / f"dominant_{date}.json"
    universe = (json.loads(dominant_path.read_text(encoding="utf-8"))
                if dominant_path.exists() else archived.get("universe"))
    if not universe:
        raise ValueError("缺少同日主次合约映射，不能离线重建日报")
    from backend.pipeline.goldman_contract import build_seat_contract_bundle, load_contract_responses
    responses = load_contract_responses(universe, root / "goldman_contract_cache" / date)
    if responses:
        missing = {item[role]: "原始合约缓存缺失" for item in universe
                   for role in ("main", "sub") if item.get(role) and item[role] not in responses}
        contracts = build_seat_contract_bundle(universe, responses, missing, date, previous)
    else:
        contracts = load_contract_positions(date, root)
        if contracts and contracts.get("prev_date") != previous:
            contracts = None
        if contracts is None:
            print("[警告] 无可用合约缓存，仅生成品种汇总看板")
    source_path = root / f"seat_sources_{date}.json"
    sources = json.loads(source_path.read_text(encoding="utf-8")) if source_path.exists() else None
    return build_facts(
        frame, date, previous, universe=universe, source_manifest=sources,
        market_context=archived.get("market_context"),
        goldman_contract=load_goldman_contract(date, root), contract_positions=contracts,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", help="数据日 YYYYMMDD；默认取最新 seat_data")
    parser.add_argument("--no-ai", action="store_true", help="只用确定性模板，不调用模型")
    parser.add_argument("--cached-only", action="store_true",
                        help="仅用原始缓存与已校验同日行情重算，不请求米筐或 AI")
    args = parser.parse_args(argv)
    candidates = sorted(SEAT_DIR.glob("seat_data_*.csv"))
    date = args.date or (candidates[-1].stem.removeprefix("seat_data_") if candidates else None)
    if not date:
        raise SystemExit("缺少 seat_data 缓存")
    if args.cached_only:
        facts = build_cached_facts(date)
        signals = build_signals(facts)
        narrative = fallback_narrative(facts, signals, "缓存重建，未调用模型")
        html_path, json_path = publish_report(facts, narrative, signals)
        if facts.get("contract_positions") is not None:
            atomic_json(SEAT_DIR / f"seat_contract_positions_{date}.json", facts["contract_positions"])
        print(f"[席位观察室] {html_path}\n[审计归档] {json_path}")
        return
    csv_path = SEAT_DIR / f"seat_data_{date}.csv"
    if not csv_path.exists():
        raise SystemExit(f"缺少 {csv_path}")
    frame = read_seat_csv(csv_path)
    previous = prev_trade_date(frame, date)
    if not previous:
        raise SystemExit("缺少前一交易日")
    from backend.pipeline.dominant_fetch import load_or_fetch
    dominants, _ = load_or_fetch(date)
    universe = [row for row in dominants if str(row.get("symbol") or "").upper() not in FINANCIAL_SYMBOLS]
    market = fetch_market_context(dominants, date, previous)
    source_path = SEAT_DIR / f"seat_sources_{date}.json"
    source_manifest = json.loads(source_path.read_text(encoding="utf-8")) if source_path.exists() else None
    facts = build_facts(
        frame, date, previous, universe=universe, source_manifest=source_manifest,
        market_context=market, goldman_contract=load_goldman_contract(date),
        contract_positions=load_contract_positions(date),
    )
    signals = build_signals(facts)
    api_key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("DEEPSEEK_API")
    narrative = (generate_narrative(facts, signals, api_key)
                 if api_key and not args.no_ai else fallback_narrative(facts, signals, "未启用模型"))
    html_path, json_path = publish_report(facts, narrative, signals)
    print(f"[席位日报] {html_path}")
    print(f"[席位日报] {json_path}")


if __name__ == "__main__":
    main()
