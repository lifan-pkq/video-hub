import { forwardRef, type InputHTMLAttributes, type ReactNode } from 'react'
import { X } from '@phosphor-icons/react'

interface Props extends Omit<InputHTMLAttributes<HTMLInputElement>, 'value' | 'onChange' | 'prefix'> {
  value: string
  onChange: (v: string) => void
  prefix?: ReactNode
  suffix?: ReactNode
  allowClear?: boolean
}

const Input = forwardRef<HTMLInputElement, Props>(function Input(
  { value, onChange, prefix, suffix, allowClear, className = '', onKeyDown, ...rest },
  ref,
) {
  return (
    <div
      className={`flex h-9 items-center gap-2 rounded-[10px] border border-line bg-fill px-3 transition-colors focus-within:border-accent focus-within:bg-transparent ${className}`}
    >
      {prefix && <span className="flex shrink-0 items-center text-ink-3">{prefix}</span>}
      <input
        ref={ref}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={onKeyDown}
        className="h-full min-w-0 flex-1 bg-transparent text-[13px] text-ink-1 outline-none placeholder:text-ink-4"
        {...rest}
      />
      {allowClear && value && (
        <button
          type="button"
          tabIndex={-1}
          aria-label="清空"
          onClick={() => onChange('')}
          className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-fill-deep text-ink-3 hover:text-ink-1"
        >
          <X size={10} weight="bold" />
        </button>
      )}
      {suffix && <span className="flex shrink-0 items-center text-ink-4">{suffix}</span>}
    </div>
  )
})

export default Input
