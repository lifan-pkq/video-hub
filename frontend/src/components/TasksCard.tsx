import type { KeyboardEvent, ReactNode } from 'react'
import { Broadcast, CaretDown, DownloadSimple, MagicWand, Scissors, Sparkle } from '@phosphor-icons/react'
import { formatElapsed, formatRelative, type Task, type TaskSummary } from '../api'
import { Button, Progress, Tag } from '../ui'
import type { TagColor } from '../ui/Tag'
import StatusBadge from './StatusBadge'
import TaskLogs from './TaskLogs'

export const TYPE_META: Record<TaskSummary['type'], { label: string; color: TagColor; icon: (size: number) => ReactNode }> = {
  download: { label: '视频下载', color: 'blue', icon: (s) => <DownloadSimple size={s} /> },
  edit: { label: '智能剪辑', color: 'orange', icon: (s) => <Scissors size={s} /> },
  narrate: { label: '历史解说', color: 'accent', icon: (s) => <MagicWand size={s} /> },
  ai: { label: 'AI 生成', color: 'blue', icon: (s) => <Sparkle size={s} /> },
  record: { label: '直播录制', color: 'orange', icon: (s) => <Broadcast size={s} /> },
}

interface Props {
  task: TaskSummary
  /** 当前时间戳（进行中任务耗时跳动用） */
  now: number
  expanded: boolean
  /** 展开时的任务详情（含日志），null = 加载中 */
  detail: Task | null
  cancelling: boolean
  onToggle: (task: TaskSummary) => void
  onOpen: (task: TaskSummary) => void
  onCancel: (task: TaskSummary) => void
  onRetry?: (task: TaskSummary) => void
  retrying?: boolean
}

/** 任务耗时：终态用 finished_at，否则用当前时间 */
function elapsedSeconds(task: TaskSummary, now: number): number {
  const start = Date.parse(task.created_at)
  if (Number.isNaN(start)) return 0
  const end = task.finished_at ? Date.parse(task.finished_at) : now
  return Math.max(0, (end - start) / 1000)
}

function progressStatus(t: TaskSummary): 'normal' | 'success' | 'error' {
  if (t.status === 'failed') return 'error'
  if (t.status === 'success' || t.status === 'partial') return 'success'
  return 'normal'
}

/** 两行加载骨架：首行全宽、末行 60% */
export function TaskRowSkeleton() {
  return (
    <div className="space-y-3 py-1">
      {['100%', '60%'].map((w, i) => (
        <div key={i} className="skeleton" style={{ width: w, height: 14 }} />
      ))}
    </div>
  )
}

/** 任务卡片：类型 Tag + 标题 + 实时进度条 + 状态徽标 + 耗时 + 展开日志 + 取消 */
export default function TasksCard({ task, now, expanded, detail, cancelling, onToggle, onOpen, onCancel, onRetry, retrying }: Props) {
  const meta = TYPE_META[task.type] ?? TYPE_META.download
  const running = task.status === 'pending' || task.status === 'running'

  return (
    <div className="vh-card px-4 py-3">
      <div
        role="button"
        tabIndex={0}
        onClick={() => onOpen(task)}
        onKeyDown={(e: KeyboardEvent<HTMLDivElement>) => {
          if (e.key === 'Enter') onOpen(task)
        }}
        className="flex cursor-pointer items-center gap-3"
      >
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-fill text-ink-2">
          {meta.icon(16)}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <Tag color={meta.color}>{meta.label}</Tag>
            <span className="truncate text-[14px] text-ink-1">{task.title}</span>
          </div>
          {/* 进行中任务进度条平滑动画（transition-all duration-500） */}
          <div className="mt-2 flex items-center gap-2">
            <Progress percent={task.progress / 100} status={progressStatus(task)} className="max-w-[240px] flex-1 [&>div]:duration-500" />
            <span className="tnum shrink-0 text-xs text-ink-3">{Math.round(task.progress)}%</span>
          </div>
          <div className="mt-1.5 text-xs text-ink-4">
            {formatRelative(task.created_at)} · 耗时 <span className="tnum">{formatElapsed(elapsedSeconds(task, now))}</span>
          </div>
          {(task.status === 'failed' || task.status === 'partial') && task.error?.message && (
            <p className="mt-1 text-[12px] leading-5 text-danger">
              {task.error.message}
              {task.error.action ? <span className="mt-0.5 block text-ink-3">{task.error.action}</span> : null}
            </p>
          )}
        </div>
        <div className="flex shrink-0 flex-col items-end gap-2 sm:flex-row sm:items-center">
          <StatusBadge status={task.status} />
          {!running && onRetry && (task.status === 'failed' || task.status === 'cancelled') && (task.type === 'download' || task.kind === 'edit.douyin') && (
            <Button
              size="sm"
              variant="ghost"
              loading={retrying}
              onClick={(e) => {
                e.stopPropagation()
                onRetry(task)
              }}
            >
              重试
            </Button>
          )}
          {running && (
            <Button
              size="sm"
              variant="ghost"
              loading={cancelling}
              onClick={(e) => {
                e.stopPropagation()
                onCancel(task)
              }}
            >
              取消
            </Button>
          )}
          <button
            type="button"
            className={`shrink-0 rounded p-1 text-ink-4 transition-transform duration-200 hover:bg-fill ${expanded ? 'rotate-180' : ''}`}
            onClick={(e) => { e.stopPropagation(); onToggle(task) }}
            aria-label={expanded ? '收起日志' : '展开日志'}
            title={expanded ? '收起日志' : '展开日志'}
          >
            <CaretDown size={14} />
          </button>
        </div>
      </div>

      {expanded && (
        <div className="border-t border-line-soft mt-3 pt-3">
          {!detail ? (
            <TaskRowSkeleton />
          ) : (
            <div className="space-y-3">
              <TaskLogs logs={detail.logs} />
              {detail.logs.length === 0 && <p className="text-xs text-ink-4">暂无日志输出</p>}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
