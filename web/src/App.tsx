import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { fetchContracts, fetchScreening, fetchSignals } from './api'
import type { ContractInfo, ScreeningBucket, ScreeningReport, SignalData } from './types'
import Leaderboard, { LEADERBOARD_BUCKETS } from './components/Leaderboard'
import LegendModal from './components/LegendModal'
import KlineChart from './components/KlineChart'
import SplitPane from './components/SplitPane'
import SymbolPicker from './components/SymbolPicker'
import ScreeningMethodModal from './components/ScreeningMethodModal'

function hashKey(): string {
  return decodeURIComponent(location.hash.slice(1))
}

function itemBucket(report: ScreeningReport, key: string): ScreeningBucket | null {
  return LEADERBOARD_BUCKETS.find(({ key: bucket }) => report.buckets[bucket].some((item) => item.key === key))?.key ?? null
}

export default function App() {
  const [contracts, setContracts] = useState<ContractInfo[]>([])
  const [contractsErr, setContractsErr] = useState<string | null>(null)
  const [report, setReport] = useState<ScreeningReport | null>(null)
  const [screeningErr, setScreeningErr] = useState<string | null>(null)
  const [bucket, setBucket] = useState<ScreeningBucket>('long_trend')
  const [activeKey, setActiveKey] = useState<string | null>(null)
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
    fetchContracts().then(setContracts).catch((e: Error) => setContractsErr(e.message))
    fetchScreening().then(setReport).catch((e: Error) => setScreeningErr(e.message))
  }, [])

  const selectSymbol = useCallback((key: string, nextBucket?: ScreeningBucket, syncHash = true) => {
    setActiveKey(key)
    if (nextBucket) setBucket(nextBucket)
    setDrawerOpen(false)
    setPickerOpen(false)
    if (syncHash && hashKey() !== key) location.hash = `#${encodeURIComponent(key)}`
  }, [])

  // 首次加载：报告中的合法 hash 优先，否则选择默认“多头趋势”的第一项。
  useEffect(() => {
    if (initialized.current || !report) return
    const key = hashKey()
    const fromHash = itemBucket(report, key)
    if (fromHash) {
      initialized.current = true
      selectSymbol(key, fromHash, false)
      return
    }
    if (key) {
      const contract = contracts.find((item) => item.key === key && item.has_data)
      if (contract) {
        initialized.current = true
        selectSymbol(contract.key, undefined, false)
        return
      }
      if (!contractsErr) return
    }
    initialized.current = true
    const first = report.buckets.long_trend[0]
    if (first) selectSymbol(first.key, 'long_trend')
  }, [contracts, contractsErr, report, selectSymbol])

  // 前进 / 后退时按 URL hash 恢复榜单类别和图表。
  useEffect(() => {
    const onHash = () => {
      if (!report) return
      const key = hashKey()
      const fromHash = itemBucket(report, key)
      if (fromHash) {
        selectSymbol(key, fromHash, false)
        return
      }
      const contract = contracts.find((item) => item.key === key && item.has_data)
      if (contract) selectSymbol(contract.key, undefined, false)
    }
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [contracts, report, selectSymbol])

  useEffect(() => {
    if (!drawerOpen) return
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setDrawerOpen(false)
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [drawerOpen])

  const changeBucket = useCallback((nextBucket: ScreeningBucket) => {
    setBucket(nextBucket)
    const first = report?.buckets[nextBucket][0]
    if (first) {
      selectSymbol(first.key, nextBucket)
    } else {
      setActiveKey(null)
      setData(null)
      setError(null)
      history.replaceState(null, '', `${location.pathname}${location.search}`)
    }
  }, [report, selectSymbol])

  const openAllSymbols = useCallback(() => {
    if (window.matchMedia('(min-width: 900px)').matches) setLeaderboardCollapsed(true)
    setPickerOpen(true)
  }, [])

  useEffect(() => {
    if (!activeKey) {
      setData(null)
      return
    }
    let cancelled = false
    setLoading(true)
    setError(null)
    fetchSignals(activeKey)
      .then((next) => { if (!cancelled) setData(next) })
      .catch((e: Error) => { if (!cancelled) { setData(null); setError(e.message) } })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [activeKey])

  const active = useMemo(
    () => contracts.find((contract) => contract.key === activeKey) ?? null,
    [contracts, activeKey],
  )

  const badge = useMemo(() => {
    if (!data || data.dates.length === 0) return null
    const n = data.dates.length
    const pos = data.POS[n - 1]
    const lastSig = data.signals.length ? data.signals[data.signals.length - 1] : null
    const posText = pos === 1 ? '持多' : pos === -1 ? '持空' : '空仓'
    const posCls = pos === 1 ? 'pos-long' : pos === -1 ? 'pos-short' : 'pos-flat'
    return { posText, posCls, lastSig, lastDate: data.dates[n - 1] }
  }, [data])

  const leaderboard = (
    <Leaderboard
      report={report}
      error={screeningErr}
      bucket={bucket}
      activeKey={activeKey}
      onBucketChange={changeBucket}
      onSelect={(key, nextBucket) => selectSymbol(key, nextBucket)}
      onShowMethod={() => setMethodOpen(true)}
    />
  )

  return (
    <div className="app">
      <header className="toolbar">
        <b className="brand">期货筛选看板</b>
        <button className="leaderboard-toggle" onClick={() => setDrawerOpen(true)}>☰ 榜单</button>
        {leaderboardCollapsed && <button className="leaderboard-restore" onClick={() => setLeaderboardCollapsed(false)}>筛选榜单</button>}
        <button className="all-symbols-btn" onClick={openAllSymbols}>全部品种</button>
        {active && <span className="active-symbol">{active.name} {active.symbol}</span>}
        {badge && (
          <span className="badge-area">
            <span className={`badge ${badge.posCls}`}>{badge.posText}</span>
            {badge.lastSig && <span className="badge badge-sig">{badge.lastSig.type} @ {data!.dates[badge.lastSig.i]}</span>}
            <span className="badge-date">{badge.lastDate}</span>
          </span>
        )}
        <button className="legend-btn" onClick={() => setLegendOpen(true)}>📖 信号解读</button>
      </header>

      <main className="workspace">
        <SplitPane
          collapsed={leaderboardCollapsed}
          left={leaderboard}
          right={(
            <section className="chart-panel">
              {contractsErr && <div className="status-msg">合约池加载失败：{contractsErr}</div>}
              {loading && <div className="status-msg">加载中…</div>}
              {!loading && error && <div className="status-msg">{error}</div>}
              {!activeKey && report && !screeningErr && <div className="empty-chart">当前分类没有命中品种</div>}
              <KlineChart data={data} />
            </section>
          )}
        />
      </main>

      {drawerOpen && (
        <div className="leaderboard-drawer-mask" onClick={() => setDrawerOpen(false)}>
          <div onClick={(event) => event.stopPropagation()}>
            <Leaderboard
              report={report}
              error={screeningErr}
              bucket={bucket}
              activeKey={activeKey}
              drawer
              onBucketChange={changeBucket}
              onSelect={(key, nextBucket) => selectSymbol(key, nextBucket)}
              onShowMethod={() => setMethodOpen(true)}
              onClose={() => setDrawerOpen(false)}
            />
          </div>
        </div>
      )}
      <SymbolPicker
        open={pickerOpen}
        contracts={contracts}
        activeKey={activeKey}
        onSelect={(key) => selectSymbol(key)}
        onClose={() => setPickerOpen(false)}
      />
      <ScreeningMethodModal open={methodOpen} onClose={() => setMethodOpen(false)} />
      <LegendModal open={legendOpen} onClose={() => setLegendOpen(false)} />
    </div>
  )
}
