# -*- coding: utf-8 -*-
"""
后端 API 服务（FastAPI）
  为前端看板/正式前端工程提供数据接口，并托管 frontend/ 静态文件。
  数据全部来自本地文件（data/store 本地行情库 + data/json 计算产物），不连数据源。
  启动：
    .venv/Scripts/python -m uvicorn backend.api.server:app --host 0.0.0.0 --port 8000
  接口：
    GET /api/health              健康检查
    GET /api/contracts           合约池元数据（品种中文名/类别/交易所/数据源/是否已有数据）
    GET /api/symbols             有数据的品种列表（含最新信号）
    GET /api/signals/{key}       某品种完整看板数据（K线+信号+通道+资金标记）
    GET /                        前端看板（frontend/ 静态目录）
"""
import json
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ..core.config import PROJECT_ROOT, load_contracts
from ..core.timeframes import json_dir, screening_file

FRONTEND_DIR = PROJECT_ROOT / "frontend"
app = FastAPI(title="期货指标监测 API", version="0.2.0")


def _load_payload(key: str, timeframe: str) -> dict:
    fp = json_dir(timeframe) / f"{key}.json"
    if not fp.exists():
        return None
    with open(fp, encoding="utf-8") as f:
        return json.load(f)


def _load_screening(timeframe: str) -> dict:
    """读取最新筛选报告；报告由 backend.pipeline.screen 生成。"""
    report_file = screening_file(timeframe)
    if not report_file.exists():
        raise HTTPException(
            status_code=404,
            detail="筛选报告不存在，请先运行 python -m backend.pipeline.screen",
        )
    try:
        with open(report_file, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail=f"筛选报告读取失败: {exc}") from exc


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/contracts")
def contracts(timeframe: Literal["1d", "4h"] = Query("1d")):
    """合约池元数据：前端构建品种选择器（按类别分组）用"""
    out = []
    for e in load_contracts():
        key = e["symbol"].split(".")[0]
        out.append({
            "key": key,
            "symbol": e["symbol"],
            "name": e.get("name", key),
            "category": e.get("category", ""),
            "exchange": e.get("exchange", ""),
            "source": e.get("source", ""),
            "extra": bool(e.get("extra", False)),
            "has_data": (json_dir(timeframe) / f"{key}.json").exists(),
        })
    return out


@app.get("/api/symbols")
def symbols(timeframe: Literal["1d", "4h"] = Query("1d")):
    """已有计算产物的品种列表（含 K 线根数、最后日期、最新一个交易信号）"""
    out = []
    for fp in sorted(json_dir(timeframe).glob("*.json")):
        with open(fp, encoding="utf-8") as f:
            d = json.load(f)
        sigs = d.get("signals") or []
        last_sig = None
        if sigs:
            s = sigs[-1]
            last_sig = {"type": s["type"], "date": d["dates"][s["i"]]}
        out.append({
            "key": fp.stem,
            "symbol": d.get("symbol"),
            "bars": len(d.get("dates", [])),
            "last_date": (d.get("dates") or [None])[-1],
            "pos": (d.get("POS") or [0])[-1],          # 当前持仓状态：1 多 / -1 空 / 0 空仓
            "last_signal": last_sig,
        })
    return out


@app.get("/api/screening")
def screening(timeframe: Literal["1d", "4h"] = Query("1d")):
    """最新筛选榜单报告（四类主筛 + 两类预警）。"""
    return _load_screening(timeframe)


@app.get("/api/signals/{key}")
def signals(key: str, timeframe: Literal["1d", "4h"] = Query("1d")):
    """某品种完整看板数据。字段说明见 docs/api.md"""
    d = _load_payload(key, timeframe)
    if d is None:
        raise HTTPException(status_code=404, detail=f"品种 '{key}' 无数据，请先运行 download + daily 流水线")
    return d


# 静态前端（放在最后，避免覆盖 /api 路由）
# 优先托管 React 构建产物 frontend/dist/（web/ 工程 npm run build 输出）；
# 不存在时回退托管 frontend/ 根目录（旧版 dashboard.html 离线快照）
DIST_DIR = FRONTEND_DIR / "dist"
_static_dir = DIST_DIR if (DIST_DIR / "index.html").exists() else FRONTEND_DIR
if _static_dir.exists():
    app.mount("/", StaticFiles(directory=_static_dir, html=True), name="frontend")
