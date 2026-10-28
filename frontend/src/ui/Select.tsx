import { useEffect, useId, useRef, useState, type ReactNode } from 'react'
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
  const [activeIndex, setActiveIndex] = useState(0)
  const rootRef = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const listboxId = useId()

  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [open])

  const current = options.find((o) => o.value === value)
  const openMenu = () => {
    const selectedIndex = options.findIndex((o) => o.value === value)
    setActiveIndex(selectedIndex >= 0 ? selectedIndex : 0)
    setOpen(true)
  }
  const selectOption = (index: number) => {
    const option = options[index]
    if (!option) return
    onChange(option.value)
    setOpen(false)
    triggerRef.current?.focus()
  }

  return (
    <div ref={rootRef} className={`relative ${className}`} style={width ? { width } : undefined}>
      <button
        ref={triggerRef}
        type="button"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listboxId : undefined}
        aria-activedescendant={open && options[activeIndex] ? `${listboxId}-${activeIndex}` : undefined}
        onClick={() => (open ? setOpen(false) : openMenu())}
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
            e.preventDefault()
            if (!open) {
              openMenu()
            } else if (options.length > 0) {
              const delta = e.key === 'ArrowDown' ? 1 : -1
              setActiveIndex((index) => (index + delta + options.length) % options.length)
            }
          } else if (e.key === 'Enter') {
            e.preventDefault()
            if (open) selectOption(activeIndex)
            else openMenu()
          } else if (e.key === 'Escape' && open) {
            e.preventDefault()
            e.stopPropagation()
            setOpen(false)
          }
        }}
        className={`flex h-9 w-full items-center justify-between gap-2 rounded-[10px] border px-3 text-[13px] transition-colors ${
          open ? 'border-accent bg-transparent' : 'border-line bg-fill hover:border-line'
        }`}
      >
        <span className={`truncate ${current ? 'text-ink-1' : 'text-ink-4'}`}>{current ? current.label : placeholder}</span>
        <CaretDown size={13} className={`shrink-0 text-ink-3 transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>
      {open && (
        <div
          id={listboxId}
          role="listbox"
          aria-label={placeholder}
          className="fade-in absolute left-0 top-[calc(100%+6px)] z-50 max-h-64 min-w-full overflow-y-auto rounded-[10px] border border-line bg-raise p-1 shadow-pop"
        >
          {options.map((o, index) => {
            const selected = o.value === value
            const highlighted = index === activeIndex
            return (
              <button
                key={o.value}
                id={`${listboxId}-${index}`}
                type="button"
                role="option"
                aria-selected={selected}
                tabIndex={-1}
                onMouseEnter={() => setActiveIndex(index)}
                onClick={() => selectOption(index)}
                className={`flex w-full items-center justify-between gap-2 rounded-lg px-2.5 py-2 text-left text-[13px] transition-colors ${
                  selected
                    ? 'bg-accent-soft text-accent'
                    : highlighted
                      ? 'bg-fill text-ink-1'
                      : 'text-ink-2 hover:bg-fill hover:text-ink-1'
                }`}
              >
                <span className="truncate">{o.label}</span>
                {selected && <Check size={13} weight="bold" className="shrink-0" />}
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}
