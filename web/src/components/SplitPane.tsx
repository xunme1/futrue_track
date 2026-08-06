import { type ReactNode, useEffect, useRef, useState } from 'react'

const STORAGE_KEY = 'futures-dashboard:left-pane-width'
const MIN_LEFT = 260
const MIN_RIGHT = 480
const DIVIDER_WIDTH = 8

interface Props {
  left: ReactNode
  right: ReactNode
  collapsed?: boolean
}

function storedWidth(): number | null {
  const value = Number(localStorage.getItem(STORAGE_KEY))
  return Number.isFinite(value) && value >= MIN_LEFT ? value : null
}

/** 桌面端可拖拽双栏；窄屏由 CSS 隐藏左栏并保留右栏。 */
export default function SplitPane({ left, right, collapsed = false }: Props) {
  const rootRef = useRef<HTMLDivElement>(null)
  const dragPointer = useRef<number | null>(null)
  const [leftWidth, setLeftWidth] = useState<number | null>(storedWidth)

  const clamp = (value: number) => {
    const width = rootRef.current?.clientWidth ?? 0
    return Math.max(MIN_LEFT, Math.min(value, Math.max(MIN_LEFT, width - MIN_RIGHT - DIVIDER_WIDTH)))
  }

  const updateWidth = (value: number, persist = false) => {
    const next = clamp(value)
    setLeftWidth(next)
    if (persist) localStorage.setItem(STORAGE_KEY, String(Math.round(next)))
  }

  useEffect(() => {
    const onResize = () => {
      if (leftWidth !== null) setLeftWidth(clamp(leftWidth))
    }
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [leftWidth])

  const beginDrag = (event: React.PointerEvent<HTMLDivElement>) => {
    event.preventDefault()
    dragPointer.current = event.pointerId
    event.currentTarget.setPointerCapture(event.pointerId)
  }

  const drag = (event: React.PointerEvent<HTMLDivElement>) => {
    if (event.pointerId !== dragPointer.current) return
    const rect = rootRef.current?.getBoundingClientRect()
    if (rect) updateWidth(event.clientX - rect.left)
  }

  const endDrag = (event: React.PointerEvent<HTMLDivElement>) => {
    if (event.pointerId !== dragPointer.current) return
    const rect = rootRef.current?.getBoundingClientRect()
    if (rect) updateWidth(event.clientX - rect.left, true)
    dragPointer.current = null
  }

  return (
    <div ref={rootRef} className={`split-pane${collapsed ? ' collapsed' : ''}`}>
      {!collapsed && <>
        <div className="split-left" style={{ width: leftWidth ?? '33.333%' }}>{left}</div>
        <div
          className="split-divider"
          role="separator"
          aria-orientation="vertical"
          onPointerDown={beginDrag}
          onPointerMove={drag}
          onPointerUp={endDrag}
          onPointerCancel={endDrag}
        />
      </>}
      <div className="split-right">{right}</div>
    </div>
  )
}
