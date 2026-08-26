import type { ReactNode } from 'react'

export interface SegmentOption {
  value: string
  label: ReactNode
}

interface Props {
  value: string
  onChange: (v: string) => void
  options: SegmentOption[]
  className?: string
}

/** 分段选择器（替代 Radio.Group） */
export default function Segment({ value, onChange, options, className = '' }: Props) {
  return (
    <div className={`flex gap-1 rounded-[10px] border border-line bg-fill p-1 ${className}`}>
      {options.map((o) => {
        const active = o.value === value
        return (
          <button
            key={o.value}
            type="button"
            onClick={() => onChange(o.value)}
            className={`flex flex-1 items-center justify-center gap-1.5 whitespace-nowrap rounded-lg px-3 py-1.5 text-[13px] transition-colors ${
              active ? 'bg-card font-medium text-ink-1 shadow-card' : 'text-ink-3 hover:text-ink-1'
            }`}
          >
            {o.label}
          </button>
        )
      })}
    </div>
  )
}
