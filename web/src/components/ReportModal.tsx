import { useEffect, useRef, useState } from 'react'
import { fetchReports } from '../api'
import type { ReportMeta } from '../types'

interface Props {
  open: boolean
  onClose: () => void
}

/**
 * 每日日报弹窗：左侧归档列表，右侧 iframe 渲染日报 HTML
 * （HTML 由 backend.pipeline.report_render 生成，/api/reports/{date}/html 提供）。
 */
export default function ReportModal({ open, onClose }: Props) {
  const [reports, setReports] = useState<ReportMeta[]>([])
  const [error, setError] = useState<string | null>(null)
  const [active, setActive] = useState<string | null>(null)
  const [archiveOpen, setArchiveOpen] = useState(false)
  const closeButton = useRef<HTMLButtonElement>(null)
  const frame = useRef<HTMLIFrameElement>(null)

  useEffect(() => {
    if (!open) return
    setError(null)
    fetchReports()
      .then((list) => {
        setReports(list)
        setActive((cur) => (cur && list.some((r) => r.date === cur && r.has_html)
          ? cur
          : list.find((r) => r.has_html)?.date ?? null))
      })
      .catch((e: Error) => setError(e.message))
  }, [open])

  useEffect(() => {
    if (!open) return
    const onKeyDown = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose() }
    const onMessage = (event: MessageEvent) => {
      if (event.origin === window.location.origin && event.source === frame.current?.contentWindow && event.data?.type === 'close-report') onClose()
    }
    const priorFocus = document.activeElement as HTMLElement | null
    const oldOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    closeButton.current?.focus()
    window.addEventListener('keydown', onKeyDown)
    window.addEventListener('message', onMessage)
    return () => {
      window.removeEventListener('keydown', onKeyDown)
      window.removeEventListener('message', onMessage)
      document.body.style.overflow = oldOverflow
      priorFocus?.focus()
    }
  }, [open, onClose])

  if (!open) return null

  const activeMeta = reports.find((r) => r.date === active) ?? null

  return <div className="modal-mask report-mask" onClick={onClose}>
    <div className="modal-panel report-panel" role="dialog" aria-modal="true" aria-labelledby="report-title" onClick={(event) => event.stopPropagation()}>
      <header className="report-toolbar">
        <h2 id="report-title" className="report-title">盘后日报</h2>
        <label className="report-date-picker">报告日
          <select aria-label="选择报告日期" value={active ?? ''} onChange={(event) => setActive(event.target.value)}>
            {reports.length === 0 && <option value="">暂无日报</option>}
            {reports.map(r => <option key={r.date} value={r.date} disabled={!r.has_html}>{r.date}</option>)}
          </select>
        </label>
        {activeMeta?.data_date && <span className="report-data-date">数据 {activeMeta.data_date}</span>}
        <div className="report-toolbar-actions">
          <button onClick={() => setArchiveOpen(!archiveOpen)} aria-expanded={archiveOpen} aria-controls="report-archive">{archiveOpen ? '收起归档' : '历史归档'}</button>
          {activeMeta && <a href={`/api/reports/${activeMeta.date}/html`} target="_blank" rel="noreferrer">独立阅读 ↗</a>}
          <button ref={closeButton} onClick={onClose} aria-label="关闭日报">✕ 关闭</button>
        </div>
      </header>
      {error && <div className="status-msg" role="alert">日报列表加载失败：{error}</div>}
      <div className="report-body">
        {archiveOpen && <aside className="report-list" id="report-archive">
          {!error && reports.length === 0 && <div className="status-msg">暂无归档日报</div>}
          {reports.map((r) => (
            <button key={r.date}
              className={`report-item${r.date === active ? ' active' : ''}`}
              disabled={!r.has_html}
              title={r.has_html ? '' : '该日暂无 HTML 产物'}
              onClick={() => { setActive(r.date); setArchiveOpen(false) }}>
              <div className="report-item-date">{r.date}{r.date === reports[0]?.date && <span className="report-tag">最新</span>}</div>
              {r.data_date && <div className="report-item-sub">数据基准 {r.data_date}</div>}
              {r.one_liner && <div className="report-item-liner">{r.one_liner}</div>}
            </button>
          ))}
        </aside>}
        <section className="report-viewer">
          {activeMeta
            ? <iframe ref={frame} key={activeMeta.date} src={`/api/reports/${activeMeta.date}/html`}
                title={`日报 ${activeMeta.date}`} />
            : <div className="status-msg">{reports.length ? '请选择左侧日报' : '日报生成后在此展示'}</div>}
        </section>
      </div>
    </div>
  </div>
}
