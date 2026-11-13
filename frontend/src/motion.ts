import http, { raiseApi } from './apiClient'
import type { AIImageAsset, Task } from './api'

export interface MotionRequest {
  video_id?: number
  start?: number
  duration?: number
  width?: number
  fps?: number
  format?: 'gif' | 'webp'
  loop?: number
  colors?: number
  dither?: boolean
  quality?: number
  ingest?: boolean
}

export async function createMotion(body: MotionRequest): Promise<Task> {
  try {
    const { data } = await http.post<Task>('/motion', body)
    return data
  } catch (error) {
    raiseApi(error)
  }
}

export async function uploadMotion(file: File, body: MotionRequest): Promise<Task> {
  const form = new FormData()
  form.append('file', file)
  const fields: Record<string, string> = {
    start: String(body.start ?? 0),
    duration: String(body.duration ?? ''),
    width: String(body.width ?? 480),
    fps: String(body.fps ?? 12),
    format: body.format ?? 'gif',
    loop: String(body.loop ?? 0),
    colors: String(body.colors ?? 256),
    dither: body.dither === false ? 'false' : 'true',
    quality: String(body.quality ?? 75),
    ingest: body.ingest ? 'true' : 'false',
  }
  Object.entries(fields).forEach(([key, value]) => form.append(key, value))
  try {
    const { data } = await http.post<Task>('/motion/upload', form)
    return data
  } catch (error) {
    raiseApi(error)
  }
}

export async function ingestMotion(jobId: string): Promise<AIImageAsset> {
  try {
    const { data } = await http.post<AIImageAsset>(`/motion/${encodeURIComponent(jobId)}/ingest`)
    return data
  } catch (error) {
    raiseApi(error)
  }
}
