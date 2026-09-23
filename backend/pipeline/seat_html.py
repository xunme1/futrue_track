# -*- coding: utf-8 -*-
"""席位持仓 HTML 日报：事实计算、受约束叙事、单文件交互报告。"""
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


REPORT_SCHEMA_VERSION = 2
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
            "members": sorted({alias for family in spec["families"] for alias in family}),
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
    if value.get("date") != date or not isinstance(groups, dict):
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


CSS = r"""
:root{color-scheme:dark;--bg:#0e1e33;--panel:#142a46;--panel2:#18314f;--edge:#294967;--gold:#d9b98a;--text:#eee9df;--muted:#9caec3;--green:#26a67b;--red:#d45858;--track:#334c68;--focus:#8ab4d8}
*{box-sizing:border-box}html,body{margin:0;background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif}button,input,select{font:inherit}button{cursor:pointer}.report{max-width:1180px;margin:0 auto;padding:36px 34px 54px}.eyebrow{color:var(--gold);font-size:12px;font-weight:700;letter-spacing:.14em}.hero{display:flex;justify-content:space-between;gap:24px;border-bottom:1px solid rgba(217,185,138,.55);padding-bottom:24px}.hero h1{font-size:30px;margin:10px 0 8px}.sub,.muted{color:var(--muted)}.date{text-align:right;font-size:20px;font-weight:700}.date small{display:block;font-size:12px;font-weight:400;color:var(--muted);margin-top:7px}.coverage{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin:18px 0}.stat{background:var(--panel);border:1px solid var(--edge);padding:12px 14px;border-radius:8px}.stat b{font-size:20px;display:block;margin-top:4px}.narrative{background:linear-gradient(135deg,var(--panel2),var(--panel));border-left:3px solid var(--gold);padding:18px 20px;margin:16px 0 18px;line-height:1.85}.narrative h2{font-size:15px;color:var(--gold);margin:0 0 7px}.risk{font-size:12px;color:var(--muted);margin-top:8px}.toolbar{position:sticky;top:0;z-index:20;background:rgba(14,30,51,.96);backdrop-filter:blur(8px);display:flex;gap:8px;align-items:center;flex-wrap:wrap;padding:12px 0;border-bottom:1px solid var(--edge)}.toolbar button,.toolbar select,.toolbar input{border:1px solid var(--edge);background:var(--panel);color:var(--text);border-radius:6px;padding:8px 10px}.toolbar button:hover,.toolbar button:focus-visible{border-color:var(--gold)}.toolbar button.active{background:var(--gold);color:#102038;border-color:var(--gold)}.picker{position:relative}.picker-panel{position:absolute;top:42px;left:0;width:360px;max-height:360px;overflow:auto;background:#10233a;border:1px solid var(--edge);border-radius:8px;padding:10px;box-shadow:0 14px 36px rgba(0,0,0,.35)}.picker-panel[hidden]{display:none}.picker-search{width:100%;margin-bottom:8px}.pick-row{display:flex;align-items:center;gap:8px;padding:6px;border-radius:5px}.pick-row:hover{background:var(--panel)}.pick-row input{accent-color:var(--gold)}.selection-status{font-size:12px;color:var(--muted)}.seat-section{margin-top:20px;background:var(--panel);border:1px solid var(--edge);border-radius:10px;overflow:hidden}.section-head{display:flex;justify-content:space-between;gap:16px;align-items:flex-start;padding:18px 20px;background:rgba(255,255,255,.018)}.section-head h2{margin:0;font-size:21px}.section-head p{margin:7px 0 0;color:var(--muted);font-size:12px;line-height:1.7}.counts{white-space:nowrap;color:var(--muted);font-size:12px}.group-note{padding:13px 20px;border-top:1px solid var(--edge);border-bottom:1px solid var(--edge);line-height:1.75;color:#dbe4ef;font-size:13px}.row-head,.position-row{display:grid;grid-template-columns:170px 175px minmax(310px,1fr) 175px;gap:12px;align-items:center;padding:0 20px}.row-head{height:38px;color:var(--muted);font-size:11px}.position-row{min-height:64px;border-top:1px solid rgba(84,116,145,.26)}.position-row:nth-child(even){background:rgba(255,255,255,.018)}.symbol strong{display:block;font-size:14px}.symbol span,.facts small{color:var(--muted);font-size:11px}.facts{font-variant-numeric:tabular-nums}.facts b{font-size:14px}.tag{display:inline-block;margin-left:5px;padding:2px 5px;border-radius:3px;font-size:10px;background:#29445f;color:#c8d6e5}.tag.fino{color:#b5e6d4}.tag.rq{color:#f2d1a4}.long{color:var(--green)}.short{color:var(--red)}.track svg{width:100%;height:42px;display:block}.market{font-size:12px;font-variant-numeric:tabular-nums}.market div{margin:3px 0}.missing{grid-column:2/5;color:var(--muted);font-size:13px}.empty{padding:28px;text-align:center;color:var(--muted)}details.contracts{border-top:1px solid var(--edge);padding:0 20px 16px}details.contracts summary{padding:14px 0;color:var(--gold);cursor:pointer}.contract-table{width:100%;border-collapse:collapse;font-size:12px}.contract-table th,.contract-table td{padding:8px;border-top:1px solid rgba(84,116,145,.3);text-align:right}.contract-table th:first-child,.contract-table td:first-child{text-align:left}.footer{margin-top:24px;border-top:1px solid rgba(217,185,138,.5);padding-top:18px;color:var(--muted);font-size:11px;line-height:1.8}.export-mode .no-export{display:none!important}.export-mode .toolbar{display:none!important}.export-mode .report{max-width:1180px;padding-top:28px}
@media(max-width:820px){.report{padding:22px 14px}.hero{display:block}.date{text-align:left;margin-top:14px}.coverage{grid-template-columns:repeat(2,1fr)}.row-head{display:none}.position-row{grid-template-columns:1fr 1fr;padding:12px 14px}.track{grid-column:1/3}.market{text-align:right}.picker-panel{position:fixed;left:12px;right:12px;top:92px;width:auto}.section-head{display:block}.counts{margin-top:8px}}
.badge{display:inline-block;padding:1px 7px;border-radius:4px;font-size:11px;font-weight:700;line-height:1.6}
.badge.long{background:rgba(38,166,123,.16);color:var(--green)}
.badge.short{background:rgba(212,88,88,.16);color:var(--red)}
.badge.flat{background:rgba(156,174,195,.12);color:var(--muted)}
.overview{display:flex;gap:12px;font-size:11px;color:var(--muted);margin-bottom:2px}
.overview .ov-item.current{color:var(--gold)}
.partial-note{font-size:11px;color:var(--muted);margin-top:2px}
.contract-panel{border-top:1px solid var(--edge);background:rgba(5,16,30,.17)}
.contract-panel>summary{display:flex;justify-content:space-between;gap:14px;align-items:center;padding:14px 20px;color:var(--gold);cursor:pointer;font-weight:700;list-style:none}
.contract-panel>summary::-webkit-details-marker{display:none}.contract-panel>summary:after{content:'展开';font-size:11px;font-weight:400;color:var(--muted)}.contract-panel[open]>summary:after{content:'收起'}
.contract-summary-meta{font-size:11px;font-weight:400;color:var(--muted);text-align:right}.contract-body{border-top:1px solid rgba(84,116,145,.35);padding:14px 20px 18px}
.contract-tabs{display:flex;gap:8px;align-items:center;margin-bottom:10px}.contract-tabs button{border:1px solid var(--edge);background:#10233a;color:var(--text);border-radius:6px;padding:7px 13px}.contract-tabs button.active{background:var(--gold);border-color:var(--gold);color:#102038;font-weight:700}.contract-tab-note{margin-left:auto;color:var(--muted);font-size:11px}
.contract-chart-scroll{overflow-x:auto;border:1px solid rgba(84,116,145,.35);border-radius:7px;background:#132a47}.contract-chart{min-width:940px}.contract-chart svg{display:block;width:100%;height:auto}.contract-chart-empty{padding:42px;text-align:center;color:var(--muted)}
.contract-detail{margin-top:12px;border:1px solid rgba(84,116,145,.35);border-radius:7px}.contract-detail>summary{padding:11px 13px;color:#dbe4ef;cursor:pointer}.contract-detail-tools{padding:0 12px 10px}.contract-detail-search{width:100%;border:1px solid var(--edge);background:#10233a;color:var(--text);border-radius:6px;padding:8px 10px}.contract-table-wrap{max-height:480px;overflow:auto;border-top:1px solid rgba(84,116,145,.35)}.contract-detail-table{width:100%;min-width:980px;border-collapse:collapse;font-size:11px}.contract-detail-table th{position:sticky;top:0;z-index:2;background:#10233a;color:var(--muted)}.contract-detail-table th,.contract-detail-table td{padding:8px 9px;border-bottom:1px solid rgba(84,116,145,.24);text-align:left;vertical-align:top}.contract-detail-table tr:hover td{background:rgba(255,255,255,.018)}.contract-value{font-variant-numeric:tabular-nums;white-space:nowrap}.member-parts{display:flex;gap:4px 9px;flex-wrap:wrap;color:var(--muted)}.member-parts b{color:#dbe4ef;font-weight:500}.contract-status{white-space:nowrap}.contract-partial{display:inline-block;margin-left:4px;padding:1px 5px;border-radius:3px;color:#e8c58f;background:rgba(217,185,138,.12);font-size:9px}
@media(max-width:820px){.contract-panel>summary{display:block;padding:13px 14px}.contract-summary-meta{display:block;text-align:left;margin-top:5px}.contract-body{padding:12px 10px}.contract-tabs{flex-wrap:wrap}.contract-tab-note{width:100%;margin-left:0}.contract-chart{min-width:900px}}
"""


JS = r"""
const payload=JSON.parse(document.getElementById('report-data').textContent);
const facts=payload.facts,narrative=payload.narrative;
const groupOrder=['goldman','major','retail'];
const state={limit:10,selected:[],query:''};
const esc=(v)=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt=(v)=>{if(v===null||v===undefined)return '—';const s=v>0?'+':'';const a=Math.abs(v);return a>=10000?`${s}${(v/10000).toFixed(1)}万`:`${s}${Math.round(v).toLocaleString('zh-CN')}`};
const sourceName=(s)=>s==='finoview'?'繁微':s==='ricequant'?'米筐':s==='finoview-legacy'?'繁微·旧档':'不可用';
const sourceClass=(s)=>s==='ricequant'?'rq':'fino';
const indexes={};groupOrder.forEach(k=>indexes[k]=Object.fromEntries(facts.groups[k].varieties.map(x=>[x.symbol,x])));
function defaultRows(key){return facts.groups[key].varieties.filter(x=>x.available).sort((a,b)=>Math.abs(b.net_today)-Math.abs(a.net_today)||a.symbol.localeCompare(b.symbol)).slice(0,state.limit)}
function visibleRows(key){return state.selected.length?state.selected.map(s=>indexes[key][s]||{symbol:s,name:s,available:false,missing_reason:'该组无数据'}):defaultRows(key)}
function sourceNote(row){const meta=(facts.source_details||{})[row.symbol]||{};return row.source==='mixed-invalid'?'检测到同一品种跨源混合或来源声明不一致，已拒绝计算':meta.fallback_reason?`繁微不可用，整段历史由米筐补缺：${meta.fallback_reason}`:row.source==='finoview'?'繁微品种全合约排名':row.source==='finoview-legacy'?'旧缓存（繁微品种全合约排名）':meta.error||'数据源不可用'}
function trackSvg(row){if(!row.available)return '';
 const x=v=>14+(Math.max(-1,Math.min(1,v??0))+1)*266;
 const xt=x(row.relative_today),xp=x(row.relative_prev),color=row.net_today>=0?'var(--green)':'var(--red)';
 let previous='';if(row.prev_comparable){const dir=xt>=xp?1:-1;const tip=xt-dir*7;previous=`<circle cx="${xp}" cy="21" r="5" fill="var(--panel)" stroke="${color}" stroke-width="2"/><line x1="${xp}" y1="21" x2="${tip}" y2="21" stroke="${color}" stroke-width="3"/><polygon points="${xt},21 ${tip},16 ${tip},26" fill="${color}"/>`}
 return `<svg viewBox="0 0 560 42" role="img" aria-label="${esc(row.name)}近20日净仓相对位置"><line x1="14" y1="21" x2="546" y2="21" stroke="var(--track)" stroke-width="3" stroke-linecap="round"/><line x1="280" y1="8" x2="280" y2="34" stroke="var(--gold)" opacity=".65"/>${previous}<circle cx="${xt}" cy="21" r="6" fill="${color}" stroke="var(--text)" stroke-width="1.2"/><text x="14" y="11" fill="var(--muted)" font-size="9">净空</text><text x="546" y="11" text-anchor="end" fill="var(--muted)" font-size="9">净多</text></svg>`}
function badgeCls(text){if(!text)return 'flat';if(/多$/.test(text))return 'long';if(/空$/.test(text))return 'short';return 'flat'}
function dirBadge(text){return `<span class="badge ${badgeCls(text)}">${esc(text)}</span>`}
function overviewStrip(symbol,currentKey){return `<div class="overview">`+groupOrder.map(k=>{const r=indexes[k][symbol];const inner=(r&&r.available)?dirBadge(r.direction):'<span class="badge flat">未披露</span>';return `<span class="ov-item${k===currentKey?' current':''}">${facts.groups[k].short_name} ${inner}</span>`}).join('')+`</div>`}
function rowHtml(row,groupKey){if(!row||!row.available)return `<div class="position-row"><div class="symbol"><strong>${esc(row?.name||row?.symbol||'—')}</strong><span>${esc(row?.symbol||'')} ${row?.source?`<i class="tag ${sourceClass(row.source)}" title="${esc(sourceNote(row))}">${sourceName(row.source)}</i>`:''}</span></div><div class="missing">— / ${esc(row?.missing_reason||'披露不完整')}</div></div>`;
 const cls=row.net_today>=0?'long':'short',m=row.market||{};
 const partialNote=row.partial&&row.missing_members?.length?`<div class="partial-note">未披露按零计：${esc(row.missing_members.join(' / '))}</div>`:'';
 const action=row.action?dirBadge(row.action):`<span class="muted">${esc(row.comparison_note||'变化未知')}</span>`;
 return `<div class="position-row"><div class="symbol"><strong>${esc(row.name)}</strong><span>${esc(row.symbol)} <i class="tag ${sourceClass(row.source)}" title="${esc(sourceNote(row))}">${sourceName(row.source)}</i></span></div><div class="facts"><b class="${cls}">${fmt(row.net_today)} 手</b><small>${dirBadge(row.direction)} ${action} · Δ ${fmt(row.net_change)}</small></div><div class="track">${overviewStrip(row.symbol,groupKey)}${trackSvg(row)}${partialNote}</div><div class="market"><div>价 ${m.available?fmt(m.price_return_pct)+'%':'—'}</div><div>OI ${m.available?fmt(m.oi_change):'—'} <span class="muted">${esc(m.contract||'')}</span></div></div></div>`}
const contractState=Object.fromEntries(groupOrder.map(k=>[k,{open:false,side:'long',detailOpen:false,query:''}]));
function legacyContractDetails(rows){const block=facts.goldman_contract;if(!block||!Array.isArray(block.varieties))return '';
 const map=Object.fromEntries(block.varieties.map(x=>[x.symbol,x]));const body=rows.map(row=>{const x=map[row.symbol];if(!x)return `<tr><td>${esc(row.name)} ${esc(row.symbol)}</td><td colspan="4">— / 无主次合约披露</td></tr>`;const cell=p=>p&&p.available?`${fmt(p.today)} / Δ ${fmt(p.change)}`:'— / 未披露';return `<tr><td>${esc(row.name)} ${esc(row.symbol)}</td><td>${esc(x.main?.contract||'—')}</td><td>${cell(x.main)}</td><td>${esc(x.sub?.contract||'—')}</td><td>${cell(x.sub)}</td></tr>`}).join('');
 return `<details class="contracts"><summary>主力 / 次主力合约明细（展开）</summary><div class="table-wrap"><table class="contract-table"><thead><tr><th>品种</th><th>主力</th><th>主力净仓 / 变化</th><th>次主力</th><th>次主力净仓 / 变化</th></tr></thead><tbody>${body}</tbody></table></div></details>`}
function contractPanel(key,rows){const root=facts.contract_positions,group=root?.groups?.[key];if(!group)return key==='goldman'?legacyContractDetails(rows):'';const c=group.coverage,s=contractState[key];return `<details class="contract-panel" id="contract-panel-${key}" ${s.open?'open':''}><summary><span>主力 / 次主力合约净仓榜</span><span class="contract-summary-meta">主力披露 ${c.main_available}（部分 ${c.main_partial}） · 次主力 ${c.sub_available}（部分 ${c.sub_partial}） · 净多 ${c.long_candidates} / 净空 ${c.short_candidates}</span></summary><div class="contract-body"><div class="contract-tabs"><button type="button" data-contract-side="long" class="${s.side==='long'?'active':''}">净多榜</button><button type="button" data-contract-side="short" class="${s.side==='short'?'active':''}">净空榜</button><span class="contract-tab-note">按主力合约今日净仓排序 · 次主力配对展示</span></div><div class="contract-chart-scroll"><div class="contract-chart" id="contract-chart-${key}"></div></div><details class="contract-detail" id="contract-detail-${key}" ${s.detailOpen?'open':''}><summary>全部品种与会员贡献明细（${group.varieties.length}）</summary><div class="contract-detail-tools"><input class="contract-detail-search" id="contract-search-${key}" value="${esc(s.query)}" placeholder="搜索品种、代码或合约"></div><div class="contract-table-wrap" id="contract-table-${key}"></div></details></div></details>`}
function chartBar(pos,y,height,zero,x,limit,isMain){if(!pos?.available)return `<text x="${zero}" y="${y+3}" text-anchor="middle" fill="#8fa3bd" font-size="10">— 未披露</text>`;const current=pos.today,previous=pos.previous,xc=x(current);let bars='';if(previous!==null&&previous!==undefined){const xp=x(previous);bars+=`<rect x="${Math.min(zero,xp)}" y="${y-height/2}" width="${Math.max(1,Math.abs(xp-zero))}" height="${height}" fill="#566d88" opacity=".82"/>`;if(current!==previous)bars+=`<rect x="${Math.min(xp,xc)}" y="${y-height*.36}" width="${Math.max(1,Math.abs(xc-xp))}" height="${height*.72}" fill="${current>previous?'#26a67b':'#d45858'}"/>`}else bars+=`<rect x="${Math.min(zero,xc)}" y="${y-height/2}" width="${Math.max(1,Math.abs(xc-zero))}" height="${height}" fill="#566d88" opacity=".58"/>`;const missing=pos.partial?`部分披露，缺 ${pos.missing_members.join(' / ')}`:'已披露';return `<g tabindex="0"><title>${esc(pos.contract||'')}：今 ${fmt(current)}，变化 ${fmt(pos.change)}；${esc(missing)}</title>${bars}<circle cx="${xc}" cy="${y}" r="${isMain?5:3.5}" fill="#d9b98a" stroke="#eee9df" stroke-width=".7"/><text x="${xc+(current>=0?7:-7)}" y="${y+3}" text-anchor="${current>=0?'start':'end'}" fill="${isMain?'#eee9df':'#9caec3'}" font-size="${isMain?10:9}" font-weight="${isMain?'700':'400'}">今 ${fmt(current)} Δ ${fmt(pos.change)}</text></g>`}
function contractChartSvg(key,side){const root=facts.contract_positions,group=root.groups[key],bySymbol=Object.fromEntries(group.varieties.map(row=>[row.symbol,row])),items=(group.rankings[side]||[]).map(value=>typeof value==='string'?bySymbol[value]:value).filter(Boolean),target=root.top_n||15;if(!items.length)return `<div class="contract-chart-empty">当前无可排名的主力合约净${side==='long'?'多':'空'}持仓</div>`;const width=1120,left=210,right=900,top=44,pair=62,bottom=42,height=top+items.length*pair+bottom;const values=[];items.forEach(item=>['main','sub'].forEach(k=>{const p=item[k];if(p?.available)[p.today,p.previous].forEach(v=>{if(v!==null&&v!==undefined)values.push(Math.abs(v))})}));const limit=Math.max(...values,1)*1.28,x=v=>left+(Math.max(-limit,Math.min(limit,v??0))+limit)/(2*limit)*(right-left),zero=x(0);const ticks=[-limit,-limit/2,0,limit/2,limit];let body=ticks.map(v=>`<line x1="${x(v)}" y1="28" x2="${x(v)}" y2="${height-26}" stroke="${v===0?'#d9b98a':'#294967'}" stroke-width="${v===0?1.2:.7}" opacity=".8"/><text x="${x(v)}" y="${height-8}" text-anchor="middle" fill="#9caec3" font-size="9">${fmt(Math.round(v))}</text>`).join('');items.forEach((item,i)=>{const ym=top+i*pair,ys=ym+24,partial=item.main.partial?' · 部分':'';body+=`<line x1="0" y1="${ym+39}" x2="${width}" y2="${ym+39}" stroke="#294967" stroke-width=".6" opacity=".65"/><text x="198" y="${ym+3}" text-anchor="end" fill="#eee9df" font-size="11">${esc(item.name)} ${esc(item.symbol)} · 主 ${esc(item.main.contract||'—')}${partial}</text><text x="198" y="${ys+3}" text-anchor="end" fill="#9caec3" font-size="10">次 ${esc(item.sub.contract||'—')}</text>${chartBar(item.main,ym,14,zero,x,limit,true)}${chartBar(item.sub,ys,8,zero,x,limit,false)}`});return `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${esc(group.name)}主次合约净${side==='long'?'多':'空'}排名"><text x="10" y="19" fill="#d9b98a" font-size="12" font-weight="700">净${side==='long'?'多':'空'} Top${items.length}/${target}</text>${body}</svg>`}
function memberParts(pos){if(!pos?.members)return '—';return `<div class="member-parts">${pos.members.map(m=>`<span><b>${esc(m.name)}</b> ${m.available?fmt(m.today):'未披露'}</span>`).join('')}</div>`}
function contractValue(pos){if(!pos?.available)return `<span class="muted">${esc(pos?.missing_reason||'未披露')}</span>`;const partial=pos.partial?`<span class="contract-partial" title="缺 ${esc(pos.missing_members.join(' / '))}">部分</span>`:'';return `<span class="contract-value">${fmt(pos.today)} · Δ ${fmt(pos.change)}</span>${partial}`}
function contractTableHtml(key){const group=facts.contract_positions.groups[key],q=contractState[key].query.trim().toLowerCase();const rows=group.varieties.filter(row=>!q||`${row.symbol} ${row.name} ${row.main?.contract||''} ${row.sub?.contract||''}`.toLowerCase().includes(q));if(!rows.length)return '<div class="empty">没有匹配的品种</div>';return `<table class="contract-detail-table"><thead><tr><th>品种</th><th>主力合约 / 组净仓</th><th>主力会员贡献</th><th>次主力合约 / 组净仓</th><th>次主力会员贡献</th></tr></thead><tbody>${rows.map(row=>`<tr><td><strong>${esc(row.name)} ${esc(row.symbol)}</strong></td><td><span class="muted">${esc(row.main?.contract||'—')}</span><br>${contractValue(row.main)}</td><td>${memberParts(row.main)}</td><td><span class="muted">${esc(row.sub?.contract||'—')}</span><br>${contractValue(row.sub)}</td><td>${memberParts(row.sub)}</td></tr>`).join('')}</tbody></table>`}
function renderContractChart(key){const host=document.getElementById(`contract-chart-${key}`);if(!host)return;host.innerHTML=contractChartSvg(key,contractState[key].side);document.querySelectorAll(`#contract-panel-${key} [data-contract-side]`).forEach(button=>button.classList.toggle('active',button.dataset.contractSide===contractState[key].side))}
function renderContractTable(key){const host=document.getElementById(`contract-table-${key}`);if(host)host.innerHTML=contractTableHtml(key)}
function bindContractPanels(){if(!facts.contract_positions?.groups)return;groupOrder.forEach(key=>{if(!facts.contract_positions.groups[key])return;const panel=document.getElementById(`contract-panel-${key}`),detail=document.getElementById(`contract-detail-${key}`),search=document.getElementById(`contract-search-${key}`);panel?.addEventListener('toggle',()=>{contractState[key].open=panel.open;if(panel.open)renderContractChart(key)});panel?.querySelectorAll('[data-contract-side]').forEach(button=>button.addEventListener('click',()=>{contractState[key].side=button.dataset.contractSide;renderContractChart(key)}));detail?.addEventListener('toggle',()=>{contractState[key].detailOpen=detail.open;if(detail.open)renderContractTable(key)});search?.addEventListener('input',event=>{contractState[key].query=event.target.value;renderContractTable(key)});if(panel?.open)renderContractChart(key);if(detail?.open)renderContractTable(key)})}
function renderGroup(key){const group=facts.groups[key],rows=visibleRows(key),c=group.coverage,note=narrative.group_notes[key];return `<section class="seat-section" id="group-${key}"><div class="section-head"><div><h2>${esc(group.code)} · ${esc(group.name)}</h2><p>${esc(group.members.join(' / '))}</p></div><div class="counts">披露 ${c.available} · 多 ${c.long} / 空 ${c.short} · 今昨日可比 ${c.prev_comparable}</div></div><div class="group-note">${esc(note.text)}</div>${contractPanel(key,rows)}<div class="row-head"><span>品种 / 来源</span><span>净仓 / 动作</span><span>三席方向 / 近20日相对定位</span><span>主力价格 / OI</span></div><div>${rows.length?rows.map(r=>rowHtml(r,key)).join(''):'<div class="empty">当前没有可展示品种</div>'}</div></section>`}
function render(){document.getElementById('sections').innerHTML=groupOrder.map(renderGroup).join('');document.querySelectorAll('[data-limit]').forEach(b=>b.classList.toggle('active',Number(b.dataset.limit)===state.limit&&!state.selected.length));document.getElementById('selection-status').textContent=state.selected.length?`对照模式：已选 ${state.selected.length} / 20`:`默认模式：每组前 ${state.limit}`;renderPicker();bindContractPanels()}
function renderPicker(){const q=state.query.trim().toLowerCase();const rows=facts.universe.filter(x=>!q||`${x.symbol} ${x.name}`.toLowerCase().includes(q));document.getElementById('pick-list').innerHTML=rows.map(x=>`<label class="pick-row"><input type="checkbox" value="${esc(x.symbol)}" ${state.selected.includes(x.symbol)?'checked':''}><span>${esc(x.name)} ${esc(x.symbol)}</span></label>`).join('')}
document.getElementById('picker-toggle').addEventListener('click',()=>{const p=document.getElementById('picker-panel');p.hidden=!p.hidden});
document.getElementById('picker-search').addEventListener('input',e=>{state.query=e.target.value;renderPicker()});
document.getElementById('pick-list').addEventListener('change',e=>{if(e.target.type!=='checkbox')return;const s=e.target.value;if(e.target.checked){if(state.selected.length>=20){e.target.checked=false;document.getElementById('selection-status').textContent='最多同时选择 20 个品种';return}state.selected.push(s)}else state.selected=state.selected.filter(x=>x!==s);render()});
document.querySelectorAll('[data-limit]').forEach(b=>b.addEventListener('click',()=>{state.limit=Number(b.dataset.limit);state.selected=[];render()}));
document.getElementById('reset').addEventListener('click',()=>{state.selected=[];state.query='';document.getElementById('picker-search').value='';render()});
document.getElementById('download-html').addEventListener('click',()=>{const blob=new Blob(['<!DOCTYPE html>\n'+document.documentElement.outerHTML],{type:'text/html;charset=utf-8'});download(URL.createObjectURL(blob),`seat_report_${facts.data_date}.html`,true)});
function download(url,name,revoke=false){const a=document.createElement('a');a.href=url;a.download=name;a.click();if(revoke)setTimeout(()=>URL.revokeObjectURL(url),1000)}
document.getElementById('export-png').addEventListener('click',async()=>{const button=document.getElementById('export-png');button.disabled=true;button.textContent='导出中…';document.body.classList.add('export-mode');try{if(typeof html2canvas!=='function')throw new Error('长图组件未加载');const canvas=await html2canvas(document.getElementById('report'),{scale:2,backgroundColor:'#0e1e33',logging:false,useCORS:false,scrollX:0,scrollY:0,windowWidth:1180});const blob=await new Promise((resolve,reject)=>canvas.toBlob(value=>value?resolve(value):reject(new Error('PNG编码失败')),'image/png'));download(URL.createObjectURL(blob),`seat_report_${facts.data_date}.png`,true)}catch(err){alert(`导出失败：${err.message}`)}finally{document.body.classList.remove('export-mode');button.disabled=false;button.textContent='导出 PNG'}});
render();
"""


def render_html(facts, narrative, signals, html2canvas_source):
    payload = json.dumps(
        {"facts": facts, "narrative": narrative, "signals": signals},
        ensure_ascii=False, separators=(",", ":"), allow_nan=False,
    ).replace("</", "<\\/")
    coverage = facts["source_coverage"]
    rq = coverage.get("ricequant", 0)
    fino = coverage.get("finoview", 0) + coverage.get("finoview-legacy", 0)
    missing = sum(value for key, value in coverage.items()
                  if key not in ("finoview", "finoview-legacy", "ricequant"))
    overall = narrative["overall_summary"]["text"]
    risks = "".join(f"<div>• {html.escape(value)}</div>" for value in narrative["risk_notes"])
    source = html2canvas_source.replace("</script", "<\\/script")
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>席位持仓日报 · {html.escape(facts['data_date'])}</title><style>{CSS}</style></head><body>
<main class="report" id="report"><header class="hero"><div><div class="eyebrow">BROKER POSITION · HTML DAILY</div><h1>席位持仓 · 每日观察</h1><div class="sub">先核验披露完整性，再读存量、变化、分歧与价格确认</div></div><div class="date">{html.escape(facts['data_date'])}<small>对比 {html.escape(facts['prev_date'])}</small></div></header>
<div class="coverage"><div class="stat"><span class="muted">商品品种池</span><b>{len(facts['universe'])}</b></div><div class="stat"><span class="muted">米筐数据</span><b>{rq}</b></div><div class="stat"><span class="muted">历史繁微</span><b>{fino}</b></div><div class="stat"><span class="muted">不可用/异常</span><b>{missing}</b></div></div>
<section class="narrative"><h2>今日跨席位观察</h2><div>{html.escape(overall)}</div><div class="risk">{risks}</div></section>
<div class="toolbar no-export"><div class="picker"><button id="picker-toggle" type="button">选择品种</button><div class="picker-panel" id="picker-panel" hidden><input id="picker-search" class="picker-search" placeholder="搜索名称或代码"><div id="pick-list"></div></div></div><span class="muted">显示</span>{''.join(f'<button type="button" data-limit="{n}">{n}</button>' for n in (5,10,15,20))}<button id="reset" type="button">恢复默认</button><span class="selection-status" id="selection-status" aria-live="polite"></span><button id="download-html" type="button">下载 HTML</button><button id="export-png" type="button">导出 PNG</button></div>
<div id="sections"></div><footer class="footer">口径：{html.escape(facts['basis'])}。净仓=已披露成员持多量-持空量；组内未披露成员按零计入并在持仓条下方标注，未披露不代表真实持仓为零。价格与 OI 使用目标日主力合约，并固定同一合约比较今昨日。图中信号是持仓观察，不构成投资建议。</footer></main>
<script id="report-data" type="application/json">{payload}</script><script>{source}</script><script>{JS}</script></body></html>"""


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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", help="数据日 YYYYMMDD；默认取最新 seat_data")
    parser.add_argument("--no-ai", action="store_true", help="只用确定性模板，不调用模型")
    args = parser.parse_args(argv)
    candidates = sorted(SEAT_DIR.glob("seat_data_*.csv"))
    date = args.date or (candidates[-1].stem.removeprefix("seat_data_") if candidates else None)
    if not date:
        raise SystemExit("缺少 seat_data 缓存")
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
