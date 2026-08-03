# -*- coding: utf-8 -*-
"""
合约池配置生成器
  从"主力合约池 CSV"生成 config/contracts.yaml（下载/更新/计算都从这个文件选合约）。
  更新流程：
    1. 把新版池 CSV 放进 config/（或修改 contracts.yaml 顶部 pool_csv 指向）
    2. 重跑本脚本：.venv/Scripts/python tools/build_contracts_config.py
    3. 池中新增合约 → 下次 download 自动全量补历史；池中移除的合约 → 自动停止更新
  手工补充池外品种（如股指、主连）：直接编辑 contracts.yaml，
  给条目加 extra: true，重跑本脚本时会被保留。
"""
import csv
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "config" / "contracts.yaml"

# 交易所映射：池CSV写法 → (代码后缀, 数据源, 交易所代码)
# 2026-07-30 用户决策：品种全部走米筐（全交易所覆盖），同花顺只供南华指数 NHCI.SL。
# 后缀仅是标识（米筐取数时用符号主体）；郑商所 3 位年月码由 ricequant.py 自动转 4 位。
EXCHANGE_MAP = {
    "上期所(SHFE)":  {"suffix": ".SHF", "source": "ricequant", "exchange": "SHFE"},
    "上期能源(INE)": {"suffix": ".INE", "source": "ricequant", "exchange": "INE"},
    "大商所(DCE)":   {"suffix": ".DCE", "source": "ricequant", "exchange": "DCE"},
    "郑商所(CZCE)":  {"suffix": ".CZC", "source": "ricequant", "exchange": "CZCE"},
    "广期所(GFEX)":  {"suffix": ".GFE", "source": "ricequant", "exchange": "GFEX"},
    "中金所(CFFEX)": {"suffix": ".CFE", "source": "ricequant", "exchange": "CFFEX"},
}

# 首次生成时的池外补充品种（extra: true，重新生成时保留）
DEFAULT_EXTRAS = [
    {"symbol": "IM8888.CFE", "source": "ricequant", "name": "中证1000主连",  "category": "股指", "exchange": "CFFEX", "extra": True},
    {"symbol": "IM2609.CFE", "source": "ricequant", "name": "中证1000·09合约", "category": "股指", "exchange": "CFFEX", "extra": True},
]


def main():
    # 读取现有 contracts.yaml：保留 pool_csv 指向和 extra 条目
    pool_csv, extras = None, list(DEFAULT_EXTRAS)
    if OUT.exists():
        old = yaml.safe_load(open(OUT, encoding="utf-8"))
        pool_csv = old.get("pool_csv")
        extras = [e for e in old.get("contracts", []) if e.get("extra")] or extras
    pool_csv = sys.argv[1] if len(sys.argv) > 1 else pool_csv
    if not pool_csv:
        raise SystemExit("未指定池 CSV：请把文件路径作为参数传入，或先手工建立 contracts.yaml 的 pool_csv 字段")
    pool_path = ROOT / pool_csv
    if not pool_path.exists():
        raise SystemExit(f"池 CSV 不存在: {pool_path}")

    contracts = []
    skipped = []
    with open(pool_path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            exch = row["交易所"].strip()
            code = row["主力合约"].strip()
            m = EXCHANGE_MAP.get(exch)
            if not m:
                skipped.append((exch, code))
                continue
            contracts.append({
                "symbol": code + m["suffix"],
                "source": m["source"],
                "name": row["品种"].strip(),
                "category": row["类别"].strip(),
                "exchange": m["exchange"],
            })
    contracts.extend(extras)

    doc = {
        "pool_csv": pool_csv,
        "contracts": contracts,
    }
    header = (
        "# 合约池配置（下载/更新/计算的唯一合约来源）\n"
        "# 由 tools/build_contracts_config.py 从 pool_csv 生成；可手工编辑。\n"
        "# - 池 CSV 更新后重跑生成器即可；extra: true 的条目会被保留\n"
        "# - source: ifind（同花顺）| ricequant（米筐，中金所专用）\n"
        f"# 本次生成: {pool_csv} → {len(contracts)} 个合约\n"
    )
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(header)
        yaml.dump(doc, f, allow_unicode=True, sort_keys=False)
    print(f"已生成 {OUT}：池内 {len(contracts) - len(extras)} + 池外 {len(extras)} = {len(contracts)} 个合约")
    if skipped:
        print(f"⚠️ 跳过未识别的交易所 {len(skipped)} 条: {skipped}")


if __name__ == "__main__":
    main()
