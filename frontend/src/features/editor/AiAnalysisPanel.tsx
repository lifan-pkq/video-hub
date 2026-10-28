import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
} from 'react'
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
import { Button, Checkbox, Input, Panel, toast } from '../../ui'
import { formatTimecode, isValidRange, MIN_DUR } from './model'

const AI_KINDS: { value: AiSuggestionKind; label: string }[] = [
  { value: 'intro', label: '去开头废话' },
  { value: 'outro', label: '去结尾废话' },
  { value: 'ad', label: '去中间广告' },
  { value: 'silence', label: '去静音停顿' },
  { value: 'repeat', label: '去重复表达' },
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

export interface AiAnalysisPanelHandle {
  start: (videoIds?: number[]) => void
}

interface Props {
  videoIds: number[]
  onPreview: (suggestion: AiSuggestionToApply) => void
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
}

interface TaskResult {
  analyses?: AiVideoAnalysis[]
  errors?: (AiAnalysisError | string)[]
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

function normalizeSuggestions(analyses: AiVideoAnalysis[]) {
  const suggestions: EditableSuggestion[] = []
  const ids = new Set<string>()
  let skipped = 0

  analyses.forEach((analysis) => {
    (analysis.waste ?? []).forEach((waste, index) => {
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
      })
    })
  })
  return { suggestions, skipped }
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

const AiAnalysisPanel = forwardRef<AiAnalysisPanelHandle, Props>(function AiAnalysisPanel(
  { videoIds, onPreview, onApply },
  ref,
) {
  const [kinds, setKinds] = useState<Set<AiSuggestionKind>>(
    () => new Set(['intro', 'outro', 'ad']),
  )
  const [prompt, setPrompt] = useState('')
  const [taskId, setTaskId] = useState<string | null>(null)
  const [task, setTask] = useState<Task | null>(null)
  const [running, setRunning] = useState(false)
  const [completed, setCompleted] = useState(false)
  const [suggestions, setSuggestions] = useState<EditableSuggestion[]>([])
  const [errors, setErrors] = useState<(AiAnalysisError | string)[]>([])
  const runningRef = useRef(false)
  const lastTargetIdsRef = useRef<number[]>([])

  const start = useCallback(async (requestedIds?: number[]) => {
    const targetIds = requestedIds ?? videoIds
    if (targetIds.length === 0) {
      toast.warning('先把视频加入时间线')
      return
    }
    if (kinds.size === 0) {
      toast.warning('至少选择一种分析类型')
      return
    }
    if (runningRef.current) return

    runningRef.current = true
    lastTargetIdsRef.current = targetIds
    setRunning(true)
    setCompleted(false)
    setSuggestions([])
    setErrors([])
    setTask(null)
    setTaskId(null)
    try {
      const { task_id } = await analyzeVideos(targetIds, Array.from(kinds), prompt)
      setTaskId(task_id)
    } catch (error) {
      runningRef.current = false
      setRunning(false)
      toast.error(`启动分析失败：${errorMessage(error)}`)
    }
  }, [kinds, prompt, videoIds])

  useImperativeHandle(ref, () => ({
    start: (ids) => { void start(ids) },
  }), [start])

  useEffect(() => {
    if (!taskId || !running) return
    let disposed = false
    let timer = 0

    const finish = (value: Task) => {
      const result = (value.result as TaskResult | undefined) ?? {}
      const analyses = result.analyses ?? []
      const normalized = normalizeSuggestions(analyses)
      const analysisErrors = analyses.flatMap((analysis) => analysis.errors ?? [])
      const skippedErrors: (AiAnalysisError | string)[] = normalized.skipped > 0
        ? [{ stage: 'parse', message: `已忽略 ${normalized.skipped} 条无效或越界建议` }]
        : []
      setSuggestions(normalized.suggestions)
      setErrors(uniqueErrors([...(result.errors ?? []), ...analysisErrors, ...skippedErrors]))
      setTask(value)
      setCompleted(true)
      setRunning(false)
      runningRef.current = false

      if (value.status === 'failed') {
        toast.error('AI 分析失败，请查看错误后重试')
      } else if (value.status === 'partial') {
        toast.warning(`部分分析完成：${normalized.suggestions.length} 条建议`)
      } else {
        toast.success(`AI 分析完成：共 ${normalized.suggestions.length} 条建议`)
      }
    }

    const tick = async () => {
      if (disposed) return
      try {
        const value = await fetchTask(taskId)
        if (disposed) return
        setTask(value)
        if (value.status === 'success' || value.status === 'partial' || value.status === 'failed') {
          finish(value)
        } else {
          timer = window.setTimeout(tick, 2000)
        }
      } catch {
        if (!disposed) timer = window.setTimeout(tick, 3000)
      }
    }
    void tick()
    return () => {
      disposed = true
      window.clearTimeout(timer)
    }
  }, [running, taskId])

  const selectedCount = useMemo(
    () => suggestions.reduce((count, suggestion) => count + (suggestion.selected ? 1 : 0), 0),
    [suggestions],
  )

  const updateSuggestion = (id: string, patch: Partial<EditableSuggestion>) => {
    setSuggestions((current) => current.map((item) => (item.id === id ? { ...item, ...patch } : item)))
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
      setSuggestions((current) => current.filter((item) => !applied.has(item.id)))
    }
  }

  const discard = () => {
    setTaskId(null)
    setTask(null)
    setCompleted(false)
    setSuggestions([])
    setErrors([])
  }

  const allSelected = suggestions.length > 0 && selectedCount === suggestions.length
  const someSelected = selectedCount > 0 && !allSelected
  const latestLog = task?.logs?.[task.logs.length - 1]

  return (
    <Panel pad="sm">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 px-1">
        <span className="flex items-center gap-1.5 text-xs font-medium text-ink-2">
          <MagicWand size={15} className="text-accent" />
          AI 智能分析（语音字幕）
        </span>
        {AI_KINDS.map((kind) => (
          <Checkbox
            key={kind.value}
            checked={kinds.has(kind.value)}
            disabled={running}
            onChange={() => setKinds((current) => {
              const next = new Set(current)
              if (next.has(kind.value)) next.delete(kind.value)
              else next.add(kind.value)
              return next
            })}
            label={kind.label}
          />
        ))}
        <Input
          value={prompt}
          onChange={setPrompt}
          maxLength={200}
          disabled={running}
          placeholder="自定义要求（可选）"
          aria-label="AI 自定义剪辑要求"
          className="min-w-0 flex-1"
        />
        <Button
          size="sm"
          variant="primary"
          icon={<MagicWand size={14} />}
          loading={running}
          disabled={videoIds.length === 0 || kinds.size === 0}
          onClick={() => { void start() }}
        >
          {running ? '分析中…' : '开始分析'}
        </Button>
      </div>

      {running && (
        <div className="mt-2 border-t border-line-soft px-1 pt-2 text-xs text-ink-3" role="status">
          {latestLog ?? '排队/语音识别中（首次需下载模型，请耐心等待）…'}
        </div>
      )}

      {completed && !running && (
        <div className="mt-3 border-t border-line-soft px-1 pt-3">
          {(task?.status === 'failed' || task?.status === 'partial' || errors.length > 0) && (
            <div className="mb-3 rounded-lg border border-warning/30 bg-warning-soft p-3 text-xs text-ink-2">
              <div className="mb-1 font-medium text-warning">
                {task?.status === 'failed' ? '分析未完成' : '部分结果可用'}
              </div>
              {latestLog && <p className="mb-1 break-words text-ink-3">{latestLog}</p>}
              {errors.map((error, index) => (
                <p key={`${errorText(error)}:${index}`} className="mt-1 break-words">
                  <span className="mr-2 text-warning">{errorStage(error)}</span>
                  {errorText(error)}
                </p>
              ))}
              <Button
                size="sm"
                variant="ghost"
                className="mt-2"
                icon={<ArrowClockwise size={13} />}
                onClick={() => { void start(lastTargetIdsRef.current) }}
              >
                重试分析
              </Button>
            </div>
          )}

          {suggestions.length === 0 ? (
            <div className="text-xs text-ink-3">
              {task?.status === 'failed' ? '没有可应用的建议。' : '分析完成：没有发现需要剪除的内容。'}
            </div>
          ) : (
            <>
              <div className="mb-2 flex flex-wrap items-center gap-2">
                <Checkbox
                  checked={allSelected}
                  indeterminate={someSelected}
                  onChange={(checked) => setSuggestions((current) => current.map((item) => ({ ...item, selected: checked })))}
                  label={`全选（已选 ${selectedCount}/${suggestions.length}）`}
                />
                <span className="text-[11px] text-ink-4">应用只影响当前时间线中对应的源区间</span>
              </div>
              <div className="ai-suggestion-list flex max-h-[26rem] flex-col gap-2 overflow-y-auto pr-1">
                {suggestions.map((suggestion) => {
                  const valid = validSuggestion(suggestion)
                  return (
                    <div key={suggestion.id} className="ai-suggestion-row rounded-lg border border-line-soft bg-fill p-2.5">
                      <div className="flex flex-wrap items-center gap-2">
                        <input
                          type="checkbox"
                          aria-label={`选择 ${suggestion.title} ${formatTimecode(Number(suggestion.startText))} 的建议`}
                          checked={suggestion.selected}
                          onChange={(event) => updateSuggestion(suggestion.id, { selected: event.target.checked })}
                          className="h-4 w-4 shrink-0 accent-[var(--accent)]"
                        />
                        <span className="rounded bg-danger-soft px-1.5 py-0.5 text-[11px] text-danger">
                          {KIND_LABEL[suggestion.kind]}
                        </span>
                        <span className="min-w-0 flex-1 truncate text-xs font-medium text-ink-2">{suggestion.title}</span>
                        <span className="text-[10px] text-ink-4">{sourceLabel(suggestion.source)}</span>
                      </div>
                      <p className="mt-1.5 break-words pl-6 text-xs leading-relaxed text-ink-3">{suggestion.reason}</p>
                      <div className="mt-2 flex flex-wrap items-end gap-2 pl-0 sm:pl-6">
                        <label className="text-[10px] text-ink-4">
                          <span className="mb-1 block">开始（秒）</span>
                          <Input
                            aria-label="建议开始时间（秒）"
                            type="number"
                            min={0}
                            max={suggestion.duration}
                            step={0.01}
                            value={suggestion.startText}
                            onChange={(startText) => updateSuggestion(suggestion.id, { startText })}
                            className="h-8 w-[104px]"
                          />
                        </label>
                        <label className="text-[10px] text-ink-4">
                          <span className="mb-1 block">结束（秒）</span>
                          <Input
                            aria-label="建议结束时间（秒）"
                            type="number"
                            min={0}
                            max={suggestion.duration}
                            step={0.01}
                            value={suggestion.endText}
                            onChange={(endText) => updateSuggestion(suggestion.id, { endText })}
                            className="h-8 w-[104px]"
                          />
                        </label>
                        <Button size="sm" variant="ghost" icon={<Play size={13} />} onClick={() => preview(suggestion)}>
                          预览
                        </Button>
                        <Button size="sm" variant="ghost" icon={<Check size={13} />} onClick={() => apply([suggestion])}>
                          应用此条
                        </Button>
                        {!valid && <span className="self-center text-[11px] text-danger">区间无效</span>}
                      </div>
                    </div>
                  )
                })}
              </div>
            </>
          )}

          <div className="mt-3 flex flex-wrap gap-2">
            {suggestions.length > 0 && (
              <Button
                variant="primary"
                size="sm"
                disabled={selectedCount === 0}
                onClick={() => apply(suggestions.filter((item) => item.selected))}
              >
                应用所选（{selectedCount}）
              </Button>
            )}
            <Button size="sm" onClick={discard}>关闭结果</Button>
          </div>
        </div>
      )}
    </Panel>
  )
})

export default AiAnalysisPanel
