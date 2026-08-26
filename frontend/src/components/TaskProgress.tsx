import { Progress } from '../ui'
import type { Task } from '../api'
import StatusBadge from './StatusBadge'
import TaskLogs from './TaskLogs'

/** 任务进度 + 状态 + 日志（轮询任务时复用） */
export default function TaskProgress({ task }: { task: Task }) {
  const status =
    task.status === 'failed' ? 'error' : task.status === 'success' || task.status === 'partial' ? 'success' : 'normal'

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <StatusBadge status={task.status} />
        <span className="text-[13px] text-ink-3">{task.title}</span>
      </div>
      <Progress percent={task.progress / 100} status={status} />
      <TaskLogs logs={task.logs} />
    </div>
  )
}
