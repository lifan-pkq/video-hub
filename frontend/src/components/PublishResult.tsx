import { Link } from 'react-router-dom'
import { ArrowRight, CheckCircle, Plus } from '@phosphor-icons/react'
import { Alert, Button, Panel } from '../ui'
import { platformLabel, type Task } from '../api'
import StatusBadge from './StatusBadge'
import TaskLogs from './TaskLogs'

interface Props {
  task: Task
  /** 本次发布目标，元素格式 "platform:account" */
  targets: string[]
  /** 任务进行中（pending/running），此时不允许再发一条 */
  busy: boolean
  onRestart: () => void
}

function targetLabel(key: string): string {
  const i = key.indexOf(':')
  if (i < 0) return key
  return `${platformLabel(key.slice(0, i))} / ${key.slice(i + 1)}`
}

/** 提交成功后的结果页：任务进度 + 各账号状态 + 日志 + 任务中心入口 */
export default function PublishResult({ task, targets, onRestart }: Props) {
  const targetBadge = (key: string) => {
    const result = task.result.targets?.[key]
    if (result) return <StatusBadge status={result.status} />
    if (task.status === 'pending') return <StatusBadge status="pending" />
    if (task.status === 'running') return <StatusBadge status="running" />
    return <StatusBadge status={task.status} />
  }

  return (
    <Panel>
      <div className="mb-4 flex items-start gap-3">
        <CheckCircle size={28} weight="fill" className="mt-0.5 shrink-0 text-success" />
        <div className="min-w-0 flex-1">
          <h2 className="text-[15px] font-medium text-ink-1">发布任务已创建</h2>
          <p className="mt-0.5 break-all text-xs text-ink-3">
            任务 ID：{task.id}（抖音如触发短信验证，会弹出验证码输入框）
          </p>
        </div>
        <div className="flex shrink-0 flex-col items-end gap-2 sm:flex-row">
          <Link to="/tasks">
            <Button variant="ghost" size="sm" icon={<ArrowRight size={13} />}>
              查看任务中心
            </Button>
          </Link>
          <Button variant="secondary" size="sm" icon={<Plus size={13} />} onClick={onRestart}>
            再发一条
          </Button>
        </div>
      </div>

      <div className="mb-3 flex flex-wrap items-center gap-2">
        <StatusBadge status={task.status} />
        <span className="tnum text-[13px] text-ink-3">{task.progress}%</span>
        <div className="flex flex-wrap items-center gap-3 sm:ml-4">
          {targets.map((key) => (
            <span key={key} className="flex items-center gap-1 text-sm">
              <span className="break-all">{targetLabel(key)}</span> {targetBadge(key)}
            </span>
          ))}
        </div>
      </div>

      {task.result.targets && (
        <div className="mb-3 space-y-1">
          {Object.entries(task.result.targets).map(([key, r]) => (
            <Alert key={key} type={r.status === 'success' ? 'success' : 'error'}>
              {`${targetLabel(key)}：${r.message || (r.status === 'success' ? '发布成功' : '发布失败')}`}
            </Alert>
          ))}
        </div>
      )}
      <TaskLogs logs={task.logs} />
    </Panel>
  )
}
