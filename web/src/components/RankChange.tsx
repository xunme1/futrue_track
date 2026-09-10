import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import * as echarts from 'echarts/core'
import { LineChart } from 'echarts/charts'
import { GridComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import type { ScreeningItem } from '../types'

echarts.use([LineChart, GridComponent, TooltipComponent, CanvasRenderer])

export function rankChangeText(item: ScreeningItem): string {
  switch (item.rank_status) {
    case 'up': return `↑${Math.abs(item.rank_change ?? 0)}`
    case 'down': return `↓${Math.abs(item.rank_change ?? 0)}`
    case 'flat': return '→0'
    case 'new': return '新入榜'
    default: return '—'
  }
}

/** Mounted only while the popover is open; never creates a chart per hidden row. */
function RankChart({ item }: { item: ScreeningItem }) {
  const element = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!element.current) return
    const points = item.rank_history ?? []
    const chart = echarts.init(element.current)
    const largestRank = Math.max(2, ...points.map((point) => point.rank ?? 1))
    chart.setOption({
      animation: false,
      grid: { left: 35, right: 20, top: 26, bottom: 28 },
      xAxis: {
        type: 'category', data: points.map((point) => point.date), boundaryGap: true,
        axisLabel: { color: '#9eacc8', fontSize: 10, interval: 0, formatter: (date: string) => date.slice(5) },
        axisTick: { show: false }, axisLine: { lineStyle: { color: '#35436b' } },
      },
      yAxis: {
        type: 'value', inverse: true, min: 1, max: largestRank, minInterval: 1,
        axisLabel: { color: '#9eacc8', fontSize: 10 },
        splitLine: { lineStyle: { color: '#28354b' } },
      },
      tooltip: {
        trigger: 'axis', confine: true, renderMode: 'richText',
        backgroundColor: '#111a2b', borderColor: '#536a98', textStyle: { color: '#e8eefc', fontSize: 11 },
        formatter: (params: { dataIndex: number }[]) => {
          const point = points[params[0]?.dataIndex]
          return point ? `${point.date}\n${point.rank === null ? '无可用排名' : `第 ${point.rank} 名／当日共 ${point.total} 个`}` : ''
        },
      },
      series: [{
        type: 'line', data: points.map((point) => point.rank), connectNulls: false,
        symbol: 'circle', symbolSize: 7, lineStyle: { width: 2, color: '#8caeff' },
        itemStyle: { color: '#8caeff' }, label: { show: true, position: 'top', color: '#e8eefc', fontSize: 11 },
      }],
    })
    const observer = new ResizeObserver(() => chart.resize())
    observer.observe(element.current)
    return () => { observer.disconnect(); chart.dispose() }
  }, [item])
  return <div className="rank-history-chart" ref={element} aria-hidden="true" />
}

export default function RankChange({ item, direction }: { item: ScreeningItem; direction: string }) {
  const [open, setOpen] = useState(false)
  const [pinned, setPinned] = useState(false)
  const [position, setPosition] = useState({ left: 8, top: 8 })
  const trigger = useRef<HTMLButtonElement>(null)
  const panel = useRef<HTMLDivElement>(null)
  const closeTimer = useRef<ReturnType<typeof setTimeout>>()
  const id = useId()
  const text = rankChangeText(item)
  const changeDescription = item.rank_status === 'up' ? `上升 ${item.rank_change} 名`
    : item.rank_status === 'down' ? `下降 ${Math.abs(item.rank_change ?? 0)} 名`
      : item.rank_status === 'flat' ? '排名持平'
        : item.rank_status === 'new' ? '新入榜' : '暂无可比排名'
  const cancelClose = () => clearTimeout(closeTimer.current)
  const scheduleClose = () => {
    cancelClose()
    if (!pinned) closeTimer.current = setTimeout(() => setOpen(false), 180)
  }

  useEffect(() => () => clearTimeout(closeTimer.current), [])
  useLayoutEffect(() => {
    if (!open) return
    const place = () => {
      if (!trigger.current || !panel.current) return
      const anchor = trigger.current.getBoundingClientRect()
      const { width, height } = panel.current.getBoundingClientRect()
      const left = Math.max(8, Math.min(anchor.left, window.innerWidth - width - 8))
      const below = anchor.bottom + 8
      const top = Math.max(8, Math.min(below + height <= window.innerHeight - 8 ? below : anchor.top - height - 8,
        window.innerHeight - height - 8))
      setPosition({ left, top })
    }
    place()
    window.addEventListener('resize', place)
    return () => window.removeEventListener('resize', place)
  }, [open])

  useEffect(() => {
    if (!open) return
    const close = () => { clearTimeout(closeTimer.current); setOpen(false); setPinned(false) }
    const outside = (event: PointerEvent) => {
      const target = event.target as Node
      if (!trigger.current?.contains(target) && !panel.current?.contains(target)) close()
    }
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') close() }
    const scroll = (event: Event) => { if (!panel.current?.contains(event.target as Node)) close() }
    document.addEventListener('pointerdown', outside)
    document.addEventListener('keydown', escape)
    document.addEventListener('scroll', scroll, true)
    return () => {
      document.removeEventListener('pointerdown', outside)
      document.removeEventListener('keydown', escape)
      document.removeEventListener('scroll', scroll, true)
    }
  }, [open])

  return <>
    <button ref={trigger} type="button" className={`rank-change rank-change-${item.rank_status}`}
      aria-label={`${item.name}：${changeDescription}，查看近7根K线排名`}
      aria-describedby={open ? id : undefined} aria-expanded={open}
      onPointerEnter={(event) => { if (event.pointerType === 'mouse') { cancelClose(); setOpen(true) } }}
      onPointerLeave={scheduleClose} onFocus={() => { cancelClose(); setOpen(true) }}
      onBlur={(event) => {
        // A tap inside the canvas has no focusable relatedTarget. Keep a
        // click-pinned chart open; outside pointerdown still dismisses it.
        if (pinned && event.relatedTarget === null) return
        if (!panel.current?.contains(event.relatedTarget)) {
          cancelClose(); setOpen(false); setPinned(false)
        }
      }}
      onClick={(event) => { event.stopPropagation(); cancelClose(); setPinned(!pinned); setOpen(!pinned) }}
    >{text}</button>
    {open && createPortal(<div ref={panel} id={id} role="tooltip" className="rank-history-popover" style={position}
      onClick={(event) => event.stopPropagation()} onPointerEnter={cancelClose} onPointerLeave={scheduleClose}>
      <strong>{item.name} · {direction}</strong>
      <p>当前第 {item.rank} 名 · 上一根{item.previous_rank == null ? ' —' : `第 ${item.previous_rank} 名`}</p>
      <p className={`rank-change-${item.rank_status}`}>{changeDescription}</p>
      <RankChart item={item} />
      <small>最近 7 根 K 线 · 本次连续在榜段 · 第 1 名在上方</small>
      <span className="sr-only">{item.rank_history?.map((point) => `${point.date}：${point.rank === null ? '无可用排名' : `第${point.rank}名，共${point.total}个`}`).join('；')}</span>
    </div>, document.body)}
  </>
}
