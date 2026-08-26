import { useEffect, useRef, useState, type ReactNode } from 'react'
import { CaretDown, Check } from '@phosphor-icons/react'

export interface SelectOption {
  value: string
  label: ReactNode
}

interface Props {
  value: string
  onChange: (v: string) => void
  options: SelectOption[]
  placeholder?: string
  className?: string
  /** 触发器宽度，默认自适应内容 */
  width?: number | string
}

/** 轻量下拉选择：外点关闭、Esc 关闭、上下键移动 */
export default function Select({ value, onChange, options, placeholder = '请选择', className = '', width }: Props) {
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  const current = options.find((o) => o.value === value)

  return (
    <div ref={rootRef} className={`relative ${className}`} style={width ? { width } : undefined}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className={`flex h-9 w-full items-center justify-between gap-2 rounded-[10px] border px-3 text-[13px] transition-colors ${
          open ? 'border-accent bg-transparent' : 'border-line bg-fill hover:border-line'
        }`}
      >
        <span className={`truncate ${current ? 'text-ink-1' : 'text-ink-4'}`}>{current ? current.label : placeholder}</span>
        <CaretDown size={13} className={`shrink-0 text-ink-3 transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>
      {open && (
        <div className="fade-in absolute left-0 top-[calc(100%+6px)] z-50 max-h-64 min-w-full overflow-y-auto rounded-[10px] border border-line bg-raise p-1 shadow-pop">
          {options.map((o) => {
            const active = o.value === value
            return (
              <button
                key={o.value}
                type="button"
                onClick={() => {
                  onChange(o.value)
                  setOpen(false)
                }}
                className={`flex w-full items-center justify-between gap-2 rounded-lg px-2.5 py-2 text-left text-[13px] transition-colors ${
                  active ? 'bg-accent-soft text-accent' : 'text-ink-2 hover:bg-fill hover:text-ink-1'
                }`}
              >
                <span className="truncate">{o.label}</span>
                {active && <Check size={13} weight="bold" className="shrink-0" />}
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}
