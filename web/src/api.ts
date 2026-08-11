import type { ContractInfo, ScreeningReport, SignalData, Timeframe } from './types'

async function getJson<T>(url: string): Promise<T> {
  const resp = await fetch(url)
  if (!resp.ok) {
    if (resp.status === 404) throw new Error('暂无数据')
    throw new Error(`请求失败 ${resp.status}: ${url}`)
  }
  return (await resp.json()) as T
}

/** 合约池元数据（品种选择器数据源） */
function timeframeQuery(timeframe: Timeframe): string {
  return timeframe === '1d' ? '' : '?timeframe=4h'
}

export function fetchContracts(timeframe: Timeframe): Promise<ContractInfo[]> {
  return getJson<ContractInfo[]>(`/api/contracts${timeframeQuery(timeframe)}`)
}

/** 某品种完整图表数据 */
export function fetchSignals(key: string, timeframe: Timeframe): Promise<SignalData> {
  return getJson<SignalData>(`/api/signals/${encodeURIComponent(key)}${timeframeQuery(timeframe)}`)
}

/** 最新本地筛选报告（由 backend.pipeline.screen 生成）。 */
export function fetchScreening(timeframe: Timeframe): Promise<ScreeningReport> {
  return getJson<ScreeningReport>(`/api/screening${timeframeQuery(timeframe)}`)
}
