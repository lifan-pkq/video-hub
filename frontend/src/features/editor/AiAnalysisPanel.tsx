import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowClockwise, Check, MagicWand, Play } from '@phosphor-icons/react'
import {
  analyzeVideos,
  errorMessage,
  fetchTask,
  type AiAnalysisError,
  type AiSuggestionKind,
  type AiVideoAnalysis,
  type Task,
} from '../../api'
import { Button, Checkbox, Input, Panel, Progress, toast } from '../../ui'
import { formatTimecode, isValidRange, MIN_DUR } from './model'

const AI_KINDS: { value: AiSuggestionKind; label: string }[] = [
  { value: 'intro', label: '开头' },
  { value: 'outro', label: '结尾' },
  { value: 'ad', label: '广告' },
  { value: 'silence', label: '静音' },
  { value: 'repeat', label: '重复' },
]

const KIND_LABEL: Record<AiSuggestionKind, string> = {
  intro: '开头',
  outro: '结尾',
  ad: '广告',
  silence: '静音',
  repeat: '重复',
}

export interface AiSuggestionToApply {
  id: string
  videoId: number
  start: number
  end: number
  kind: AiSuggestionKind
}

interface PreviewRange {
  videoId: number
  start: number
  end: number
}

interface Props {
  initialTask?: Task | null
  onTaskCreated?: (taskId: string) => void
  onPreview: (range: PreviewRange) => void
  onApply: (suggestions: AiSuggestionToApply[]) => boolean
}

interface EditableSuggestion extends AiSuggestionToApply {
  title: string
  duration: number
  startText: string
  endText: string
  reason: string
  source: string
  selected: boolean
  applied: boolean
}

interface TaskResult {
  analyses?: AiVideoAnalysis[]
  errors?: (AiAnalysisError | string)[]
}

type Filter = 'all' | AiSuggestionKind

function resultStorageKey(taskId: string): string {
  return `video-hub:analysis-result:v1:${taskId}`
}

function sourceLabel(source: string): string {
  if (source === 'audio') return '本地音频'
  if (source === 'transcript') return '本地转写'
  if (source === 'llm' || source === 'ai') return 'AI 语义'
  return source || 'AI 分析'
}

function errorStage(error: AiAnalysisError | string): string {
  if (typeof error === 'string') return '分析错误'
  const stage = (error.stage ?? error.type)?.toLowerCase()
  if (stage === 'transcribe' || stage === 'transcription') return '转写失败'
  if (stage === 'ai' || stage === 'llm') return 'AI 服务失败'
  if (stage === 'parse' || stage === 'parsing') return '返回解析失败'
  if (stage === 'silence' || stage === 'audio') return '静音检测失败'
  return '分析错误'
}

function errorText(error: AiAnalysisError | string): string {
  return typeof error === 'string' ? error : error.message
}

function uniqueErrors(errors: (AiAnalysisError | string)[]) {
  const seen = new Set<string>()
  return errors.filter((error) => {
    const key = typeof error === 'string'
      ? `string:${error}`
      : [error.video_id, error.stage, error.type, error.message].join(':')
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
}

function restoreSuggestions(analyses: AiVideoAnalysis[], taskId: string) {
  const suggestions: EditableSuggestion[] = []
  const ids = new Set<string>()
  let skipped = 0

  analyses.forEach((analysis) => {
    ;(analysis.waste ?? []).forEach((waste, index) => {
      const start = Math.max(0, waste.start)
      const end = Math.min(analysis.duration, waste.end)
      const videoId = waste.video_id ?? analysis.video_id
      if (!isValidRange(start, end, analysis.duration) || !(waste.kind in KIND_LABEL)) {
        skipped += 1
        return
      }
      const baseId = waste.id ?? `${videoId}:${waste.kind}:${start.toFixed(3)}:${end.toFixed(3)}:${index}`
      let id = baseId
      let suffix = 1
      while (ids.has(id)) {
        id = `${baseId}:${suffix}`
        suffix += 1
      }
      ids.add(id)
      suggestions.push({
        id,
        videoId,
        title: analysis.title,
        duration: analysis.duration,
        startText: start.toFixed(2),
        endText: end.toFixed(2),
        start,
        end,
        kind: waste.kind,
        reason: waste.reason || KIND_LABEL[waste.kind],
        source: waste.source ?? analysis.source ?? 'ai',
        selected: true,
        applied: false,
      })
    })
  })

  try {
    const raw = localStorage.getItem(resultStorageKey(taskId))
    if (!raw) return { suggestions, skipped }
    const saved = JSON.parse(raw) as Partial<EditableSuggestion>[]
    if (!Array.isArray(saved)) return { suggestions, skipped }
    const byId = new Map(saved.filter((item) => typeof item.id === 'string').map((item) => [item.id, item]))
    return {
      suggestions: suggestions.map((suggestion) => {
        const previous = byId.get(suggestion.id)
        if (!previous) return suggestion
        return {
          ...suggestion,
          startText: typeof previous.startText === 'string' ? previous.startText : suggestion.startText,
          endText: typeof previous.endText === 'string' ? previous.endText : suggestion.endText,
          selected: typeof previous.selected === 'boolean' ? previous.selected : suggestion.selected,
          applied: typeof previous.applied === 'boolean' ? previous.applied : suggestion.applied,
        }
      }),
      skipped,
    }
  } catch {
    return { suggestions, skipped }
  }
}

function validSuggestion(suggestion: EditableSuggestion): boolean {
  return isValidRange(Number(suggestion.startText), Number(suggestion.endText), suggestion.duration)
}

function toRange(suggestion: EditableSuggestion): AiSuggestionToApply {
  return {
    id: suggestion.id,
    videoId: suggestion.videoId,
    start: Number(suggestion.startText),
    end: Number(suggestion.endText),
    kind: suggestion.kind,
  }
}

export default function AiAnalysisPanel({ initialTask, onTaskCreated, onPreview, onApply }: Props) {
  const [kinds, setKinds] = useState<Set<AiSuggestionKind>>(() => new Set(['intro', 'outro', 'ad']))
  const [prompt, setPrompt] = useState('')
  const [taskId, setTaskId] = useState<string | null>(null)
  const [task, setTask] = useState<Task | null>(null)
  const [running, setRunning] = useState(false)
  const [completed, setCompleted] = useState(false)
  const [suggestions, setSuggestions] = useState<EditableSuggestion[]>([])
  const [errors, setErrors] = useState<(AiAnalysisError | string)[]>([])
  const [filter, setFilter] = useState<Filter>('all')
  const runningRef = useRef(false)
  const lastTargetIdsRef = useRef<number[]>([])

  const initialResult = (initialTask?.result as TaskResult | undefined) ?? {}
  const isAnalysisTask = !!initialTask && (
    initialTask.input?.mode === 'analyze' || (!initialTask.input?.mode && initialResult.analyses !== undefined)
  )
  const initialTaskId = initialTask?.id

  const finish = useCallback((value: Task, notify: boolean) => {
    const result = (value.result as TaskResult | undefined) ?? {}
    const analyses = result.analyses ?? []
    const restored = restoreSuggestions(analyses, value.id)
    const analysisErrors = analyses.flatMap((analysis) => analysis.errors ?? [])
    const skippedErrors: (AiAnalysisError | string)[] = restored.skipped > 0
      ? [{ stage: 'parse', message: `已忽略 ${restored.skipped} 条无效或越界建议` }]
      : []
    setSuggestions(restored.suggestions)
    setErrors(uniqueErrors([...(result.errors ?? []), ...analysisErrors, ...skippedErrors]))
    setTask(value)
    setCompleted(true)
    setRunning(false)
    runningRef.current = false

    if (!notify) return
    if (value.status === 'failed' || value.status === 'cancelled') toast.error('AI 分析失败，请查看错误后重试')
    else if (value.status === 'partial') toast.warning(`部分分析完成：${restored.suggestions.length} 条建议`)
    else toast.success(`AI 分析完成：共 ${restored.suggestions.length} 条建议`)
  }, [])

  useEffect(() => {
    if (!initialTask || !isAnalysisTask) return
    setTaskId(initialTask.id)
    setTask(initialTask)
    setPrompt(initialTask.input?.custom_prompt ?? '')
    lastTargetIdsRef.current = initialTask.input?.video_ids
      ?? (initialResult.analyses ?? []).map((analysis) => analysis.video_id)
    const restoredKinds = (initialTask.input?.kinds ?? []).filter((kind): kind is AiSuggestionKind => (
      AI_KINDS.some((option) => option.value === kind)
    ))
    if (restoredKinds.length > 0) setKinds(new Set(restoredKinds))

    if (initialTask.status === 'pending' || initialTask.status === 'running') {
      runningRef.current = true
      setRunning(true)
      setCompleted(false)
      setSuggestions([])
      setErrors([])
    } else {
      finish(initialTask, false)
    }
  }, [initialTaskId, finish])

  useEffect(() => {
    if (!taskId || !running) return
    let disposed = false
    let timer = 0
    const tick = async () => {
      if (disposed) return
      try {
        const value = await fetchTask(taskId)
        if (disposed) return
        setTask(value)
        if (['success', 'partial', 'failed', 'cancelled'].includes(value.status)) finish(value, true)
        else timer = window.setTimeout(tick, 2000)
      } catch {
        if (!disposed) timer = window.setTimeout(tick, 3000)
      }
    }
    void tick()
    return () => {
      disposed = true
      window.clearTimeout(timer)
    }
  }, [finish, running, taskId])

  useEffect(() => {
    if (!taskId || !completed || suggestions.length === 0) return
    try {
      localStorage.setItem(resultStorageKey(taskId), JSON.stringify(suggestions))
    } catch {
      // 结果仍可使用，仅本次修改无法持久保存。
    }
  }, [completed, suggestions, taskId])

  const selected = useMemo(() => suggestions.filter((suggestion) => suggestion.selected), [suggestions])
  const selectedTotal = selected.reduce((total, suggestion) => (
    total + Math.max(0, Number(suggestion.endText) - Number(suggestion.startText))
  ), 0)
  const visible = filter === 'all' ? suggestions : suggestions.filter((suggestion) => suggestion.kind === filter)
  const visibleSelected = visible.filter((suggestion) => suggestion.selected).length
  const allVisibleSelected = visible.length > 0 && visibleSelected === visible.length
  const someVisibleSelected = visibleSelected > 0 && !allVisibleSelected
  const latestLog = task?.logs?.[task.logs.length - 1]
  const fatal = task?.status === 'failed' || task?.status === 'cancelled'

  const updateSuggestion = (id: string, patch: Partial<EditableSuggestion>) => {
    setSuggestions((current) => current.map((item) => item.id === id ? { ...item, ...patch } : item))
  }

  const preview = (suggestion: EditableSuggestion) => {
    if (!validSuggestion(suggestion)) {
      toast.warning(`请先修正区间：时长至少 ${MIN_DUR} 秒且不能超出源视频`)
      return
    }
    onPreview(toRange(suggestion))
  }

  const apply = (items: EditableSuggestion[]) => {
    if (items.length === 0) return
    if (items.some((item) => !validSuggestion(item))) {
      toast.warning(`所选建议中有无效区间，请修正后再应用（最短 ${MIN_DUR} 秒）`)
      return
    }
    if (onApply(items.map(toRange))) {
      const applied = new Set(items.map((item) => item.id))
      setSuggestions((current) => current.map((item) => (
        applied.has(item.id) ? { ...item, applied: true, selected: false } : item
      )))
    }
  }

  const retry = async () => {
    const targetIds = lastTargetIdsRef.current
    if (targetIds.length === 0) {
      toast.warning('缺少源视频，请返回首页重新创建任务')
      return
    }
    if (runningRef.current) return
    runningRef.current = true
    setRunning(true)
    setCompleted(false)
    setSuggestions([])
    setErrors([])
    try {
      const { task_id } = await analyzeVideos(targetIds, Array.from(kinds), prompt)
      setTaskId(task_id)
      onTaskCreated?.(task_id)
    } catch (error) {
      runningRef.current = false
      setRunning(false)
      toast.error(`重新创建分析任务失败：${errorMessage(error)}`)
    }
  }

  if (!isAnalysisTask) return null

  return (
    <Panel pad="sm" className="min-w-0">
      <div className="flex flex-wrap items-center justify-between gap-2 px-1">
        <div>
          <div className="flex items-center gap-1.5 text-sm font-medium text-ink-1">
            <MagicWand size={16} className="text-accent" />分析结果
          </div>
          <p className="mt-1 text-[11px] text-ink-4">按类型筛选，确认哪些区间需要剪除。</p>
        </div>
        {suggestions.length > 0 && <span className="tnum text-[11px] text-ink-3">{suggestions.length} 条建议</span>}
      </div>

      {running ? (
        <div className="mt-4 rounded-xl border border-line-soft bg-fill p-4">
          <div className="mb-2 flex items-center justify-between gap-3 text-xs text-ink-2">
            <span>正在转写并分析视频内容…</span>
            <span className="tnum">{Math.round(task?.progress ?? 0)}%</span>
          </div>
          <Progress percent={(task?.progress ?? 0) / 100} />
          <p className="mt-2 break-words text-[11px] leading-relaxed text-ink-4">{latestLog ?? '任务已提交，等待处理'}</p>
        </div>
      ) : completed && (
        <>
          {(fatal || task?.status === 'partial' || errors.length > 0) && (
            <div className="mt-4 rounded-xl border border-warning/30 bg-warning-soft p-4">
              <div className="text-sm font-medium text-warning">{fatal ? '分析未完成' : '部分结果或提示需要处理'}</div>
              {latestLog && <p className="mt-2 break-words text-xs leading-relaxed text-ink-3">{latestLog}</p>}
              {errors.map((error, index) => (
                <p key={`${errorText(error)}:${index}`} className="mt-1.5 break-words text-[11px] leading-relaxed text-ink-2">
                  <span className="mr-2 text-warning">{errorStage(error)}</span>{errorText(error)}
                </p>
              ))}
              <div className="mt-3 flex flex-wrap gap-2">
                <Button size="sm" variant="primary" icon={<ArrowClockwise size={13} />} onClick={() => void retry()}>重新分析</Button>
                <Link to="/edit"><Button size="sm" variant="ghost">返回首页调整</Button></Link>
              </div>
            </div>
          )}

          {!fatal && suggestions.length === 0 ? (
            <div className="mt-4 rounded-xl border border-line-soft bg-fill p-5 text-center">
              <Check size={22} className="mx-auto text-success" />
              <div className="mt-2 text-sm font-medium text-ink-1">没有发现需要剪除的内容</div>
              <p className="mt-1 text-xs leading-relaxed text-ink-3">可以直接使用下方时间线手动剪辑，或返回首页调整分析类型。</p>
              <Link to="/edit" className="mt-3 inline-block"><Button size="sm" variant="ghost">返回首页调整</Button></Link>
            </div>
          ) : suggestions.length > 0 && (
            <>
              <div className="mt-4 grid grid-cols-2 gap-2">
                <div className="rounded-xl bg-fill p-3">
                  <div className="text-[10px] text-ink-4">已选建议</div>
                  <div className="tnum mt-1 text-lg font-semibold text-ink-1">{selected.length}<span className="text-xs font-normal text-ink-3"> / {suggestions.length}</span></div>
                </div>
                <div className="rounded-xl bg-fill p-3">
                  <div className="text-[10px] text-ink-4">预计剪掉</div>
                  <div className="tnum mt-1 text-lg font-semibold text-ink-1">{formatTimecode(selectedTotal)}</div>
                </div>
              </div>

              <div className="mt-3 flex gap-1.5 overflow-x-auto pb-1">
                <button
                  type="button"
                  onClick={() => setFilter('all')}
                  className={`shrink-0 rounded-lg px-2.5 py-1.5 text-[11px] ${filter === 'all' ? 'bg-accent text-white' : 'bg-fill text-ink-3'}`}
                >
                  全部 {suggestions.length}
                </button>
                {AI_KINDS.map((kind) => {
                  const count = suggestions.filter((suggestion) => suggestion.kind === kind.value).length
                  if (count === 0) return null
                  return (
                    <button
                      key={kind.value}
                      type="button"
                      onClick={() => setFilter(kind.value)}
                      className={`shrink-0 rounded-lg px-2.5 py-1.5 text-[11px] ${filter === kind.value ? 'bg-accent text-white' : 'bg-fill text-ink-3'}`}
                    >
                      {kind.label} {count}
                    </button>
                  )
                })}
              </div>

              <div className="mt-2 border-b border-line-soft pb-2">
                <Checkbox
                  checked={allVisibleSelected}
                  indeterminate={someVisibleSelected}
                  onChange={(checked) => {
                    const ids = new Set(visible.map((item) => item.id))
                    setSuggestions((current) => current.map((item) => ids.has(item.id) ? { ...item, selected: checked } : item))
                  }}
                  label={filter === 'all' ? '全选' : `全选${KIND_LABEL[filter]}建议`}
                />
              </div>

              <div className="mt-2 max-h-[420px] space-y-2 overflow-y-auto pr-1">
                {visible.map((suggestion) => {
                  const valid = validSuggestion(suggestion)
                  return (
                    <div key={suggestion.id} className={`rounded-xl border p-3 ${suggestion.applied ? 'border-success/30 bg-success-soft/40' : 'border-line-soft bg-fill'}`}>
                      <div className="flex items-start gap-2">
                        <input
                          type="checkbox"
                          aria-label={`选择 ${suggestion.title} ${formatTimecode(Number(suggestion.startText))} 的建议`}
                          checked={suggestion.selected}
                          onChange={(event) => updateSuggestion(suggestion.id, { selected: event.target.checked })}
                          className="mt-0.5 h-4 w-4 shrink-0 accent-[var(--accent)]"
                        />
                        <div className="min-w-0 flex-1">
                          <div className="flex flex-wrap items-center gap-1.5">
                            <span className="rounded bg-danger-soft px-1.5 py-0.5 text-[10px] text-danger">{KIND_LABEL[suggestion.kind]}</span>
                            <span className="truncate text-xs font-medium text-ink-2">{suggestion.title}</span>
                            {suggestion.applied && <span className="flex items-center gap-1 text-[10px] text-success"><Check size={11} />已应用</span>}
                          </div>
                          <p className="mt-1 break-words text-[11px] leading-relaxed text-ink-3">{suggestion.reason}</p>
                          <span className="mt-1 block text-[10px] text-ink-4">{sourceLabel(suggestion.source)}</span>
                        </div>
                      </div>
                      <div className="mt-2 flex flex-wrap items-end gap-2 pl-6">
                        <Input
                          aria-label="建议开始时间（秒）"
                          type="number"
                          min={0}
                          max={suggestion.duration}
                          step={0.1}
                          value={suggestion.startText}
                          onChange={(startText) => updateSuggestion(suggestion.id, { startText })}
                          className="h-8 w-[88px]"
                        />
                        <span className="pb-2 text-[10px] text-ink-4">至</span>
                        <Input
                          aria-label="建议结束时间（秒）"
                          type="number"
                          min={0}
                          max={suggestion.duration}
                          step={0.1}
                          value={suggestion.endText}
                          onChange={(endText) => updateSuggestion(suggestion.id, { endText })}
                          className="h-8 w-[88px]"
                        />
                        <Button size="sm" variant="ghost" icon={<Play size={12} />} onClick={() => preview(suggestion)}>预览</Button>
                        <Button size="sm" variant="ghost" icon={<Check size={12} />} onClick={() => apply([suggestion])}>应用</Button>
                        {!valid && <span className="self-center text-[10px] text-danger">区间无效</span>}
                      </div>
                    </div>
                  )
                })}
              </div>
              <Button variant="primary" full className="mt-3" disabled={selected.length === 0} onClick={() => apply(selected)}>
                应用所选 {selected.length} 条到时间线
              </Button>
            </>
          )}
        </>
      )}
    </Panel>
  )
}
