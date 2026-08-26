import { CircleNotch } from '@phosphor-icons/react'
import { Tag } from '../ui'
import type { TaskStatus } from '../api'

const STATUS_STYLE: Record<string, { label: string; color: 'gray' | 'accent' | 'green' | 'red' | 'orange' }> = {
  pending: { label: '等待', color: 'gray' },
  running: { label: '运行中', color: 'accent' },
  success: { label: '成功', color: 'green' },
  failed: { label: '失败', color: 'red' },
  partial: { label: '部分成功', color: 'orange' },
}

export default function StatusBadge({ status }: { status: TaskStatus | string }) {
  const s = STATUS_STYLE[status] ?? { label: status, color: 'gray' as const }
  return (
    <Tag color={s.color} icon={status === 'running' ? <CircleNotch size={11} className="spin" /> : undefined}>
      {s.label}
    </Tag>
  )
}
