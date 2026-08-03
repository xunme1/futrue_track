import { useCallback, useEffect, useMemo, useState } from 'react'
import { fetchContracts, fetchSignals } from './api'
import type { ContractInfo, SignalData } from './types'
import SymbolPicker from './components/SymbolPicker'
import LegendModal from './components/LegendModal'
import KlineChart from './components/KlineChart'

function hashKey(): string {
  return decodeURIComponent(location.hash.slice(1))
}

export default function App() {
  const [contracts, setContracts] = useState<ContractInfo[]>([])
  const [contractsErr, setContractsErr] = useState<string | null>(null)
  const [activeKey, setActiveKey] = useState<string | null>(null)
  const [data, setData] = useState<SignalData | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [pickerOpen, setPickerOpen] = useState(false)
  const [legendOpen, setLegendOpen] = useState(false)

  // 加载合约池
  useEffect(() => {
    fetchContracts()
      .then((list) => {
        setContracts(list)
        // 初始品种：URL hash 优先，否则第一个有数据的品种
        const h = hashKey()
        const first =
          list.find((c) => c.key === h && c.has_data) ??
          list.find((c) => c.has_data) ??
          null
        if (first) setActiveKey(first.key)
      })
      .catch((e: Error) => setContractsErr(e.message))
  }, [])

  // 加载选中品种的图表数据
  useEffect(() => {
    if (!activeKey) return
    let cancelled = false
    setLoading(true)
    setError(null)
    fetchSignals(activeKey)
      .then((d) => { if (!cancelled) setData(d) })
      .catch((e: Error) => { if (!cancelled) { setData(null); setError(e.message) } })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [activeKey])

  // 浏览器前进/后退（hash 变化）时同步选中品种
  useEffect(() => {
    const onHash = () => {
      const h = hashKey()
      if (h && contracts.some((c) => c.key === h && c.has_data)) setActiveKey(h)
    }
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [contracts])

  const selectSymbol = useCallback((key: string) => {
    setActiveKey(key)
    setPickerOpen(false)
    location.hash = `#${encodeURIComponent(key)}`
  }, [])

  const active = useMemo(
    () => contracts.find((c) => c.key === activeKey) ?? null,
    [contracts, activeKey],
  )

  // 最新一根 bar 的持仓状态与最近信号（工具栏徽标）
  const badge = useMemo(() => {
    if (!data || data.dates.length === 0) return null
    const n = data.dates.length
    const pos = data.POS[n - 1]
    const lastSig = data.signals.length ? data.signals[data.signals.length - 1] : null
    const posText = pos === 1 ? '持多' : pos === -1 ? '持空' : '空仓'
    const posCls = pos === 1 ? 'pos-long' : pos === -1 ? 'pos-short' : 'pos-flat'
    return { posText, posCls, lastSig, lastDate: data.dates[n - 1] }
  }, [data])

  return (
    <div className="app">
      <header className="toolbar">
        <b className="brand">信号看板</b>
        <button className="symbol-btn" onClick={() => setPickerOpen((v) => !v)}>
          {active ? `${active.name} ${active.symbol}` : '选择品种'} ▾
        </button>
        {badge && (
          <span className="badge-area">
            <span className={`badge ${badge.posCls}`}>{badge.posText}</span>
            {badge.lastSig && (
              <span className="badge badge-sig">
                {badge.lastSig.type} @ {data!.dates[badge.lastSig.i]}
              </span>
            )}
            <span className="badge-date">{badge.lastDate}</span>
          </span>
        )}
        <button className="legend-btn" onClick={() => setLegendOpen(true)}>
          📖 信号解读
        </button>
      </header>

      <main className="chart-area">
        {contractsErr && <div className="status-msg">合约池加载失败：{contractsErr}（请先启动后端 uvicorn）</div>}
        {loading && <div className="status-msg">加载中…</div>}
        {!loading && error && <div className="status-msg">{error}</div>}
        <KlineChart data={data} />
      </main>

      <SymbolPicker
        open={pickerOpen}
        contracts={contracts}
        activeKey={activeKey}
        onSelect={selectSymbol}
        onClose={() => setPickerOpen(false)}
      />
      <LegendModal open={legendOpen} onClose={() => setLegendOpen(false)} />
    </div>
  )
}
