import type { ReactNode } from 'react'
import { CheckCircle, Info, Warning, XCircle } from '@phosphor-icons/react'

export type AlertType = 'info' | 'success' | 'warning' | 'error'

const META: Record<AlertType, { icon: typeof Info; cls: string; iconCls: string }> = {
  info: { icon: Info, cls: 'border-line bg-fill', iconCls: 'text-ink-3' },
  success: { icon: CheckCircle, cls: 'border-transparent bg-success-soft', iconCls: 'text-success' },
  warning: { icon: Warning, cls: 'border-transparent bg-warning-soft', iconCls: 'text-warning' },
  error: { icon: XCircle, cls: 'border-transparent bg-danger-soft', iconCls: 'text-danger' },
}

interface Props {
  type?: AlertType
  children: ReactNode
  className?: string
}

export default function Alert({ type = 'info', children, className = '' }: Props) {
  const m = META[type]
  const Icon = m.icon
  return (
    <div className={`flex items-start gap-2 rounded-[10px] border px-3 py-2.5 text-[13px] leading-relaxed text-ink-2 ${m.cls} ${className}`}>
      <Icon size={16} weight="fill" className={`mt-0.5 shrink-0 ${m.iconCls}`} />
      <div className="min-w-0 break-all">{children}</div>
    </div>
  )
}
