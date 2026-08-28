import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { ClockCounterClockwise, DownloadSimple } from '@phosphor-icons/react'
import http, {
  errorMessage,
  fetchTask,
  fetchTasks,
  type Task,
  type TaskSummary,
} from '../api'
import { useEvents, type EventMsg } from '../events'
import { Button, EmptyState, PageHeader, Segment, confirmDialog, toast } from '../ui'
import TasksCard, { TaskRowSkeleton } from '../components/TasksCard'

type Filter = 'all' | 'running' | 'done' | 'failed'

const FILTERS: { key: Filter; label: string }[] = [
  { key: 'all', label: '全部' },
  { key: 'running', label: '进行中' },
  { key: 'done', label: '已完成' },
  { key: 'failed', label: '失败' },
]

function matchFilter(t: TaskSummary, f: Filter): boolean {
  if (f === 'running') return t.status === 'pending' || t.status === 'running'
  if (f === 'done') return t.status === 'success' || t.status === 'partial'
  if (f === 'failed') return t.status === 'failed'
  return true
}

/** 兜底全量刷新间隔（实时事件由 useEvents 驱动，此处仅防丢帧） */
const FALLBACK_INTERVAL = 60_000

/**
 * 取消任务：api.ts 暂无该函数，按契约约定用 http 实例就地请求。
 * 后端 POST /api/tasks/{id}/cancel（若后端未实现会 toast 报错，不影响列表）。
 */
async function cancelTask(taskId: string): Promise<{ ok: boolean }> {
  const { data } = await http.post<{ ok: boolean }>(`/tasks/${taskId}/cancel`)
  return data
}

export default function TasksPage() {
  const [tasks, setTasks] = useState<TaskSummary[]>([])
  const [loading, setLoading] = useState<boolean>(true)
  const [filter, setFilter] = useState<Filter>('all')
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [detail, setDetail] = useState<Task | null>(null)
  const [cancellingId, setCancellingId] = useState<string | null>(null)

  // 进行中任务耗时每秒跳动
  const [now, setNow] = useState(() => Date.now())
  const hasRunning = tasks.some((t) => t.status === 'pending' || t.status === 'running')
  useEffect(() => {
    if (!hasRunning) return
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [hasRunning])

  // ================= 实时事件驱动 =================

  const onEvent = useCallback((msg: EventMsg) => {
    if (msg.type === 'snapshot') {
      setTasks(msg.tasks)
      setLoading(false)
    } else if (msg.type === 'task_updated') {
      const task = msg.task
      setTasks((prev) => {
        const idx = prev.findIndex((t) => t.id === task.id)
        if (idx >= 0) {
          const next = [...prev]
          next[idx] = task
          return next
        }
        return [task, ...prev]
      })
    }
  }, [])
  useEvents(onEvent)

  // 60s 兜底刷新：防止 WS / 降级轮询丢帧；也保证首屏在 snapshot 到达前有数据
  useEffect(() => {
    let dead = false
    const tick = async () => {
      try {
        const list = await fetchTasks()
        if (!dead) {
          setTasks(list)
          setLoading(false)
        }
      } catch {
        // 静默，下一轮重试
      }
    }
    void tick()
    const timer = window.setInterval(tick, FALLBACK_INTERVAL)
    return () => {
      dead = true
      window.clearInterval(timer)
    }
  }, [])

  // ================= 展开日志 =================

  const expandedTask = useMemo(
    () => (expandedId ? tasks.find((t) => t.id === expandedId) ?? null : null),
    [tasks, expandedId],
  )

  // 展开时拉详情（含日志）；任务状态/进度变化时同步刷新详情
  useEffect(() => {
    if (!expandedId) {
      setDetail(null)
      return
    }
    let dead = false
    fetchTask(expandedId)
      .then((d) => {
        if (!dead) setDetail(d)
      })
      .catch((e) => {
        if (!dead) toast.error(`加载任务详情失败：${errorMessage(e)}`)
      })
    return () => {
      dead = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [expandedId, expandedTask?.status, expandedTask?.progress])

  const toggle = (t: TaskSummary) => {
    setDetail(null)
    setExpandedId((prev) => (prev === t.id ? null : t.id))
  }

  // ================= 取消任务 =================

  const handleCancel = async (t: TaskSummary) => {
    const ok = await confirmDialog({
      title: '取消任务',
      content: `确定要取消「${t.title}」吗？`,
      okText: '确认取消',
      cancelText: '再想想',
      danger: true,
    })
    if (!ok) return
    setCancellingId(t.id)
    try {
      await cancelTask(t.id)
      toast.success('已请求取消')
    } catch (e) {
      toast.error(`取消失败：${errorMessage(e)}`)
    } finally {
      setCancellingId(null)
    }
  }

  // ================= 派生数据 =================

  const counts = useMemo(() => {
    const c: Record<Filter, number> = { all: tasks.length, running: 0, done: 0, failed: 0 }
    for (const t of tasks) {
      if (matchFilter(t, 'running')) c.running += 1
      else if (matchFilter(t, 'done')) c.done += 1
      else if (matchFilter(t, 'failed')) c.failed += 1
    }
    return c
  }, [tasks])

  const filtered = tasks.filter((t) => matchFilter(t, filter))

  return (
    <div>
      <PageHeader title="任务中心" desc="下载、发布与剪辑任务的实时状态（事件流驱动）" />

      {/* 分组筛选 Tab */}
      <div className="mb-4 overflow-x-auto">
        <Segment
          value={filter}
          onChange={(v) => setFilter(v as Filter)}
          options={FILTERS.map((f) => ({
            value: f.key,
            label: (
              <>
                {f.label} <span className="tnum text-xs">{counts[f.key]}</span>
              </>
            ),
          }))}
          className="min-w-[320px] sm:w-[420px]"
        />
      </div>

      {loading ? (
        <div className="space-y-3">
          {[0, 1, 2].map((i) => (
            <div key={i} className="vh-card p-4">
              <TaskRowSkeleton />
            </div>
          ))}
        </div>
      ) : tasks.length === 0 ? (
        <div className="vh-card">
          <EmptyState
            icon={<ClockCounterClockwise size={26} />}
            title="暂无任务"
            desc="从导入向导下载视频或发布视频后，任务会实时出现在这里"
            action={
              <Link to="/library/import">
                <Button variant="primary" icon={<DownloadSimple size={15} />}>
                  去下载视频
                </Button>
              </Link>
            }
          />
        </div>
      ) : filtered.length === 0 ? (
        <div className="vh-card">
          <EmptyState title="该分组暂无任务" desc="切换到其他分组看看" />
        </div>
      ) : (
        <div className="space-y-3">
          {filtered.map((t, i) => (
            <div key={t.id} className="enter-item" style={{ animationDelay: `${Math.min(i * 40, 300)}ms` }}>
              <TasksCard
                task={t}
                now={now}
                expanded={expandedId === t.id}
                detail={expandedId === t.id ? detail : null}
                cancelling={cancellingId === t.id}
                onToggle={toggle}
                onCancel={handleCancel}
              />
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
