import type { DouyinShort, TaskSummary, Video } from '../../api'

export const STUDIO_STEPS = [
  { id: 'transcribe', label: '转写' },
  { id: 'select', label: '选段' },
  { id: 'render', label: '渲染' },
  { id: 'finish', label: '成片' },
] as const

const BANDS: Array<[number, number]> = [[0, 42], [42, 48], [48, 94], [94, 100]]

export function studioStepIndex(stage: string | undefined, status: string | undefined): number {
  if (status === 'success' || status === 'partial') return 3
  const value = stage || ''
  if (value === 'render') return 2
  if (value === 'plan' || value === 'select') return 1
  if (value === 'pack' || value === 'done' || value === 'finish') return 3
  return 0
}

export function studioStepFill(index: number, current: number, progress: number, status?: string): number {
  if (status === 'success' || status === 'partial') return 100
  if (index < current) return 100
  if (index > current) return 0
  const [start, end] = BANDS[index] || [0, 100]
  const span = Math.max(1, end - start)
  return Math.max(10, Math.min(100, ((progress - start) / span) * 100))
}

export function clock(seconds?: number | null): string {
  const safe = Number.isFinite(Number(seconds)) ? Math.max(0, Number(seconds)) : 0
  const minutes = Math.floor(safe / 60)
  const rest = Math.floor(safe - minutes * 60)
  return `${minutes}:${String(rest).padStart(2, '0')}`
}

export function copyBlock(short: Pick<DouyinShort, 'title' | 'hashtags' | 'description'>): string {
  const tags = (short.hashtags || []).filter(Boolean).join(' ')
  return [
    short.title ? `标题：${short.title}` : '',
    tags ? `话题：${tags}` : '',
    short.description ? `简介：${short.description}` : '',
  ].filter(Boolean).join('\n')
}

export function layoutName(layout?: string | null): string {
  return layout === 'podcast' ? '满屏播客式' : '访谈式'
}

export function framingLabel(short: Pick<DouyinShort, 'layout' | 'framing'>): string {
  if (short.layout === 'podcast' || short.framing === 'podcast') return '满屏播客式'
  if (short.framing === 'subject') return '访谈式 · 跟着说话人'
  return '访谈式 · 居中裁切'
}

export function downloadFile(url: string) {
  const link = document.createElement('a')
  link.href = url
  link.rel = 'noopener'
  document.body.appendChild(link)
  link.click()
  link.remove()
}

export function taskSourceTitle(task: Pick<TaskSummary, 'title' | 'input' | 'result'>, videos: Video[]): string {
  if (task.result?.source_title) return task.result.source_title
  const id = task.input?.video_id
  if (id) return videos.find((video) => video.id === id)?.title || `视频 #${id}`
  return task.title || '抖音成片'
}

export function historyLabel(task: Pick<TaskSummary, 'status' | 'stage' | 'result'>): string {
  const count = task.result?.shorts?.length || 0
  if (task.status === 'success') return count ? `${count} 条成片` : '已完成'
  if (task.status === 'partial') return count ? `${count} 条可用` : '部分完成'
  if (task.status === 'failed') return '失败'
  if (task.status === 'cancelled') return '已取消'
  return STUDIO_STEPS[studioStepIndex(task.stage, task.status)].label
}
