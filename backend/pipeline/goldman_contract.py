# -*- coding: utf-8 -*-
"""高盛主力/次主力合约净持仓追踪。

主次合约与具体合约会员持仓均由 RiceQuant 提供（繁微合约级接口存在品种错配与
覆盖缺口，仅品种合计口径仍走繁微，见 seat_fetch）。结果按主力合约当日净持仓
拆成净多、净空两个 Top N 榜单，并生成日报可内嵌的 PNG。

    python -m backend.pipeline.goldman_contract --date 20260917 --top 15

同日 JSON 与两张 PNG 均存在时直接命中缓存；使用 ``--force`` 可重新抓取。
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
import time

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

from backend.pipeline.dominant_fetch import load_or_fetch
from backend.pipeline.report_store import atomic_json
from backend.pipeline.seat_core import (
    CN_NAME,
    SEAT_DIR,
    prev_trade_date,
    read_seat_csv,
)
from backend.pipeline.seat_plot import setup_font
from backend.core.config import load_config, load_contracts


MEMBER_NAME = "高盛期货"
DEFAULT_TOP_N = 15

_rq_client = None


def _rq():
    """惰性登录米筐（复用 dominant_fetch 的 license 约定）。"""
    global _rq_client
    if _rq_client is None:
        import rqdatac
        key = (load_config().get("ricequant") or {}).get("license_key", "")
        if not key:
            raise RuntimeError(
                "米筐 license 为空，请设置 FUTURES_RQDATA_LICENSE_KEY "
                "或填写 config.yaml 的 ricequant.license_key"
            )
        rqdatac.init("license", key)
        _rq_client = rqdatac
    return _rq_client


def rq_member_rank(contract, start_date, end_date, fields=None):
    """米筐合约级会员持仓排名 → 繁微兼容形状 {"data": {"data": [...]}}。

    持多/持空榜各查一次（top 20 披露口径），按 (trade_date, member_name) 外连接合并；
    未上榜一侧记 0（与交易所披露口径一致：未披露按 0 展示，但不等于真实为零）。
    每行带 code=contract，供下游校验响应与请求是否一致。
    """
    rq = _rq()
    merged = {}
    for rank_by, value_key, change_key in (
        ("long", "total_long", "total_long_change"),
        ("short", "total_short", "total_short_change"),
    ):
        df = rq.futures.get_member_rank(
            contract, rank_by=rank_by, start_date=start_date, end_date=end_date
        )
        if df is None or len(df) == 0:
            continue
        df = df.reset_index()
        for rec in df.to_dict("records"):
            day = str(rec["trading_date"])[:10].replace("-", "")
            name = str(rec.get("member_name") or "").strip()
            if not name:
                continue
            row = merged.setdefault((day, name), {
                "code": contract, "trade_date": day, "member_name": name,
                "total_long": 0, "total_short": 0,
                "total_long_change": 0, "total_short_change": 0,
            })
            row[value_key] = int(rec.get("volume") or 0)
            row[change_key] = int(rec.get("volume_change") or 0)
    return {"data": {"data": list(merged.values())}}

# 与现有席位方向图保持同一视觉体系。
BG = "#0e1e33"
PANEL = "#142a46"
PANEL_EDGE = "#27425f"
GOLD = "#d9b98a"
TEXT = "#e8e2d4"
SUB = "#8fa3bd"
PREV = "#566d88"
LONGWARD = "#1f9d78"
SHORTWARD = "#c64b4b"
GRID = "#29415e"


def validate_day(value, label="日期"):
    """Validate a compact calendar day without accepting loose strptime input."""
    value = str(value or "")
    if not re.fullmatch(r"\d{8}", value):
        raise ValueError(f"{label}必须为 YYYYMMDD")
    try:
        datetime.strptime(value, "%Y%m%d")
    except ValueError as exc:
        raise ValueError(f"{label}不是有效日期: {value}") from exc
    return value


def _day(value):
    """Normalise API/CSV dates to YYYYMMDD."""
    text = str(value or "").strip().replace("-", "")
    return text[:8] if len(text) >= 8 and text[:8].isdigit() else text


def _number(value):
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _pick(rows, *fields):
    """Pick one disclosed value without double-counting duplicate rank rows.

    繁微字段名有两种风格：品种合计口径用 total_long/total_short，
    具体合约口径用 long/short；按别名顺序取每行第一个非空值。
    """
    values = []
    for row in rows:
        for field in fields:
            value = _number(row.get(field))
            if value is not None:
                values.append(value)
                break
    return max(values, key=abs) if values else None


def _member_position(rows, date):
    """Return the exact Goldman row for one date under the top-20 disclosure basis."""
    matched = [
        row for row in rows
        if _day(row.get("trade_date")) == date
        and str(row.get("member_name") or "").strip() == MEMBER_NAME
    ]
    if not matched:
        return None
    long_value = _pick(matched, "total_long", "long")
    short_value = _pick(matched, "total_short", "short")
    if long_value is None and short_value is None:
        return None
    long_value = long_value or 0.0
    short_value = short_value or 0.0
    long_change = _pick(matched, "total_long_change", "long_change")
    short_change = _pick(matched, "total_short_change", "short_change")
    change = None
    if long_change is not None or short_change is not None:
        change = (long_change or 0.0) - (short_change or 0.0)
    return {
        "net": long_value - short_value,
        "total_long": long_value,
        "total_short": short_value,
        "reported_change": change,
    }


def build_contract_position(rows, contract, trade_date, prev_date):
    """Build today/previous net position for one fixed contract.

    Prefer a directly disclosed previous-day row.  If it is absent, reconstruct
    the previous net position from today's long/short change fields.  A missing
    Goldman row is kept as an explicit unavailable value, never coerced to zero.
    """
    today = _member_position(rows, trade_date)
    if today is None:
        return {
            "contract": contract,
            "available": False,
            "today": None,
            "previous": None,
            "change": None,
            "previous_source": None,
            "missing_reason": "当日无高盛期货披露记录",
        }

    previous = _member_position(rows, prev_date)
    if previous is not None:
        previous_net = previous["net"]
        previous_source = "previous_row"
    elif today["reported_change"] is not None:
        previous_net = today["net"] - today["reported_change"]
        previous_source = "reported_change"
    else:
        previous_net = None
        previous_source = None
    return {
        "contract": contract,
        "available": True,
        "today": today["net"],
        "previous": previous_net,
        "change": today["net"] - previous_net if previous_net is not None else None,
        "previous_source": previous_source,
        "total_long": today["total_long"],
        "total_short": today["total_short"],
        "missing_reason": None,
    }


def _response_rows(response, contract=None):
    if not isinstance(response, dict):
        raise ValueError("会员持仓返回不是 JSON 对象")
    data = response.get("data")
    if not isinstance(data, dict) or "data" not in data:
        raise ValueError(f"会员持仓返回缺少 data.data（code={response.get('code')}）")
    rows = data.get("data") or []
    if not isinstance(rows, list):
        raise ValueError("会员持仓 data.data 不是数组")
    if contract is not None:
        # 繁微合约级接口存在品种错配（如请求 M2701 返回 JM2701），逐行校验 code
        mismatched = sorted({
            str(row.get("code")) for row in rows
            if row.get("code") is not None and str(row.get("code")) != str(contract)
        })
        if mismatched:
            raise ValueError(f"响应合约错配: 请求 {contract}，返回 {mismatched}")
    return rows


def fetch_contracts(dominants, trade_date, prev_date, query_fn=rq_member_rank,
                    sleep_sec=0, cache_dir=None, force=False):
    """Fetch every unique main/sub contract once; isolate individual failures."""
    contracts = []
    for row in dominants:
        for key in ("main", "sub"):
            contract = row.get(key)
            if contract and contract not in contracts:
                contracts.append(contract)

    cache_root = Path(cache_dir) if cache_dir is not None else None
    if cache_root is not None:
        cache_root.mkdir(parents=True, exist_ok=True)

    fetched, errors = {}, {}
    successful_requests = 0
    for index, contract in enumerate(contracts, 1):
        cache_hit = False
        try:
            cache_path = None
            if cache_root is not None:
                # Keep filenames readable while making unexpected contract strings safe.
                stem = re.sub(r"[^A-Za-z0-9._-]+", "_", str(contract)).strip("._") or "contract"
                suffix = hashlib.sha256(str(contract).encode("utf-8")).hexdigest()[:10]
                cache_path = cache_root / f"{stem}_{suffix}.json"
            response = None
            if cache_path is not None and cache_path.exists() and not force:
                try:
                    cached = json.loads(cache_path.read_text(encoding="utf-8"))
                    _response_rows(cached, contract)
                    response = cached
                    cache_hit = True
                except (OSError, ValueError, TypeError):
                    response = None
            if response is None:
                response = query_fn(contract, prev_date, trade_date)
                _response_rows(response, contract)
                if cache_path is not None:
                    atomic_json(cache_path, response)
            rows = _response_rows(response, contract)
            fetched[contract] = build_contract_position(
                rows, contract, trade_date, prev_date
            )
            successful_requests += 1
            status = "有披露" if fetched[contract]["available"] else "无高盛记录"
            cached_label = " · 缓存" if cache_hit else ""
            print(f"  [{index:3d}/{len(contracts)}] {contract:<10} {status}{cached_label}")
        except Exception as exc:  # noqa: BLE001 - 单合约失败不能阻断全市场
            message = f"{type(exc).__name__}: {exc}"
            errors[contract] = message
            fetched[contract] = {
                "contract": contract,
                "available": False,
                "today": None,
                "previous": None,
                "change": None,
                "previous_source": None,
                "missing_reason": message,
            }
            print(f"  [{index:3d}/{len(contracts)}] {contract:<10} 失败: {message}")
        if sleep_sec and index < len(contracts) and not cache_hit:
            time.sleep(sleep_sec)
    if contracts and successful_requests == 0:
        raise RuntimeError("所有具体合约的会员持仓请求均失败")
    return fetched, errors, len(contracts), successful_requests


def _variety_names():
    names = dict(CN_NAME)
    try:
        contracts = load_contracts()
    except (OSError, ValueError, TypeError):
        return names
    for entry in contracts:
        key = str(entry.get("symbol") or "").split(".")[0]
        matched = re.match(r"[A-Za-z]+", key)
        name = str(entry.get("name") or "").strip()
        if matched and name:
            names.setdefault(matched.group(0).upper(), name.split("·", 1)[0])
    return names


def build_bundle(dominants, fetched, errors, trade_date, prev_date, top_n):
    names = _variety_names()
    varieties = []
    for dominant in dominants:
        symbol = str(dominant.get("symbol") or "")
        main_contract = dominant.get("main")
        sub_contract = dominant.get("sub")
        main = fetched.get(main_contract) if main_contract else None
        sub = fetched.get(sub_contract) if sub_contract else None
        if main is None:
            main = {
                "contract": main_contract,
                "available": False,
                "today": None,
                "previous": None,
                "change": None,
                "previous_source": None,
                "missing_reason": "缺少主力合约映射" if not main_contract else "未请求",
            }
        if sub is None:
            sub = {
                "contract": sub_contract,
                "available": False,
                "today": None,
                "previous": None,
                "change": None,
                "previous_source": None,
                "missing_reason": "无次主力合约" if not sub_contract else "未请求",
            }
        varieties.append({
            "symbol": symbol,
            "name": dominant.get("name") or names.get(symbol.upper(), symbol),
            "main": dict(main),
            "sub": dict(sub),
        })

    ranked = [row for row in varieties if row["main"]["available"]]
    long_rank = sorted(
        (row for row in ranked if row["main"]["today"] > 0),
        key=lambda row: row["main"]["today"], reverse=True,
    )[:top_n]
    short_rank = sorted(
        (row for row in ranked if row["main"]["today"] < 0),
        key=lambda row: abs(row["main"]["today"]), reverse=True,
    )[:top_n]
    main_missing = sum(not row["main"]["available"] for row in varieties)
    sub_missing = sum(not row["sub"]["available"] for row in varieties)
    return {
        "schema_version": 1,
        "date": trade_date,
        "prev_date": prev_date,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "member": MEMBER_NAME,
        "basis": "交易所会员持仓前20名披露口径；未披露不代表真实持仓为零",
        "dominant_source": "RiceQuant get_dominant rank=1/2",
        "top_n": top_n,
        "coverage": {
            "dominant_varieties": len(dominants),
            "main_missing": main_missing,
            "sub_missing": sub_missing,
            "request_errors": len(errors),
            "long_candidates": sum(
                row["main"]["available"] and row["main"]["today"] > 0
                for row in varieties
            ),
            "short_candidates": sum(
                row["main"]["available"] and row["main"]["today"] < 0
                for row in varieties
            ),
        },
        "errors": errors,
        "varieties": varieties,
        "rankings": {"long": long_rank, "short": short_rank},
    }


def _fmt(value):
    if value is None:
        return "—"
    sign = "+" if value > 0 else ""
    absolute = abs(value)
    if absolute >= 10000:
        return f"{sign}{value / 10000:.1f}万"
    return f"{sign}{value:,.0f}"


def _plot_bar(ax, y, position, height, limit, is_main):
    if not position.get("available"):
        ax.text(0, y, "—  未披露", color=SUB, fontsize=9, va="center", ha="center")
        return
    current = position["today"]
    previous = position.get("previous")
    change = position.get("change")
    if previous is not None:
        ax.barh(y, previous, height=height, color=PREV, alpha=0.78, zorder=2)
        left = min(previous, current)
        width = abs(current - previous)
        if width:
            ax.barh(
                y, width, left=left, height=height * 0.72,
                color=LONGWARD if current > previous else SHORTWARD,
                alpha=0.96, zorder=3,
            )
    else:
        ax.barh(
            y, current, height=height, color=PREV, alpha=0.55, zorder=2,
        )
    ax.scatter([current], [y], s=34 if is_main else 22, color=GOLD,
               edgecolor=TEXT, linewidth=0.6, zorder=4)
    pad = limit * 0.018
    label = f"今 {_fmt(current)}  Δ {_fmt(change)}"
    ax.text(
        current + (pad if current >= 0 else -pad), y, label,
        color=TEXT if is_main else SUB, fontsize=9 if is_main else 8,
        fontweight="bold" if is_main else "normal", va="center",
        ha="left" if current >= 0 else "right", clip_on=False,
    )


def render_chart(items, side, trade_date, prev_date, out_png):
    """Render one signed, paired main/sub chart."""
    title = "净多排名" if side == "long" else "净空排名"
    row_count = max(1, len(items) * 2)
    height_px = max(640, 245 + row_count * 48 + 145)
    fig, ax = plt.subplots(figsize=(10.8, height_px / 100), dpi=100)
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(PANEL)

    values = []
    for item in items:
        for key in ("main", "sub"):
            pos = item[key]
            if pos.get("available"):
                values.extend(v for v in (pos.get("today"), pos.get("previous")) if v is not None)
    limit = max([abs(value) for value in values] + [1.0]) * 1.28

    y_positions, labels = [], []
    for index, item in enumerate(items):
        main_y = (len(items) - index - 1) * 2 + 0.34
        sub_y = main_y - 0.68
        y_positions.extend([main_y, sub_y])
        labels.extend([
            f"{item['name']} {item['symbol']}  · 主 {item['main'].get('contract') or '—'}",
            f"次 {item['sub'].get('contract') or '—'}",
        ])
        _plot_bar(ax, main_y, item["main"], 0.48, limit, True)
        _plot_bar(ax, sub_y, item["sub"], 0.30, limit, False)
        if index < len(items) - 1:
            ax.axhline(sub_y - 0.45, color=PANEL_EDGE, linewidth=0.8, alpha=0.7)

    if not items:
        ax.text(0.5, 0.5, "当日无可排名的高盛主力合约净持仓",
                transform=ax.transAxes, color=SUB, ha="center", va="center", fontsize=13)
        y_positions, labels = [], []

    ax.axvline(0, color=GOLD, linewidth=1.0, alpha=0.8, zorder=1)
    ax.set_xlim(-limit, limit)
    ax.set_yticks(y_positions)
    ax.set_yticklabels(labels, color=TEXT, fontsize=9)
    ax.tick_params(axis="y", length=0, pad=8)
    ax.tick_params(axis="x", colors=SUB, labelsize=8)
    ax.xaxis.grid(True, color=GRID, linewidth=0.7, alpha=0.75)
    ax.set_axisbelow(True)
    for spine in ax.spines.values():
        spine.set_color(PANEL_EDGE)
    ax.set_xlabel("净持仓（手） · 左侧净空 / 右侧净多", color=SUB, labelpad=10)
    ax.set_title(
        f"高盛主次合约净持仓 · {title}\n{trade_date} 对比 {prev_date} · 按主力合约今日净仓排序",
        color=TEXT, fontsize=18, fontweight="bold", loc="left", pad=22,
    )
    legend = [
        Patch(facecolor=PREV, label="昨日净仓"),
        Patch(facecolor=LONGWARD, label="净仓向多头方向变化"),
        Patch(facecolor=SHORTWARD, label="净仓向空头方向变化"),
        Line2D([0], [0], marker="o", color="none", markerfacecolor=GOLD,
               markeredgecolor=TEXT, label="今日端点"),
    ]
    leg = ax.legend(handles=legend, loc="upper right", ncol=2, frameon=False,
                    fontsize=8, bbox_to_anchor=(1, 1.10))
    for text in leg.get_texts():
        text.set_color(SUB)
    fig.text(
        0.09, 0.022,
        "口径：净持仓=披露持多量 - 披露持空量；底柱为昨日净仓，彩色段为今日变化。\n"
        "主次合约按目标日固定；仅统计“高盛期货”。交易所前20名披露存在截断，未披露不等于真实持仓为零。",
        color=SUB, fontsize=8, linespacing=1.6,
    )
    fig.subplots_adjust(left=0.27, right=0.91, top=0.86, bottom=0.11)
    fig.savefig(out_png, dpi=100, facecolor=BG)
    plt.close(fig)


def output_paths(trade_date, directory=None):
    root = Path(directory) if directory else SEAT_DIR
    return {
        "json": root / f"goldman_contract_positions_{trade_date}.json",
        "long": root / f"goldman_contract_long_{trade_date}.png",
        "short": root / f"goldman_contract_short_{trade_date}.png",
    }


def _file_hash(path):
    hasher = hashlib.sha256()
    with open(path, "rb") as fp:
        for block in iter(lambda: fp.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def _temporary_png(directory):
    fd, name = tempfile.mkstemp(prefix=".goldman-contract-", suffix=".png", dir=directory)
    os.close(fd)
    return Path(name)


def generate(trade_date, prev_date, top_n=DEFAULT_TOP_N, force=False,
             query_fn=rq_member_rank, sleep_sec=0, directory=None,
             dominants=None):
    """Generate/cache the Goldman contract JSON and two ranking charts."""
    trade_date = validate_day(trade_date, "数据日")
    prev_date = validate_day(prev_date, "比较日")
    if prev_date >= trade_date:
        raise ValueError("比较日必须早于数据日")
    if top_n < 1:
        raise ValueError("top_n 必须大于 0")
    paths = output_paths(trade_date, directory)
    paths["json"].parent.mkdir(parents=True, exist_ok=True)
    if not force and all(path.exists() for path in paths.values()):
        try:
            bundle = json.loads(paths["json"].read_text(encoding="utf-8"))
            hashes = bundle.get("image_hashes") or {}
            cache_valid = (
                bundle.get("date") == trade_date
                and bundle.get("prev_date") == prev_date
                and bundle.get("top_n") == top_n
                and hashes.get("long") == _file_hash(paths["long"])
                and hashes.get("short") == _file_hash(paths["short"])
            )
        except (OSError, ValueError, TypeError):
            cache_valid = False
        if cache_valid:
            print(f"高盛主次合约缓存命中: {paths['json']}")
            return bundle, paths

    if dominants is None:
        try:
            dominants, _ = load_or_fetch(trade_date, force=force)
        except SystemExit as exc:
            raise RuntimeError(str(exc)) from exc
    fetched, errors, requested, succeeded = fetch_contracts(
        dominants, trade_date, prev_date, query_fn=query_fn, sleep_sec=sleep_sec,
        cache_dir=paths["json"].parent / "goldman_contract_cache" / trade_date,
        force=force,
    )
    bundle = build_bundle(dominants, fetched, errors, trade_date, prev_date, top_n)
    bundle["coverage"].update(
        requested_contracts=requested, successful_requests=succeeded
    )
    setup_font()
    temporary = {
        "long": _temporary_png(paths["long"].parent),
        "short": _temporary_png(paths["short"].parent),
    }
    try:
        render_chart(bundle["rankings"]["long"], "long", trade_date, prev_date, temporary["long"])
        render_chart(bundle["rankings"]["short"], "short", trade_date, prev_date, temporary["short"])
        bundle["image_hashes"] = {
            "long": _file_hash(temporary["long"]),
            "short": _file_hash(temporary["short"]),
        }
        os.replace(temporary["long"], paths["long"])
        os.replace(temporary["short"], paths["short"])
        # JSON is the commit marker: cache reads only accept images matching these hashes.
        atomic_json(paths["json"], bundle)
    finally:
        for path in temporary.values():
            path.unlink(missing_ok=True)
    print(f"[产物] {paths['json']}")
    print(f"[产物] {paths['long']}")
    print(f"[产物] {paths['short']}")
    return bundle, paths


def infer_previous_date(trade_date):
    csv_path = SEAT_DIR / f"seat_data_{trade_date}.csv"
    if not csv_path.exists():
        raise ValueError(f"缺少 {csv_path}，无法推导前一交易日")
    previous = prev_trade_date(read_seat_csv(csv_path), trade_date)
    if not previous:
        raise ValueError(f"{trade_date} 是席位缓存中的首个交易日")
    return previous


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--date", required=True, help="数据交易日 YYYYMMDD")
    parser.add_argument("--prev", help="前一交易日 YYYYMMDD；默认从 seat_data 缓存推导")
    parser.add_argument("--top", type=int, default=DEFAULT_TOP_N, help="每张图显示数量（默认15）")
    parser.add_argument("--force", action="store_true", help="忽略当日缓存并重新抓取")
    parser.add_argument("--no-rerender", action="store_true", help="只生成席位产物，不重渲染日报")
    args = parser.parse_args(argv)
    try:
        trade_date = validate_day(args.date, "数据日")
        previous = validate_day(args.prev, "比较日") if args.prev else infer_previous_date(trade_date)
    except ValueError as exc:
        parser.error(str(exc))
    generate(trade_date, previous, top_n=args.top, force=args.force)
    if not args.no_rerender:
        from backend.pipeline.summary_render import rerender_report_for_data_date
        out = rerender_report_for_data_date(
            datetime.strptime(trade_date, "%Y%m%d").strftime("%Y-%m-%d")
        )
        if out:
            print(f"[日报已更新] {out}")
        else:
            print("[提示] 尚无匹配日报，席位图已保存，后续渲染会自动带入")


if __name__ == "__main__":
    main()
