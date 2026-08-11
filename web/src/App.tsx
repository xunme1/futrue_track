import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { fetchContracts, fetchScreening, fetchSignals } from './api'
import type { ContractInfo, ScreeningBucket, ScreeningReport, SignalData, Timeframe } from './types'
import Leaderboard, { LEADERBOARD_BUCKETS } from './components/Leaderboard'
import LegendModal from './components/LegendModal'
import KlineChart from './components/KlineChart'
import SplitPane from './components/SplitPane'
import SymbolPicker from './components/SymbolPicker'
import ScreeningMethodModal from './components/ScreeningMethodModal'

function hashKey(): string { return decodeURIComponent(location.hash.slice(1)) }
function urlTimeframe(): Timeframe { return new URLSearchParams(location.search).get('timeframe') === '4h' ? '4h' : '1d' }
function itemBucket(report: ScreeningReport, key: string, timeframe: Timeframe): ScreeningBucket | null {
  const buckets: ScreeningBucket[] = timeframe === '4h' ? ['short_to_long', 'long_to_short'] : LEADERBOARD_BUCKETS.map((item) => item.key)
  return buckets.find((bucket) => report.buckets[bucket].some((item) => item.key === key)) ?? null
}

export default function App() {
  const [timeframe, setTimeframe] = useState<Timeframe>(urlTimeframe)
  const [contracts, setContracts] = useState<ContractInfo[]>([])
  const [contractsErr, setContractsErr] = useState<string | null>(null)
  const [report, setReport] = useState<ScreeningReport | null>(null)
  const [screeningErr, setScreeningErr] = useState<string | null>(null)
  const [bucket, setBucket] = useState<ScreeningBucket>(() => urlTimeframe() === '4h' ? 'short_to_long' : 'long_trend')
  const [activeKey, setActiveKey] = useState<string | null>(hashKey() || null)
  const [data, setData] = useState<SignalData | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [pickerOpen, setPickerOpen] = useState(false)
  const [leaderboardCollapsed, setLeaderboardCollapsed] = useState(false)
  const [legendOpen, setLegendOpen] = useState(false)
  const [methodOpen, setMethodOpen] = useState(false)
  const initialized = useRef(false)

  useEffect(() => {
    initialized.current = false
    // 切换周期时先落到该周期的默认榜单，避免 4 小时页沿用日线的趋势榜单。
    setBucket(timeframe === '4h' ? 'short_to_long' : 'long_trend')
    setData(null); setError(null); setContractsErr(null); setScreeningErr(null)
    fetchContracts(timeframe).then(setContracts).catch((e: Error) => setContractsErr(e.message))
    fetchScreening(timeframe).then(setReport).catch((e: Error) => setScreeningErr(e.message))
  }, [timeframe])

  const selectSymbol = useCallback((key: string, nextBucket?: ScreeningBucket, syncHash = true) => {
    setActiveKey(key)
    if (nextBucket) setBucket(nextBucket)
    setDrawerOpen(false); setPickerOpen(false)
    if (syncHash && hashKey() !== key) location.hash = `#${encodeURIComponent(key)}`
  }, [])

  useEffect(() => {
    if (initialized.current || !report) return
    const key = hashKey(); const fromHash = itemBucket(report, key, timeframe)
    if (fromHash) { initialized.current = true; selectSymbol(key, fromHash, false); return }
    const contract = contracts.find((item) => item.key === key && item.has_data)
    if (contract) { initialized.current = true; selectSymbol(contract.key, undefined, false); return }
    if (key && contracts.length === 0 && !contractsErr) return
    initialized.current = true
    const defaultBucket: ScreeningBucket = timeframe === '4h' ? 'short_to_long' : 'long_trend'
    const first = report.buckets[defaultBucket][0] ?? (timeframe === '4h' ? report.buckets.long_to_short[0] : undefined)
    if (first) selectSymbol(first.key, first ? (report.buckets[defaultBucket].some((item) => item.key === first.key) ? defaultBucket : 'long_to_short') : defaultBucket)
    else setActiveKey(null)
  }, [contracts, contractsErr, report, selectSymbol])

  useEffect(() => {
    const onNavigation = () => {
      const nextTimeframe = urlTimeframe()
      if (nextTimeframe !== timeframe) { setTimeframe(nextTimeframe); return }
      if (!report) return
      const key = hashKey(); const fromHash = itemBucket(report, key, timeframe)
      if (fromHash) selectSymbol(key, fromHash, false)
      else if (contracts.some((item) => item.key === key && item.has_data)) selectSymbol(key, undefined, false)
    }
    window.addEventListener('hashchange', onNavigation); window.addEventListener('popstate', onNavigation)
    return () => { window.removeEventListener('hashchange', onNavigation); window.removeEventListener('popstate', onNavigation) }
  }, [contracts, report, selectSymbol, timeframe])

  const changeTimeframe = useCallback((next: Timeframe) => {
    if (next === timeframe) return
    const search = next === '4h' ? '?timeframe=4h' : ''
    history.pushState(null, '', `${location.pathname}${search}${location.hash}`)
    setBucket(next === '4h' ? 'short_to_long' : 'long_trend')
    setTimeframe(next)
  }, [timeframe])

  useEffect(() => {
    if (!drawerOpen) return
    const onKeyDown = (event: KeyboardEvent) => { if (event.key === 'Escape') setDrawerOpen(false) }
    window.addEventListener('keydown', onKeyDown); return () => window.removeEventListener('keydown', onKeyDown)
  }, [drawerOpen])

  const changeBucket = useCallback((nextBucket: ScreeningBucket) => {
    setBucket(nextBucket)
    const first = report?.buckets[nextBucket][0]
    if (first) selectSymbol(first.key, nextBucket)
    else { setActiveKey(null); setData(null); setError(null); history.replaceState(null, '', `${location.pathname}${location.search}`) }
  }, [report, selectSymbol])
  const openAllSymbols = useCallback(() => { if (window.matchMedia('(min-width: 900px)').matches) setLeaderboardCollapsed(true); setPickerOpen(true) }, [])

  useEffect(() => {
    if (!activeKey) { setData(null); return }
    let cancelled = false; setLoading(true); setError(null)
    fetchSignals(activeKey, timeframe)
      .then((next) => { if (!cancelled) setData(next) })
      .catch((e: Error) => { if (!cancelled) { setData(null); setError(e.message) } })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [activeKey, timeframe])

  const active = useMemo(() => contracts.find((contract) => contract.key === activeKey) ?? null, [contracts, activeKey])
  const badge = useMemo(() => {
    if (!data || !data.dates.length) return null
    const n = data.dates.length; const pos = data.POS[n - 1]; const lastSig = data.signals.length ? data.signals[data.signals.length - 1] : null
    return { posText: pos === 1 ? '持多' : pos === -1 ? '持空' : '空仓', posCls: pos === 1 ? 'pos-long' : pos === -1 ? 'pos-short' : 'pos-flat', lastSig, lastDate: data.dates[n - 1] }
  }, [data])
  const leaderboard = <Leaderboard report={report} error={screeningErr} bucket={bucket} activeKey={activeKey} timeframe={timeframe} onBucketChange={changeBucket} onSelect={(key, nextBucket) => selectSymbol(key, nextBucket)} onShowMethod={() => setMethodOpen(true)} />

  return <div className="app">
    <header className="toolbar">
      <b className="brand">期货筛选看板</b>
      <div className="timeframe-switch" role="group" aria-label="K线周期">
        <button className={timeframe === '1d' ? 'active' : ''} onClick={() => changeTimeframe('1d')}>日线</button>
        <button className={timeframe === '4h' ? 'active' : ''} onClick={() => changeTimeframe('4h')}>4小时</button>
      </div>
      <button className="leaderboard-toggle" onClick={() => setDrawerOpen(true)}>☰ 榜单</button>
      {leaderboardCollapsed && <button className="leaderboard-restore" onClick={() => setLeaderboardCollapsed(false)}>筛选榜单</button>}
      <button className="all-symbols-btn" onClick={openAllSymbols}>全部品种</button>
      {active && <span className="active-symbol">{active.name} {active.symbol}</span>}
      {badge && <span className="badge-area"><span className={`badge ${badge.posCls}`}>{badge.posText}</span>{badge.lastSig && <span className="badge badge-sig">{badge.lastSig.type} @ {data!.dates[badge.lastSig.i]}</span>}<span className="badge-date">{badge.lastDate}</span></span>}
      <button className="legend-btn" onClick={() => setLegendOpen(true)}>📖 信号解读</button>
    </header>
    <main className="workspace"><SplitPane collapsed={leaderboardCollapsed} left={leaderboard} right={<section className="chart-panel">
      {contractsErr && <div className="status-msg">合约池加载失败：{contractsErr}</div>}{loading && <div className="status-msg">加载中…</div>}{!loading && error && <div className="status-msg">{error}</div>}{!activeKey && report && !screeningErr && <div className="empty-chart">当前分类没有命中品种</div>}<KlineChart data={data} />
    </section>} /></main>
    {drawerOpen && <div className="leaderboard-drawer-mask" onClick={() => setDrawerOpen(false)}><div onClick={(event) => event.stopPropagation()}><Leaderboard report={report} error={screeningErr} bucket={bucket} activeKey={activeKey} timeframe={timeframe} drawer onBucketChange={changeBucket} onSelect={(key, nextBucket) => selectSymbol(key, nextBucket)} onShowMethod={() => setMethodOpen(true)} onClose={() => setDrawerOpen(false)} /></div></div>}
    <SymbolPicker open={pickerOpen} contracts={contracts} activeKey={activeKey} onSelect={(key) => selectSymbol(key)} onClose={() => setPickerOpen(false)} />
    <ScreeningMethodModal open={methodOpen} onClose={() => setMethodOpen(false)} timeframe={timeframe} />
    <LegendModal open={legendOpen} onClose={() => setLegendOpen(false)} timeframe={timeframe} />
  </div>
}
