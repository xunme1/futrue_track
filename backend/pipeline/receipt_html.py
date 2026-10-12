# -*- coding: utf-8 -*-
"""仓单研究监控 HTML 日报：事实汇总 → 自包含交互报告（内嵌 JSON + 运行时 SVG，无外部依赖）。

产物：data/receipt/receipt_report_<YYYYMMDD>.html + 同名 .json 审计（含 html_hash 校验链）。
图表：仓单×主力价双轴（近1年）、分年季节性叠加（近5年）、日增减柱（近60日）、
      仓单+有效预报（仅郑商所）。信号为确定性规则（receipt_core.evaluate_signals）。
"""
from __future__ import annotations

import argparse
from datetime import datetime
import html
import json
import math
from pathlib import Path

from backend.core.config import load_contracts
from backend.core.store import Store
from backend.pipeline.report_store import atomic_json, atomic_text, digest
from backend.pipeline.receipt_core import (
    CN_NAME,
    HISTORY_DIR,
    RECEIPT_DIR,
    UNIT_LABEL,
    build_series,
    compute_symbol_metrics,
    load_history,
    underlying_of,
)

REPORT_SCHEMA_VERSION = 1


def contract_map():
    """品种代码 → 合约池条目（用于取主力价格/持仓；pinned 条目自然生效）。"""
    out = {}
    for entry in load_contracts():
        symbol = str(entry.get("symbol") or "")
        if not symbol:
            continue
        underlying = underlying_of(symbol)
        if underlying and underlying not in out:
            out[underlying] = entry
    return out


def load_price(entry, store=None):
    """读取合约日线（close/ccl）；缺数据返回 None。"""
    if not entry:
        return None
    store = store or Store()
    try:
        return store.read(entry.get("source") or "ricequant", "futures_daily", entry["symbol"])
    except (FileNotFoundError, KeyError, OSError, ValueError):
        return None


def _sanitize(obj):
    """递归把 NaN/Inf 换成 None，保证 allow_nan=False 的 JSON 序列化与 digest 稳定。"""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize(v) for v in obj]
    return obj


def build_facts(directory=None, store=None):
    """汇总全部有 history 的品种 → facts dict（含图表序列与规则信号）。"""
    root = Path(directory) if directory else HISTORY_DIR
    store = store or Store()
    contracts = contract_map()
    symbols = []
    for path in sorted(root.glob("*.csv")):
        symbol = path.stem.upper()
        hist = load_history(path)
        entry = contracts.get(symbol)
        price = load_price(entry, store)
        metrics = compute_symbol_metrics(hist, price)
        if not metrics.get("available"):
            continue
        metrics["symbol"] = symbol
        metrics["name"] = CN_NAME.get(symbol, symbol)
        metrics["unit"] = UNIT_LABEL.get(metrics.get("exchange", ""), "单位")
        metrics["contract"] = (entry or {}).get("symbol")
        metrics["series"] = build_series(hist, price)
        symbols.append(metrics)
    data_date = max((m["date"] for m in symbols), default=None)
    n_long = sum(1 for m in symbols for s in m["signals"] if s["tone"] == "long")
    n_short = sum(1 for m in symbols for s in m["signals"] if s["tone"] == "short")
    n_warn = sum(1 for m in symbols for s in m["signals"] if s["tone"] == "warn")
    return _sanitize({
        "data_date": data_date,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "symbols": symbols,
        "coverage": {"total": len(symbols), "long": n_long, "short": n_short, "warn": n_warn},
    })


CSS = r"""
:root{color-scheme:dark;--bg:#0e1e33;--panel:#142a46;--panel2:#18314f;--edge:#294967;--gold:#d9b98a;--text:#eee9df;--muted:#9caec3;--green:#26a67b;--red:#d45858;--track:#334c68;--blue:#8ab4d8}
*{box-sizing:border-box}html{scroll-padding-top:58px}body{margin:0;background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif}
button,input,select{font:inherit}button{cursor:pointer}
.report{max-width:1560px;margin:0 auto;padding:18px 24px 32px;font-size:13px}
.masthead{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:24px;align-items:center;border-bottom:1px solid rgba(217,185,138,.55);padding-bottom:12px}
.eyebrow{color:var(--gold);font-size:10px;font-weight:700;letter-spacing:.14em}.masthead h1{font-size:24px;margin:5px 0}.sub{color:var(--muted);font-size:12px}.date{font-size:17px;font-weight:700;text-align:right}.date small{display:block;font-size:11px;font-weight:400;color:var(--muted);margin-top:4px}
.coverage{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;margin:12px 0}.stat{background:var(--panel);border:1px solid var(--edge);padding:8px 10px;border-radius:8px;font-size:11px;color:var(--muted)}.stat b{display:block;font-size:20px;color:var(--text);margin-top:2px}
.sigstrip{border:1px solid var(--edge);border-radius:8px;background:var(--panel);padding:10px 14px;margin:10px 0;font-size:12px;line-height:2}
.sigstrip .lab{color:var(--muted);margin-right:6px}.chip{display:inline-block;margin:2px 4px 2px 0;padding:1px 8px;border-radius:4px;font-size:11px;font-weight:700;text-decoration:none;border:1px solid transparent}
.chip.long{background:rgba(38,166,123,.16);color:var(--green)}.chip.short{background:rgba(212,88,88,.16);color:var(--red)}.chip.warn{background:rgba(217,185,138,.14);color:var(--gold)}
.toolbar{position:sticky;top:0;z-index:20;background:rgba(14,30,51,.96);backdrop-filter:blur(8px);display:flex;gap:6px;align-items:center;flex-wrap:wrap;padding:7px 0;border-bottom:1px solid var(--edge);font-size:12px}
.toolbar button,.toolbar select,.toolbar input{border:1px solid var(--edge);background:var(--panel);color:var(--text);border-radius:6px;padding:6px 9px;min-height:32px}
.toolbar button:hover{border-color:var(--gold)}.toolbar button.active{background:var(--gold);color:#102038;border-color:var(--gold)}
.toolbar-group{display:flex;align-items:center;gap:5px}.toolbar-exports{margin-left:auto}.selection-status{font-size:11px;color:var(--muted)}
.picker{position:relative}.picker-panel{position:absolute;top:38px;left:0;z-index:30;width:340px;max-height:380px;overflow:auto;background:#10233a;border:1px solid var(--edge);border-radius:8px;padding:10px;box-shadow:0 10px 26px rgba(0,0,0,.4)}
.picker-search{width:100%;margin-bottom:6px}.pick-row{display:flex;gap:7px;align-items:center;padding:4px 6px;border-radius:5px;font-size:12px}.pick-row:hover{background:rgba(255,255,255,.04)}
#cards{display:grid;grid-template-columns:minmax(0,1fr);gap:12px;margin-top:12px}
@media(min-width:1280px){#cards{grid-template-columns:repeat(2,minmax(0,1fr))}}
.card{background:var(--panel);border:1px solid var(--edge);border-radius:10px;scroll-margin-top:56px;min-width:0}
.card-head{display:flex;align-items:center;gap:10px;flex-wrap:wrap;padding:11px 14px;border-bottom:1px solid var(--edge)}
.card-head h2{font-size:16px;margin:0}.card-head .code{color:var(--muted);font-size:11px}.card-head .ex{font-size:10px;color:var(--blue);border:1px solid var(--edge);border-radius:4px;padding:0 5px}
.badges{margin-left:auto;display:flex;gap:4px;flex-wrap:wrap}
.badge{display:inline-block;padding:1px 7px;border-radius:4px;font-size:11px;font-weight:700;line-height:1.7;cursor:help}
.badge.long{background:rgba(38,166,123,.16);color:var(--green)}.badge.short{background:rgba(212,88,88,.16);color:var(--red)}.badge.warn{background:rgba(217,185,138,.14);color:var(--gold)}
.metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:6px;padding:10px 14px}
.metric{background:var(--panel2);border:1px solid rgba(84,116,145,.3);border-radius:7px;padding:6px 9px;font-size:10px;color:var(--muted)}
.metric b{display:block;font-size:14px;color:var(--text);margin-top:2px;font-variant-numeric:tabular-nums}
.metric b.up{color:var(--green)}.metric b.down{color:var(--red)}
.charts{display:grid;grid-template-columns:minmax(0,1fr);gap:8px;padding:0 14px 12px}
@media(min-width:760px){.charts{grid-template-columns:repeat(2,minmax(0,1fr))}}
.chart{background:#10233a;border:1px solid rgba(84,116,145,.35);border-radius:7px;padding:8px}
.chart h3{margin:0 0 4px;font-size:11px;color:var(--muted);font-weight:400}
.chart svg{display:block;width:100%;height:auto}
.stale{color:var(--gold);font-size:10px}
.footer{margin-top:16px;padding-top:12px;border-top:1px solid var(--edge);color:var(--muted);font-size:11px;line-height:1.7}
.empty{padding:40px;text-align:center;color:var(--muted)}
@media(max-width:760px){.report{padding:12px}.masthead{grid-template-columns:1fr}.date{text-align:left}.coverage{grid-template-columns:repeat(2,1fr)}.metrics{grid-template-columns:repeat(2,1fr)}.picker-panel{position:fixed;left:12px;right:12px;top:92px;width:auto}}
"""


JS = r"""
const payload=JSON.parse(document.getElementById('report-data').textContent);
const facts=payload.facts;
const state={selected:[],query:'',limit:10,sort:'signal'};
const esc=(v)=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmtI=(v)=>{if(v===null||v===undefined||isNaN(v))return '—';const a=Math.abs(v);return a>=10000?(v/10000).toFixed(1)+'万':Math.round(v).toLocaleString('zh-CN')};
const fmtS=(v)=>{if(v===null||v===undefined||isNaN(v))return '—';return (v>0?'+':'')+fmtI(v)};
const fmtP=(v)=>v===null||v===undefined||isNaN(v)?'—':(v*100).toFixed(0)+'%';
const fmtD=(d)=>d&&d.length===8?`${d.slice(0,4)}-${d.slice(4,6)}-${d.slice(6)}`:d;
const bySymbol=Object.fromEntries(facts.symbols.map(s=>[s.symbol,s]));

/* ---------- 通用 SVG ---------- */
const W=560,H=190,PL=46,PR=46,PT=10,PB=22;
function ext(vals){let lo=Infinity,hi=-Infinity;vals.forEach(v=>{if(v===null||v===undefined||isNaN(v))return;if(v<lo)lo=v;if(v>hi)hi=v});if(lo>hi){lo=0;hi=1}if(lo===hi){lo-=1;hi+=1}const pad=(hi-lo)*.08;return[lo-pad,hi+pad]}
function ticks(lo,hi,n){const out=[];for(let i=0;i<=n;i++)out.push(lo+(hi-lo)*i/n);return out}
function fmtTick(v){const a=Math.abs(v);if(a>=10000)return (v/10000).toFixed(1)+'万';if(a>=100)return v.toFixed(0);if(a>=1)return v.toFixed(1);return v.toFixed(2)}
function lineChart(spec){
 // spec: {series:[{pts:[[x,y]],color,width,dash}], y2:[lo,hi]|null, xTicks:[{v,label}], aria}
 const [lo,hi]=spec.y||ext(spec.series.filter(s=>!s.right).flatMap(s=>s.pts.map(p=>p[1])));
 const xs=spec.series.flatMap(s=>s.pts.map(p=>p[0])),xLo=Math.min(...xs),xHi=Math.max(...xs);
 const X=v=>PL+(xHi===xLo?.5:(v-xLo)/(xHi-xLo))*(W-PL-PR), Y=v=>PT+(1-(v-lo)/(hi-lo))*(H-PT-PB);
 let inner=ticks(lo,hi,4).map(v=>`<line x1="${PL}" y1="${Y(v)}" x2="${W-PR}" y2="${Y(v)}" stroke="#294967" stroke-width=".6"/><text x="${PL-5}" y="${Y(v)+3}" text-anchor="end" fill="#9caec3" font-size="9">${fmtTick(v)}</text>`).join('');
 inner+=(spec.xTicks||[]).map(t=>`<text x="${X(t.v)}" y="${H-7}" text-anchor="middle" fill="#9caec3" font-size="9">${esc(t.label)}</text>`).join('');
 if(spec.y2){const[l2,h2]=spec.y2;const Y2=v=>PT+(1-(v-l2)/(h2-l2))*(H-PT-PB);
  inner+=ticks(l2,h2,4).map(v=>`<text x="${W-PR+5}" y="${Y2(v)+3}" fill="#8ab4d8" font-size="9">${fmtTick(v)}</text>`).join('')}
 spec.series.forEach(s=>{const yF=s.right?(v=>PT+(1-(v-spec.y2[0])/(spec.y2[1]-spec.y2[0]))*(H-PT-PB)):Y;
  const d=s.pts.filter(p=>p[1]!==null&&p[1]!==undefined).map((p,i)=>`${i?'L':'M'}${X(p[0]).toFixed(1)},${yF(p[1]).toFixed(1)}`).join('');
  if(d)inner+=`<path d="${d}" fill="none" stroke="${s.color}" stroke-width="${s.width||1.4}" ${s.dash?'stroke-dasharray="4 3"':''} stroke-linejoin="round"/>`});
 return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(spec.aria||'')}">${inner}</svg>`}
function barChart(rows,aria){
 const vals=rows.map(r=>r[1]),[lo,hi]=ext(vals.concat([0])),xLo=0,xHi=rows.length-1||1;
 const X=i=>PL+(xHi===0?.5:i/xHi)*(W-PL-PR),Y=v=>PT+(1-(v-lo)/(hi-lo))*(H-PT-PB);
 const bw=Math.max(1.5,(W-PL-PR)/Math.max(rows.length,1)*.62);
 let inner=ticks(lo,hi,4).map(v=>`<line x1="${PL}" y1="${Y(v)}" x2="${W-PR}" y2="${Y(v)}" stroke="#294967" stroke-width=".6"/><text x="${PL-5}" y="${Y(v)+3}" text-anchor="end" fill="#9caec3" font-size="9">${fmtTick(v)}</text>`).join('');
 inner+=`<line x1="${PL}" y1="${Y(0)}" x2="${W-PR}" y2="${Y(0)}" stroke="#d9b98a" stroke-width=".8"/>`;
 rows.forEach((r,i)=>{const v=r[1],y0=Y(0),y1=Y(v);inner+=`<rect x="${(X(i)-bw/2).toFixed(1)}" y="${Math.min(y0,y1).toFixed(1)}" width="${bw.toFixed(1)}" height="${Math.max(1,Math.abs(y1-y0)).toFixed(1)}" fill="${v>=0?'#d9b98a':'#566d88'}"><title>${fmtD(r[0])} ${fmtS(v)} 手</title></rect>`});
 const xi=[0,.25,.5,.75,1].map(f=>Math.round(f*xHi));
 inner+=xi.map(i=>rows[i]?`<text x="${X(i)}" y="${H-7}" text-anchor="middle" fill="#9caec3" font-size="9">${fmtD(rows[i][0]).slice(5)}</text>`:'').join('');
 return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(aria)}">${inner}</svg>`}

/* ---------- 卡片 ---------- */
function dateTicks(daily){const n=daily.length-1;return [0,.25,.5,.75,1].map(f=>{const i=Math.round(f*n);return daily[i]?{v:i,label:fmtD(daily[i][0]).slice(5)}:null}).filter(Boolean)}
function chartDual(s){const pts=s.series.daily.map((r,i)=>[i,r[1]]);const price=s.series.daily.map((r,i)=>[i,r[2]]).filter(p=>p[2]!==null);
 const spec={series:[{pts,color:'#d9b98a',width:1.6}],xTicks:dateTicks(s.series.daily),aria:'注册仓单与主力价格双轴走势'};
 if(price.length>5){spec.series.push({pts:price,color:'#8ab4d8',width:1.2,right:true});spec.y2=ext(price.map(p=>p[1]))}
 return lineChart(spec)}
function chartSeasonal(s){const palette=['#566d88','#7f95ad','#8ab4d8','#26a67b','#c98a5e'];const years=Object.keys(s.series.seasonal).sort();
 const cur=years[years.length-1];const series=years.map((y,i)=>({pts:s.series.seasonal[y].map(p=>[p[0],p[1]]),color:y===cur?'#d9b98a':palette[i%palette.length],width:y===cur?1.9:1.1,dash:y!==cur&&i%2===1}));
 const xTicks=[1,32,60,91,121,152,182,213,244,274,305,335].map((v,i)=>({v,label:(i+1)+'月'}));
 return lineChart({series,xTicks,y:ext(series.flatMap(sr=>sr.pts.map(p=>p[1]))),aria:'分年仓单季节性叠加'})}
function chartChanges(s){return barChart(s.series.changes,'近60交易日仓单增减')}
function chartEf(s){const pts=s.series.ef.map((r,i)=>[i,r[1]]),ef=s.series.ef.map((r,i)=>[i,r[2]]),tot=s.series.ef.map((r,i)=>[i,r[1]+r[2]]);
 return lineChart({series:[{pts:tot,color:'#26a67b',width:1.6},{pts,color:'#d9b98a',width:1.2},{pts:ef,color:'#8ab4d8',width:1.1,dash:true}],xTicks:dateTicks(s.series.ef),aria:'仓单与有效预报合计（绿=合计，金=仓单，蓝虚线=有效预报）'})}
function metric(label,value,cls){return `<div class="metric">${label}<b class="${cls||''}">${value}</b></div>`}
function cardHtml(s){
 const badges=s.signals.map(g=>`<span class="badge ${g.tone}" title="${esc(g.detail)}">${esc(g.title)}</span>`).join('');
 const stale=s.date!==facts.data_date?`<span class="stale" title="该品种仓单日报滞后于全市场最新日">数据至 ${fmtD(s.date)}</span>`:'';
 const cls5=s.change_5d>0?'up':s.change_5d<0?'down':'';
 const metrics=[
  metric(`注册仓单（${esc(s.unit)}）`,fmtI(s.on_warrant)),
  metric('折手（估算）',fmtI(s.lots)),
  metric('日增减',fmtS(s.change_1d),s.change_1d>0?'up':s.change_1d<0?'down':''),
  metric('5日增减',fmtS(s.change_5d)+(s.change_5d_pct!==null?` (${(s.change_5d_pct*100).toFixed(1)}%)`:''),cls5),
  metric('20日累计',s.change_20d_pct!==null?(s.change_20d_pct*100).toFixed(1)+'%':'—',s.change_20d<0?'down':s.change_20d>0?'up':''),
  metric('历史分位',fmtP(s.pct_all)),
  metric('价格分位(1y)',fmtP(s.price_pct_1y)),
  metric(s.exchange==='CZCE'?'有效预报(折手)':'虚实比(仓单/持仓)',s.exchange==='CZCE'?fmtI(s.ef_lots):(s.virtual_ratio!==null&&s.virtual_ratio!==undefined?(s.virtual_ratio*100).toFixed(1)+'%':'—')),
 ].join('');
 const charts=[`<div class="chart"><h3>仓单 × 主力价（近1年，右轴价 ${esc(s.contract||'')}）</h3>${chartDual(s)}</div>`,
  `<div class="chart"><h3>分年季节性（当年加粗）</h3>${chartSeasonal(s)}</div>`,
  `<div class="chart"><h3>日增减（近60交易日，折手）</h3>${chartChanges(s)}</div>`];
 if(s.series.ef)charts.push(`<div class="chart"><h3>仓单 + 有效预报（绿合计/金仓单/蓝预报）</h3>${chartEf(s)}</div>`);
 return `<section class="card" id="card-${esc(s.symbol)}"><div class="card-head"><h2>${esc(s.name)}</h2><span class="code">${esc(s.symbol)}</span><span class="ex">${esc(s.exchange)}</span>${stale}<div class="badges">${badges}</div></div><div class="metrics">${metrics}</div><div class="charts">${charts.join('')}</div></section>`}

/* ---------- 汇总条 + 交互 ---------- */
function sortedSymbols(){let list=state.selected.length?state.selected.map(x=>bySymbol[x]).filter(Boolean):facts.symbols.slice();
 const sig=s=>(s.signals.length?0:1);const abspct=s=>Math.abs(s.change_5d_pct||0);
 if(state.sort==='signal')list.sort((a,b)=>sig(a)-sig(b)||abspct(b)-abspct(a));
 else if(state.sort==='up')list.sort((a,b)=>(b.change_5d_pct||-9)-(a.change_5d_pct||-9));
 else if(state.sort==='down')list.sort((a,b)=>(a.change_5d_pct||9)-(b.change_5d_pct||9));
 else if(state.sort==='pctl')list.sort((a,b)=>(a.pct_all??9)-(b.pct_all??9));
 else if(state.sort==='pcth')list.sort((a,b)=>(b.pct_all??-9)-(a.pct_all??-9));
 if(!state.selected.length&&state.limit>0)list=list.slice(0,state.limit);return list}
function renderStrip(){
 const by={long:[],short:[],warn:[]};facts.symbols.forEach(s=>s.signals.forEach(g=>{if(!by[g.tone].some(x=>x.symbol===s.symbol))by[g.tone].push(s)}));
 const grp=(tone,label)=>by[tone].length?`<div><span class="lab">${label} ${by[tone].length}：</span>${by[tone].map(s=>`<a class="chip ${tone}" href="#card-${esc(s.symbol)}" title="${esc(s.signals.map(g=>g.title+'：'+g.detail).join('；'))}">${esc(s.name)}</a>`).join('')}</div>`:'';
 document.getElementById('sigstrip').innerHTML=grp('short','偏空')+grp('long','偏多')+grp('warn','提示')||'<span class="lab">今日无触发信号的品种</span>'}
function render(){const list=sortedSymbols();document.getElementById('cards').innerHTML=list.length?list.map(cardHtml).join(''):'<div class="empty">没有可展示的品种</div>';
 document.getElementById('selection-status').textContent=state.selected.length?`对照：已选 ${state.selected.length} 个品种`:`信号优先 · 显示前 ${Math.min(state.limit||list.length,list.length)} / ${facts.symbols.length}`;
 document.querySelectorAll('[data-limit]').forEach(b=>b.classList.toggle('active',Number(b.dataset.limit)===state.limit&&!state.selected.length))}
function renderPicker(){const q=state.query.trim().toLowerCase();const rows=facts.symbols.filter(x=>!q||`${x.symbol} ${x.name}`.toLowerCase().includes(q));
 document.getElementById('pick-list').innerHTML=rows.map(x=>`<label class="pick-row"><input type="checkbox" value="${esc(x.symbol)}" ${state.selected.includes(x.symbol)?'checked':''}><span>${esc(x.name)} ${esc(x.symbol)}${x.signals.length?' · '+x.signals.map(g=>g.title).join('/') :''}</span></label>`).join('')}
document.getElementById('picker-toggle').addEventListener('click',()=>{const p=document.getElementById('picker-panel');p.hidden=!p.hidden});
document.getElementById('picker-search').addEventListener('input',e=>{state.query=e.target.value;renderPicker()});
document.getElementById('pick-list').addEventListener('change',e=>{if(e.target.type!=='checkbox')return;const s=e.target.value;
 if(e.target.checked)state.selected.push(s);else state.selected=state.selected.filter(x=>x!==s);render()});
document.querySelectorAll('[data-limit]').forEach(b=>b.addEventListener('click',()=>{state.limit=Number(b.dataset.limit);state.selected=[];render()}));
document.getElementById('sort').addEventListener('change',e=>{state.sort=e.target.value;render()});
document.getElementById('reset').addEventListener('click',()=>{state.selected=[];state.query='';state.limit=10;state.sort='signal';document.getElementById('picker-search').value='';document.getElementById('sort').value='signal';renderPicker();render()});
document.getElementById('download-html').addEventListener('click',()=>{const blob=new Blob(['<!DOCTYPE html>\n'+document.documentElement.outerHTML],{type:'text/html;charset=utf-8'});const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=`receipt_report_${facts.data_date}.html`;a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000)});
renderStrip();renderPicker();render();
"""


def render_html(facts):
    payload = json.dumps({"facts": facts}, ensure_ascii=False,
                         separators=(",", ":"), allow_nan=False).replace("</", "<\\/")
    cov = facts["coverage"]
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>仓单研究监控 · {html.escape(facts['data_date'])}</title><style>{CSS}</style></head><body>
<main class="report" id="report"><div class="masthead"><header><div class="eyebrow">WAREHOUSE RECEIPT · DAILY</div><h1>仓单研究监控</h1><div class="sub">注册仓单 = 可交割货源的官方库存；看水平（分位）、看变化（增减/注销）、看相对持仓（虚实比）</div></header><div class="date">{html.escape(facts['data_date'])}<small>生成 {html.escape(facts['generated_at'][:16])}</small></div></div>
<div class="coverage"><div class="stat"><span>覆盖品种</span><b>{cov['total']}</b></div><div class="stat"><span>偏空信号</span><b>{cov['short']}</b></div><div class="stat"><span>偏多信号</span><b>{cov['long']}</b></div><div class="stat"><span>提示</span><b>{cov['warn']}</b></div></div>
<div class="sigstrip" id="sigstrip"></div>
<div class="toolbar"><div class="picker"><button id="picker-toggle" type="button">选择品种</button><div class="picker-panel" id="picker-panel" hidden><input id="picker-search" class="picker-search" aria-label="搜索名称或代码" placeholder="搜索名称或代码"><div id="pick-list"></div></div></div><div class="toolbar-group"><span class="sub">显示</span><button type="button" data-limit="10">10</button><button type="button" data-limit="20">20</button><button type="button" data-limit="0">全部</button></div><div class="toolbar-group"><span class="sub">排序</span><select id="sort" aria-label="排序方式"><option value="signal">信号优先</option><option value="up">5日增幅</option><option value="down">5日减幅</option><option value="pcth">历史分位高</option><option value="pctl">历史分位低</option></select></div><button id="reset" type="button">恢复默认</button><span class="selection-status" id="selection-status" aria-live="polite"></span><div class="toolbar-group toolbar-exports"><button id="download-html" type="button">下载 HTML</button></div></div>
<div id="cards"></div><footer class="footer">口径：注册仓单/有效预报来自米筐 futures.get_warehouse_stocks（交易所仓单日报）；折手为估算——郑商所 张×warrant_units，上期所/能源中心 吨÷合约乘数，大商所原值即手；虚实比=仓单折手÷合约池当前主力合约持仓量，价格取同一主力合约收盘价。信号为确定性规则（R1供应压力/R2供应偏紧/R3高位套保/R4注销潮/R5虚实比警示），仅供观察，不构成投资建议。</footer></main>
<script id="report-data" type="application/json">{payload}</script><script>{JS}</script></body></html>"""


def publish_report(facts, directory=None):
    root = Path(directory) if directory else RECEIPT_DIR
    root.mkdir(parents=True, exist_ok=True)
    body = render_html(facts)
    html_path = root / f"receipt_report_{facts['data_date']}.html"
    json_path = root / f"receipt_report_{facts['data_date']}.json"
    atomic_text(html_path, body)
    record = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "data_date": facts["data_date"],
        "generated_at": facts["generated_at"],
        "input_hash": digest(facts),
        "html_hash": digest(body),
        "summary": f"覆盖 {facts['coverage']['total']} 品种；偏空 {facts['coverage']['short']} / "
                   f"偏多 {facts['coverage']['long']} / 提示 {facts['coverage']['warn']}",
        "facts": facts,
    }
    atomic_json(json_path, record)
    return html_path, json_path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    facts = build_facts()
    if not facts["symbols"]:
        raise SystemExit("缺少 history 数据，请先运行 python -m backend.pipeline.receipt_fetch --backfill")
    html_path, json_path = publish_report(facts)
    print(f"[仓单日报] {html_path}")
    print(f"[仓单日报] {json_path}")


if __name__ == "__main__":
    main()
