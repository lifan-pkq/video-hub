import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import {
  errorMessage,
  fetchTask,
  fetchTasks,
  submitVerifyCode,
  type Task,
  type TaskSummary,
} from '../api'

const ACTIVE_TASK_KEY = 'video-hub:active-publish-task'
const POLL_INTERVAL = 3000
const VERIFY_TIMEOUT = 30000

export type VerificationPhase = 'idle' | 'submitting' | 'waiting' | 'rejected'

interface VerificationState {
  phase: VerificationPhase
  consumed: boolean
  submittedAt: number
  dismissed: boolean
  error: string | null
}

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
  verificationTask: TaskSummary | null
  verificationPhase: VerificationPhase
  verificationError: string | null
  openVerification: (taskId: string) => void
  dismissVerification: () => void
  submitVerification: (taskId: string, code: string) => Promise<boolean>
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

function storeActiveTaskId(taskId: string | null) {
  try {
    if (taskId) window.localStorage.setItem(ACTIVE_TASK_KEY, taskId)
    else window.localStorage.removeItem(ACTIVE_TASK_KEY)
  } catch {
    // Storage can be unavailable in private browsing; in-memory recovery still works.
  }
}

export function TaskCenterProvider({ children }: { children: ReactNode }) {
  const [summaries, setSummaries] = useState<TaskSummary[]>([])
  const [details, setDetails] = useState<Record<string, Task>>({})
  const [activeTaskId, setActiveTaskId] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [verificationStates, setVerificationStates] = useState<Record<string, VerificationState>>({})
  const [verificationOpenId, setVerificationOpenId] = useState<string | null>(null)
  const activeTaskIdRef = useRef<string | null>(null)
  const initializedRef = useRef(false)
  const mountedRef = useRef(true)
  const inFlightRef = useRef<Promise<void> | null>(null)
  const verificationStatesRef = useRef(verificationStates)
  verificationStatesRef.current = verificationStates

  const selectActiveTask = useCallback((taskId: string | null) => {
    activeTaskIdRef.current = taskId
    setActiveTaskId(taskId)
    storeActiveTaskId(taskId)
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
          const savedTask = list.find((item) => item.id === savedId && item.type === 'publish')
          const runningPublish = list.find((item) => item.type === 'publish' && isActive(item))
          selectActiveTask(runningPublish?.id ?? savedTask?.id ?? null)
        }

        setSummaries(list)
        const detailIds = new Set(list.filter(isActive).map((item) => item.id))
        if (activeTaskIdRef.current) detailIds.add(activeTaskIdRef.current)
        const fetched = await Promise.all(
          [...detailIds].map(async (taskId) => {
            try {
              return await fetchTask(taskId)
            } catch {
              return null
            }
          }),
        )
        if (!mountedRef.current) return
        setDetails((current) => {
          const next = { ...current }
          fetched.forEach((task) => {
            if (task) next[task.id] = task
          })
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

  const trackTask = useCallback(
    (taskId: string) => {
      selectActiveTask(taskId)
      void refreshTasks()
    },
    [refreshTasks, selectActiveTask],
  )

  const patchVerification = useCallback((taskId: string, patch: Partial<VerificationState>) => {
    setVerificationStates((current) => {
      const state: VerificationState = current[taskId] ?? {
        phase: 'idle',
        consumed: false,
        submittedAt: 0,
        dismissed: false,
        error: null,
      }
      return { ...current, [taskId]: { ...state, ...patch } }
    })
  }, [])

  useEffect(() => {
    setVerificationStates((current) => {
      const next: Record<string, VerificationState> = {}
      summaries.forEach((summary) => {
        const state = current[summary.id]
        if (!isActive(summary)) return
        if (summary.needs_verify) {
          const value: VerificationState = state ?? {
            phase: 'idle',
            consumed: false,
            submittedAt: 0,
            dismissed: false,
            error: null,
          }
          if (value.phase === 'waiting') {
            if (value.consumed) {
              next[summary.id] = { ...value, phase: 'rejected', consumed: false, submittedAt: 0 }
            } else if (Date.now() - value.submittedAt > VERIFY_TIMEOUT) {
              next[summary.id] = { ...value, phase: 'rejected', submittedAt: 0 }
            } else {
              next[summary.id] = value
            }
          } else {
            next[summary.id] = value
          }
        } else if (state?.phase === 'waiting' && !state.consumed) {
          next[summary.id] = { ...state, consumed: true }
        }
      })
      return next
    })
  }, [summaries])

  useEffect(() => {
    setVerificationOpenId((currentId) => {
      const current = summaries.find((item) => item.id === currentId)
      if (current?.needs_verify && isActive(current) && !verificationStates[current.id]?.dismissed) {
        return current.id
      }
      return summaries.find(
        (item) => item.needs_verify && isActive(item) && !verificationStates[item.id]?.dismissed,
      )?.id ?? null
    })
  }, [summaries, verificationStates])

  const openVerification = useCallback(
    (taskId: string) => {
      patchVerification(taskId, { dismissed: false, error: null })
      setVerificationOpenId(taskId)
    },
    [patchVerification],
  )

  const dismissVerification = useCallback(() => {
    if (verificationOpenId) patchVerification(verificationOpenId, { dismissed: true })
    setVerificationOpenId(null)
  }, [patchVerification, verificationOpenId])

  const submitVerification = useCallback(
    async (taskId: string, code: string): Promise<boolean> => {
      const previousPhase = verificationStatesRef.current[taskId]?.phase
      patchVerification(taskId, {
        phase: 'submitting',
        consumed: false,
        submittedAt: 0,
        dismissed: false,
        error: null,
      })
      try {
        await submitVerifyCode(taskId, code)
        patchVerification(taskId, {
          phase: 'waiting',
          consumed: false,
          submittedAt: Date.now(),
          dismissed: false,
          error: null,
        })
        setVerificationOpenId(taskId)
        return true
      } catch (cause) {
        patchVerification(taskId, {
          phase: previousPhase === 'rejected' ? 'rejected' : 'idle',
          error: errorMessage(cause),
        })
        setVerificationOpenId(taskId)
        return false
      }
    },
    [patchVerification],
  )

  const verificationTask = summaries.find(
    (item) => item.id === verificationOpenId && item.needs_verify && isActive(item),
  ) ?? null
  const verificationState = verificationTask ? verificationStates[verificationTask.id] : undefined
  const task = activeTaskId ? details[activeTaskId] ?? null : null
  const runningTasks = summaries.filter(isActive).length

  return (
    <TaskCenterContext.Provider
      value={{
        summaries,
        details,
        task,
        activeTaskId,
        runningTasks,
        loading,
        error,
        refreshTasks,
        trackTask,
        verificationTask,
        verificationPhase: verificationState?.phase ?? 'idle',
        verificationError: verificationState?.error ?? null,
        openVerification,
        dismissVerification,
        submitVerification,
      }}
    >
      {children}
    </TaskCenterContext.Provider>
  )
}

export function useTaskCenter(): TaskCenterValue {
  const context = useContext(TaskCenterContext)
  if (!context) throw new Error('useTaskCenter must be used within TaskCenterProvider')
  return context
}
