import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { Link } from 'react-router-dom'
import {
  CaretDown,
  ClockCounterClockwise,
  DownloadSimple,
  PaperPlaneTilt,
} from '@phosphor-icons/react'
import {
  errorMessage,
  fetchTask,
  formatRelative,
  platformLabel,
  type Task,
  type TaskSummary,
} from '../api'
import { Alert, Button, EmptyState, PageHeader, toast } from '../ui'
import StatusBadge from '../components/StatusBadge'
import TaskLogs from '../components/TaskLogs'
import { useTaskCenter } from '../context/TaskCenter'

type Filter = 'all' | 'running' | 'done' | 'failed'

const FILTERS: { key: Filter; label: string }[] = [
  { key: 'all', label: '全部' },
  { key: 'running', label: '进行中' },
  { key: 'done', label: '已完成' },
  { key: 'failed', label: '失败' },
]

const TYPE_LABEL: Record<TaskSummary['type'], string> = {
  download: '视频下载',
  publish: '视频发布',
  edit: '剪辑拼接',
}

function matchFilter(t: TaskSummary, f: Filter): boolean {
  if (f === 'running') return t.status === 'pending' || t.status === 'running'
  if (f === 'done') return t.status === 'success' || t.status === 'partial'
  if (f === 'failed') return t.status === 'failed'
  return true
}

/** 两行加载骨架：首行全宽、末行 60% */
function RowSkeleton() {
  return (
    <div className="space-y-3 py-1">
      {['100%', '60%'].map((w, i) => (
        <div key={i} className="skeleton" style={{ width: w, height: 14 }} />
      ))}
    </div>
  )
}

export default function TasksPage() {
  const { summaries: tasks, loading, error, refreshTasks, openVerification } = useTaskCenter()
  const [filter, setFilter] = useState<Filter>('all')
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [detail, setDetail] = useState<Task | null>(null)
  const expandedIdRef = useRef<string | null>(null)
  expandedIdRef.current = expandedId

  useEffect(() => {
    if (!expandedId) return
    let dead = false
    let first = true
    let timer = 0
    const taskId = expandedId
    const tick = async () => {
      try {
        const next = await fetchTask(taskId)
        if (!dead && expandedIdRef.current === taskId) setDetail(next)
      } catch (cause) {
        if (first && !dead && expandedIdRef.current === taskId) {
          toast.error(`加载任务详情失败：${errorMessage(cause)}`)
        }
      } finally {
        first = false
        if (!dead) timer = window.setTimeout(tick, 3000)
      }
    }
    void tick()
    return () => {
      dead = true
      window.clearTimeout(timer)
    }
  }, [expandedId])

  const toggle = (t: TaskSummary) => {
    setExpandedId(expandedId === t.id ? null : t.id)
    setDetail(null)
  }

  const filtered = tasks.filter((t) => matchFilter(t, filter))
  const expandedDetail = detail?.id === expandedId ? detail : null

  return (
    <div>
      <PageHeader title="任务中心" desc="下载与发布任务的实时状态，每 3 秒自动刷新" />

      {/* 过滤 */}
      <div className="mb-4 flex items-center gap-2">
        {FILTERS.map((f) => {
          const active = filter === f.key
          return (
            <button
              key={f.key}
              type="button"
              onClick={() => setFilter(f.key)}
              className={`rounded-full px-3.5 py-1.5 text-[13px] transition-colors ${
                active
                  ? 'bg-accent-soft font-medium text-accent'
                  : 'text-ink-3 hover:bg-fill hover:text-ink-1'
              }`}
            >
              {f.label}
            </button>
          )
        })}
      </div>

      {loading ? (
        <div className="space-y-3">
          {[0, 1, 2].map((i) => (
            <div key={i} className="vh-card p-4">
              <RowSkeleton />
            </div>
          ))}
        </div>
      ) : error && tasks.length === 0 ? (
        <Alert type="error">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <span>任务加载失败：{error}</span>
            <Button size="sm" onClick={() => void refreshTasks()}>重试</Button>
          </div>
        </Alert>
      ) : filtered.length === 0 ? (
        <div className="vh-card">
          <EmptyState
            icon={<ClockCounterClockwise size={26} />}
            title="暂无任务"
            desc="从视频下载或发布视频页发起任务后，会出现在这里"
            action={
              <Link to="/download">
                <Button variant="primary">去下载</Button>
              </Link>
            }
          />
        </div>
      ) : (
        <div className="space-y-3">
          {filtered.map((t, i) => {
            const expanded = expandedId === t.id
            return (
              <div
                key={t.id}
                className="vh-card enter-item px-4 py-3"
                style={{ animationDelay: `${Math.min(i * 40, 300)}ms` }}
              >
                <div
                  role="button"
                  tabIndex={0}
                  onClick={() => toggle(t)}
                  onKeyDown={(e: KeyboardEvent<HTMLDivElement>) => {
                    if (e.key === 'Enter') toggle(t)
                  }}
                  className="flex cursor-pointer items-center gap-3"
                >
                  <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-fill text-ink-2">
                    {t.type === 'download' ? (
                      <DownloadSimple size={16} />
                    ) : (
                      <PaperPlaneTilt size={16} />
                    )}
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-[14px] text-ink-1">{t.title}</div>
                    <div className="mt-0.5 text-xs text-ink-4">
                      {TYPE_LABEL[t.type]} · {formatRelative(t.created_at)} · 进度{' '}
                      <span className="tnum">{Math.round(t.progress)}%</span>
                      {t.needs_verify && <span className="ml-2 text-accent">等待短信验证码</span>}
                    </div>
                  </div>
                  {t.needs_verify && (
                    <Button
                      size="sm"
                      variant="primary"
                      onClick={(event) => {
                        event.stopPropagation()
                        openVerification(t.id)
                      }}
                      onKeyDown={(event) => event.stopPropagation()}
                    >
                      待验证
                    </Button>
                  )}
                  <StatusBadge status={t.status} />
                  <span
                    className={`shrink-0 text-ink-4 transition-transform duration-200 ${
                      expanded ? 'rotate-180' : ''
                    }`}
                  >
                    <CaretDown size={14} />
                  </span>
                </div>

                {expanded && (
                  <div className="border-t border-line-soft mt-3 pt-3">
                    {!expandedDetail ? (
                      <RowSkeleton />
                    ) : (
                      <div className="space-y-3">
                        {expandedDetail.result.targets &&
                          Object.keys(expandedDetail.result.targets).length > 0 && (
                            <div className="space-y-1.5">
                              {Object.entries(expandedDetail.result.targets).map(([key, r]) => {
                                const [platform, account] = key.split(':')
                                return (
                                  <div key={key} className="flex items-center gap-2 text-[13px]">
                                    <span className="text-ink-2">
                                      {platformLabel(platform)} / {account}
                                    </span>
                                    <StatusBadge status={r.status} />
                                    {r.message && (
                                      <span className="truncate text-xs text-ink-4">{r.message}</span>
                                    )}
                                  </div>
                                )
                              })}
                            </div>
                          )}
                        <TaskLogs logs={expandedDetail.logs} />
                        {expandedDetail.logs.length === 0 && (
                          <p className="text-xs text-ink-4">暂无日志输出</p>
                        )}
                      </div>
                    )}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
