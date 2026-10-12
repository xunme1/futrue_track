import { useEffect, useState } from 'react'
import { fetchReceiptList } from '../api'
import type { ReceiptMeta } from '../types'

interface Props {
  open: boolean
  onClose: () => void
}

/** YYYYMMDD → YYYY-MM-DD */
function fmtDate(d: string): string {
  return d.length === 8 ? `${d.slice(0, 4)}-${d.slice(4, 6)}-${d.slice(6)}` : d
}

/**
 * 仓单研究监控弹窗：自包含 HTML 报告 + 历史归档列表。
 */
export default function ReceiptModal({ open, onClose }: Props) {
  const [items, setItems] = useState<ReceiptMeta[]>([])
  const [error, setError] = useState<string | null>(null)
  const [active, setActive] = useState<string | null>(null)
  const [archiveOpen, setArchiveOpen] = useState(false)

  useEffect(() => {
    if (!open) return
    setError(null)
    fetchReceiptList()
      .then((list) => {
        setItems(list)
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

  const activeMeta = items.find((r) => r.date === active) ?? null

  return <div className="modal-mask seat-modal-mask" onClick={onClose}>
    <div className="modal-panel report-panel seat-panel" onClick={(event) => event.stopPropagation()}>
      <button className="modal-close" onClick={onClose}>✕ 关闭</button>
      {activeMeta?.has_html && <a className="modal-download"
        href={`/api/receipt/${activeMeta.date}/html`}
        download={`receipt_report_${activeMeta.date}.html`}>⬇️ HTML 报告</a>}
      <div className="seat-heading">
        <h2 className="report-title">📦 仓单研究监控</h2>
        <select aria-label="仓单报告日期" value={active ?? ''}
          onChange={(event) => setActive(event.target.value)}>
          {!active && <option value="">选择日期</option>}
          {items.map((item) => <option key={item.date} value={item.date}
            disabled={!item.has_html}>
            {fmtDate(item.date)}{item.date === items[0]?.date ? ' · 最新' : ''}
          </option>)}
        </select>
        <button type="button" aria-expanded={archiveOpen} aria-controls="receipt-archive-list"
          onClick={() => setArchiveOpen((value) => !value)}>
          {archiveOpen ? '收起归档' : '历史归档'}
        </button>
      </div>
      {error && <div className="status-msg">列表加载失败:{error}</div>}
      <div className="report-body">
        <aside className="report-list" id="receipt-archive-list" hidden={!archiveOpen}>
          {!error && items.length === 0 && <div className="status-msg">暂无归档仓单报告</div>}
          {items.map((r) => (
            <button key={r.date}
              className={`report-item${r.date === active ? ' active' : ''}`}
              disabled={!r.has_html}
              title={r.has_html ? '' : '该日暂无可查看产物'}
              onClick={() => setActive(r.date)}>
              <div className="report-item-date">{fmtDate(r.date)}{r.date === items[0]?.date && <span className="report-tag">最新</span>}</div>
              {r.summary && <div className="report-item-liner">{r.summary}</div>}
            </button>
          ))}
        </aside>
        <section className="report-viewer seat-viewer">
          {activeMeta?.has_html
            ? <iframe key={activeMeta.date} src={`/api/receipt/${activeMeta.date}/html`}
                title={`仓单研究监控 ${fmtDate(activeMeta.date)}`} />
            : <div className="status-msg">{items.length ? '请选择左侧日期' : '仓单数据生成后在此展示'}</div>}
        </section>
      </div>
    </div>
  </div>
}
