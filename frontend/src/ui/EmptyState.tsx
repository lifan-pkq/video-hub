import type { ReactNode } from 'react'

interface Props {
  icon?: ReactNode
  title: string
  desc?: string
  action?: ReactNode
}

/** 空状态：图标 + 标题 + 引导操作 */
export default function EmptyState({ icon, title, desc, action }: Props) {
  return (
    <div className="flex flex-col items-center justify-center px-6 py-14 text-center">
      {icon && (
        <div className="mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-fill text-2xl text-ink-3">
          {icon}
        </div>
      )}
      <div className="text-[15px] font-medium text-ink-1">{title}</div>
      {desc && <div className="mt-1.5 max-w-sm text-[13px] text-ink-3">{desc}</div>}
      {action && <div className="mt-5 flex items-center gap-3">{action}</div>}
    </div>
  )
}
