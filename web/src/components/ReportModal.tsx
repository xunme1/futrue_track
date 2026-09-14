import { useEffect, useState } from 'react'
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
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [open, onClose])

  if (!open) return null

  const activeMeta = reports.find((r) => r.date === active) ?? null

  return <div className="modal-mask" onClick={onClose}>
    <div className="modal-panel report-panel" onClick={(event) => event.stopPropagation()}>
      <button className="modal-close" onClick={onClose}>✕ 关闭</button>
      <h2 className="report-title">📰 每日日报</h2>
      <div className="report-body">
        <aside className="report-list">
          {error && <div className="status-msg">日报列表加载失败:{error}</div>}
          {!error && reports.length === 0 && <div className="status-msg">暂无归档日报</div>}
          {reports.map((r) => (
            <button key={r.date}
              className={`report-item${r.date === active ? ' active' : ''}`}
              disabled={!r.has_html}
              title={r.has_html ? '' : '该日暂无 HTML 产物'}
              onClick={() => setActive(r.date)}>
              <div className="report-item-date">{r.date}{r.date === reports[0]?.date && <span className="report-tag">最新</span>}</div>
              {r.data_date && <div className="report-item-sub">数据基准 {r.data_date}</div>}
              {r.one_liner && <div className="report-item-liner">{r.one_liner}</div>}
            </button>
          ))}
        </aside>
        <section className="report-viewer">
          {activeMeta
            ? <iframe key={activeMeta.date} src={`/api/reports/${activeMeta.date}/html`}
                title={`日报 ${activeMeta.date}`} />
            : <div className="status-msg">{reports.length ? '请选择左侧日报' : '日报生成后在此展示'}</div>}
        </section>
      </div>
    </div>
  </div>
}
