interface Props {
  /** "YYYY-MM-DD HH:mm"，空串 = 未设置 */
  value: string
  onChange: (v: string) => void
  className?: string
}

function toLocalInput(v: string): string {
  return v ? v.replace(' ', 'T') : ''
}

/** 定时选择：包一层原生 datetime-local，输出 "YYYY-MM-DD HH:mm" */
export default function DateTimeInput({ value, onChange, className = '' }: Props) {
  return (
    <input
      type="datetime-local"
      value={toLocalInput(value)}
      onChange={(e) => onChange(e.target.value ? e.target.value.replace('T', ' ') : '')}
      className={`h-9 rounded-[10px] border border-line bg-fill px-3 text-[13px] text-ink-1 outline-none transition-colors focus:border-accent focus:bg-transparent ${className}`}
    />
  )
}
