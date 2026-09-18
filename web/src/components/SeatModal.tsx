import { useEffect, useState } from 'react'
import { fetchSeatAnalysis, fetchSeatList } from '../api'
import type { SeatMeta } from '../types'

interface Props {
  open: boolean
  onClose: () => void
}

/** YYYYMMDD → YYYY-MM-DD */
function fmtDate(d: string): string {
  return d.length === 8 ? `${d.slice(0, 4)}-${d.slice(4, 6)}-${d.slice(6)}` : d
}

/** 席位解读 Markdown 的轻量渲染：仅处理一级标题与分段，其余按纯文本转义展示。 */
function AnalysisBlock({ markdown }: { markdown: string }) {
  const blocks = markdown.split(/\n{2,}/).map((b) => b.trim()).filter(Boolean)
  return <div className="seat-analysis">
    {blocks.map((b, i) => b.startsWith('# ')
      ? <h3 key={i}>{b.slice(2)}</h3>
      : <p key={i}>{b}</p>)}
  </div>
}

/**
 * 席位追踪弹窗：左侧归档列表，右侧为当日 AI 解读 + 席位方向图
 * （产物由 backend.pipeline.seat_daily 生成，/api/seat/* 提供）。
 */
export default function SeatModal({ open, onClose }: Props) {
  const [items, setItems] = useState<SeatMeta[]>([])
  const [error, setError] = useState<string | null>(null)
  const [active, setActive] = useState<string | null>(null)
  const [analysis, setAnalysis] = useState<string | null>(null)
  const [analysisErr, setAnalysisErr] = useState<string | null>(null)

  useEffect(() => {
    if (!open) return
    setError(null)
    fetchSeatList()
      .then((list) => {
        setItems(list)
        setActive((cur) => (cur && list.some((r) => r.date === cur && r.has_image)
          ? cur
          : list.find((r) => r.has_image)?.date ?? null))
      })
      .catch((e: Error) => setError(e.message))
  }, [open])

  useEffect(() => {
    if (!open || !active) return
    setAnalysis(null)
    setAnalysisErr(null)
    fetchSeatAnalysis(active)
      .then((r) => setAnalysis(r.markdown))
      .catch((e: Error) => setAnalysisErr(e.message))
  }, [open, active])

  useEffect(() => {
    if (!open) return
    const onKeyDown = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [open, onClose])

  if (!open) return null

  const activeMeta = items.find((r) => r.date === active) ?? null

  return <div className="modal-mask" onClick={onClose}>
    <div className="modal-panel report-panel" onClick={(event) => event.stopPropagation()}>
      <button className="modal-close" onClick={onClose}>✕ 关闭</button>
      <h2 className="report-title">🪑 席位追踪</h2>
      <div className="report-body">
        <aside className="report-list">
          {error && <div className="status-msg">列表加载失败:{error}</div>}
          {!error && items.length === 0 && <div className="status-msg">暂无归档席位数据</div>}
          {items.map((r) => (
            <button key={r.date}
              className={`report-item${r.date === active ? ' active' : ''}`}
              disabled={!r.has_image}
              title={r.has_image ? '' : '该日暂无方向图产物'}
              onClick={() => setActive(r.date)}>
              <div className="report-item-date">{fmtDate(r.date)}{r.date === items[0]?.date && <span className="report-tag">最新</span>}</div>
              <div className="report-item-sub">
                {r.has_analysis ? '图 + AI 解读' : '仅方向图'}
              </div>
            </button>
          ))}
        </aside>
        <section className="report-viewer seat-viewer">
          {activeMeta
            ? <div className="seat-scroll">
                {analysis && <AnalysisBlock markdown={analysis} />}
                {analysisErr && activeMeta.has_analysis && <div className="status-msg">解读加载失败:{analysisErr}</div>}
                <img key={activeMeta.date} src={`/api/seat/${activeMeta.date}/image`}
                  alt={`席位方向图 ${fmtDate(activeMeta.date)}`} />
              </div>
            : <div className="status-msg">{items.length ? '请选择左侧日期' : '席位数据生成后在此展示'}</div>}
        </section>
      </div>
    </div>
  </div>
}
