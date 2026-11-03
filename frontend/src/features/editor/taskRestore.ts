import type {
  AiEditPlan,
  AiVideoAnalysis,
  Task,
  TaskSummary,
  Video,
} from '../../api'
import {
  DRAFT_STORAGE_KEY,
  MIN_DUR,
  newClip,
  pack,
  type EditorDocument,
} from './model'

type EditTask = Task | TaskSummary

interface EditTaskResult {
  analyses?: AiVideoAnalysis[]
  plan?: AiEditPlan
}

function uniqueIds(ids: number[]): number[] {
  return Array.from(new Set(ids.filter((id) => Number.isInteger(id) && id > 0)))
}

export function editorDraftStorageKey(taskId?: string): string {
  return taskId ? `video-hub:editor-draft:v2:${taskId}` : DRAFT_STORAGE_KEY
}

export function taskVideoIds(task: EditTask): number[] {
  const input = task.input
  if (input?.mode === 'concat') {
    return uniqueIds((input.clips ?? []).map((clip) => clip.video_id))
  }
  if (input?.video_ids) return uniqueIds(input.video_ids)

  const result = task.result as EditTaskResult
  if (result.analyses) {
    return uniqueIds(result.analyses.map((analysis) => analysis.video_id))
  }
  if (result.plan) return uniqueIds([result.plan.video_id])
  return []
}

export function taskModeLabel(task: EditTask): string {
  if (task.input?.mode === 'concat') return '拼接导出'
  if (task.input?.mode === 'plan') return 'AI 智能成片'
  if (task.input?.mode === 'analyze') return 'AI 智能分析'
  const result = task.result as EditTaskResult
  if (result.plan) return 'AI 智能成片'
  if (result.analyses) return 'AI 智能分析'
  return '视频剪辑'
}

export function restoreTaskDocument(task: EditTask, sources: readonly Video[]): EditorDocument | null {
  const byId = new Map(sources.map((source) => [source.id, source]))
  const input = task.input

  if (input?.mode === 'concat') {
    if (!input.clips?.length) return null
    try {
      const clips = input.clips.map((item, index) => {
        const source = byId.get(item.video_id)
        if (!source || (source.duration ?? 0) < MIN_DUR) throw new Error('missing source')
        return {
          ...newClip(source),
          id: `${task.id}:clip:${index}`,
          trimStart: item.start,
          trimEnd: item.end,
        }
      })
      return { clips: pack(clips), title: input.title ?? '' }
    } catch {
      return null
    }
  }

  const ids = taskVideoIds(task)
  if (ids.length === 0) return null
  const picked = ids.map((id) => byId.get(id))
  if (picked.some((video) => !video || (video.duration ?? 0) < MIN_DUR)) return null
  return {
    clips: pack((picked as Video[]).map(newClip)),
    title: '',
  }
}
