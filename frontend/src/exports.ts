import http from './api'

export type ExportStatus = 'running' | 'success' | 'failed'

export interface ExportManifest {
  export_id: string
  /** running/failed 记录只有状态字段；success 才带完整清单。 */
  status?: ExportStatus
  error?: string
  without_subtitles_note?: string
  title: string
  complete: boolean
  archive: string
  archive_size: number
  download_url: string
  selected_cover: string
  selected_title: string
  files: {
    landscape: string
    portrait: string
    square: string
    /** 拿不到真正无字幕源时后端不写该字段，前端需容忍缺失。 */
    without_subtitles?: string
    srt: string
    covers: string[]
    titles: string
  }
  title_options: Array<{ kind: string; title: string; description: string }>
}

/** 创建导出任务：后端立刻返回 running 记录，导出在后台线程进行。 */
export async function createExport(videoId: number): Promise<ExportManifest> {
  const { data } = await http.post<ExportManifest>('/exports', { video_id: videoId }, { timeout: 30000 })
  return data
}

/** 轮询导出状态：running → success（带完整清单）/ failed（带 error）。 */
export async function getExport(exportId: string): Promise<ExportManifest> {
  const { data } = await http.get<ExportManifest>(`/exports/${exportId}`, { timeout: 30000 })
  return data
}

export async function selectExport(exportId: string, cover: string, title: string): Promise<ExportManifest> {
  const { data } = await http.put<ExportManifest>(`/exports/${exportId}/select`, { cover, title })
  return data
}

export function exportFileUrl(exportId: string, name: string): string {
  return `/api/exports/${exportId}/files/${encodeURIComponent(name)}`
}
