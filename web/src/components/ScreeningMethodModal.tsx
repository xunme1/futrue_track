import { useEffect } from 'react'

interface Props {
  open: boolean
  onClose: () => void
}

/** 筛选榜单计算口径说明。 */
export default function ScreeningMethodModal({ open, onClose }: Props) {
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
        <p className="legend-intro">报告由每日数据计算后生成。榜单沿用后端的既有排序，不在前端二次重排。</p>

        <h3>统一指标与分数</h3>
        <ul>
          <li><b>ATR14</b>：使用 Wilder 平滑的 14 日真实波幅，衡量品种自身的波动尺度。</li>
          <li><b>MA7</b>：最新收盘价的 7 日均线。</li>
          <li><b>score</b>：<code>(最新收盘价 − MA7) / ATR14</code>。它将偏离均线的幅度按波动率标准化，便于不同价格、不同波动水平的品种比较。</li>
        </ul>

        <h3>四类主筛与排序</h3>
        <table>
          <thead><tr><th>类别</th><th>命中条件</th><th>排序</th></tr></thead>
          <tbody>
            <tr><td>多头趋势</td><td>POS=1、最新 K 为红 K（PQ=true）、收盘价 ≥ EE</td><td>score 降序，越大越强</td></tr>
            <tr><td>空头趋势</td><td>POS=-1、最新 K 为蓝 K（PR=true）、收盘价 ≤ PP</td><td>score 升序，越小越强</td></tr>
            <tr><td>多转空</td><td>最近 8 根内由红 K 直接转为首根蓝 K；该根收盘价 &lt; EE；转折前为持多，之后蓝 K 连续至少 1 根，且最新 K 仍为蓝 K</td><td>按最新 score 升序</td></tr>
            <tr><td>空转多</td><td>最近 8 根内由蓝 K 直接转为首根红 K；该根收盘价 &gt; PP；转折前为持空，之后红 K 连续至少 1 根，且最新 K 仍为红 K</td><td>按最新 score 降序</td></tr>
          </tbody>
        </table>

        <h3>边界与颜色说明</h3>
        <p>DD–EE 是多头趋势带，EE 为下边界；KK–PP 是空头趋势带，PP 为上边界。主筛允许收盘价恰好落在对应边界上；转换筛要求严格突破。灰 K 不计入转换的目标色连续段。</p>
        <h3>榜单预警类别</h3>
        <ul>
          <li><b>压力回踩</b>：持空时，最高价与收盘价均在 KK–PP 压力带内，收盘未上破 PP；表示压力仍有效，需留意反弹后的方向选择。</li>
          <li><b>支撑回踩</b>：持多时，最低价与收盘价均在 EE–DD 支撑带内，且收盘严格高于下沿 EE；表示支撑仍有效，需留意反弹延续。</li>
        </ul>
        <p className="legend-note">报告中仍保留多转空、空转多两类转换预警，供策略分析使用；它们不显示在榜单预警区。</p>
      </section>
    </div>
  )
}
