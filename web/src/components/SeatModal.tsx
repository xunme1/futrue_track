import { useEffect, useState } from 'react'
import { fetchSeatAnalysis, fetchSeatList } from '../api'
import { latestSeatDate, refreshedSeatDate, seatHtmlUrl } from '../seatReports'
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
 * 正式席位观察室：按日期展示归档，新版与旧版 HTML 使用同一入口。
 * 更早的纯图片归档继续回退 AI 解读 + 方向图。
 */
export default function SeatModal({ open, onClose }: Props) {
  const [items, setItems] = useState<SeatMeta[]>([])
  const [error, setError] = useState<string | null>(null)
  const [active, setActive] = useState<string | null>(null)
  const [analysis, setAnalysis] = useState<string | null>(null)
  const [analysisErr, setAnalysisErr] = useState<string | null>(null)
  const [archiveOpen, setArchiveOpen] = useState(false)
  const activeHasHtml = items.find((item) => item.date === active)?.has_html

  useEffect(() => {
    if (!open) return
    let cancelled = false
    let previousLatest: string | null = null
    let inFlight = false
    let controller: AbortController | null = null
    setError(null)
    const refresh = async () => {
      if (cancelled || inFlight || document.visibilityState === 'hidden') return
      inFlight = true
      controller = new AbortController()
      try {
        const list = await fetchSeatList(controller.signal)
        if (cancelled) return
        const priorLatest = previousLatest
        previousLatest = latestSeatDate(list)
        setItems(list)
        setActive((current) => refreshedSeatDate(current, list, priorLatest))
        setError(null)
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : '更新失败')
      } finally {
        inFlight = false
      }
    }
    void refresh()
    const timer = window.setInterval(() => { void refresh() }, 60_000)
    window.addEventListener('focus', refresh)
    document.addEventListener('visibilitychange', refresh)
    return () => {
      cancelled = true
      controller?.abort()
      window.clearInterval(timer)
      window.removeEventListener('focus', refresh)
      document.removeEventListener('visibilitychange', refresh)
    }
  }, [open])

  useEffect(() => {
    if (!open || !active) return
    setAnalysis(null)
    setAnalysisErr(null)
    if (activeHasHtml) return
    let cancelled = false
    fetchSeatAnalysis(active)
      .then((r) => { if (!cancelled) setAnalysis(r.markdown) })
      .catch((e: Error) => { if (!cancelled) setAnalysisErr(e.message) })
    return () => { cancelled = true }
  }, [open, active, activeHasHtml])

  useEffect(() => {
    if (!open) return
    const onKeyDown = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [open, onClose])

  if (!open) return null

  const activeMeta = items.find((r) => r.date === active) ?? null
  const htmlUrl = activeMeta?.has_html ? seatHtmlUrl(activeMeta) : undefined

  const downloadAnalysis = () => {
    if (!active || !analysis) return
    const blob = new Blob([analysis], { type: 'text/markdown;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `seat_analysis_${active}.md`
    a.click()
    URL.revokeObjectURL(url)
  }

  return <div className="modal-mask seat-modal-mask" onClick={onClose}>
    <div className="modal-panel report-panel seat-panel" onClick={(event) => event.stopPropagation()}>
      <button className="modal-close" onClick={onClose}>✕ 关闭</button>
      {activeMeta?.has_html ? <a className="modal-download"
        href={htmlUrl}
        download={`seat_report_${activeMeta.date}.html`}>⬇️ HTML 日报</a>
        : activeMeta?.has_image && <a className="modal-download"
        href={`/api/seat/${activeMeta.date}/image`}
        download={`seat_direction_${activeMeta.date}.png`}>⬇️ 方向图</a>}
      {!activeMeta?.has_html && activeMeta?.has_analysis && analysis && <button
        className="modal-download modal-download-2"
        onClick={downloadAnalysis}>⬇️ 解读</button>}
      <div className="seat-heading">
        <h2 className="report-title">🪑 席位观察室</h2>
        <select aria-label="席位日报日期" value={active ?? ''}
          onChange={(event) => setActive(event.target.value)}>
          {!active && <option value="">选择日期</option>}
          {items.map((item) => <option key={item.date} value={item.date}
            disabled={!item.has_html && !item.has_image}>
            {fmtDate(item.date)}{item.date === items[0]?.date ? ' · 最新' : ''}
          </option>)}
        </select>
        <button type="button" aria-expanded={archiveOpen} aria-controls="seat-archive-list"
          onClick={() => setArchiveOpen((value) => !value)}>
          {archiveOpen ? '收起归档' : '历史归档'}
        </button>
        {activeMeta?.has_html && <a className="seat-open-page"
          href={htmlUrl} target="_blank" rel="noopener noreferrer">
          独立打开 ↗
        </a>}
      </div>
      {error && <div className="status-msg">列表加载失败:{error}</div>}
      <div className="report-body">
        <aside className="report-list" id="seat-archive-list" hidden={!archiveOpen}>
          {!error && items.length === 0 && <div className="status-msg">暂无归档席位数据</div>}
          {items.map((r) => (
            <button key={r.date}
              className={`report-item${r.date === active ? ' active' : ''}`}
              disabled={!r.has_html && !r.has_image}
              title={r.has_html || r.has_image ? '' : '该日暂无可查看产物'}
              onClick={() => setActive(r.date)}>
              <div className="report-item-date">{fmtDate(r.date)}{r.date === items[0]?.date && <span className="report-tag">最新</span>}</div>
              <div className="report-item-sub">
                {r.has_html ? '交互 HTML 日报' : (r.has_analysis ? '图 + AI 解读' : '仅方向图')}
              </div>
              {r.summary && <div className="report-item-liner">{r.summary}</div>}
            </button>
          ))}
        </aside>
        <section className="report-viewer seat-viewer">
          {activeMeta?.has_html
            ? <iframe key={htmlUrl} src={htmlUrl}
                title={`席位持仓日报 ${fmtDate(activeMeta.date)}`} />
            : activeMeta
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
