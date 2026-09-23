# -*- coding: utf-8 -*-
"""主力合约换月检查（每日日更前运行）。

用米筐 ``futures.get_dominant(rule=0, rank=1)`` 逐品种核对 config/contracts.yaml
中的合约是否仍是主力；发现换月则就地更新 contracts.yaml（保留注释与顺序），
并立即为新合约全量补数（日线/周线/4 小时线）与重算看板产物，保证当日
scan_report 的 Δ4h 环比即可用。

    python -m backend.pipeline.rollover_check              # 检查并应用换月
    python -m backend.pipeline.rollover_check --dry-run    # 只报告，不改文件不下载

extra: true 的条目（股指/ETF/定点合约）不参与换月。单品种查询失败只告警，
保留原合约，不中断整体流程。
"""
from __future__ import annotations

import argparse
from datetime import datetime
import re
import subprocess
import sys

from backend.core.config import CONTRACTS_FILE, load_contracts
from backend.pipeline.dominant_fetch import _login

_SYMBOL_LINE = "- symbol: "


def _split_base(base):
    """拆配置 symbol 主体为 (字母, 年月数字)，如 'rb2610' → ('rb', '2610')。"""
    m = re.fullmatch(r"([A-Za-z]+)(\d+)", base)
    if not m:
        return None
    return m.group(1), m.group(2)


def to_config_symbol(rq_contract, current_symbol):
    """把米筐合约代码（如 RB2701 / CF2701）按现有条目的风格还原为配置 symbol。

    大小写与年月位数跟随当前条目：小写主体保持小写；3 位年月的交易所（郑商所）
    把米筐 4 位年月去掉前导 '2'；交易所后缀不变。
    """
    base, dot, suffix = current_symbol.partition(".")
    if not dot:
        raise ValueError(f"配置 symbol 缺少交易所后缀: {current_symbol}")
    current = _split_base(base)
    m = re.fullmatch(r"([A-Za-z]+)(\d{3,4})", str(rq_contract).strip())
    if current is None or not m:
        raise ValueError(f"无法解析合约代码: {current_symbol} / {rq_contract}")
    letters, digits = m.group(1), m.group(2)
    if current[0].islower():
        letters = letters.lower()
    if len(current[1]) == 3 and len(digits) == 4 and digits.startswith("2"):
        digits = digits[1:]
    return f"{letters}{digits}.{suffix}"


def detect_rollovers(rq, contracts, date):
    """逐品种核对主力合约，返回 (换月清单, 告警清单)。

    换月清单元素: {"entry", "old", "new"}；告警清单元素: (symbol, 原因)。
    """
    changes, warnings = [], []
    pool = [e for e in contracts if not e.get("extra")]
    for i, entry in enumerate(pool, 1):
        symbol = entry["symbol"]
        base = symbol.split(".")[0]
        parts = _split_base(base)
        if parts is None:
            warnings.append((symbol, "symbol 主体无法解析品种代码"))
            continue
        underlying = parts[0].upper()
        try:
            d = rq.futures.get_dominant(underlying, date, date, rule=0, rank=1)
        except Exception as exc:  # noqa: BLE001
            warnings.append((symbol, f"get_dominant 异常: {exc!r}"))
            continue
        if d is None or len(d) == 0:
            warnings.append((symbol, "get_dominant 无返回（退市或停牌？），保留原合约"))
            continue
        try:
            new_symbol = to_config_symbol(str(d.iloc[0]), symbol)
        except ValueError as exc:
            warnings.append((symbol, str(exc)))
            continue
        if new_symbol != symbol:
            changes.append({"entry": entry, "old": symbol, "new": new_symbol})
            print(f"[{i:3d}/{len(pool)}] {underlying:5s} 换月: {symbol} → {new_symbol}")
        else:
            print(f"[{i:3d}/{len(pool)}] {underlying:5s} 仍为主力 {symbol}")
    return changes, warnings


def apply_to_config(path, changes):
    """就地替换 contracts.yaml 中的 symbol 行，保留注释与条目顺序。"""
    text = path.read_text(encoding="utf-8")
    for change in changes:
        old_line = f"{_SYMBOL_LINE}{change['old']}\n"
        if text.count(old_line) != 1:
            raise ValueError(
                f"{path} 中 '- symbol: {change['old']}' 出现 "
                f"{text.count(old_line)} 次（应为 1），拒绝写入"
            )
        text = text.replace(old_line, f"{_SYMBOL_LINE}{change['new']}\n", 1)
    path.write_text(text, encoding="utf-8")


def backfill(new_symbols):
    """为换月后的新合约全量补数并重算产物；返回失败的 symbol 列表。

    注意：screen 不能在单品种模式下跑——它会整份重写 screening/latest.json，
    只留单品种子集。这里只补数据与信号，榜单由最后的 full_screen 统一重建。
    """
    failed = []
    for symbol in new_symbols:
        for step in (
            ["-m", "backend.pipeline.download", "--symbols", symbol, "--timeframe", "all"],
            ["-m", "backend.pipeline.daily", "--symbols", symbol, "--timeframe", "all"],
        ):
            print(f"[补数] {symbol}: {' '.join(step[2:])}")
            result = subprocess.run([sys.executable, *step], check=False)
            if result.returncode != 0:
                print(f"[警告] {symbol} 补数步骤失败（{' '.join(step[2:])}），跳过后续步骤")
                failed.append(symbol)
                break
    return failed


def full_screen():
    """换月后整体重建筛选榜单（1d+4h），保证 latest.json 覆盖全部池内品种。"""
    print("[补数] 重建全量筛选榜单: screen --timeframe all")
    return subprocess.run(
        [sys.executable, "-m", "backend.pipeline.screen", "--timeframe", "all"],
        check=False,
    ).returncode


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", help="交易日期 YYYYMMDD（默认当天）")
    parser.add_argument("--dry-run", action="store_true", help="只报告换月，不改配置不下载")
    args = parser.parse_args(argv)
    date = args.date or datetime.now().strftime("%Y%m%d")

    contracts = load_contracts()
    rq = _login()
    print(f"[开始] 主力合约换月检查 date={date} 池内 {sum(1 for e in contracts if not e.get('extra'))} 个品种")
    changes, warnings = detect_rollovers(rq, contracts, date)
    for symbol, reason in warnings:
        print(f"[警告] {symbol}: {reason}")

    if not changes:
        print(f"[完成] 无换月，合约池保持不变（告警 {len(warnings)} 条）")
        return 0

    print(f"[换月] 共 {len(changes)} 个品种: " + ", ".join(f"{c['old']}→{c['new']}" for c in changes))
    if args.dry_run:
        print("[dry-run] 不修改配置、不补数")
        return 0

    apply_to_config(CONTRACTS_FILE, changes)
    print(f"[配置] 已就地更新 {CONTRACTS_FILE}")
    failed = backfill([c["new"] for c in changes])
    screen_rc = full_screen()
    if failed:
        print(f"[完成] 换月已应用，但 {len(failed)} 个新合约补数失败: {', '.join(failed)}")
        return 1
    if screen_rc != 0:
        print("[警告] 全量筛选榜单重建失败，将由后续日更流程重跑")
        return 1
    print(f"[完成] {len(changes)} 个品种换月并补数完成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
