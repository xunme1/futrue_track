/**
 * ECharts option 构造 —— 移植自 tools/make_dashboard.py 内嵌模板的 render(key)。
 * 图形逻辑保持一致：三色K线、信号 markPoint、持仓趋势带(堆叠面积 hack)、
 * 通道虚线、双 grid 联动、自定义 tooltip。
 * 差异：默认只显示最后 DEFAULT_BARS(60) 根，inside 支持滚轮缩放与拖动平移。
 */
import type { EChartsOption } from 'echarts'
import type { SignalData } from '../types'

/** 页面默认展示的 K 线根数 */
export const DEFAULT_BARS = 60

const UP = '#d43f3f'
const DOWN = '#3a9e6f'
const PQ_COLOR = '#ff5252'
const PR_COLOR = '#2979ff'

type SigType = 'BK' | 'SK' | 'SP' | 'BP'
const SIG_STYLE: Record<SigType, { c: string; t: string; pos: 'top' | 'bottom' }> = {
  BK: { c: '#ff3355', t: '▲BK', pos: 'bottom' },
  SK: { c: '#22cc88', t: '▼SK', pos: 'top' },
  SP: { c: '#ffaa00', t: '△SP', pos: 'top' },
  BP: { c: '#ffaa00', t: '▽BP', pos: 'bottom' },
}

const FUNDING_EMOJI = {
  SB: '🤭',
  DSB: '🤔',
  DSBE: '👎',
} as const

/**
 * ECharts 使用 Canvas 渲染时，Unicode emoji 文本在部分 Windows 字体回退链中不可见。
 * 将 emoji 包装为 SVG 图片符号，保证它作为图像而非 Canvas 文本渲染。
 */
function emojiSymbol(emoji: string): string {
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="28" height="28" viewBox="0 0 28 28"><text x="14" y="23" text-anchor="middle" font-size="24" font-family="Segoe UI Emoji, Apple Color Emoji, Noto Color Emoji">${emoji}</text></svg>`
  return `image://data:image/svg+xml;charset=UTF-8,${encodeURIComponent(svg)}`
}

/** 只在持仓方向 want（1多/-1空）期间保留通道线值 */
function mask(arr: (number | null)[], posArr: number[], want: number): (number | null)[] {
  return arr.map((v, i) => (posArr[i] === want ? v : null))
}

export function buildOption(d: SignalData): EChartsOption {
  const isFourHour = d.timeframe === '4h'
  // 日线：PQ红(强) / PR蓝(弱)；4小时：上涨红、下跌蓝、平盘灰。
  const kdata = d.ohlc.map((v, i) => {
    let color: string, color0: string
    if (isFourHour && d.bar_colors?.[i] === 'red') { color = PQ_COLOR; color0 = PQ_COLOR }
    else if (isFourHour && d.bar_colors?.[i] === 'blue') { color = PR_COLOR; color0 = PR_COLOR }
    else if (!isFourHour && d.PQ?.[i]) { color = PQ_COLOR; color0 = PQ_COLOR }
    else if (!isFourHour && d.PR?.[i]) { color = PR_COLOR; color0 = PR_COLOR }
    else { color = '#8b95ab'; color0 = '#454e63' }
    return { value: v, itemStyle: { color, color0, borderColor: color, borderColor0: color0 } }
  })

  // 交易信号标注（BK/SK/SP/BP）
  const sigPts: Record<string, unknown>[] = d.signals.map((s) => {
    const st = SIG_STYLE[s.type]
    return {
      coord: [s.i, s.price],
      value: st.t,
      itemStyle: { color: st.c },
      label: { show: true, formatter: st.t, color: st.c, fontWeight: 'bold', fontSize: 13, position: st.pos, distance: 6 },
    }
  })
  // 资金信号：以 emoji 标记，避免和交易信号箭头混淆。
  d.SB.forEach((v, i) => {
    if (v) sigPts.push({
      coord: [i, d.ohlc[i][2] * 0.995], value: 'SB', symbol: emojiSymbol(FUNDING_EMOJI.SB), symbolSize: 24,
      label: { show: false },
    })
  })
  d.DSB.forEach((v, i) => {
    if (v) sigPts.push({
      coord: [i, d.ohlc[i][2] * 0.99], value: 'DSB', symbol: emojiSymbol(FUNDING_EMOJI.DSB), symbolSize: 24,
      label: { show: false },
    })
  })
  d.DSBE.forEach((v, i) => {
    if (v) sigPts.push({
      coord: [i, d.ohlc[i][3] * 1.005], value: 'DSBE', symbol: emojiSymbol(FUNDING_EMOJI.DSBE), symbolSize: 24,
      label: { show: false },
    })
  })

  // NN/GG 强弱数字（默认隐藏，图例可开）
  const nnPts: Record<string, unknown>[] = []
  const ggPts: Record<string, unknown>[] = []
  ;(d.NN ?? []).forEach((v, i) => {
    if (v !== null && v !== 0) nnPts.push({
      coord: [i, d.ohlc[i][3] * 1.01], value: v, symbolSize: 1,
      label: { show: true, formatter: String(v), color: '#ff6b6b', fontSize: 9, position: 'top' },
    })
  })
  ;(d.GG ?? []).forEach((v, i) => {
    if (v !== null && v !== 0) ggPts.push({
      coord: [i, d.ohlc[i][2] * 0.99], value: v, symbolSize: 1,
      label: { show: true, formatter: String(v), color: '#51cf66', fontSize: 9, position: 'bottom' },
    })
  })

  // 持仓通道虚线
  const chan = (arr: (number | null)[], color: string, name: string) => ({
    name, type: 'line' as const, data: arr, connectNulls: false, showSymbol: false,
    lineStyle: { type: 'dashed' as const, width: 1.2, color },
    xAxisIndex: 0, yAxisIndex: 0, z: 3,
  })

  // 持仓趋势带（复刻麦语言 FILLRGN）：堆叠面积 hack
  // base=下线(隐形) + diff=上线-下线(带 areaStyle)，只在持仓期间有值
  const band = (lowerArr: (number | null)[], upperArr: (number | null)[], color: string, name: string) => {
    const diff = lowerArr.map((v, i) =>
      v !== null && upperArr[i] !== null ? +((upperArr[i] as number) - v).toFixed(4) : null)
    return [
      {
        name: name + '_base', type: 'line' as const, data: lowerArr, stack: name,
        showSymbol: false, lineStyle: { opacity: 0 }, areaStyle: { opacity: 0 },
        silent: true, connectNulls: false, emphasis: { disabled: true },
        tooltip: { show: false }, xAxisIndex: 0, yAxisIndex: 0, z: 1,
      },
      {
        name, type: 'line' as const, data: diff, stack: name, showSymbol: false,
        connectNulls: false, lineStyle: { opacity: 0 }, areaStyle: { color, opacity: 0.32 },
        silent: true, emphasis: { disabled: true }, tooltip: { show: false },
        xAxisIndex: 0, yAxisIndex: 0, z: 1,
      },
    ]
  }
  const longLower = mask(d.EE, d.POS, 1)
  const longUpper = mask(d.DD, d.POS, 1)     // 持多：EE~DD 红带
  const shortLower = mask(d.KK, d.POS, -1)
  const shortUpper = mask(d.PP, d.POS, -1)   // 持空：KK~PP 绿带

  // 默认显示最后 DEFAULT_BARS 根 K 线
  const n = d.dates.length
  const start = n > DEFAULT_BARS ? Math.max(0, 100 - (DEFAULT_BARS / n) * 100) : 0

  return {
    backgroundColor: '#0f1420',
    animation: false,
    legend: {
      data: ['多头趋势带', '空头趋势带', '通道DD(开多)', '通道EE(平多)', '通道KK(开空)', '通道PP(平空)', '七日中点', ...(!isFourHour ? ['NN/GG强弱数字'] : [])],
      textStyle: { color: '#aab4cc' }, top: 4,
      selected: { 'NN/GG强弱数字': false },
    },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross' },
      backgroundColor: '#1a2338', borderColor: '#35436b',
      textStyle: { color: '#dfe6f2' },
      formatter: (ps) => {
        const list = ps as { dataIndex: number }[]
        const i = list[0].dataIndex
        const o = d.ohlc[i]
        let h = `<b>${d.dates[i]}</b><br>开 ${o[0]} 收 ${o[1]} 低 ${o[2]} 高 ${o[3]}<br>` +
          `量 ${(d.volume[i] / 1e4).toFixed(1)}万 持仓 ${(d.opi[i] / 1e4).toFixed(1)}万<br>`
        const s = d.signals.find((x) => x.i === i)
        if (s) h += `<b style="color:${SIG_STYLE[s.type].c}">信号：${s.type}</b><br>`
        if (isFourHour) h += `<span style="color:${d.bar_colors?.[i] === 'red' ? '#ff5252' : d.bar_colors?.[i] === 'blue' ? '#2979ff' : '#8b95ab'}">${d.bar_colors?.[i] === 'red' ? '上涨红K' : d.bar_colors?.[i] === 'blue' ? '下跌蓝K' : '平盘灰K'}</span><br>`
        else if (d.PQ?.[i]) h += '<span style="color:#ff5252">强于南华指数</span><br>'
        else if (d.PR?.[i]) h += '<span style="color:#2979ff">弱于南华指数</span><br>'
        if (d.SB[i]) h += `<span>🤭 SB 多头增仓</span><br>`
        if (d.DSB[i]) h += `<span>😓 DSB 洗盘后站起</span><br>`
        if (d.DSBE[i]) {
          h += '<span>👎 DSBE 反击扑灭</span>'
          const note = d.DSBE_NOTE?.[i]
          if (note) h += `<span>（${note}）</span>`
          h += '<br>'
        }
        h += `${isFourHour ? '日线' : '周线'}许可: 空${d.AA1[i] ? '✓' : '✗'} 多${d.ZZ1[i] ? '✓' : '✗'} 非盘整${d.TT1[i] ? '✓' : '✗'}`
        return h
      },
    },
    axisPointer: { link: [{ xAxisIndex: 'all' }] },
    grid: [
      { left: 60, right: 20, top: 36, height: '58%' },
      { left: 60, right: 20, top: '70%', height: '24%' },
    ],
    xAxis: [
      { type: 'category', data: d.dates, gridIndex: 0, axisLine: { lineStyle: { color: '#35436b' } } },
      { type: 'category', data: d.dates, gridIndex: 1, axisLine: { lineStyle: { color: '#35436b' } } },
    ],
    yAxis: [
      { scale: true, gridIndex: 0, splitLine: { lineStyle: { color: '#1c2537' } }, axisLine: { lineStyle: { color: '#35436b' } } },
      { scale: true, gridIndex: 1, splitLine: { lineStyle: { color: '#1c2537' } }, axisLine: { lineStyle: { color: '#35436b' } } },
    ],
    dataZoom: [
      // inside：滚轮缩放、按住拖动平移（左右滑动）
      {
        type: 'inside', xAxisIndex: [0, 1], start, end: 100,
        zoomOnMouseWheel: true, moveOnMouseMove: true, moveOnMouseWheel: false,
      },
      {
        type: 'slider', xAxisIndex: [0, 1], start, end: 100, bottom: 4,
        backgroundColor: '#161d2e', fillerColor: 'rgba(60,80,140,.3)',
        textStyle: { color: '#8b97b5' },
      },
    ],
    series: [
      {
        name: 'K线', type: 'candlestick', data: kdata, xAxisIndex: 0, yAxisIndex: 0,
        markPoint: { data: sigPts as never[], symbol: 'pin', symbolSize: 1, label: { show: false } },
      },
      {
        name: 'NN/GG强弱数字', type: 'scatter', data: [], xAxisIndex: 0, yAxisIndex: 0,
        markPoint: { data: nnPts.concat(ggPts) as never[], symbol: 'circle', symbolSize: 1 },
      },
      ...band(longLower, longUpper, '#e54545', '多头趋势带'),
      ...band(shortLower, shortUpper, '#2fa36b', '空头趋势带'),
      chan(mask(d.DD, d.POS, 1), '#ff5566', '通道DD(开多)'),
      chan(mask(d.EE, d.POS, 1), '#ff8899', '通道EE(平多)'),
      chan(mask(d.KK, d.POS, -1), '#33dd99', '通道KK(开空)'),
      chan(mask(d.PP, d.POS, -1), '#88eebb', '通道PP(平空)'),
      {
        name: '七日中点', type: 'line', data: d.ZD, showSymbol: false,
        lineStyle: { width: 1, color: '#5c6b8a', opacity: 0.7 }, xAxisIndex: 0, yAxisIndex: 0,
      },
      {
        name: '成交量', type: 'bar',
        data: d.volume.map((v, i) => ({
          value: v,
          itemStyle: { color: d.ohlc[i][1] >= d.ohlc[i][0] ? UP : DOWN },
        })),
        xAxisIndex: 1, yAxisIndex: 1,
      },
      {
        name: '持仓量', type: 'line', data: d.opi, showSymbol: false,
        lineStyle: { width: 1.5, color: '#e8c45a' }, xAxisIndex: 1, yAxisIndex: 1,
      },
    ],
  }
}
