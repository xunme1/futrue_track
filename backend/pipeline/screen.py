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

from backend.core.config import DATA_DIR, JSON_DIR, load_contracts


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
    pq = bool(payload["PQ"][index])
    pr = bool(payload["PR"][index])
    if pq == pr:
        return None
    return "red" if pq else "blue"


def _payload_is_usable(payload):
    required = ("dates", "ohlc", "PQ", "PR", "POS", "DD", "EE", "KK", "PP")
    if any(key not in payload for key in required):
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
        "PQ": bool(payload["PQ"][index]),
        "PR": bool(payload["PR"][index]),
        "POS": payload["POS"][index],
        "DD": _number(payload["DD"][index]),
        "EE": _number(payload["EE"][index]),
        "KK": _number(payload["KK"][index]),
        "PP": _number(payload["PP"][index]),
    }
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
    """检测目标色段的首根 K 是否在转折处突破指定通道边界。"""
    start = _target_run_start(payload, target, lookback)
    if start is None or bar_color(payload, start - 1) == target:
        return None
    source = "red" if target == "blue" else "blue"
    if bar_color(payload, start - 1) != source or payload["POS"][start - 1] != source_pos:
        return None
    transition_close = _close(payload, start)
    boundary = _number(payload[boundary_key][start])
    if transition_close is None or boundary is None or not comparator(transition_close, boundary):
        return None
    return start, transition_close, boundary


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

    # 主筛：趋势带只在策略当前持仓方向一致时生效。
    if pos == 1 and color == "red" and ee is not None and close >= ee:
        result["long_trend"].append(latest_item)
    if pos == -1 and color == "blue" and pp is not None and close <= pp:
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

    short_turn = _transition(payload, "blue", 1, "EE", lambda price, line: price < line, lookback)
    if short_turn:
        index, transition_close, boundary = short_turn
        result["long_to_short"].append(_make_item(
            key, payload, contract, latest, ma7, atr14,
            transition_date=payload["dates"][index], transition_from="red", transition_to="blue",
            transition_close=transition_close, transition_boundary="EE", transition_boundary_value=boundary,
        ))

    long_turn = _transition(payload, "red", -1, "PP", lambda price, line: price > line, lookback)
    if long_turn:
        index, transition_close, boundary = long_turn
        result["short_to_long"].append(_make_item(
            key, payload, contract, latest, ma7, atr14,
            transition_date=payload["dates"][index], transition_from="blue", transition_to="red",
            transition_close=transition_close, transition_boundary="PP", transition_boundary_value=boundary,
        ))
    return result


def _sort_results(results):
    descending = {"long_trend", "short_to_long", "short_to_long_warning", "long_support_warning"}
    for bucket, items in results.items():
        items.sort(key=lambda item: item["score"], reverse=bucket in descending)


def screen_contracts(contracts, json_dir=JSON_DIR, symbols=None, lookback=8, atr_window=14):
    """扫描合约池中的 JSON 产物，返回完整的可序列化筛选报告。"""
    json_dir = Path(json_dir)
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
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "rules": {
            "lookback": lookback,
            "atr_window": atr_window,
            "atr_method": "wilder",
            "ma_window": 7,
            "score": "(close - MA7) / ATR14",
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
    parser.add_argument("--output-dir", type=Path, default=DATA_DIR / "screening", help="报告输出目录")
    args = parser.parse_args()
    if args.lookback < 2:
        parser.error("--lookback 必须至少为 2")
    if args.atr_window < 1:
        parser.error("--atr-window 必须大于 0")

    report = screen_contracts(
        load_contracts(), symbols=args.symbols, lookback=args.lookback, atr_window=args.atr_window
    )
    print_report(report)
    json_path, csv_path = write_report(report, args.output_dir)
    print(f"\n[产物] {json_path}\n[产物] {csv_path}")


if __name__ == "__main__":
    main()
