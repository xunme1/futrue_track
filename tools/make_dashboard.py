# -*- coding: utf-8 -*-
"""
HTML K线信号看板生成器（前端构建器）
读取 data/json/*.json（backend.pipeline.daily 产物），生成单个自包含 frontend/dashboard.html：
  - K线三色：红=强于南华指数(PQ)，蓝=弱于指数(PR)，灰=强弱中性
  - 交易信号：BK/SK/SP/BP 箭头标注
  - 持仓趋势带：持多 EE~DD 红带、持空 KK~PP 绿带（复刻麦语言 FILLRGN）
  - 资金信号：SB / DSB / DSBE 图标 + DSBE 文字提示
  - NN/GG 强弱数字（图例可开关）
  - 副图：成交量 + 持仓量(ccl)
  - 支持 URL 锚点选品种：dashboard.html#m8888
运行：.venv/Scripts/python tools/make_dashboard.py
"""
import glob
import json
import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
JSON_DIR = ROOT / "data" / "json"
OUT_HTML = ROOT / "frontend" / "dashboard.html"
CONTRACTS = ROOT / "config" / "contracts.yaml"

# 品种标签与展示范围来自合约池（不在池里的旧 JSON 不上看板）
_pool = yaml.safe_load(open(CONTRACTS, encoding="utf-8")).get("contracts", [])
NAME_MAP = {e["symbol"].split(".")[0]: e["name"] for e in _pool}
POOL_KEYS = set(NAME_MAP)

datasets = {}
for fp in sorted(JSON_DIR.glob("*.json")):
    key = fp.stem
    if POOL_KEYS and key not in POOL_KEYS:
        continue                      # 已不在合约池中的旧数据，跳过
    with open(fp, encoding="utf-8") as f:
        d = json.load(f)
    d["label"] = NAME_MAP.get(key, d["symbol"])
    datasets[key] = d

if not datasets:
    raise SystemExit("data/json/ 下没有合约池内的 JSON，请先运行 python -m backend.pipeline.daily")

HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>iFinD 策略信号看板（文华策略复刻·测试版）</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js"></script>
<style>
  body{margin:0;background:#0f1420;color:#dfe6f2;font-family:"Microsoft YaHei",sans-serif}
  #bar{display:flex;gap:8px;align-items:center;padding:10px 14px;background:#161d2e;position:sticky;top:0;z-index:9}
  #bar button{background:#232d45;color:#cfd8ec;border:1px solid #35436b;border-radius:6px;
    padding:6px 16px;cursor:pointer;font-size:14px}
  #bar button.active{background:#c93a3a;border-color:#c93a3a;color:#fff}
  #bar .tip{font-size:12px;color:#8b97b5;margin-left:auto}
  #chart{width:100vw;height:70vh;min-height:480px}
  /* 下方图例说明区 */
  #legend-doc{background:#0b101b;border-top:1px solid #232d45;padding:18px 26px 30px;
    font-size:13px;line-height:1.9}
  #legend-doc h2{font-size:16px;color:#e8eefc;margin:0 0 4px}
  #legend-doc h3{font-size:14px;color:#9fb2d8;margin:18px 0 2px;border-left:3px solid #c93a3a;padding-left:8px}
  #legend-doc table{border-collapse:collapse;width:100%;max-width:1080px}
  #legend-doc td,#legend-doc th{border:1px solid #232d45;padding:5px 10px;vertical-align:top}
  #legend-doc th{background:#161d2e;color:#aebadb;text-align:left;white-space:nowrap}
  .tag{display:inline-block;min-width:44px;text-align:center;border-radius:4px;padding:0 6px;
    font-weight:bold;margin-right:6px}
</style>
</head>
<body>
<div id="bar">
  <b style="margin-right:8px">信号看板</b>
  <span id="btns"></span>
  <span class="tip">红K=强于南华指数 · 蓝K=弱于指数 · 灰K=强弱中性 · ▲BK开多 ▼SK开空 · 🤭SB / 🤔DSB / 👎DSBE资金信号 · 虚线=持仓通道</span>
</div>
<div id="chart"></div>

<!-- ==================== 标记详细解释区 ==================== -->
<div id="legend-doc">
  <h2>看板标记详解（文华麦语言策略复刻 · iFinD 数据版）</h2>
  <div style="color:#8b97b5">策略逻辑一句话：周线定方向 → M头/W底形态突破进场 → 盘整过滤避震荡 → 对比南华商品指数看强弱 → 持仓量验证资金。鼠标悬停任意 K 线可查看当日全部状态。</div>

  <h3>一、K 线颜色（相对强弱 · 对应麦语言 XDD 指标）</h3>
  <table>
    <tr><th>颜色</th><th>含义</th><th>判定条件（对比南华商品指数 NHCI.SL）</th></tr>
    <tr><td><span class="tag" style="background:#ff5252;color:#fff">红K</span></td><td><b>强于大盘（PQ）</b></td>
        <td>现价在七日中点上方时，本品种 7 日涨幅 &gt; 1.5 倍指数涨幅（领涨）；或在中点下方时，跌幅比指数轻（抗跌）</td></tr>
    <tr><td><span class="tag" style="background:#2979ff;color:#fff">蓝K</span></td><td><b>弱于大盘（PR）</b></td>
        <td>中点上方时涨幅 &lt; 0.7 倍指数（涨不动）；或中点下方时跌幅 &gt; 1.5 倍指数（领跌）</td></tr>
    <tr><td><span class="tag" style="background:#8b95ab;color:#1a2338">阳</span><span class="tag" style="background:#454e63;color:#cfd8ec">阴</span></td>
        <td><b>强弱中性</b>（灰K）</td><td>当天相对南华指数强弱不突出（既非 PQ 也非 PR），阳线浅灰、阴线深灰，仅作方向区分</td></tr>
  </table>

  <h3>二、交易信号箭头（会直接下单的信号 · 对应麦语言 SK/BK/BP/SP）</h3>
  <table>
    <tr><th>标记</th><th>含义</th><th>触发条件</th></tr>
    <tr><td><span class="tag" style="color:#ff3355">▲BK</span></td><td><b>开多单</b>（W 底确认）</td>
        <td>周线在 7 周均线上方（ZZ1）∧ 周线非盘整（TT1）∧ 价格突破「W 底颈线 HH 与 30 日区间 73% 分位线 DD 中较高者」∧ 本周期非盘整（7 日振幅 ≥ 均价 3%）</td></tr>
    <tr><td><span class="tag" style="color:#22cc88">▼SK</span></td><td><b>开空单</b>（M 头确认）</td>
        <td>周线在 7 周均线下方（AA1）∧ 周线非盘整（TT1）∧ 价格跌破「M 头颈线 LL 与 30 日区间 27% 分位线 KK 中较低者」∧ 本周期非盘整</td></tr>
    <tr><td><span class="tag" style="color:#ffaa00">△SP</span></td><td><b>平多单</b>（止盈/止损）</td>
        <td>持多期间价格跌破 EE 线（30 日区间约 64% 分位）——回撤不到一成就离场</td></tr>
    <tr><td><span class="tag" style="color:#ffaa00">▽BP</span></td><td><b>平空单</b>（止盈/止损）</td>
        <td>持空期间价格收复 PP 线（30 日区间约 36% 分位）</td></tr>
  </table>
  <div style="color:#8b97b5">说明：分位由文华原参数 G1=3.7、G2=2.8 决定（进场≈区间 27%/73% 深度确认，离场≈回撤 8.7 个百分点），信号按 AUTOFILTER 规则严格开平交替。</div>

  <h3>三、持仓趋势带与通道虚线（只在持仓期间显示 · 复刻麦语言 FILLRGN）</h3>
  <table>
    <tr><th>元素</th><th>含义</th></tr>
    <tr><td><span class="tag" style="background:rgba(229,69,69,.25);color:#ff8899">红带</span></td>
        <td><b>多头趋势带</b>：持多期间在 DD（开多阈值，区间 73% 分位）与 EE（平多防线，64% 分位）之间填充——色带内部就是这波多单的"盈利走廊"，价格跌穿下沿 EE 即触发 SP 离场</td></tr>
    <tr><td><span class="tag" style="background:rgba(47,163,107,.25);color:#7ee2ae">绿带</span></td>
        <td><b>空头趋势带</b>：持空期间在 KK（开空阈值，27% 分位）与 PP（平空防线，36% 分位）之间填充——价格收复上沿 PP 即触发 BP 离场</td></tr>
    <tr><td><span class="tag" style="color:#ff5566">DD</span><span class="tag" style="color:#ff8899">EE</span><span class="tag" style="color:#33dd99">KK</span><span class="tag" style="color:#88eebb">PP</span></td>
        <td><b>通道边界虚线</b>：趋势带的上下沿，与色带同步显示，方便读取具体价位</td></tr>
    <tr><td><span class="tag" style="color:#5c6b8a">灰线</span></td>
        <td><b>七日中点 ZD</b>：近 7 日最高价与最低价的中值，价格强弱的分水岭（上方偏强、下方偏弱）</td></tr>
  </table>

  <h3>四、资金信号 emoji（持仓量 OPI 驱动，只提示不下单）</h3>
  <table>
    <tr><th>标记</th><th>含义</th><th>触发条件</th></tr>
    <tr><td><span class="tag">🤭 SB</span></td><td><b>多头增仓</b></td>
        <td>持仓量单日 +4% 且价格涨 3%（或连续两日增仓创新高），KDJ 不超买（K&lt;85），且是近 5 根内首次出现；或增仓 ≥7%/超 4 万手的超级增仓——新钱在买</td></tr>
    <tr><td><span class="tag">🤔 DSB</span></td><td><b>洗盘后站起来</b></td>
        <td>两根前收阴，今日收盘收复近两根实体区间上 1/3，且期间持仓量换手 &gt;3%——浮筹被洗干净，少数人获利，多头更稳</td></tr>
    <tr><td><span class="tag">👎 DSBE</span></td><td><b>反击扑灭</b>（做空版）</td>
        <td>两根前收阳，今日收盘被砸进实体区间下 1/3 且伴随巨量换手——多头反击失败，跌势大概率延续。悬停可见附加判断：「减仓耗散筹码变轻」=多头割肉离场、跌势或近尾声；「增仓能量增强」=新空头加码、跌势延续</td></tr>
  </table>

  <h3>五、其他辅助元素</h3>
  <table>
    <tr><th>元素</th><th>含义</th></tr>
    <tr><td>NN / GG 数字</td><td>默认隐藏，点顶部图例「NN/GG强弱数字」开启。<b style="color:#ff6b6b">NN</b>（K线上方红字）= 本品种 7 日位置分层 − 指数分层，正数=比大盘站得高；<b style="color:#51cf66">GG</b>（K线下方绿字）= 当日收盘力度分层差，正数=今天比大盘硬</td></tr>
    <tr><td>副图黄线</td><td>持仓量（ccl）走势：增仓上涨=新资金进场；减仓上涨=空头回补，性质不同</td></tr>
    <tr><td>副图量柱</td><td>成交量，红绿随当日涨跌</td></tr>
    <tr><td>悬停提示</td><td>任意 K 线上悬停：OHLC、量、持仓、当日信号、强弱状态、周线三许可（空 AA1 / 多 ZZ1 / 非盘整 TT1）是否通过</td></tr>
  </table>

  <div style="color:#5c6b8a;margin-top:16px">数据：同花顺 iFinD（主力连续合约 8888 系列 + 南华商品指数 NHCI.SL） · 基准指数已由原脚本的文华商品指数(7186)替换为南华商品指数 · 本看板为测试阶段产物</div>
</div>
<!-- ==================== 解释区结束 ==================== -->

<script>
const DATA = __DATA__;
const chart = echarts.init(document.getElementById('chart'), null, {renderer:'canvas'});

// 品种切换按钮
const keys = Object.keys(DATA);
const span = document.getElementById('btns');
keys.forEach((k, i) => {
  const b = document.createElement('button');
  b.textContent = DATA[k].label;
  if (i === 0) b.classList.add('active');
  b.onclick = () => {
    document.querySelectorAll('#bar button').forEach(x => x.classList.remove('active'));
    b.classList.add('active');
    render(k);
  };
  span.appendChild(b);
});

function mask(arr, posArr, want) {
  // 只在持仓方向 want（1多/-1空）期间保留通道线值
  return arr.map((v, i) => posArr[i] === want ? v : null);
}

function render(key) {
  const d = DATA[key];
  const up = '#d43f3f', down = '#3a9e6f', pqC = '#ff5252', prC = '#2979ff';

  // K线着色：PQ红(强) / PR蓝(弱) / 中性灰(强弱不突出，阳线浅灰、阴线深灰)
  const kdata = d.ohlc.map((v, i) => {
    let color, color0, border, border0;
    if (d.PQ[i])      { color = pqC;        color0 = pqC;        border = pqC;        border0 = pqC; }
    else if (d.PR[i]) { color = prC;        color0 = prC;        border = prC;        border0 = prC; }
    else              { color = '#8b95ab';  color0 = '#454e63';  border = '#8b95ab';  border0 = '#8b95ab'; }
    return {value: v, itemStyle: {color, color0, borderColor: border, borderColor0: border0}};
  });

  // 交易信号标注
  const sigStyle = {BK:{c:'#ff3355',t:'▲BK',pos:'bottom'}, SK:{c:'#22cc88',t:'▼SK',pos:'top'},
                    SP:{c:'#ffaa00',t:'△SP',pos:'top'},    BP:{c:'#ffaa00',t:'▽BP',pos:'bottom'}};
  const sigPts = d.signals.map(s => {
    const st = sigStyle[s.type];
    return {coord: [s.i, s.price], value: st.t,
            itemStyle: {color: st.c}, label: {show: true, formatter: st.t, color: st.c,
            fontWeight: 'bold', fontSize: 13, position: st.pos, distance: 6}};
  });
  // Canvas 对 Unicode emoji 的字体回退不稳定，改为 SVG 图片符号。
  const emojiSymbol = emoji => 'image://data:image/svg+xml;charset=UTF-8,' + encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" width="28" height="28" viewBox="0 0 28 28">' +
    '<text x="14" y="23" text-anchor="middle" font-size="24" font-family="Segoe UI Emoji, Apple Color Emoji, Noto Color Emoji">' + emoji + '</text></svg>'
  );
  // 资金信号 emoji：🤭 SB / 🤔 DSB / 👎 DSBE
  d.SB.forEach((v, i)   => { if (v) sigPts.push({coord: [i, d.ohlc[i][2] * 0.995], value: 'SB',
      symbol: emojiSymbol('🤭'), symbolSize: 24, label: {show: false}}); });
  d.DSB.forEach((v, i)  => { if (v) sigPts.push({coord: [i, d.ohlc[i][2] * 0.99], value: 'DSB',
      symbol: emojiSymbol('🤔'), symbolSize: 24, label: {show: false}}); });
  d.DSBE.forEach((v, i) => { if (v) sigPts.push({coord: [i, d.ohlc[i][3] * 1.005], value: 'DSBE',
      symbol: emojiSymbol('👎'), symbolSize: 24, label: {show: false}}); });

  // NN/GG 强弱数字（默认隐藏，图例可开）
  const nnPts = [], ggPts = [];
  d.NN.forEach((v, i) => { if (v !== null && v !== 0) nnPts.push({coord: [i, d.ohlc[i][3] * 1.01], value: v,
      label: {show: true, formatter: String(v), color: '#ff6b6b', fontSize: 9, position: 'top'}, symbolSize: 1}); });
  d.GG.forEach((v, i) => { if (v !== null && v !== 0) ggPts.push({coord: [i, d.ohlc[i][2] * 0.99], value: v,
      label: {show: true, formatter: String(v), color: '#51cf66', fontSize: 9, position: 'bottom'}, symbolSize: 1}); });

  // 持仓通道：持多 DD/EE 红虚线、持空 KK/PP 绿虚线
  const chan = (arr, color, name) => ({
    name, type: 'line', data: arr, connectNulls: false, showSymbol: false,
    lineStyle: {type: 'dashed', width: 1.2, color}, xAxisIndex: 0, yAxisIndex: 0, z: 3});

  // 持仓趋势带（复刻麦语言 FILLRGN）：两条通道线之间填色
  // 用堆叠面积实现：base=下线(隐形) + diff=上线-下线(带 areaStyle)，只在持仓期间有值
  const band = (lowerArr, upperArr, color, name) => {
    const diff = lowerArr.map((v, i) =>
      (v !== null && upperArr[i] !== null) ? +(upperArr[i] - v).toFixed(4) : null);
    return [
      {name: name + '_base', type: 'line', data: lowerArr, stack: name, showSymbol: false,
       lineStyle: {opacity: 0}, areaStyle: {opacity: 0}, silent: true, connectNulls: false,
       emphasis: {disabled: true}, tooltip: {show: false}, xAxisIndex: 0, yAxisIndex: 0, z: 1},
      {name, type: 'line', data: diff, stack: name, showSymbol: false, connectNulls: false,
       lineStyle: {opacity: 0}, areaStyle: {color, opacity: 0.32}, silent: true,
       emphasis: {disabled: true}, tooltip: {show: false}, xAxisIndex: 0, yAxisIndex: 0, z: 1}
    ];
  };
  const longLower  = mask(d.EE, d.POS, 1),  longUpper  = mask(d.DD, d.POS, 1);   // 持多：EE~DD 红带
  const shortLower = mask(d.KK, d.POS, -1), shortUpper = mask(d.PP, d.POS, -1);  // 持空：KK~PP 绿带

  chart.setOption({
    backgroundColor: '#0f1420',
    animation: false,
    legend: {data: ['多头趋势带', '空头趋势带', '通道DD(开多)', '通道EE(平多)', '通道KK(开空)', '通道PP(平空)', '七日中点', 'NN/GG强弱数字'],
             textStyle: {color: '#aab4cc'}, top: 4, selected: {'NN/GG强弱数字': false}},
    tooltip: {trigger: 'axis', axisPointer: {type: 'cross'},
      backgroundColor: '#1a2338', borderColor: '#35436b', textStyle: {color: '#dfe6f2'},
      formatter: ps => {
        const i = ps[0].dataIndex, o = d.ohlc[i];
        let h = `<b>${d.dates[i]}</b><br>开 ${o[0]} 收 ${o[1]} 低 ${o[2]} 高 ${o[3]}<br>` +
                `量 ${(d.volume[i]/1e4).toFixed(1)}万 持仓 ${(d.opi[i]/1e4).toFixed(1)}万<br>`;
        const s = d.signals.find(x => x.i === i);
        if (s) h += `<b style="color:${sigStyle[s.type].c}">信号：${s.type}</b><br>`;
        if (d.PQ[i]) h += '<span style="color:#ff5252">强于南华指数</span><br>';
        if (d.PR[i]) h += '<span style="color:#2979ff">弱于南华指数</span><br>';
        if (d.SB[i]) h += '<span>🤭 SB 多头增仓</span><br>';
        if (d.DSB[i]) h += '<span>😓 DSB 洗盘后站起</span><br>';
        if (d.DSBE[i]) h += '<span>👎 DSBE 反击扑灭</span><br>';
        h += `周线许可: 空${d.AA1[i] ? '✓' : '✗'} 多${d.ZZ1[i] ? '✓' : '✗'} 非盘整${d.TT1[i] ? '✓' : '✗'}`;
        return h;
      }},
    axisPointer: {link: [{xAxisIndex: 'all'}]},
    grid: [{left: 60, right: 20, top: 36, height: '58%'},
           {left: 60, right: 20, top: '70%', height: '24%'}],
    xAxis: [{type: 'category', data: d.dates, gridIndex: 0, axisLine: {lineStyle: {color: '#35436b'}}},
            {type: 'category', data: d.dates, gridIndex: 1, axisLine: {lineStyle: {color: '#35436b'}}}],
    yAxis: [{scale: true, gridIndex: 0, splitLine: {lineStyle: {color: '#1c2537'}},
             axisLine: {lineStyle: {color: '#35436b'}}},
            {scale: true, gridIndex: 1, splitLine: {lineStyle: {color: '#1c2537'}},
             axisLine: {lineStyle: {color: '#35436b'}}}],
    dataZoom: [{type: 'inside', xAxisIndex: [0, 1]}, {type: 'slider', xAxisIndex: [0, 1], bottom: 4,
                backgroundColor: '#161d2e', fillerColor: 'rgba(60,80,140,.3)', textStyle: {color: '#8b97b5'}}],
    series: [
      {name: 'K线', type: 'candlestick', data: kdata, xAxisIndex: 0, yAxisIndex: 0,
       markPoint: {data: sigPts, symbol: 'pin', symbolSize: 1, label: {show: false}}},
      {name: 'NN/GG强弱数字', type: 'scatter', data: [], xAxisIndex: 0, yAxisIndex: 0,
       markPoint: {data: nnPts.concat(ggPts), symbol: 'circle', symbolSize: 1}},
      ...band(longLower,  longUpper,  '#e54545', '多头趋势带'),
      ...band(shortLower, shortUpper, '#2fa36b', '空头趋势带'),
      chan(mask(d.DD, d.POS, 1),  '#ff5566', '通道DD(开多)'),
      chan(mask(d.EE, d.POS, 1),  '#ff8899', '通道EE(平多)'),
      chan(mask(d.KK, d.POS, -1), '#33dd99', '通道KK(开空)'),
      chan(mask(d.PP, d.POS, -1), '#88eebb', '通道PP(平空)'),
      {name: '七日中点', type: 'line', data: d.ZD, showSymbol: false,
       lineStyle: {width: 1, color: '#5c6b8a', opacity: .7}, xAxisIndex: 0, yAxisIndex: 0},
      {name: '成交量', type: 'bar', data: d.volume.map((v, i) => ({
          value: v, itemStyle: {color: d.ohlc[i][1] >= d.ohlc[i][0] ? up : down}})),
       xAxisIndex: 1, yAxisIndex: 1},
      {name: '持仓量', type: 'line', data: d.opi, showSymbol: false,
       lineStyle: {width: 1.5, color: '#e8c45a'}, xAxisIndex: 1, yAxisIndex: 1},
    ]
  }, true);
}

// 支持 URL 锚点选品种：dashboard.html#m8888
const hashKey = decodeURIComponent(location.hash.slice(1));
if (hashKey && keys.includes(hashKey)) {
  document.querySelectorAll('#bar button').forEach(x => x.classList.remove('active'));
  document.querySelectorAll('#bar button')[keys.indexOf(hashKey)].classList.add('active');
  render(hashKey);
} else {
  render(keys[0]);
}
window.addEventListener('resize', () => chart.resize());
</script>
</body>
</html>
"""

payload = json.dumps(datasets, ensure_ascii=False)
html = HTML.replace("__DATA__", payload)
# tooltip 需要周线许可字段，从 JSON 补充（若策略脚本未导出则用空数组兜底）
for k, d in datasets.items():
    n = len(d["dates"])
    for f_ in ("AA1", "ZZ1", "TT1"):
        d.setdefault(f_, [None] * n)
payload = json.dumps(datasets, ensure_ascii=False)
html = HTML.replace("__DATA__", payload)

OUT_HTML.parent.mkdir(parents=True, exist_ok=True)
with open(OUT_HTML, "w", encoding="utf-8") as f:
    f.write(html)
print(f"看板已生成: {OUT_HTML}（{len(datasets)} 个品种）")
