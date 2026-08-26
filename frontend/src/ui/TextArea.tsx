import type { TextareaHTMLAttributes } from 'react'

interface Props extends Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, 'value' | 'onChange'> {
  value: string
  onChange: (v: string) => void
}

export default function TextArea({ value, onChange, className = '', rows = 3, ...rest }: Props) {
  return (
    <textarea
      value={value}
      onChange={(e) => onChange(e.target.value)}
      rows={rows}
      className={`w-full resize-y rounded-[10px] border border-line bg-fill px-3 py-2.5 text-[13px] leading-relaxed text-ink-1 outline-none transition-colors placeholder:text-ink-4 focus:border-accent focus:bg-transparent ${className}`}
      {...rest}
    />
  )
}
