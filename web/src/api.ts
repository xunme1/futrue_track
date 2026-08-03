import type { ContractInfo, SignalData } from './types'

async function getJson<T>(url: string): Promise<T> {
  const resp = await fetch(url)
  if (!resp.ok) {
    if (resp.status === 404) throw new Error('暂无数据')
    throw new Error(`请求失败 ${resp.status}: ${url}`)
  }
  return (await resp.json()) as T
}

/** 合约池元数据（品种选择器数据源） */
export function fetchContracts(): Promise<ContractInfo[]> {
  return getJson<ContractInfo[]>('/api/contracts')
}

/** 某品种完整图表数据 */
export function fetchSignals(key: string): Promise<SignalData> {
  return getJson<SignalData>(`/api/signals/${encodeURIComponent(key)}`)
}
