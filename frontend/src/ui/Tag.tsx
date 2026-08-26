import type { ReactNode } from 'react'

export type TagColor = 'accent' | 'green' | 'red' | 'orange' | 'gray' | 'blue'

const COLORS: Record<TagColor, string> = {
  accent: 'bg-accent-soft text-accent',
  green: 'bg-success-soft text-success',
  red: 'bg-danger-soft text-danger',
  orange: 'bg-warning-soft text-warning',
  gray: 'bg-fill-deep text-ink-3',
  blue: 'bg-[rgba(59,130,246,0.14)] text-[#60a5fa]',
}

interface Props {
  color?: TagColor
  icon?: ReactNode
  children: ReactNode
  className?: string
}

export default function Tag({ color = 'gray', icon, children, className = '' }: Props) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-xs font-medium leading-4 ${COLORS[color]} ${className}`}
    >
      {icon}
      {children}
    </span>
  )
}
