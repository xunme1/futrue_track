import { useEffect } from 'react'
import type { Timeframe } from '../types'

interface Props {
  open: boolean
  onClose: () => void
  timeframe: Timeframe
}

/** 筛选榜单计算口径说明。 */
export default function ScreeningMethodModal({ open, onClose, timeframe }: Props) {
  useEffect(() => {
    if (!open) return
    const onKeyDown = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [open, onClose])

  if (!open) return null

  return (
    <div className="modal-mask" onClick={onClose}>
      <section className="modal-panel screening-method" onClick={(event) => event.stopPropagation()}>
        <button className="modal-close" onClick={onClose} aria-label="关闭计算方法">×</button>
        <h2>筛选榜单计算方法</h2>
        <p className="legend-intro">报告由{timeframe === '4h' ? '收盘后更新的 4 小时数据' : '每日数据'}计算生成。榜单沿用后端的既有排序，不在前端二次重排。</p>

        <h3>统一指标与分数</h3>
        <ul>
          <li><b>ATR14</b>：使用 Wilder 平滑的 14 根{timeframe === '4h' ? ' 4 小时' : '日'} K 线真实波幅，作为报告参考字段，不参与榜单排序。</li>
          <li><b>MA7</b>：最新收盘价的 7 根{timeframe === '4h' ? ' 4 小时' : '日'} K 线均线。</li>
          <li><b>score</b>：<code>(最新收盘价 − MA7) / MA7 × 100%</code>。表示最新收盘价相对 MA7 的涨跌幅，所有榜单均据此排序。</li>
        </ul>

        <h3>{timeframe === '4h' ? '4 小时转向信号' : '四类主筛与排序'}</h3>
        <table>
          <thead><tr><th>类别</th><th>命中条件</th><th>排序</th></tr></thead>
          <tbody>
            {timeframe !== '4h' && <><tr><td>多头趋势</td><td>POS=1（不额外限制 K 线颜色或趋势带位置）</td><td>score 降序，越大越强</td></tr><tr><td>空头趋势</td><td>POS=-1（不额外限制 K 线颜色或趋势带位置）</td><td>score 升序，越小越强</td></tr></>}
            <tr><td>多转空</td><td>{timeframe === '4h' ? '最近 9 根存在多头支撑带；当前下跌蓝 K 的收盘价跌破该带下沿 EE。前 8 根含偏强红 K、或含 DSBE 减仓倒手指时，各加 1 个 🌟。' : '最近 8 根内由红 K 转为蓝 K，转折前为持多；蓝 K 连续至少 2 根且最新仍为蓝 K。该连续蓝 K 段内任一根收盘价严格 &lt; EE 即确认。'}</td><td>{timeframe === '4h' ? '🌟 数降序，再按 score 升序' : '按最新 score 升序'}</td></tr>
            <tr><td>空转多</td><td>{timeframe === '4h' ? '最近 9 根存在空头压力带；当前上涨红 K 的收盘价突破该带上沿 PP。前 8 根含偏弱蓝 K、或含 SB 增仓笑脸时，各加 1 个 🌟。' : '最近 8 根内由蓝 K 转为红 K，转折前为持空；红 K 连续至少 2 根且最新仍为红 K。该连续红 K 段内任一根收盘价严格 &gt; PP 即确认。'}</td><td>{timeframe === '4h' ? '🌟 数降序，再按 score 降序' : '按最新 score 降序'}</td></tr>
          </tbody>
        </table>

        <h3>边界与颜色说明</h3>
        <p>DD–EE 是多头趋势带，EE 为下边界；KK–PP 是空头趋势带，PP 为上边界。{timeframe === '4h' ? '4 小时红 K=收盘高于开盘，蓝 K=收盘低于开盘；榜单只监测上述两种确认转向。' : '趋势主筛只看 POS；转换筛要求严格突破。日线红/蓝 K 表示相对南华指数的强弱。'}</p>
        {timeframe !== '4h' && <><h3>榜单预警类别</h3><ul><li><b>压力回踩</b>：最新 K 必须仍为持空状态并有 KK–PP 压力带；随后回看最近 9 根，任一持空 K 线的最高价在压力带内、收盘严格低于上沿 PP 即命中。收盘可低于压力带。</li><li><b>支撑回踩</b>：最新 K 必须仍为持多状态并有 EE–DD 支撑带；随后回看最近 9 根，任一持多 K 线的最低价在支撑带内、收盘严格高于下沿 EE 即命中。收盘可高于支撑带。</li></ul><p className="legend-note">报告中仍保留多转空、空转多两类转换预警，供策略分析使用；它们不显示在榜单预警区。</p></>}
      </section>
    </div>
  )
}
