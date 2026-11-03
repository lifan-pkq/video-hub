import { useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowClockwise, Check, Play, Sparkle } from '@phosphor-icons/react'
import {
  errorMessage,
  fetchTask,
  planEdit,
  type AiEditPlan,
  type Task,
} from '../../api'
import { Button, Checkbox, Input, Panel, Progress, toast } from '../../ui'
import { formatTimecode, isValidRange } from './model'

interface PreviewRange {
  videoId: number
  start: number
  end: number
}

interface Props {
  initialTask?: Task | null
  onTaskCreated?: (taskId: string) => void
  onPreview: (range: PreviewRange) => void
  onApply: (plan: AiEditPlan) => boolean
}

interface PlanClipState {
  id: string
  startText: string
  endText: string
  reason: string
  selected: boolean
  applied: boolean
}

function resultStorageKey(taskId: string): string {
  return `video-hub:plan-result:v1:${taskId}`
}

function restoreClips(plan: AiEditPlan, taskId: string): PlanClipState[] {
  const base: PlanClipState[] = plan.clips.map((clip, index) => ({
    id: `${plan.video_id}:${index}:${clip.start}:${clip.end}`,
    startText: clip.start.toFixed(2),
    endText: clip.end.toFixed(2),
    reason: clip.reason || '保留片段',
    selected: true,
    applied: false,
  }))
  try {
    const raw = localStorage.getItem(resultStorageKey(taskId))
    if (!raw) return base
    const saved = JSON.parse(raw) as Partial<PlanClipState>[]
    if (!Array.isArray(saved) || saved.length !== base.length) return base
    return base.map((clip, index) => ({
      ...clip,
      startText: typeof saved[index].startText === 'string' ? saved[index].startText! : clip.startText,
      endText: typeof saved[index].endText === 'string' ? saved[index].endText! : clip.endText,
      selected: typeof saved[index].selected === 'boolean' ? saved[index].selected : clip.selected,
      applied: typeof saved[index].applied === 'boolean' ? saved[index].applied : clip.applied,
    }))
  } catch {
    return base
  }
}

export default function AiPlanPanel({ initialTask, onTaskCreated, onPreview, onApply }: Props) {
  const [taskId, setTaskId] = useState<string | null>(null)
  const [task, setTask] = useState<Task | null>(null)
  const [plan, setPlan] = useState<AiEditPlan | null>(null)
  const [clips, setClips] = useState<PlanClipState[]>([])
  const [running, setRunning] = useState(false)
  const timerRef = useRef(0)

  const initialResult = (initialTask?.result as { plan?: AiEditPlan } | undefined)?.plan
  const isPlanTask = !!initialTask && (
    initialTask.input?.mode === 'plan' || (!initialTask.input?.mode && !!initialResult)
  )
  const initialTaskId = initialTask?.id

  useEffect(() => {
    if (!initialTask || !isPlanTask) return
    setTaskId(initialTask.id)
    setTask(initialTask)
    if (initialTask.status === 'pending' || initialTask.status === 'running') {
      setRunning(true)
      setPlan(null)
      setClips([])
    } else {
      setRunning(false)
      setPlan(initialResult ?? null)
      setClips(initialResult ? restoreClips(initialResult, initialTask.id) : [])
    }
  }, [initialTaskId])

  useEffect(() => {
    if (!taskId || !running) return
    let disposed = false
    const tick = async () => {
      try {
        const value = await fetchTask(taskId)
        if (disposed) return
        setTask(value)
        if (['success', 'failed', 'partial', 'cancelled'].includes(value.status)) {
          setRunning(false)
          const result = (value.result as { plan?: AiEditPlan } | undefined)?.plan
          setPlan(result ?? null)
          setClips(result ? restoreClips(result, taskId) : [])
          if (result) toast.success(`AI 选段完成：${result.clips.length} 段，共 ${result.total.toFixed(1)} 秒`)
          else toast.error('AI 选段失败：' + (value.logs?.[value.logs.length - 1] || '没有返回规划'))
          return
        }
        timerRef.current = window.setTimeout(tick, 2500)
      } catch {
        timerRef.current = window.setTimeout(tick, 4000)
      }
    }
    void tick()
    return () => { disposed = true; window.clearTimeout(timerRef.current) }
  }, [running, taskId])

  useEffect(() => {
    if (!taskId || !plan || clips.length === 0) return
    try {
      localStorage.setItem(resultStorageKey(taskId), JSON.stringify(clips))
    } catch {
      // 结果仍可使用，仅本次修改无法持久保存。
    }
  }, [clips, plan, taskId])

  const selected = useMemo(() => clips.filter((clip) => clip.selected), [clips])
  const selectedTotal = selected.reduce((total, clip) => (
    total + Math.max(0, Number(clip.endText) - Number(clip.startText))
  ), 0)
  const allSelected = clips.length > 0 && selected.length === clips.length
  const someSelected = selected.length > 0 && !allSelected

  const updateClip = (id: string, patch: Partial<PlanClipState>) => {
    setClips((current) => current.map((clip) => clip.id === id ? { ...clip, ...patch } : clip))
  }

  const preview = (clip: PlanClipState) => {
    if (!plan || !isValidRange(Number(clip.startText), Number(clip.endText), plan.duration)) {
      toast.warning('请先修正片段起止时间')
      return
    }
    onPreview({ videoId: plan.video_id, start: Number(clip.startText), end: Number(clip.endText) })
  }

  const applySelected = () => {
    if (!plan || selected.length === 0) return
    if (selected.some((clip) => !isValidRange(Number(clip.startText), Number(clip.endText), plan.duration))) {
      toast.warning('选中的片段里有无效区间，请修正后再应用')
      return
    }
    const nextClips = selected.map((clip) => ({
      start: Number(clip.startText),
      end: Number(clip.endText),
      reason: clip.reason,
    }))
    const nextPlan: AiEditPlan = {
      ...plan,
      clips: nextClips,
      total: nextClips.reduce((total, clip) => total + clip.end - clip.start, 0),
    }
    if (onApply(nextPlan)) {
      setClips((current) => current.map((clip) => clip.selected ? { ...clip, selected: false, applied: true } : clip))
      toast.success(`已应用 ${selected.length} 个片段，时间线可继续编辑`)
    }
  }

  const retry = async () => {
    const videoId = initialTask?.input?.video_ids?.[0] ?? plan?.video_id
    if (!videoId) {
      toast.warning('缺少源视频，请返回首页重新创建任务')
      return
    }
    setRunning(true)
    setPlan(null)
    setClips([])
    try {
      const { task_id } = await planEdit(
        videoId,
        initialTask?.input?.target_duration ?? plan?.target_duration ?? 60,
        initialTask?.input?.custom_prompt ?? '',
      )
      setTaskId(task_id)
      onTaskCreated?.(task_id)
    } catch (error) {
      setRunning(false)
      toast.error(`重新创建选段任务失败：${errorMessage(error)}`)
    }
  }

  if (!isPlanTask) return null

  return (
    <Panel pad="sm" className="min-w-0">
      <div className="flex flex-wrap items-center justify-between gap-2 px-1">
        <div>
          <div className="flex items-center gap-1.5 text-sm font-medium text-ink-1">
            <Sparkle size={16} className="text-accent" />选段结果
          </div>
          <p className="mt-1 text-[11px] text-ink-4">勾选、预览或调整片段，再应用到时间线。</p>
        </div>
        {plan && <span className="tnum text-[11px] text-ink-3">原方案 {plan.clips.length} 段</span>}
      </div>

      {running ? (
        <div className="mt-4 rounded-xl border border-line-soft bg-fill p-4">
          <div className="mb-2 flex items-center justify-between gap-3 text-xs text-ink-2">
            <span>正在分析源视频并生成选段…</span>
            <span className="tnum">{Math.round(task?.progress ?? 0)}%</span>
          </div>
          <Progress percent={(task?.progress ?? 0) / 100} />
          <p className="mt-2 break-words text-[11px] leading-relaxed text-ink-4">
            {task?.logs?.[task.logs.length - 1] ?? '任务已提交，等待处理'}
          </p>
        </div>
      ) : !plan ? (
        <div className="mt-4 rounded-xl border border-line-soft bg-fill p-4">
          <div className="text-sm font-medium text-ink-1">
            {task?.status === 'failed' || task?.status === 'cancelled' ? '这次没有生成选段' : '暂无可展示的选段'}
          </div>
          <p className="mt-2 break-words text-xs leading-relaxed text-ink-3">
            {task?.logs?.[task.logs.length - 1] ?? '可以返回首页调整素材和要求后重试。'}
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            <Button size="sm" variant="primary" icon={<ArrowClockwise size={13} />} onClick={() => void retry()}>重新生成</Button>
            <Link to="/edit"><Button size="sm" variant="ghost">返回首页调整</Button></Link>
          </div>
        </div>
      ) : (
        <>
          <div className="mt-4 grid grid-cols-2 gap-2">
            <div className="rounded-xl bg-fill p-3">
              <div className="text-[10px] text-ink-4">已选片段</div>
              <div className="tnum mt-1 text-lg font-semibold text-ink-1">{selected.length}<span className="text-xs font-normal text-ink-3"> / {clips.length}</span></div>
            </div>
            <div className="rounded-xl bg-fill p-3">
              <div className="text-[10px] text-ink-4">选中总时长</div>
              <div className="tnum mt-1 text-lg font-semibold text-ink-1">{formatTimecode(selectedTotal)}</div>
            </div>
          </div>
          <div className="mt-3 flex items-center justify-between gap-2 border-b border-line-soft pb-2">
            <Checkbox
              checked={allSelected}
              indeterminate={someSelected}
              onChange={(checked) => setClips((current) => current.map((clip) => ({ ...clip, selected: checked })))}
              label="全选"
            />
            <span className="text-[10px] text-ink-4">{plan.title}</span>
          </div>
          <div className="mt-2 max-h-[420px] space-y-2 overflow-y-auto pr-1">
            {clips.map((clip, index) => {
              const valid = isValidRange(Number(clip.startText), Number(clip.endText), plan.duration)
              return (
                <div key={clip.id} className={`rounded-xl border p-3 ${clip.applied ? 'border-success/30 bg-success-soft/40' : 'border-line-soft bg-fill'}`}>
                  <div className="flex items-start gap-2">
                    <input
                      type="checkbox"
                      aria-label={`选择第 ${index + 1} 段`}
                      checked={clip.selected}
                      onChange={(event) => updateClip(clip.id, { selected: event.target.checked })}
                      className="mt-0.5 h-4 w-4 shrink-0 accent-[var(--accent)]"
                    />
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <span className="text-xs font-medium text-ink-2">片段 {index + 1}</span>
                        {clip.applied && <span className="flex items-center gap-1 text-[10px] text-success"><Check size={11} />已应用</span>}
                      </div>
                      <p className="mt-1 break-words text-[11px] leading-relaxed text-ink-3">{clip.reason}</p>
                    </div>
                  </div>
                  <div className="mt-2 flex flex-wrap items-end gap-2 pl-6">
                    <Input
                      aria-label={`片段 ${index + 1} 开始时间`}
                      type="number"
                      min={0}
                      max={plan.duration}
                      step={0.1}
                      value={clip.startText}
                      onChange={(startText) => updateClip(clip.id, { startText })}
                      className="h-8 w-[88px]"
                    />
                    <span className="pb-2 text-[10px] text-ink-4">至</span>
                    <Input
                      aria-label={`片段 ${index + 1} 结束时间`}
                      type="number"
                      min={0}
                      max={plan.duration}
                      step={0.1}
                      value={clip.endText}
                      onChange={(endText) => updateClip(clip.id, { endText })}
                      className="h-8 w-[88px]"
                    />
                    <Button size="sm" variant="ghost" icon={<Play size={12} />} onClick={() => preview(clip)}>预览</Button>
                    {!valid && <span className="self-center text-[10px] text-danger">区间无效</span>}
                  </div>
                </div>
              )
            })}
          </div>
          <Button
            variant="primary"
            full
            className="mt-3"
            disabled={selected.length === 0}
            onClick={applySelected}
          >
            应用所选 {selected.length} 段到时间线
          </Button>
        </>
      )}
    </Panel>
  )
}
