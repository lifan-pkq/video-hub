import type { ReactNode } from 'react'

interface Props {
  label: string
  value: ReactNode
  /** 数值后缀/副信息 */
  sub?: ReactNode
  icon?: ReactNode
}

/** 仪表盘统计卡 */
export default function StatCard({ label, value, sub, icon }: Props) {
  return (
    <div className="vh-card p-5">
      <div className="flex items-center justify-between">
        <span className="text-[13px] text-ink-3">{label}</span>
        {icon && <span className="text-ink-4">{icon}</span>}
      </div>
      <div className="stat-value mt-2 text-[28px] leading-none text-ink-1">{value}</div>
      {sub && <div className="mt-2 text-xs text-ink-3">{sub}</div>}
    </div>
  )
}
