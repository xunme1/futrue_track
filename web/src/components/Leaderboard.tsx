import type { ScreeningBucket, ScreeningReport, Timeframe } from '../types'
import RankChange from './RankChange'

export const MAIN_BUCKETS: { key: ScreeningBucket; label: string }[] = [
  { key: 'long_trend', label: '多头趋势' },
  { key: 'short_trend', label: '空头趋势' },
  { key: 'long_to_short', label: '多转空' },
  { key: 'short_to_long', label: '空转多' },
]

export const WARNING_BUCKETS: { key: ScreeningBucket; label: string }[] = [
  { key: 'short_pressure_warning', label: '压力回踩' },
  { key: 'long_support_warning', label: '支撑回踩' },
]

export const LEADERBOARD_BUCKETS = [...MAIN_BUCKETS, ...WARNING_BUCKETS]
export const FOUR_HOUR_BUCKETS: { key: ScreeningBucket; label: string }[] = [
  { key: 'short_to_long', label: '空转多' },
  { key: 'long_to_short', label: '多转空' },
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
  timeframe: Timeframe
}

function scoreText(value: number | null): string {
  return value !== null && Number.isFinite(value) ? `${value.toFixed(2)}%` : '—'
}

function closeText(value: number): string {
  return Number.isFinite(value) ? value.toLocaleString('zh-CN', { maximumFractionDigits: 2 }) : '—'
}

function scoreTitle(item: { score_entry_date?: string; score_entry_open?: number; score_entry_source?: string; score_center?: number }): string | undefined {
  const entryOpen = item.score_entry_open
  const center = item.score_center
  if (typeof entryOpen !== 'number' || !Number.isFinite(entryOpen) || typeof center !== 'number' || !Number.isFinite(center) || !item.score_entry_date) return undefined
  return `开仓 ${item.score_entry_date} · ${closeText(entryOpen)}（${item.score_entry_source ?? '—'}）；中心价 ${closeText(center)}`
}

/** 左侧筛选榜单；drawer=true 时供窄屏覆盖式抽屉复用。 */
export default function Leaderboard({
  report, error, bucket, activeKey, drawer = false, onBucketChange, onSelect, onShowMethod, onClose, timeframe,
}: Props) {
  const items = report?.buckets[bucket] ?? []
  const mainBuckets = timeframe === '4h' ? FOUR_HOUR_BUCKETS : MAIN_BUCKETS
  const activeMeta = [...mainBuckets, ...WARNING_BUCKETS].find((item) => item.key === bucket)
  const isTrend = timeframe === '1d' && (bucket === 'long_trend' || bucket === 'short_trend')
  const ranking = isTrend ? report?.trend_ranking : undefined

  return (
    <aside className={`leaderboard${drawer ? ' leaderboard-drawer' : ''}`} aria-label="筛选榜单">
      <div className="leaderboard-head">
        <div>
          <h2>筛选榜单</h2>
          <div className="leaderboard-date">
            {report ? `数据截至 ${ranking?.as_of ?? items[0]?.date ?? '—'} · 扫描 ${report.scanned_symbols} 个品种` : '等待筛选报告…'}
          </div>
          {!!ranking?.excluded_symbols.length && <div className="leaderboard-stale" title={ranking.excluded_symbols.map((item) => `${item.name} ${item.key}：${item.last_date ?? '无数据'}`).join('\n')}>
            {ranking.excluded_symbols.length} 个品种缺当日数据，暂未参与排名
          </div>}
        </div>
        <div className="leaderboard-actions">
          <button className="leaderboard-method" onClick={onShowMethod}>计算方法</button>
          {drawer && <button className="leaderboard-close" onClick={onClose} aria-label="关闭榜单">✕</button>}
        </div>
      </div>

      <div className="leaderboard-tabs" role="tablist" aria-label="筛选类别">
        {mainBuckets.map((item) => (
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

      {timeframe !== '4h' && <div className="leaderboard-warning-tabs" role="tablist" aria-label="趋势带预警">
        <span className="leaderboard-warning-title">预警</span>
        <div className="leaderboard-warning-buttons">
          {WARNING_BUCKETS.map((item) => (
            <button
              key={item.key}
              role="tab"
              aria-selected={bucket === item.key}
              className={`leaderboard-warning-tab${bucket === item.key ? ' active' : ''}`}
              onClick={() => onBucketChange(item.key)}
            >
              <span>{item.label}</span>
              <b>{report?.summary[item.key] ?? 0}</b>
            </button>
          ))}
        </div>
      </div>}

      <div className="leaderboard-list" role="tabpanel">
        {error && <div className="leaderboard-message error">{error}</div>}
        {!error && !report && <div className="leaderboard-message">加载筛选报告中…</div>}
        {!error && report && items.length === 0 && (
          <div className="leaderboard-message">当前没有符合“{activeMeta?.label}”条件的品种</div>
        )}
        {items.map((item, index) => (
          <div
            key={`${bucket}-${item.key}`}
            className={`leaderboard-item${item.key === activeKey ? ' active' : ''}`}
            onClick={() => onSelect(item.key, bucket)}
          >
            <button type="button" className="leaderboard-select" aria-label={`查看${item.name}K线`}>
            <span className="leaderboard-rank">{isTrend ? item.rank ?? index + 1 : index + 1}</span>
            <span className="leaderboard-name">
              <b>{item.name}</b>
              <small>{item.symbol}</small>
              {item.trend_transition_label && <em>🌟 {item.trend_transition_label}</em>}
              {item.transition_date && <em>转折 {item.transition_date}</em>}
              {item.confirmation_date && <em title={`确认收盘 ${closeText(item.confirmation_close ?? Number.NaN)}；${item.transition_boundary ?? '趋势带'} ${closeText(item.confirmation_boundary_value ?? Number.NaN)}`}>
                {item.transition_boundary === 'PP' ? '突破 PP' : '跌破 EE'} {item.confirmation_date}
              </em>}
              {item.retest_dates?.length ? <em>回踩 {item.retest_dates.join('、')}</em> : null}
              {item.signal_date && <em title={item.star_reasons?.join('；')}>确认 {item.signal_date} · 标准突破{item.stars ? ` ${'🌟'.repeat(item.stars)}` : ''}</em>}
            </span>
            </button>
            {isTrend && item.rank_status && item.rank_history && <RankChange item={item} direction={activeMeta?.label ?? ''} />}
            <span className="leaderboard-values">
              <b className={item.score == null ? '' : item.score >= 0 ? 'score-up' : 'score-down'} title={timeframe === '1d' ? scoreTitle(item) : undefined}>{scoreText(item.score)}</b>
              <small>收 {closeText(item.close)}</small>
            </span>
          </div>
        ))}
      </div>
    </aside>
  )
}
