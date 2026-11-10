import type { EngineInfo, Health, Job, NarrateTask, Suggestion, Video, VoiceSettings } from './types.ts'

export class ApiError extends Error {
  code: string
  action: string
  status: number

  constructor(message: string, code: string, action: string, status: number) {
    super(message)
    this.code = code
    this.action = action
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      ...(init?.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
      ...init?.headers,
    },
  })
  const text = await response.text()
  let data: unknown = null
  if (text) {
    try {
      data = JSON.parse(text)
    } catch {
      data = { detail: text.slice(0, 300) }
    }
  }
  if (!response.ok) {
    const detail = (data as { detail?: unknown } | null)?.detail
    if (detail && typeof detail === 'object') {
      const record = detail as { message?: string; code?: string; action?: string }
      throw new ApiError(record.message || '请求失败', record.code || 'http', record.action || '', response.status)
    }
    throw new ApiError(typeof detail === 'string' ? detail : '请求失败', 'http', '', response.status)
  }
  return data as T
}

export function fileUrl(video: Video): string {
  return video.file_url || `/api/videos/${video.id}/file`
}

export function asVideoList(data: unknown): Video[] {
  if (Array.isArray(data)) return data as Video[]
  if (data && typeof data === 'object') {
    const record = data as { items?: unknown; videos?: unknown }
    if (Array.isArray(record.items)) return record.items as Video[]
    if (Array.isArray(record.videos)) return record.videos as Video[]
  }
  throw new ApiError('素材库返回格式无法识别', 'library', '确认 /api/videos 返回数组，或 {items}/{videos}。', 500)
}

export const api = {
  health: () => request<Health>('/api/health'),
  videos: async () => asVideoList(await request<unknown>('/api/videos')),
  seedDemo: () => request<{ videos: Video[] }>('/api/videos/seed-demo', { method: 'POST' }),
  job: (id: string) => request<Job>(`/api/jobs/${id}`),
  cancel: (id: string) => request<Job>(`/api/jobs/${id}/cancel`, { method: 'POST' }),
  analyze: (videoIds: number[]) =>
    request<Job>('/api/edit/analyze', { method: 'POST', body: JSON.stringify({ video_ids: videoIds }) }),
  plan: (videoId: number, suggestions: Suggestion[], useLlm: boolean, instruction: string) =>
    request<{ source: string; suggestions: Suggestion[]; warnings: string[]; drop_threshold: number }>('/api/edit/plan', {
      method: 'POST',
      body: JSON.stringify({ video_id: videoId, suggestions, use_llm: useLlm, instruction }),
    }),
  exportEdit: (title: string, clips: { video_id: number; start: number; end: number }[]) =>
    request<Job>('/api/edit/export', { method: 'POST', body: JSON.stringify({ title, clips }) }),
  voices: () => request<EngineInfo>('/api/narrate/voices'),
  createNarration: (videoId: number, maxFrames: number) =>
    request<{ job: Job; task: NarrateTask | null }>('/api/narrate/tasks', {
      method: 'POST',
      body: JSON.stringify({ video_id: videoId, max_frames: maxFrames }),
    }),
  narration: (id: string) => request<NarrateTask>(`/api/narrate/tasks/${id}`),
  saveTranscript: (id: string, segments: NarrateTask['transcript']['segments']) =>
    request<NarrateTask>(`/api/narrate/tasks/${id}/transcript`, {
      method: 'PUT',
      body: JSON.stringify({ segments }),
    }),
  draftScript: (id: string, body: { mode: 'llm' | 'skeleton'; instruction: string; tone: string; target_duration: number }) =>
    request<{ job: Job; task: NarrateTask | null }>(`/api/narrate/tasks/${id}/script`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  saveScript: (id: string, beats: NonNullable<NarrateTask['script']>['beats'], voice: VoiceSettings) =>
    request<NarrateTask>(`/api/narrate/tasks/${id}/script`, {
      method: 'PUT',
      body: JSON.stringify({ beats, voice }),
    }),
  renderNarration: (id: string) =>
    request<{ job: Job; task: NarrateTask | null }>(`/api/narrate/tasks/${id}/render`, { method: 'POST' }),
  importNarration: (id: string) =>
    request<{ video: Video; task: NarrateTask }>(`/api/narrate/tasks/${id}/import`, { method: 'POST' }),
  previewVoice: async (text: string, voice: VoiceSettings) => {
    const response = await fetch('/api/narrate/tts-preview', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, engine: voice.engine, voice: voice.voice, rate: voice.rate }),
    })
    if (!response.ok) {
      const data = await response.json().catch(() => ({}))
      const detail = data.detail
      if (detail && typeof detail === 'object') {
        throw new ApiError(detail.message || '试听失败', detail.code || 'tts', detail.action || '', response.status)
      }
      throw new ApiError('试听失败', 'tts', '', response.status)
    }
    return response.blob()
  },
}
