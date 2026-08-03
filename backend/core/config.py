# -*- coding: utf-8 -*-
"""
配置加载：定位项目根目录的 config/config.yaml
优先级：环境变量 FUTURES_MONITOR_CONFIG > config/config.yaml > 项目根 config.yaml（兼容旧版）
"""
import os
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]

ENV_OVERRIDES = {
    "FUTURES_IFIND_USERNAME": ("account", "username"),
    "FUTURES_IFIND_PASSWORD": ("account", "password"),
    "FUTURES_RQDATA_LICENSE_KEY": ("ricequant", "license_key"),
    "FUTURES_DATA_SOURCE": ("data_source", "futures"),
}


def _apply_env_overrides(cfg):
    """用环境变量覆盖敏感项/运行时选项，避免凭据必须写入 YAML。"""
    for env_name, path in ENV_OVERRIDES.items():
        value = os.environ.get(env_name)
        if value is None:
            continue
        node = cfg
        for key in path[:-1]:
            node = node.setdefault(key, {})
        node[path[-1]] = value
    return cfg


def load_config(path=None):
    if path is None:
        path = (os.environ.get("FUTURES_MONITOR_CONFIG")
                or PROJECT_ROOT / "config" / "config.yaml")
        if not Path(path).exists():
            path = PROJECT_ROOT / "config.yaml"  # 兼容旧布局
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    return _apply_env_overrides(cfg)


CFG = load_config()
PARAMS = CFG["strategy"]                      # 策略参数字典（G1/G2/阈值等）
DATA_DIR = PROJECT_ROOT / CFG["output"]["dir"]  # 数据产物根目录（默认 data/）
JSON_DIR = DATA_DIR / "json"                  # 看板数据
CSV_DIR = DATA_DIR / "csv"                    # 信号明细
for _d in (JSON_DIR, CSV_DIR):
    _d.mkdir(parents=True, exist_ok=True)

CONTRACTS_FILE = PROJECT_ROOT / "config" / "contracts.yaml"


def load_contracts(path=None):
    """读取合约池（config/contracts.yaml）→ [{symbol, source, name, category, exchange, ...}]"""
    p = Path(path) if path else CONTRACTS_FILE
    with open(p, encoding="utf-8") as f:
        doc = yaml.safe_load(f) or {}
    contracts = doc.get("contracts", [])
    if not isinstance(contracts, list):
        raise ValueError(f"{p} 的 contracts 必须是列表")

    default_source = (CFG.get("data_source") or {}).get("futures")
    seen = set()
    for i, entry in enumerate(contracts, start=1):
        if not isinstance(entry, dict) or not entry.get("symbol"):
            raise ValueError(f"{p} 第 {i} 个合约缺少 symbol")
        entry.setdefault("source", default_source)
        if not entry.get("source"):
            raise ValueError(f"{p} 中 {entry['symbol']} 缺少 source，且未配置 data_source.futures")
        if entry["symbol"] in seen:
            raise ValueError(f"{p} 中存在重复合约: {entry['symbol']}")
        seen.add(entry["symbol"])
    return contracts
