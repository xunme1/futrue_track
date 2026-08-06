import type { ScreeningBucket, ScreeningReport } from '../types'

export const MAIN_BUCKETS: { key: ScreeningBucket; label: string }[] = [
  { key: 'long_trend', label: '多头趋势' },
  { key: 'short_trend', label: '空头趋势' },
  { key: 'long_to_short', label: '多转空' },
  { key: 'short_to_long', label: '空转多' },
]

interface Props {
  report: ScreeningReport | null
  error: string | null
  bucket: ScreeningBucket
  activeKey: string | null
  drawer?: boolean
  onBucketChange: (bucket: ScreeningBucket) => void
  onSelect: (key: string, bucket: ScreeningBucket) => void
  onShowMethod: () => void
  onClose?: () => void
}

function scoreText(value: number): string {
  return Number.isFinite(value) ? value.toFixed(2) : '—'
}

function closeText(value: number): string {
  return Number.isFinite(value) ? value.toLocaleString('zh-CN', { maximumFractionDigits: 2 }) : '—'
}

/** 左侧筛选榜单；drawer=true 时供窄屏覆盖式抽屉复用。 */
export default function Leaderboard({
  report, error, bucket, activeKey, drawer = false, onBucketChange, onSelect, onShowMethod, onClose,
}: Props) {
  const items = report?.buckets[bucket] ?? []
  const activeMeta = MAIN_BUCKETS.find((item) => item.key === bucket)

  return (
    <aside className={`leaderboard${drawer ? ' leaderboard-drawer' : ''}`} aria-label="筛选榜单">
      <div className="leaderboard-head">
        <div>
          <h2>筛选榜单</h2>
          <div className="leaderboard-date">
            {report ? `数据截至 ${items[0]?.date ?? '—'} · 扫描 ${report.scanned_symbols} 个品种` : '等待筛选报告…'}
          </div>
        </div>
        <div className="leaderboard-actions">
          <button className="leaderboard-method" onClick={onShowMethod}>计算方法</button>
          {drawer && <button className="leaderboard-close" onClick={onClose} aria-label="关闭榜单">✕</button>}
        </div>
      </div>

      <div className="leaderboard-tabs" role="tablist" aria-label="筛选类别">
        {MAIN_BUCKETS.map((item) => (
          <button
            key={item.key}
            role="tab"
            aria-selected={bucket === item.key}
            className={`leaderboard-tab${bucket === item.key ? ' active' : ''}`}
            onClick={() => onBucketChange(item.key)}
          >
            <span>{item.label}</span>
            <b>{report?.summary[item.key] ?? 0}</b>
          </button>
        ))}
      </div>

      <div className="leaderboard-list" role="tabpanel">
        {error && <div className="leaderboard-message error">{error}</div>}
        {!error && !report && <div className="leaderboard-message">加载筛选报告中…</div>}
        {!error && report && items.length === 0 && (
          <div className="leaderboard-message">当前没有符合“{activeMeta?.label}”条件的品种</div>
        )}
        {items.map((item, index) => (
          <button
            key={`${bucket}-${item.key}`}
            className={`leaderboard-item${item.key === activeKey ? ' active' : ''}`}
            onClick={() => onSelect(item.key, bucket)}
          >
            <span className="leaderboard-rank">{index + 1}</span>
            <span className="leaderboard-name">
              <b>{item.name}</b>
              <small>{item.symbol}</small>
              {item.transition_date && <em>转折 {item.transition_date}</em>}
            </span>
            <span className="leaderboard-values">
              <b className={item.score >= 0 ? 'score-up' : 'score-down'}>{scoreText(item.score)}</b>
              <small>收 {closeText(item.close)}</small>
            </span>
          </button>
        ))}
      </div>
    </aside>
  )
}
