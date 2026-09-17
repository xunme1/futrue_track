"""日报版本绑定与原子归档；只使用标准库。"""
import hashlib
import json
import os
import tempfile
from datetime import date
from pathlib import Path

RULES_VERSION = 'summary-v2.0'


def iso_day(value):
    """行情时间标签的日期部分；不把文件生成时间当行情时间。"""
    try:
        return date.fromisoformat(str(value)[:10]).isoformat()
    except (ValueError, TypeError):
        return None


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     allow_nan=False).encode()).hexdigest()


def atomic_text(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as fp:
            name = fp.name
            fp.write(value)
        os.replace(name, path)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def atomic_json(path, value):
    atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))
