import { useEffect } from 'react'
import type { Timeframe } from '../types'

interface Props {
  open: boolean
  onClose: () => void
  timeframe: Timeframe
}

interface TagSpec { text: string; bg?: string; color?: string }
interface RowSpec { cells: (string | TagSpec[])[] }   // 单元格：HTML 字符串 或 标签组
interface SectionSpec {
  title: string
  headers?: string[]
  rows: RowSpec[]
  note?: string
}

/**
 * 信号解读文档内容 —— 移植自旧看板 #legend-doc（tools/make_dashboard.py 模板），
 * 改为结构化数据渲染。更新文档只需改这里。
 */
const SECTIONS: SectionSpec[] = [
  {
    title: '一、K 线颜色（相对强弱 · 对应麦语言 XDD 指标）',
    headers: ['颜色', '含义', '判定条件（对比南华商品指数 NHCI.SL）'],
    rows: [
      {
        cells: [
          [{ text: '红K', bg: '#ff5252', color: '#fff' }],
          '<b>强于大盘（PQ）</b>',
          '现价在七日中点上方时，本品种 7 日涨幅 &gt; 1.5 倍指数涨幅（领涨）；或在中点下方时，跌幅比指数轻（抗跌）',
        ],
      },
      {
        cells: [
          [{ text: '蓝K', bg: '#2979ff', color: '#fff' }],
          '<b>弱于大盘（PR）</b>',
          '中点上方时涨幅 &lt; 0.7 倍指数（涨不动）；或中点下方时跌幅 &gt; 1.5 倍指数（领跌）',
        ],
      },
      {
        cells: [
          [
            { text: '阳', bg: '#8b95ab', color: '#1a2338' },
            { text: '阴', bg: '#454e63', color: '#cfd8ec' },
          ],
          '<b>强弱中性</b>（灰K）',
          '当天相对南华指数强弱不突出（既非 PQ 也非 PR），阳线浅灰、阴线深灰，仅作方向区分',
        ],
      },
    ],
  },
  {
    title: '二、交易信号箭头（会直接下单的信号 · 对应麦语言 SK/BK/BP/SP）',
    headers: ['标记', '含义', '触发条件'],
    rows: [
      {
        cells: [
          [{ text: '▲BK', color: '#ff3355' }],
          '<b>开多单</b>（W 底确认）',
          '周线在 7 周均线上方（ZZ1）∧ 周线非盘整（TT1）∧ 价格突破「W 底颈线 HH 与 30 日区间 73% 分位线 DD 中较高者」∧ 本周期非盘整（7 日振幅 ≥ 均价 3%）',
        ],
      },
      {
        cells: [
          [{ text: '▼SK', color: '#22cc88' }],
          '<b>开空单</b>（M 头确认）',
          '周线在 7 周均线下方（AA1）∧ 周线非盘整（TT1）∧ 价格跌破「M 头颈线 LL 与 30 日区间 27% 分位线 KK 中较低者」∧ 本周期非盘整',
        ],
      },
      {
        cells: [
          [{ text: '△SP', color: '#ffaa00' }],
          '<b>平多单</b>（止盈/止损）',
          '持多期间价格跌破 EE 线（30 日区间约 64% 分位）——回撤不到一成就离场',
        ],
      },
      {
        cells: [
          [{ text: '▽BP', color: '#ffaa00' }],
          '<b>平空单</b>（止盈/止损）',
          '持空期间价格收复 PP 线（30 日区间约 36% 分位）',
        ],
      },
    ],
    note: '说明：分位由参数 G1=4、G2=3 决定（进场≈区间 25%/75% 深度确认，离场≈区间 33%/67% 防线），信号按 AUTOFILTER 规则严格开平交替。',
  },
  {
    title: '三、持仓趋势带与通道虚线（只在持仓期间显示 · 复刻麦语言 FILLRGN）',
    headers: ['元素', '含义'],
    rows: [
      {
        cells: [
          [{ text: '红带', bg: 'rgba(229,69,69,.25)', color: '#ff8899' }],
          '<b>多头趋势带</b>：持多期间在 DD（开多阈值，区间 75% 分位）与 EE（平多防线，67% 分位）之间填充——色带内部就是这波多单的"盈利走廊"，价格跌穿下沿 EE 即触发 SP 离场',
        ],
      },
      {
        cells: [
          [{ text: '绿带', bg: 'rgba(47,163,107,.25)', color: '#7ee2ae' }],
          '<b>空头趋势带</b>：持空期间在 KK（开空阈值，25% 分位）与 PP（平空防线，33% 分位）之间填充——价格收复上沿 PP 即触发 BP 离场',
        ],
      },
      {
        cells: [
          [
            { text: 'DD', color: '#ff5566' },
            { text: 'EE', color: '#ff8899' },
            { text: 'KK', color: '#33dd99' },
            { text: 'PP', color: '#88eebb' },
          ],
          '<b>通道边界虚线</b>：趋势带的上下沿，与色带同步显示，方便读取具体价位',
        ],
      },
      {
        cells: [
          [{ text: '灰线', color: '#5c6b8a' }],
          '<b>七日中点 ZD</b>：近 7 日最高价与最低价的中值，价格强弱的分水岭（上方偏强、下方偏弱）',
        ],
      },
    ],
  },
  {
    title: '四、资金信号 emoji（持仓量 OPI 驱动，只提示不下单）',
    headers: ['标记', '含义', '触发条件'],
    rows: [
      {
        cells: [
          [{ text: '🤭 SB' }],
          '<b>多头增仓</b>',
          '持仓量单日 +4% 且价格涨 3%（或连续两日增仓创新高），KDJ 不超买（K&lt;85），且是近 5 根内首次出现；或增仓 ≥7%/超 4 万手的超级增仓——新钱在买',
        ],
      },
      {
        cells: [
          [{ text: '🤔 DSB' }],
          '<b>洗盘后站起来</b>',
          '两根前收阴，今日收盘收复近两根实体区间上 1/3，且期间持仓量换手 &gt;3%——浮筹被洗干净，少数人获利，多头更稳',
        ],
      },
      {
        cells: [
          [{ text: '👎 DSBE' }],
          '<b>反击扑灭</b>（做空版）',
          '两根前收阳，今日收盘被砸进实体区间下 1/3 且伴随巨量换手——多头反击失败，跌势大概率延续。悬停可见附加判断：「减仓耗散筹码变轻」=多头割肉离场、跌势或近尾声；「增仓能量增强」=新空头加码、跌势延续',
        ],
      },
    ],
  },
  {
    title: '五、其他辅助元素',
    headers: ['元素', '含义'],
    rows: [
      {
        cells: [
          'NN / GG 数字',
          '默认隐藏，点顶部图例「NN/GG强弱数字」开启。<b style="color:#ff6b6b">NN</b>（K线上方红字）= 本品种 7 日位置分层 − 指数分层，正数=比大盘站得高；<b style="color:#51cf66">GG</b>（K线下方绿字）= 当日收盘力度分层差，正数=今天比大盘硬',
        ],
      },
      { cells: ['副图黄线', '持仓量（ccl）走势：增仓上涨=新资金进场；减仓上涨=空头回补，性质不同'] },
      { cells: ['副图量柱', '成交量，红绿随当日涨跌'] },
      {
        cells: [
          '悬停提示',
          '任意 K 线上悬停：OHLC、量、持仓、当日信号、强弱状态、周线三许可（空 AA1 / 多 ZZ1 / 非盘整 TT1）是否通过',
        ],
      },
      {
        cells: [
          '图表操作',
          '默认显示最近 60 根 K 线；<b>按住左右拖动</b>平移历史，<b>鼠标滚轮</b>放大缩小，底部滑块亦可调整范围',
        ],
      },
    ],
  },
]

function Cell({ content }: { content: string | TagSpec[] }) {
  if (typeof content === 'string') {
    // 文档内容为本地静态可信文本，含少量内联样式标签
    return <td dangerouslySetInnerHTML={{ __html: content }} />
  }
  return (
    <td>
      {content.map((t, i) => (
        <span key={i} className="tag" style={{ background: t.bg, color: t.color }}>
          {t.text}
        </span>
      ))}
    </td>
  )
}

/** 信号解读文档弹窗：右上角按钮打开，Esc / 点遮罩关闭 */
export default function LegendModal({ open, onClose, timeframe }: Props) {
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!open) return null

  return (
    <div className="modal-mask" onClick={onClose}>
      <div className="modal-panel legend-doc" onClick={(e) => e.stopPropagation()}>
        <button className="modal-close" onClick={onClose}>✕</button>
        <h2>{timeframe === '4h' ? '4 小时看板标记详解' : '看板标记详解（文华麦语言策略复刻）'}</h2>
        <div className="legend-intro">
          {timeframe === '4h'
            ? '策略逻辑：上一已完成日线定方向 → 4小时 M头/W底形态突破进场 → 盘整过滤 → 持仓量验证资金。红 K 为上涨、蓝 K 为下跌；不含南华指数强弱对比。'
            : '策略逻辑一句话：周线定方向 → M头/W底形态突破进场 → 盘整过滤避震荡 → 对比南华商品指数看强弱 → 持仓量验证资金。鼠标悬停任意 K 线可查看当日全部状态。'}
        </div>
        {(timeframe === '4h' ? SECTIONS.filter((sec) => !sec.title.startsWith('一、') && !sec.title.startsWith('五、')) : SECTIONS).map((sec) => (
          <section key={sec.title}>
            <h3>{sec.title}</h3>
            <table>
              {sec.headers && (
                <thead>
                  <tr>{sec.headers.map((h) => <th key={h}>{h}</th>)}</tr>
                </thead>
              )}
              <tbody>
                {sec.rows.map((row, ri) => (
                  <tr key={ri}>
                    {row.cells.map((c, ci) => <Cell key={ci} content={c} />)}
                  </tr>
                ))}
              </tbody>
            </table>
            {sec.note && <div className="legend-note">{sec.note}</div>}
          </section>
        ))}
        <div className="legend-footer">{timeframe === '4h' ? '数据：米筐 RQData 240 分钟期货行情；信号仅使用已收线 K。' : '数据：米筐 RQData（期货全品种）+ 同花顺 iFinD（南华商品指数 NHCI.SL） · 本看板为测试阶段产物'}</div>
      </div>
    </div>
  )
}
