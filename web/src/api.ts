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
    .then((payload) => assertRequestedTimeframe(payload, timeframe))
}

/** 最新本地筛选报告（由 backend.pipeline.screen 生成）。 */
export function fetchScreening(timeframe: Timeframe): Promise<ScreeningReport> {
  return getJson<ScreeningReport>(`/api/screening${timeframeQuery(timeframe)}`)
    .then((report) => assertRequestedTimeframe(report, timeframe))
}

/** 防止旧版服务端忽略 ?timeframe=4h 后把日线数据静默混入 4 小时页面。 */
function assertRequestedTimeframe<T extends { timeframe?: Timeframe }>(payload: T, timeframe: Timeframe): T {
  if (timeframe === '4h' && payload.timeframe !== '4h') {
    throw new Error('服务器未返回 4 小时数据：请更新并重启 API 服务，再执行 4 小时更新任务。')
  }
  return payload
}
