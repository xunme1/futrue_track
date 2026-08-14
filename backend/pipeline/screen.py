# -*- coding: utf-8 -*-
"""期货品种筛选器：根据每日策略产物筛选趋势、转换与预警品种。

用法：
    python -m backend.pipeline.screen
    python -m backend.pipeline.screen --symbols rb2610.SHF ag2610
    python -m backend.pipeline.screen --lookback 8 --atr-window 14

输入为 ``data/json/*.json``，不连接任何外部数据源。输出为终端摘要，以及
``data/screening/latest.json`` 和 ``data/screening/latest.csv``。
"""
import argparse
import csv
import json
import math
from datetime import datetime
from pathlib import Path

from backend.core.config import load_contracts
from backend.core.timeframes import json_dir as timeframe_json_dir, output_dir


BUCKETS = (
    "long_trend",
    "short_trend",
    "long_to_short",
    "short_to_long",
    "long_to_short_warning",
    "short_to_long_warning",
    "short_pressure_warning",
    "long_support_warning",
)

BUCKET_LABELS = {
    "long_trend": "多头趋势",
    "short_trend": "空头趋势",
    "long_to_short": "多转空",
    "short_to_long": "空转多",
    "long_to_short_warning": "多转空预警",
    "short_to_long_warning": "空转多预警",
    "short_pressure_warning": "空头压力预警",
    "long_support_warning": "多头支撑预警",
}


def _number(value):
    """将有效数值转为 float；None、NaN 和非数值返回 None。"""
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def wilder_atr(ohlc, window=14):
    """计算 Wilder ATR，返回与输入等长、预热期为 None 的列表。

    ``ohlc`` 使用看板 JSON 的顺序：[open, close, low, high]。
    """
    if window < 1:
        raise ValueError("atr window 必须大于 0")

    atr = [None] * len(ohlc)
    trs = []
    previous_close = None
    for bar in ohlc:
        if not isinstance(bar, (list, tuple)) or len(bar) < 4:
            trs.append(None)
            previous_close = None
            continue
        close, low, high = _number(bar[1]), _number(bar[2]), _number(bar[3])
        if close is None or low is None or high is None:
            trs.append(None)
            previous_close = None
            continue
        tr = high - low if previous_close is None else max(
            high - low, abs(high - previous_close), abs(low - previous_close)
        )
        trs.append(tr)
        previous_close = close

    running = None
    for i, tr in enumerate(trs):
        if tr is None:
            running = None
            continue
        if running is None:
            segment = trs[max(0, i - window + 1): i + 1]
            if len(segment) == window and all(v is not None for v in segment):
                running = sum(segment) / window
                atr[i] = running
        else:
            running = (running * (window - 1) + tr) / window
            atr[i] = running
    return atr


def moving_average(values, window=7):
    """简单移动平均，返回与输入等长、预热期为 None 的列表。"""
    if window < 1:
        raise ValueError("ma window 必须大于 0")
    out = [None] * len(values)
    for i in range(window - 1, len(values)):
        segment = [_number(v) for v in values[i - window + 1: i + 1]]
        if all(v is not None for v in segment):
            out[i] = sum(segment) / window
    return out


def bar_color(payload, index):
    """返回 red / blue / None；红蓝同时为真时视为无效。"""
    colors = payload.get("bar_colors")
    if colors is not None:
        return colors[index] if colors[index] in ("red", "blue") else None
    pq = bool(payload["PQ"][index])
    pr = bool(payload["PR"][index])
    if pq == pr:
        return None
    return "red" if pq else "blue"


def _payload_is_usable(payload):
    required = ("dates", "ohlc", "POS", "DD", "EE", "KK", "PP")
    if any(key not in payload for key in required):
        return False
    if "bar_colors" not in payload and ("PQ" not in payload or "PR" not in payload):
        return False
    size = len(payload["dates"])
    return size > 0 and all(len(payload[key]) == size for key in required if key != "dates")


def _close(payload, index):
    bar = payload["ohlc"][index]
    return _number(bar[1]) if isinstance(bar, (list, tuple)) and len(bar) >= 2 else None


def _low(payload, index):
    bar = payload["ohlc"][index]
    return _number(bar[2]) if isinstance(bar, (list, tuple)) and len(bar) >= 3 else None


def _high(payload, index):
    bar = payload["ohlc"][index]
    return _number(bar[3]) if isinstance(bar, (list, tuple)) and len(bar) >= 4 else None


def _make_item(key, payload, contract, index, ma7, atr14, **extra):
    close = _close(payload, index)
    score = None if close is None or ma7 is None or atr14 in (None, 0) else (close - ma7) / atr14
    item = {
        "key": key,
        "symbol": payload.get("symbol", contract.get("symbol", key)),
        "name": contract.get("name", key),
        "category": contract.get("category", ""),
        "exchange": contract.get("exchange", ""),
        "date": payload["dates"][index],
        "close": close,
        "ma7": ma7,
        "atr14": atr14,
        "score": score,
        "POS": payload["POS"][index],
        "DD": _number(payload["DD"][index]),
        "EE": _number(payload["EE"][index]),
        "KK": _number(payload["KK"][index]),
        "PP": _number(payload["PP"][index]),
    }
    if "PQ" in payload:
        item["PQ"] = bool(payload["PQ"][index])
        item["PR"] = bool(payload["PR"][index])
    if "bar_colors" in payload:
        item["bar_color"] = payload["bar_colors"][index]
    item.update(extra)
    return item


def _target_run_start(payload, target, lookback):
    """取得包含最新 K 的连续目标色段起点；不满足最少两根时返回 None。"""
    n = len(payload["dates"])
    if bar_color(payload, n - 1) != target:
        return None
    start = n - 1
    while start > 0 and bar_color(payload, start - 1) == target:
        start -= 1
    if n - start < 2:
        return None
    # 转折前一根和首根目标色 K 都必须落在最近 lookback 根内。
    window_start = max(0, n - lookback)
    return start if start - 1 >= window_start else None


def _transition(payload, target, source_pos, boundary_key, comparator, lookback):
    """检测日线转色后的前两根同色 K 是否完成趋势带突破。

    首根目标色 K 只用于确认转色，突破可以发生在首根或紧随其后的
    第二根目标色 K。这样保留连续变色和严格边界突破的确认条件，同时
    不会遗漏首根 K 仍在趋势带内、次日才完成突破的转折。
    """
    start = _target_run_start(payload, target, lookback)
    if start is None or bar_color(payload, start - 1) == target:
        return None
    source = "red" if target == "blue" else "blue"
    if bar_color(payload, start - 1) != source or payload["POS"][start - 1] != source_pos:
        return None

    transition_close = _close(payload, start)
    transition_boundary = _number(payload[boundary_key][start])
    if transition_close is None or transition_boundary is None:
        return None

    # _target_run_start 已保证目标色段至少有两根；只在转色后的前两根
    # 连续目标色 K 中寻找严格突破，避免将数日后的无关波动归为本次转折。
    for confirmation_index in (start, start + 1):
        confirmation_close = _close(payload, confirmation_index)
        confirmation_boundary = _number(payload[boundary_key][confirmation_index])
        if (
            confirmation_close is not None
            and confirmation_boundary is not None
            and comparator(confirmation_close, confirmation_boundary)
        ):
            return (
                start,
                transition_close,
                transition_boundary,
                confirmation_index,
                confirmation_close,
                confirmation_boundary,
            )
    return None


def _recent_band(payload, source_pos, boundary_key, window=9):
    """Find the newest visible trend band in the current nine-bar window.

    Channels are calculated for every bar, but a pressure/support *band* is
    visible only while the strategy was holding the corresponding direction.
    """
    n = len(payload["dates"])
    for index in range(n - 1, max(-1, n - window - 1), -1):
        boundary = _number(payload[boundary_key][index])
        if payload["POS"][index] == source_pos and boundary is not None:
            return index, boundary
    return None


def _four_hour_transition(payload, target):
    """Evaluate the configured four-hour reversal signal and enhancement stars."""
    n, latest = len(payload["dates"]), len(payload["dates"]) - 1
    close = _close(payload, latest)
    if close is None or bar_color(payload, latest) != target:
        return None

    # 空转多：最近9根有空头压力带，当前上涨红K收于压力带上方。
    if target == "red":
        band = _recent_band(payload, -1, "PP")
        if band is None or close <= band[1]:
            return None
        prior_color, funding, label, boundary_key = "blue", "SB", "空转多", "PP"
        reasons = ("前8根含偏弱K", "前8根含增仓笑脸")
    # 多转空：最近9根有多头支撑带，当前下跌蓝K收于支撑带下方。
    else:
        band = _recent_band(payload, 1, "EE")
        if band is None or close >= band[1]:
            return None
        prior_color, funding, label, boundary_key = "red", "DSBE", "多转空", "EE"
        reasons = ("前8根含偏强K", "前8根含减仓倒手指")

    prior_start = max(0, latest - 8)
    prior_indices = range(prior_start, latest)  # excludes the current confirming bar
    flags = [
        any(bar_color(payload, i) == prior_color for i in prior_indices),
        any(bool(payload.get(funding, [False] * n)[i]) for i in prior_indices),
    ]
    star_reasons = [reason for enabled, reason in zip(flags, reasons) if enabled]
    return {
        "signal_name": label,
        "signal_date": payload["dates"][latest],
        "trend_band_date": payload["dates"][band[0]],
        "transition_boundary": boundary_key,
        "transition_boundary_value": band[1],
        "stars": len(star_reasons),
        "star_reasons": star_reasons,
    }


def screen_payload(key, payload, contract, lookback=8, atr_window=14):
    """筛选单个 JSON 载荷，返回六个 bucket 的命中列表。

    该函数不读写文件，供 CLI 与测试复用。
    """
    result = {bucket: [] for bucket in BUCKETS}
    if not _payload_is_usable(payload) or lookback < 2:
        return result

    n = len(payload["dates"])
    closes = [_close(payload, i) for i in range(n)]
    atr_values = wilder_atr(payload["ohlc"], atr_window)
    ma_values = moving_average(closes, 7)
    latest = n - 1
    atr14, ma7, close = atr_values[latest], ma_values[latest], closes[latest]
    if close is None or atr14 in (None, 0) or ma7 is None:
        return result

    latest_item = _make_item(key, payload, contract, latest, ma7, atr14)
    dd, ee, kk, pp = latest_item["DD"], latest_item["EE"], latest_item["KK"], latest_item["PP"]
    pos, color = latest_item["POS"], bar_color(payload, latest)

    # 主筛：只要策略处于对应持仓方向，即视为该方向趋势。
    # K 线颜色及趋势带位置会持续变化，不再作为趋势榜单的额外门槛。
    if pos == 1:
        result["long_trend"].append(latest_item)
    if pos == -1:
        result["short_trend"].append(latest_item)

    # 原有预警：颜色已反向，但价格仍处于来源趋势带内，尚未突破离场边界。
    if pos == 1 and color == "blue" and ee is not None and dd is not None and ee < close <= dd:
        result["long_to_short_warning"].append(latest_item)
    if pos == -1 and color == "red" and kk is not None and pp is not None and kk <= close < pp:
        result["short_to_long_warning"].append(latest_item)

    # 趋势带预警：极值进入趋势带，收盘仍守住该趋势带的有效边界。
    # 空头压力带为 KK~PP；最高价与收盘均在带内，且收盘没有上破 PP。
    high = _high(payload, latest)
    if (
        pos == -1 and high is not None and kk is not None and pp is not None
        and kk <= high <= pp and kk <= close <= pp
    ):
        result["short_pressure_warning"].append(latest_item)

    # 多头支撑带为 EE~DD；最低价、收盘均在带内，且收盘严格高于下沿 EE。
    low = _low(payload, latest)
    if (
        pos == 1 and low is not None and ee is not None and dd is not None
        and ee <= low <= dd and ee < close <= dd
    ):
        result["long_support_warning"].append(latest_item)

    if "bar_colors" in payload:
        short_turn = _four_hour_transition(payload, "blue")
        if short_turn:
            result["long_to_short"].append(_make_item(key, payload, contract, latest, ma7, atr14, **short_turn))
        long_turn = _four_hour_transition(payload, "red")
        if long_turn:
            result["short_to_long"].append(_make_item(key, payload, contract, latest, ma7, atr14, **long_turn))
    else:
        # 日线转换：转色后前两根连续目标色 K 内完成严格突破。
        short_turn = _transition(payload, "blue", 1, "EE", lambda price, line: price < line, lookback)
        if short_turn:
            (
                index,
                transition_close,
                boundary,
                confirmation_index,
                confirmation_close,
                confirmation_boundary,
            ) = short_turn
            result["long_to_short"].append(_make_item(
                key, payload, contract, latest, ma7, atr14,
                transition_date=payload["dates"][index], transition_from="red", transition_to="blue",
                transition_close=transition_close, transition_boundary="EE", transition_boundary_value=boundary,
                confirmation_date=payload["dates"][confirmation_index],
                confirmation_close=confirmation_close, confirmation_boundary_value=confirmation_boundary,
            ))
        long_turn = _transition(payload, "red", -1, "PP", lambda price, line: price > line, lookback)
        if long_turn:
            (
                index,
                transition_close,
                boundary,
                confirmation_index,
                confirmation_close,
                confirmation_boundary,
            ) = long_turn
            result["short_to_long"].append(_make_item(
                key, payload, contract, latest, ma7, atr14,
                transition_date=payload["dates"][index], transition_from="blue", transition_to="red",
                transition_close=transition_close, transition_boundary="PP", transition_boundary_value=boundary,
                confirmation_date=payload["dates"][confirmation_index],
                confirmation_close=confirmation_close, confirmation_boundary_value=confirmation_boundary,
            ))
    return result


def _sort_results(results):
    # 趋势榜单按均线偏离的 ATR 标准化分数排列；多头越大越强、空头越小越强。
    descending = {"long_trend", "short_to_long", "short_to_long_warning", "long_support_warning"}
    for bucket, items in results.items():
        if bucket in {"long_to_short", "short_to_long"}:
            items.sort(key=lambda item: (-item.get("stars", 0), -item["score"] if bucket in descending else item["score"]))
        else:
            items.sort(key=lambda item: item["score"], reverse=bucket in descending)


def screen_contracts(contracts, json_dir=None, symbols=None, lookback=8, atr_window=14, timeframe="1d"):
    """扫描合约池中的 JSON 产物，返回完整的可序列化筛选报告。"""
    json_dir = Path(json_dir) if json_dir is not None else timeframe_json_dir(timeframe)
    wanted = set(symbols or [])
    known = {entry["symbol"] for entry in contracts} | {entry["symbol"].split(".")[0] for entry in contracts}
    unknown = wanted - known
    if unknown:
        raise ValueError(f"不在合约池中的品种: {sorted(unknown)}")

    selected = [
        entry for entry in contracts
        if not wanted or entry["symbol"] in wanted or entry["symbol"].split(".")[0] in wanted
    ]
    results = {bucket: [] for bucket in BUCKETS}
    scanned = 0
    skipped = []
    for entry in selected:
        key = entry["symbol"].split(".")[0]
        path = json_dir / f"{key}.json"
        if not path.exists():
            skipped.append(key)
            continue
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
        scanned += 1
        per_symbol = screen_payload(key, payload, entry, lookback=lookback, atr_window=atr_window)
        for bucket in BUCKETS:
            results[bucket].extend(per_symbol[bucket])
    _sort_results(results)
    return {
        "timeframe": timeframe,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "rules": {
            "lookback": lookback,
            "atr_window": atr_window,
            "atr_method": "wilder",
            "ma_window": 7,
            "score": "(close - MA7) / ATR14",
            "main_trends": {
                "long_trend": "POS=1; sorted by score descending",
                "short_trend": "POS=-1; sorted by score ascending",
            },
            "daily_transitions": {
                "long_to_short": "red->blue after POS=1; latest blue run has at least 2 bars; close<EE on either of the first 2 blue bars",
                "short_to_long": "blue->red after POS=-1; latest red run has at least 2 bars; close>PP on either of the first 2 red bars",
            },
            "trend_band_warnings": {
                "short_pressure_warning": "POS=-1; KK<=high<=PP; KK<=close<=PP",
                "long_support_warning": "POS=1; EE<=low<=DD; EE<close<=DD",
            },
        },
        "scanned_symbols": scanned,
        "skipped_symbols": skipped,
        "summary": {bucket: len(results[bucket]) for bucket in BUCKETS},
        "buckets": results,
    }


def write_report(report, output_dir):
    """将报告写入 latest.json 与 latest.csv，并返回两个路径。"""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "latest.json"
    csv_path = output_dir / "latest.csv"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    rows = flatten_report(report)
    fieldnames = ["bucket", "bucket_name"]
    for row in rows:
        for field in row:
            if field not in fieldnames:
                fieldnames.append(field)
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return json_path, csv_path


def flatten_report(report):
    """将分组 JSON 报告展平为 CSV 行，供写出与测试共用。"""
    return [
        {"bucket": bucket, "bucket_name": BUCKET_LABELS[bucket], **item}
        for bucket in BUCKETS
        for item in report["buckets"][bucket]
    ]


def print_report(report):
    """打印按筛选方向排序的紧凑终端摘要。"""
    print(f"[筛选完成] 扫描 {report['scanned_symbols']} 个品种")
    for bucket in BUCKETS:
        items = report["buckets"][bucket]
        print(f"\n【{BUCKET_LABELS[bucket]}】{len(items)} 个")
        if not items:
            print("  无")
            continue
        for item in items:
            line = f"  {item['key']:<8} {item['name']:<8} 收={item['close']:.4f} score={item['score']:.3f}"
            if "transition_date" in item:
                line += f" 转折={item['transition_date']} {item['transition_from']}→{item['transition_to']}"
            print(line)


def main():
    parser = argparse.ArgumentParser(description="期货趋势/转换品种筛选（读取本地 JSON 产物）")
    parser.add_argument("--symbols", nargs="+", help="只筛指定品种，支持完整 symbol 或 key")
    parser.add_argument("--lookback", type=int, default=8, help="转换检测回看 K 线数（默认 8）")
    parser.add_argument("--atr-window", type=int, default=14, help="Wilder ATR 周期（默认 14）")
    parser.add_argument("--output-dir", type=Path, default=None, help="覆盖单周期报告输出目录")
    parser.add_argument("--timeframe", choices=("1d", "4h", "all"), default="1d")
    args = parser.parse_args()
    if args.lookback < 2:
        parser.error("--lookback 必须至少为 2")
    if args.atr_window < 1:
        parser.error("--atr-window 必须大于 0")

    if args.output_dir is not None and args.timeframe == "all":
        parser.error("--output-dir 不能与 --timeframe all 同时使用")
    timeframes = ("1d", "4h") if args.timeframe == "all" else (args.timeframe,)
    for timeframe in timeframes:
        report = screen_contracts(
            load_contracts(), symbols=args.symbols, lookback=args.lookback,
            atr_window=args.atr_window, timeframe=timeframe,
        )
        print_report(report)
        target = args.output_dir or output_dir(timeframe) / "screening"
        json_path, csv_path = write_report(report, target)
        print(f"\n[产物] {json_path}\n[产物] {csv_path}")


if __name__ == "__main__":
    main()
