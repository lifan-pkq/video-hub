import { Check, Minus } from '@phosphor-icons/react'

interface Props {
  checked: boolean
  onChange: (checked: boolean) => void
  indeterminate?: boolean
  disabled?: boolean
  label?: string
}

export default function Checkbox({ checked, onChange, indeterminate, disabled, label }: Props) {
  const box = (
    <span
      aria-hidden
      className={`flex h-4 w-4 shrink-0 items-center justify-center rounded-[5px] border transition-colors ${
        checked || indeterminate ? 'border-accent bg-accent text-white' : 'border-line bg-fill'
      } ${disabled ? 'opacity-50' : ''}`}
    >
      {indeterminate ? (
        <Minus size={10} weight="bold" />
      ) : checked ? (
        <Check size={10} weight="bold" />
      ) : null}
    </span>
  )
  return (
    <label
      className={`inline-flex select-none items-center gap-2 text-[13px] text-ink-2 ${disabled ? 'cursor-not-allowed opacity-60' : 'cursor-pointer'}`}
    >
      <input
        type="checkbox"
        className="sr-only"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
      />
      {box}
      {label}
    </label>
  )
}
