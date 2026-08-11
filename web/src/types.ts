/**
 * 与后端 API（docs/api.md）对齐的类型定义。
 * 注意：ohlc 数组顺序为 [开, 收, 低, 高]（ECharts candlestick 约定），
 * 与 data/csv/ 的 open,high,low,close 顺序不同。
 */

/** GET /api/contracts 单项 */
export type Timeframe = '1d' | '4h'

export interface ContractInfo {
  key: string        // 品种键，调 /api/signals/{key} 用这个值
  symbol: string     // 完整代码，如 TA609.CZC
  name: string       // 中文名，如 PTA
  category: string   // 板块类别：有色金属/黑色/化工/农产品/能源/贵金属/新能源/股指…
  exchange: string   // SHFE/DCE/CZCE/INE/GFEX/CFFEX
  source: string     // ricequant / ifind
  extra: boolean     // 是否池外手工补充品种
  has_data: boolean  // 是否已有计算产物
}

/** GET /api/symbols 单项 */
export interface SymbolSummary {
  key: string
  symbol: string
  bars: number
  last_date: string
  pos: number                 // 1 多 / -1 空 / 0 空仓
  last_signal: { type: string; date: string } | null
}

/** 交易信号（稀疏，仅信号日出现） */
export interface TradeSignal {
  i: number                   // bar 索引
  type: 'BK' | 'SK' | 'SP' | 'BP'
  price: number
}

/** GET /api/signals/{key} 完整响应；所有数组等长、按索引 i 对齐，null=指标不可用 */
export interface SignalData {
  symbol: string
  timeframe?: Timeframe
  dates: string[]
  ohlc: [number, number, number, number][]   // [开, 收, 低, 高]
  volume: number[]
  opi: number[]                               // 持仓量（ccl）
  PQ?: boolean[]                              // 日线：强于南华指数（红K）
  PR?: boolean[]                              // 日线：弱于南华指数（蓝K）
  NN?: (number | null)[]                      // 日线强弱数字（默认隐藏）
  GG?: (number | null)[]
  bar_colors?: ('red' | 'blue' | 'gray')[]    // 4小时：上涨 / 下跌 / 平盘
  signals: TradeSignal[]
  SB: boolean[]                               // 多头增仓（金钻）
  DSB: boolean[]                              // 洗盘后站起（橙钻）
  DSBE: boolean[]                             // 反击扑灭（紫钻）
  AA1: (boolean | null)[]                     // 周线许可：可空
  ZZ1: (boolean | null)[]                     // 周线许可：可多
  TT1: (boolean | null)[]                     // 周线许可：非盘整
  KK: (number | null)[]                       // 通道线
  PP: (number | null)[]
  DD: (number | null)[]
  EE: (number | null)[]
  POS: number[]                               // 1 多 / -1 空 / 0 空仓
  ZD: (number | null)[]                       // 七日中点线
  DSBE_NOTE?: (string | null)[]                // DSBE 附加文字；可选用于兼容旧 JSON
}

/** 筛选报告的类别；榜单首页仅展示前四类主筛。 */
export type ScreeningBucket =
  | 'long_trend'
  | 'short_trend'
  | 'long_to_short'
  | 'short_to_long'
  | 'long_to_short_warning'
  | 'short_to_long_warning'
  | 'short_pressure_warning'
  | 'long_support_warning'

/** GET /api/screening 的榜单条目。 */
export interface ScreeningItem {
  key: string
  symbol: string
  name: string
  category: string
  exchange: string
  date: string
  close: number
  ma7: number
  atr14: number
  score: number
  PQ?: boolean
  PR?: boolean
  bar_color?: 'red' | 'blue' | 'gray'
  POS: number
  DD: number | null
  EE: number | null
  KK: number | null
  PP: number | null
  transition_date?: string
  transition_from?: 'red' | 'blue'
  transition_to?: 'red' | 'blue'
  transition_close?: number
  transition_boundary?: 'EE' | 'PP'
  transition_boundary_value?: number
  signal_name?: '空转多' | '多转空'
  signal_date?: string
  trend_band_date?: string
  stars?: number
  star_reasons?: string[]
}

export interface ScreeningReport {
  timeframe?: Timeframe
  generated_at: string
  rules: {
    lookback: number
    atr_window: number
    atr_method: string
    ma_window: number
    score: string
  }
  scanned_symbols: number
  skipped_symbols: string[]
  summary: Record<ScreeningBucket, number>
  buckets: Record<ScreeningBucket, ScreeningItem[]>
}
