import { useEffect, useMemo, useRef, useState } from 'react'
import { Bell, CheckCircle, Checks, Info, Trash, WarningCircle } from '@phosphor-icons/react'
import { formatRelative } from '../api'
import type { EventMsg } from '../events'

/* ================= 通知数据模型 ================= */

export interface AppNotification {
  id: string
  taskId: string
  /** 通知标题 = 任务标题 */
  title: string
  /** success = 成功/部分成功；failed = 失败；info = 其它终态 */
  kind: 'success' | 'failed' | 'info'
  /** 进入终态时间（ISO 字符串） */
  time: string
  read: boolean
}

const STORAGE_KEY = 'video-hub-notifications'
const MAX_NOTIFICATIONS = 50

function loadStored(): AppNotification[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return []
    const list: unknown = JSON.parse(raw)
    if (!Array.isArray(list)) return []
    return list.filter(
      (n): n is AppNotification =>
        typeof n === 'object' && n !== null && typeof (n as AppNotification).id === 'string',
    )
  } catch {
    return []
  }
}

function persist(list: AppNotification[]) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(list.slice(0, MAX_NOTIFICATIONS)))
  } catch {
    // 忽略持久化失败
  }
}

function kindOf(status: string): AppNotification['kind'] {
  if (status === 'success' || status === 'partial') return 'success'
  if (status === 'failed') return 'failed'
  return 'info'
}

/* ================= useNotifications ================= */

export interface NotificationsApi {
  notifications: AppNotification[]
  unreadCount: number
  /** 全部标记已读 */
  markAllRead: () => void
  /** 清空通知 */
  clear: () => void
}

/**
 * App 层通知状态管理：消费 useEvents 的最新事件，把进入终态
 * （非 pending/running）的任务转成通知，localStorage 持久化（上限 50 条）。
 * snapshot 事件只用于建立基线，不产生通知（避免刷新后刷屏）。
 */
export function useNotifications(latest: EventMsg | null): NotificationsApi {
  const [notifications, setNotifications] = useState<AppNotification[]>(() => loadStored())
  /** taskId → 上次见到的状态，用于识别"进入终态"这一跳变 */
  const seenRef = useRef<Map<string, string>>(new Map())

  useEffect(() => persist(notifications), [notifications])

  useEffect(() => {
    if (!latest) return
    if (latest.type === 'snapshot') {
      for (const t of latest.tasks) seenRef.current.set(t.id, t.status)
      return
    }
    if (latest.type !== 'task_updated') return
    const task = latest.task
    const prev = seenRef.current.get(task.id)
    seenRef.current.set(task.id, task.status)
    const terminal = task.status !== 'pending' && task.status !== 'running'
    if (!terminal || prev === task.status) return
    setNotifications((list) =>
      [
        {
          id: `${task.id}:${task.status}:${task.finished_at ?? Date.now()}`,
          taskId: task.id,
          title: task.title,
          kind: kindOf(task.status),
          time: task.finished_at ?? new Date().toISOString(),
          read: false,
        },
        ...list,
      ].slice(0, MAX_NOTIFICATIONS),
    )
  }, [latest])

  const unreadCount = useMemo(() => notifications.filter((n) => !n.read).length, [notifications])

  return {
    notifications,
    unreadCount,
    markAllRead: () => setNotifications((list) => list.map((n) => ({ ...n, read: true }))),
    clear: () => setNotifications([]),
  }
}

/* ================= NotifyCenter（受控组件） ================= */

interface Props {
  notifications: AppNotification[]
  /** 全部标记已读 */
  onRead: () => void
  /** 清空通知 */
  onClear: () => void
}

const KIND_ICON = {
  success: <CheckCircle size={16} weight="fill" className="shrink-0 text-success" />,
  failed: <WarningCircle size={16} weight="fill" className="shrink-0 text-danger" />,
  info: <Info size={16} weight="fill" className="shrink-0 text-ink-3" />,
}

/** 顶栏通知中心：铃铛 + 未读红点 + 下拉面板（状态由 useNotifications 托管，经 props 受控传入） */
export default function NotifyCenter({ notifications, onRead, onClear }: Props) {
  const [open, setOpen] = useState(false)
  const wrapRef = useRef<HTMLDivElement>(null)
  const unread = notifications.filter((n) => !n.read).length

  // 点外部 / Esc 关闭
  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  return (
    <div ref={wrapRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="relative flex h-8 w-8 items-center justify-center rounded-lg text-ink-3 transition-colors hover:bg-fill hover:text-ink-1"
        aria-label={unread > 0 ? `通知，${unread} 条未读` : '通知'}
        aria-expanded={open}
      >
        <Bell size={16} weight={unread > 0 ? 'fill' : 'regular'} />
        {unread > 0 && (
          <span className="absolute right-1 top-1 h-2 w-2 rounded-full bg-danger ring-2 ring-card" />
        )}
      </button>

      {open && (
        <div className="fade-in absolute right-0 top-full z-50 mt-2 w-[320px] max-w-[calc(100vw-2rem)] overflow-hidden rounded-2xl border border-line bg-card shadow-pop">
          <div className="flex items-center justify-between border-b border-line-soft px-4 py-3">
            <span className="text-[13px] font-medium text-ink-1">
              通知{unread > 0 && <span className="ml-1.5 text-[11px] text-ink-3">{unread} 条未读</span>}
            </span>
            <div className="flex items-center gap-1">
              <button
                type="button"
                onClick={onRead}
                disabled={unread === 0}
                className="flex h-7 items-center gap-1 rounded-lg px-2 text-[11px] text-ink-3 transition-colors hover:bg-fill hover:text-ink-1 disabled:opacity-40"
              >
                <Checks size={12} /> 全部已读
              </button>
              <button
                type="button"
                onClick={onClear}
                disabled={notifications.length === 0}
                className="flex h-7 items-center gap-1 rounded-lg px-2 text-[11px] text-ink-3 transition-colors hover:bg-fill hover:text-ink-1 disabled:opacity-40"
              >
                <Trash size={12} /> 清空
              </button>
            </div>
          </div>

          <div className="max-h-[50vh] overflow-y-auto">
            {notifications.length === 0 ? (
              <div className="px-4 py-10 text-center text-[12px] text-ink-4">暂无通知</div>
            ) : (
              notifications.map((n) => (
                <div
                  key={n.id}
                  className={`flex items-start gap-2.5 border-b border-line-soft px-4 py-3 last:border-b-0 ${
                    n.read ? 'opacity-55' : ''
                  }`}
                >
                  <span className="mt-0.5">{KIND_ICON[n.kind]}</span>
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-[13px] text-ink-1">{n.title}</div>
                    <div className="mt-0.5 text-[11px] text-ink-4">
                      {n.kind === 'success' ? '已完成' : n.kind === 'failed' ? '失败' : '已结束'} ·{' '}
                      {formatRelative(n.time)}
                    </div>
                  </div>
                  {!n.read && <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-accent" />}
                </div>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  )
}
