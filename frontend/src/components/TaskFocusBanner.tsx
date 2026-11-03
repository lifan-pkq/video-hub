import { useEffect, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { ClockCounterClockwise, X } from '@phosphor-icons/react'
import { fetchTask, type Task } from '../api'
import { Button, Progress } from '../ui'
import StatusBadge from './StatusBadge'

export default function TaskFocusBanner() {
  const [params, setParams] = useSearchParams()
  const navigate = useNavigate()
  const taskId = params.get('task')
  const [task, setTask] = useState<Task | null>(null)

  useEffect(() => {
    setTask(null)
    if (!taskId) return
    let dead = false
    const tick = async () => {
      try {
        const detail = await fetchTask(taskId)
        if (!dead) setTask(detail)
        if (!dead && (detail.status === 'pending' || detail.status === 'running')) {
          timer = window.setTimeout(tick, 2000)
        }
      } catch {
        if (!dead) timer = window.setTimeout(tick, 5000)
      }
    }
    let timer = window.setTimeout(tick, 0)
    return () => {
      dead = true
      window.clearTimeout(timer)
    }
  }, [taskId])

  if (!taskId || !task) return null

  const running = task.status === 'pending' || task.status === 'running'
  const close = () => {
    const next = new URLSearchParams(params)
    next.delete('task')
    setParams(next, { replace: true })
  }

  return (
    <aside className="task-focus" aria-live="polite">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-[11px] text-ink-4">
            <ClockCounterClockwise size={14} />
            {running ? '任务进行中' : '任务已结束'}
            <StatusBadge status={task.status} />
          </div>
          <div className="mt-1 truncate text-[13px] font-medium text-ink-1">{task.title}</div>
        </div>
        <button type="button" className="icon-button h-6 w-6" onClick={close} aria-label="关闭任务进度"><X size={14} /></button>
      </div>
      <div className="mt-2 flex items-center gap-2">
        <Progress percent={task.progress / 100} status={task.status === 'failed' ? 'error' : task.status === 'success' ? 'success' : 'normal'} className="flex-1" />
        <span className="tnum text-[11px] text-ink-3">{Math.round(task.progress)}%</span>
      </div>
      <p className="mt-1.5 truncate text-[10px] text-ink-4">
        {task.status === 'success' ? '任务已完成' : task.status === 'failed' ? '任务失败' : (task.logs?.[task.logs.length - 1] || '等待任务输出')}
      </p>
      <div className="mt-2 flex justify-end gap-2">
        <Button size="sm" variant="ghost" onClick={() => navigate('/tasks')}>任务中心</Button>
      </div>
    </aside>
  )
}
