import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import { errorMessage, fetchTask, fetchTasks, type Task, type TaskSummary } from '../api'

const ACTIVE_TASK_KEY = 'video-hub:active-task'
const POLL_INTERVAL = 3000

interface TaskCenterValue {
  summaries: TaskSummary[]
  details: Record<string, Task>
  task: Task | null
  activeTaskId: string | null
  runningTasks: number
  loading: boolean
  error: string | null
  refreshTasks: () => Promise<void>
  trackTask: (taskId: string) => void
}

const TaskCenterContext = createContext<TaskCenterValue | null>(null)

function isActive(task: TaskSummary): boolean {
  return task.status === 'pending' || task.status === 'running'
}

function readActiveTaskId(): string | null {
  try {
    return window.localStorage.getItem(ACTIVE_TASK_KEY)
  } catch {
    return null
  }
}

export function TaskCenterProvider({ children }: { children: ReactNode }) {
  const [summaries, setSummaries] = useState<TaskSummary[]>([])
  const [details, setDetails] = useState<Record<string, Task>>({})
  const [activeTaskId, setActiveTaskId] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const activeTaskIdRef = useRef<string | null>(null)
  const initializedRef = useRef(false)
  const mountedRef = useRef(true)
  const inFlightRef = useRef<Promise<void> | null>(null)

  const selectActiveTask = useCallback((taskId: string | null) => {
    activeTaskIdRef.current = taskId
    setActiveTaskId(taskId)
    try {
      if (taskId) window.localStorage.setItem(ACTIVE_TASK_KEY, taskId)
      else window.localStorage.removeItem(ACTIVE_TASK_KEY)
    } catch {
      // 内存中的任务跟踪仍然有效。
    }
  }, [])

  const refreshTasks = useCallback(async () => {
    if (inFlightRef.current) return inFlightRef.current
    const request = (async () => {
      try {
        const list = await fetchTasks()
        if (!mountedRef.current) return
        if (!initializedRef.current) {
          initializedRef.current = true
          const savedId = readActiveTaskId()
          const savedTask = list.find((item) => item.id === savedId)
          const running = list.find(isActive)
          selectActiveTask(running?.id ?? savedTask?.id ?? null)
        }
        setSummaries(list)
        const detailIds = new Set(list.filter(isActive).map((item) => item.id))
        if (activeTaskIdRef.current) detailIds.add(activeTaskIdRef.current)
        const fetched = await Promise.all([...detailIds].map(async (taskId) => {
          try {
            return await fetchTask(taskId)
          } catch {
            return null
          }
        }))
        if (!mountedRef.current) return
        setDetails((current) => {
          const next = { ...current }
          fetched.forEach((task) => { if (task) next[task.id] = task })
          return next
        })
        setError(null)
      } catch (cause) {
        if (mountedRef.current) setError(errorMessage(cause))
      } finally {
        if (mountedRef.current) setLoading(false)
      }
    })()
    inFlightRef.current = request
    try {
      await request
    } finally {
      if (inFlightRef.current === request) inFlightRef.current = null
    }
  }, [selectActiveTask])

  useEffect(() => {
    mountedRef.current = true
    let active = true
    let timer = 0
    const tick = async () => {
      await refreshTasks()
      if (active) timer = window.setTimeout(tick, POLL_INTERVAL)
    }
    void tick()
    return () => {
      active = false
      mountedRef.current = false
      window.clearTimeout(timer)
    }
  }, [refreshTasks])

  const trackTask = useCallback((taskId: string) => {
    selectActiveTask(taskId)
    void refreshTasks()
  }, [refreshTasks, selectActiveTask])

  const task = activeTaskId ? details[activeTaskId] ?? null : null
  const runningTasks = summaries.filter(isActive).length

  return (
    <TaskCenterContext.Provider value={{
      summaries,
      details,
      task,
      activeTaskId,
      runningTasks,
      loading,
      error,
      refreshTasks,
      trackTask,
    }}>
      {children}
    </TaskCenterContext.Provider>
  )
}

export function useTaskCenter(): TaskCenterValue {
  const context = useContext(TaskCenterContext)
  if (!context) throw new Error('useTaskCenter must be used within TaskCenterProvider')
  return context
}
