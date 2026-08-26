import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from 'react'
import { CircleNotch } from '@phosphor-icons/react'

export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger' | 'text'

interface Props extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'children'> {
  variant?: ButtonVariant
  size?: 'sm' | 'md'
  loading?: boolean
  icon?: ReactNode
  /** 占满父宽 */
  full?: boolean
  children?: ReactNode
}

const VARIANT: Record<ButtonVariant, string> = {
  primary:
    'bg-accent text-white hover:bg-accent-hover active:bg-accent-active disabled:bg-accent disabled:text-white/70',
  secondary: 'bg-fill-deep text-ink-1 hover:bg-fill disabled:text-ink-3',
  ghost: 'border border-line bg-transparent text-ink-2 hover:bg-fill hover:text-ink-1 disabled:text-ink-4',
  danger: 'bg-danger text-white hover:brightness-110 active:brightness-95 disabled:bg-danger disabled:text-white/70',
  text: 'bg-transparent text-ink-3 hover:bg-fill hover:text-ink-1 disabled:text-ink-4',
}

const SIZE = {
  sm: 'h-7 px-2.5 text-xs gap-1 rounded-lg',
  md: 'h-9 px-4 text-[13px] gap-1.5 rounded-[10px]',
}

const Button = forwardRef<HTMLButtonElement, Props>(function Button(
  { variant = 'secondary', size = 'md', loading, icon, full, className = '', children, disabled, type = 'button', ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      disabled={disabled || loading}
      className={`inline-flex select-none items-center justify-center font-medium transition-all duration-150 active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-60 ${VARIANT[variant]} ${SIZE[size]} ${full ? 'w-full' : ''} ${className}`}
      {...rest}
    >
      {loading ? <CircleNotch className="spin" size={15} weight="bold" /> : icon}
      {children}
    </button>
  )
})

export default Button
