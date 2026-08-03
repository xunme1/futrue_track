import { useEffect, useMemo, useRef, useState } from 'react'
import type { ContractInfo } from '../types'

interface Props {
  open: boolean
  contracts: ContractInfo[]
  activeKey: string | null
  onSelect: (key: string) => void
  onClose: () => void
}

/**
 * 品种选择抽屉面板：点击工具栏「品种 ▾」展开，覆盖在图表上方。
 * 按板块分组 + 搜索过滤；has_data=false 置灰；extra 品种带角标。
 */
export default function SymbolPicker({ open, contracts, activeKey, onSelect, onClose }: Props) {
  const [keyword, setKeyword] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (open) {
      setKeyword('')
      // 展开后自动聚焦搜索框
      setTimeout(() => inputRef.current?.focus(), 50)
    }
  }, [open])

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  const groups = useMemo(() => {
    const kw = keyword.trim().toLowerCase()
    const filtered = kw
      ? contracts.filter((c) =>
          c.name.toLowerCase().includes(kw) ||
          c.key.toLowerCase().includes(kw) ||
          c.symbol.toLowerCase().includes(kw))
      : contracts
    const map = new Map<string, ContractInfo[]>()
    for (const c of filtered) {
      const arr = map.get(c.category) ?? []
      arr.push(c)
      map.set(c.category, arr)
    }
    return [...map.entries()]
  }, [contracts, keyword])

  if (!open) return null

  return (
    <div className="picker-mask" onClick={onClose}>
      <div className="picker-panel" onClick={(e) => e.stopPropagation()}>
        <div className="picker-head">
          <input
            ref={inputRef}
            className="picker-search"
            placeholder="搜索品种（中文名 / 代码）…"
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
          />
          <button className="picker-close" onClick={onClose}>✕</button>
        </div>
        <div className="picker-body">
          {groups.length === 0 && <div className="picker-empty">没有匹配的品种</div>}
          {groups.map(([category, items]) => (
            <div key={category} className="picker-group">
              <div className="picker-cat">{category}</div>
              <div className="picker-items">
                {items.map((c) => (
                  <button
                    key={c.key}
                    className={
                      'picker-item' +
                      (c.key === activeKey ? ' active' : '') +
                      (c.has_data ? '' : ' disabled')
                    }
                    disabled={!c.has_data}
                    title={c.has_data ? c.symbol : `${c.symbol}（暂无数据）`}
                    onClick={() => onSelect(c.key)}
                  >
                    {c.name}
                    {c.extra && <span className="picker-extra">池外</span>}
                  </button>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
