import { useEffect, useRef } from 'react'
import { fetchTasks, getWsUrl, type TaskSummary } from './api'

/** 实时事件（WS 推送或轮询降级合成） */
export type EventMsg =
  | { type: 'snapshot'; tasks: TaskSummary[] }
  | { type: 'task_updated'; task: TaskSummary }
  | { type: 'ping' }

/** 连续 WS 连接失败多少次后降级为轮询 */
const MAX_WS_FAILURES = 3
/** 降级后轮询间隔 */
const POLL_INTERVAL = 15000
/** 重连退避：1s 起步指数翻倍，30s 封顶 */
const backoff = (attempt: number) => Math.min(30000, 1000 * 2 ** attempt)

function isEventMsg(v: unknown): v is EventMsg {
  if (typeof v !== 'object' || v === null) return false
  const t = (v as { type?: unknown }).type
  return t === 'snapshot' || t === 'task_updated' || t === 'ping'
}

function sameTask(a: TaskSummary, b: TaskSummary): boolean {
  return (
    a.status === b.status &&
    a.progress === b.progress &&
    a.title === b.title &&
    a.needs_verify === b.needs_verify &&
    a.finished_at === b.finished_at
  )
}

/**
 * 订阅全局实时事件：优先 WebSocket（/api/ws），连续 3 次连接失败后
 * 自动降级为 15s 轮询 fetchTasks，并 diff 合成 snapshot / task_updated 事件，
 * 保证无 WS 环境下页面照常工作。组件卸载自动清理。
 */
export function useEvents(handler: (msg: EventMsg) => void): void {
  const handlerRef = useRef(handler)
  handlerRef.current = handler

  useEffect(() => {
    let dead = false
    let ws: WebSocket | null = null
    let retryTimer: number | null = null
    let pollTimer: number | null = null
    let failures = 0
    let attempt = 0
    /** 降级模式：上一次轮询结果，用于 diff */
    let prevTasks = new Map<string, TaskSummary>()

    const emit = (msg: EventMsg) => {
      if (!dead) handlerRef.current(msg)
    }

    const stopPolling = () => {
      if (pollTimer !== null) {
        window.clearInterval(pollTimer)
        pollTimer = null
      }
    }

    const poll = async () => {
      try {
        const tasks = await fetchTasks()
        if (dead) return
        const first = pollTimer !== null && prevTasks.size === 0
        const next = new Map<string, TaskSummary>()
        for (const t of tasks) {
          next.set(t.id, t)
          const prev = prevTasks.get(t.id)
          if (!first && (!prev || !sameTask(prev, t))) emit({ type: 'task_updated', task: t })
        }
        if (first) emit({ type: 'snapshot', tasks })
        prevTasks = next
      } catch {
        // 静默，下一轮重试
      }
    }

    const startPolling = () => {
      if (pollTimer !== null || dead) return
      prevTasks = new Map()
      pollTimer = window.setInterval(poll, POLL_INTERVAL)
      void poll()
    }

    const scheduleRetry = () => {
      if (dead || retryTimer !== null) return
      retryTimer = window.setTimeout(() => {
        retryTimer = null
        connect()
      }, backoff(attempt))
      attempt += 1
    }

    const onWsFailure = () => {
      failures += 1
      if (failures >= MAX_WS_FAILURES) {
        // 放弃 WS，降级轮询
        startPolling()
      } else {
        scheduleRetry()
      }
    }

    const connect = () => {
      if (dead || pollTimer !== null) return
      try {
        ws = new WebSocket(getWsUrl())
      } catch {
        onWsFailure()
        return
      }
      ws.onopen = () => {
        failures = 0
        attempt = 0
      }
      ws.onmessage = (e: MessageEvent<string>) => {
        try {
          const msg: unknown = JSON.parse(e.data)
          if (isEventMsg(msg)) emit(msg)
        } catch {
          // 忽略无法解析的帧
        }
      }
      ws.onerror = () => {
        ws?.close()
      }
      ws.onclose = () => {
        ws = null
        if (!dead) onWsFailure()
      }
    }

    connect()

    return () => {
      dead = true
      stopPolling()
      if (retryTimer !== null) window.clearTimeout(retryTimer)
      if (ws) {
        ws.onclose = null // 阻止卸载后触发重连
        ws.close()
        ws = null
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
}
