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
  访问控制：
    环境变量 FUTURES_DASHBOARD_PASSWORD 设置后，全站（页面+API）需要密码登录；
    未设置则不启用验证（本地开发默认开放）。
    GET /login                   登录页；POST /api/login 校验口令并种下 7 天会话 cookie
"""
import hashlib
import hmac
import json
import os
import re
from datetime import datetime, timedelta
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from ..core.config import DATA_DIR, PROJECT_ROOT, load_contracts
from ..core.timeframes import json_dir, screening_file
from ..pipeline.report_store import digest, iso_day

FRONTEND_DIR = PROJECT_ROOT / "frontend"
REPORTS_DIR = DATA_DIR / "reports"
SCAN_DIR = REPORTS_DIR / "scan"
NARRATIVE_DIR = REPORTS_DIR / "narrative"
_REPORT_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
app = FastAPI(title="期货指标监测 API", version="0.2.0")

# ---------------------------------------------------------------- 登录验证
# 密码只从环境变量读取，不写入任何配置文件；未设置时全站开放（本地开发）。
DASHBOARD_PASSWORD = os.environ.get("FUTURES_DASHBOARD_PASSWORD", "")
AUTH_COOKIE = "ft_auth"
AUTH_COOKIE_MAX_AGE = 7 * 24 * 3600  # 7 天
_AUTH_OPEN_PATHS = ("/login", "/api/login", "/api/health")
_AUTH_TOKEN_MSG = b"futrue_track-dashboard-v1"


def _auth_token() -> str:
    """由口令派生的会话令牌；改口令即令全部已登录会话失效。"""
    return hmac.new(DASHBOARD_PASSWORD.encode("utf-8"), _AUTH_TOKEN_MSG,
                    hashlib.sha256).hexdigest()


def _is_authed(request: Request) -> bool:
    if not DASHBOARD_PASSWORD:
        return True
    token = request.cookies.get(AUTH_COOKIE, "")
    return bool(token) and hmac.compare_digest(token, _auth_token())


@app.middleware("http")
async def dashboard_auth(request: Request, call_next):
    if DASHBOARD_PASSWORD and request.url.path not in _AUTH_OPEN_PATHS \
            and not _is_authed(request):
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": "未登录或会话已过期"}, status_code=401)
        return RedirectResponse("/login", status_code=302)
    return await call_next(request)


_LOGIN_PAGE = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>登录 · 期货指标监测</title><style>
body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;background:#0b1526;font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif}
.card{background:#132a47;border:1px solid #294967;border-radius:10px;padding:36px 40px;width:300px;color:#eee9df}
h1{font-size:18px;margin:0 0 6px;color:#d9b98a}p.sub{font-size:12px;color:#8fa3bd;margin:0 0 22px}
input{width:100%;box-sizing:border-box;padding:10px 12px;border-radius:6px;border:1px solid #294967;background:#10233a;color:#eee9df;font-size:14px;outline:none}
input:focus{border-color:#d9b98a}
button{width:100%;margin-top:14px;padding:10px;border:0;border-radius:6px;background:#d9b98a;color:#102038;font-size:14px;font-weight:700;cursor:pointer}
.err{color:#d45858;font-size:12px;min-height:16px;margin-top:10px}
</style></head><body>
<form class="card" id="f"><h1>期货指标监测</h1><p class="sub">请输入访问密码</p>
<input type="password" id="pw" autocomplete="current-password" autofocus>
<button type="submit">登 录</button><div class="err" id="err"></div></form>
<script>
document.getElementById('f').addEventListener('submit',async e=>{e.preventDefault();
const r=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password:document.getElementById('pw').value})});
if(r.ok){location.href='/'}else{document.getElementById('err').textContent='密码错误，请重试'}});
</script></body></html>"""


@app.get("/login", response_class=HTMLResponse)
def login_page():
    return _LOGIN_PAGE


@app.post("/api/login")
def login(payload: dict):
    password = str((payload or {}).get("password") or "")
    if not DASHBOARD_PASSWORD or not hmac.compare_digest(password, DASHBOARD_PASSWORD):
        raise HTTPException(status_code=401, detail="密码错误")
    response = JSONResponse({"ok": True})
    response.set_cookie(AUTH_COOKIE, _auth_token(), max_age=AUTH_COOKIE_MAX_AGE,
                        httponly=True, samesite="lax")
    return response



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


def _check_report_date(date: str) -> str:
    if not _REPORT_DATE_RE.fullmatch(date) or iso_day(date) != date:
        raise HTTPException(status_code=400, detail="日期格式应为 YYYY-MM-DD")
    return date


@app.get("/api/reports")
def reports():
    """已归档每日总结列表（倒序）。由 backend.pipeline.scan_report/summary_render 生成。"""
    out = []
    if not REPORTS_DIR.exists():
        return out
    for fp in sorted(REPORTS_DIR.glob("daily_summary_*.html"), reverse=True):
        date = fp.stem.replace("daily_summary_", "")
        if not _REPORT_DATE_RE.fullmatch(date) or iso_day(date) != date:
            continue
        meta = {"date": date, "has_html": True,
                "data_date": None, "generated_at": None, "one_liner": None}
        record = _archived_report(date)
        if record:
            meta.update(data_date=record['data_date'],
                        generated_at=record['facts'].get('created_at'), one_liner=record.get('one_liner'))
        out.append(meta)
    return out


def _archived_report(day):
    """成品与自己的事实副本绑定；缺少归档记录时不推算、拼接扫描数据。"""
    path = REPORTS_DIR / f'daily_summary_{day}.json'
    try:
        with open(path, encoding='utf-8') as fp:
            record = json.load(fp)
        if not isinstance(record, dict):
            return None
        if record.get('report_date') != day or record.get('data_date') != record['facts'].get('data_date'):
            return None
        html_path = REPORTS_DIR / f'daily_summary_{day}.html'
        if digest(html_path.read_text(encoding='utf-8')) != record.get('html_hash'):
            return None
        return record
    except (OSError, ValueError, KeyError, TypeError):
        return None


@app.get("/api/reports/{date}")
def report_detail(date: str):
    """返回发布HTML时归档的同批事实及已核验叙事，不读取后来变化的源文件。"""
    date = _check_report_date(date)
    record = _archived_report(date)
    if record is None:
        raise HTTPException(status_code=404, detail=f"无 {date} 的有效成品记录，请重新扫描并渲染")
    return {"report_date": date, "facts": record['facts'], "narrative": record.get('narrative')}


@app.get("/api/reports/{date}/html")
def report_html(date: str):
    """某日每日总结 HTML（供前端 iframe 直接展示）。"""
    fp = REPORTS_DIR / f"daily_summary_{_check_report_date(date)}.html"
    if not fp.exists():
        raise HTTPException(status_code=404, detail=f"无 {date} 的每日总结 HTML")
    return FileResponse(fp, media_type="text/html")


SEAT_DIR = DATA_DIR / "seat"
_SEAT_DATE_RE = re.compile(r"^\d{8}$")


def _check_seat_date(date: str) -> str:
    if not _SEAT_DATE_RE.fullmatch(date):
        raise HTTPException(status_code=400, detail="日期格式应为 YYYYMMDD")
    return date


@app.get("/api/seat")
def seat_list():
    """已归档席位追踪列表（倒序）。由 backend.pipeline.seat_daily 生成。"""
    out = []
    if not SEAT_DIR.exists():
        return out
    for fp in sorted(SEAT_DIR.glob("seat_data_*.csv"), reverse=True):
        date = fp.stem.removeprefix("seat_data_")
        if not _SEAT_DATE_RE.fullmatch(date):
            continue
        report_path = SEAT_DIR / f"seat_report_{date}.json"
        report = None
        if report_path.exists():
            try:
                value = json.loads(report_path.read_text(encoding="utf-8"))
                html_path = SEAT_DIR / f"seat_report_{date}.html"
                if (value.get("data_date") == date and html_path.exists()
                        and value.get("html_hash") == digest(html_path.read_text(encoding="utf-8"))):
                    report = value
            except (OSError, ValueError, TypeError):
                report = None
        out.append({"date": date,
                    "has_image": (SEAT_DIR / f"seat_direction_{date}.png").exists(),
                    "has_analysis": (SEAT_DIR / f"seat_analysis_{date}.md").exists(),
                    "has_detail": (SEAT_DIR / f"seat_detail_{date}.json").exists(),
                    "has_html": report is not None,
                    "summary": report.get("summary") if report else None,
                    "generated_at": report.get("generated_at") if report else None})
    return out


@app.get("/api/seat/{date}/image")
def seat_image(date: str):
    """某日席位方向图 PNG。"""
    fp = SEAT_DIR / f"seat_direction_{_check_seat_date(date)}.png"
    if not fp.exists():
        raise HTTPException(status_code=404, detail=f"无 {date} 的席位方向图")
    return FileResponse(fp, media_type="image/png")


@app.get("/api/seat/{date}/analysis")
def seat_analysis(date: str):
    """某日席位分歧解读 Markdown 文本。"""
    date = _check_seat_date(date)
    fp = SEAT_DIR / f"seat_analysis_{date}.md"
    if not fp.exists():
        raise HTTPException(status_code=404, detail=f"无 {date} 的席位解读")
    return {"date": date, "markdown": fp.read_text(encoding="utf-8")}


@app.get("/api/seat/{date}/html")
def seat_html(date: str):
    """某日自包含席位持仓 HTML 日报。"""
    date = _check_seat_date(date)
    fp = SEAT_DIR / f"seat_report_{date}.html"
    record_path = SEAT_DIR / f"seat_report_{date}.json"
    if not fp.exists() or not record_path.exists():
        raise HTTPException(status_code=404, detail=f"无 {date} 的席位 HTML 日报")
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
        body = fp.read_text(encoding="utf-8")
    except (OSError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=404, detail="席位日报归档损坏") from exc
    if record.get("data_date") != date or record.get("html_hash") != digest(body):
        raise HTTPException(status_code=404, detail="席位日报归档校验失败")
    return FileResponse(fp, media_type="text/html")


# 静态前端（放在最后，避免覆盖 /api 路由）
# 优先托管 React 构建产物 frontend/dist/（web/ 工程 npm run build 输出）；
# 不存在时回退托管 frontend/ 根目录（旧版 dashboard.html 离线快照）
DIST_DIR = FRONTEND_DIR / "dist"
_static_dir = DIST_DIR if (DIST_DIR / "index.html").exists() else FRONTEND_DIR
if _static_dir.exists():
    app.mount("/", StaticFiles(directory=_static_dir, html=True), name="frontend")
