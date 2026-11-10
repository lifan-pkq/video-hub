import axios from 'axios'

// ================= 类型定义（与后端契约一致） =================

export interface Video {
  id: number
  title: string
  filename: string
  size: number
  duration: number | null
  width: number | null
  height: number | null
  cover: string | null
  source_platform: string | null
  source_url: string | null
  quality_score?: number | null
  analysis_status?: 'pending' | 'running' | 'success' | 'partial' | 'failed'
  analysis_error?: string
  analysis?: {
    summary?: string
    people?: string[]
    scenes?: string[]
    emotion_tags?: string[]
    best_segments?: Array<{ start: number; end: number; reason?: string }>
  } | null
  created_at: string
  file_url: string
  cover_url: string | null
}

export interface ParseResult {
  url: string
  title: string
  author: string
  cover_url: string
  duration: number
  platform: string
  video_id: string
  available_best?: { width?: number; height?: number; format_note?: string; tbr?: number; vcodec?: string }
}

export type TaskStatus = 'pending' | 'running' | 'success' | 'failed' | 'partial' | 'cancelled'

export interface EditTaskInput {
  mode?: 'analyze' | 'plan' | 'concat'
  video_ids?: number[]
  kinds?: string[]
  custom_prompt?: string
  target_duration?: number
  title?: string
  clips?: EditClip[]
  split_parts?: number
}

export interface TaskError {
  code?: string
  message?: string
  action?: string
}

export interface Task {
  id: string
  type: 'download' | 'edit' | 'ai' | 'narrate' | 'record'
  kind?: string
  title: string
  status: TaskStatus
  progress: number
  input?: EditTaskInput & { task_id?: string; kind?: string; mode?: string; video_id?: number }
  result: {
    video?: Video
    videos?: Video[]
    task_id?: string
    clips?: Array<{ video_id?: number; suggestions?: Suggestion[]; transcript?: { warning?: string; segments?: TranscriptSegment[] } }>
    download_url?: string
    output_path?: string
    warnings?: string[]
    source?: string
    source_title?: string
    pack_url?: string
    budget?: number
    video_id?: number
    shorts?: DouyinShort[]
  } | null
  error?: TaskError | null
  created_at: string
  finished_at: string | null
  logs: string[]
}

export type RecordingStatus = 'recording' | 'completed' | 'stopped' | 'failed'

export interface Recording {
  id: number
  url: string
  platform: string
  title: string
  filename: string
  status: RecordingStatus
  size: number
  duration: number | null
  error: string | null
  video_id: number | null
  quality: string
  fps: number
  created_at: string
  started_at?: string | null
  finished_at: string | null
  /** 仅 recording 状态时返回（秒）；未真正开始录制前为 0 */
  elapsed?: number
  /** 仍在解析/握手/等待首帧，计时尚未开始 */
  starting?: boolean
  segments?: Array<{ index: number; video_id: number; duration: number; size: number; quality_score: number; summary_status: string }>
}

/** 画质档位 */
export const QUALITY_OPTIONS = [
  { value: 'best', label: '原画' },
  { value: 'high', label: '高清' },
  { value: 'medium', label: '标清' },
  { value: 'low', label: '流畅' },
]

export function qualityLabel(key: string): string {
  return QUALITY_OPTIONS.find((q) => q.value === key)?.label ?? key
}

/** 帧率档位（0 = 原始） */
export const FPS_OPTIONS = [
  { value: 0, label: '原始帧率' },
  { value: 60, label: '60 fps' },
  { value: 30, label: '30 fps' },
  { value: 24, label: '24 fps' },
]

export function fpsLabel(fps: number): string {
  return fps > 0 ? `${fps} fps` : '原始帧率'
}

// ================= axios 实例 =================

const http = axios.create({
  baseURL: '/api',
  timeout: 120000,
})

export default http

// ================= WebSocket =================

/**
 * 推导实时事件流 WebSocket 地址（ws/wss 协议自适应）。
 * 由 axios baseURL（默认 '/api'，可为绝对 URL）+ 当前页面 host 推导，
 * 结果形如 ws(s)://host/api/ws。
 */
export function getWsUrl(): string {
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  try {
    const base = http.defaults.baseURL ?? '/api'
    const u = new URL(base, window.location.origin)
    return `${proto}//${u.host}${u.pathname.replace(/\/+$/, '')}/ws`
  } catch {
    return `${proto}//${window.location.host}/api/ws`
  }
}

// ================= 服务器状态 =================

export interface ServerServiceStatus {
  key: string
  name: string
  active: boolean
  latency_ms: number
  memory_mb: number | null
  pid: number | null
  detail: string
}

export interface SystemStatus {
  memory: {
    total_mb?: number
    used_mb?: number
    available_mb?: number
    percent?: number
    swap_total_mb?: number
    swap_used_mb?: number
  }
  disk: {
    total_gb?: number
    used_gb?: number
    free_gb?: number
    percent?: number
  }
  load: number[]
  uptime_seconds: number | null
  services: ServerServiceStatus[]
  checked_at: number
}

export async function fetchSystemStatus(): Promise<SystemStatus> {
  const { data } = await http.get<SystemStatus>('/system/status', { timeout: 15000 })
  return data
}

export interface HealthReport {
  status: string
  ffmpeg: boolean
  llm: boolean
  vision: boolean
  asr: string
  tts: Array<{ id: string; available: boolean; detail: string }>
}

export async function fetchHealth(): Promise<HealthReport> {
  const { data } = await http.get<HealthReport>('/health', { timeout: 10000 })
  return data
}

// ================= AI 媒体 / Key 池 =================

export interface AIPoolKey {
  hint: string
  available: boolean
  cooldown_seconds: number
  successes: number
  failures: number
  last_status: number | null
  last_error: string
}

export interface AIPoolStatus {
  name: string
  total: number
  available: number
  keys: AIPoolKey[]
}

export interface ConsistencyReport {
  status: 'pass' | 'review' | 'unknown' | 'skipped'
  score?: number
  reason?: string
  mismatches?: string[]
}

export interface AIImageResult {
  ok: boolean
  url: string
  name: string
  model: string
  size: number
  attempts?: number
  consistency?: ConsistencyReport
}

export async function fetchAIPool(): Promise<AIPoolStatus> {
  const { data } = await http.get<AIPoolStatus>('/ai/pool', { timeout: 15000 })
  return data
}

export interface AIImageAsset {
  id: number
  filename: string
  title: string
  prompt: string
  size: number
  width: number | null
  height: number | null
  created_at: string
  url: string
  public_url: string
  download_url: string
}

export async function fetchAIImages(): Promise<AIImageAsset[]> {
  const { data } = await http.get<AIImageAsset[]>('/ai/images', { timeout: 30000 })
  return data
}

export async function saveGeneratedAIImage(payload: {
  name: string
  title?: string
  prompt?: string
}): Promise<AIImageAsset> {
  const { data } = await http.post<AIImageAsset>('/ai/images/save', payload, { timeout: 60000 })
  return data
}

export async function uploadAIImage(file: File, title = ''): Promise<AIImageAsset> {
  const form = new FormData()
  form.append('file', file)
  form.append('title', title)
  const { data } = await http.post<AIImageAsset>('/ai/images', form, { timeout: 120000 })
  return data
}

export async function deleteAIImage(imageId: number): Promise<void> {
  await http.delete('/ai/images/' + imageId)
}

export interface AIReference {
  url: string
  data_uri: string
  name: string
  size: number
}

export async function uploadAIReference(file: File): Promise<AIReference> {
  const form = new FormData()
  form.append('file', file)
  const { data } = await http.post<AIReference>('/ai/media/references', form, { timeout: 120000 })
  return data
}

export async function generateAIImage(payload: {
  prompt: string
  size: string
  ratio: string
  images?: string[]
  character_profile_id?: string
  scene_profile_id?: string
  style_profile_id?: string
}): Promise<AIImageResult> {
  const { data } = await http.post<AIImageResult>('/ai/media/images', payload, { timeout: 300000 })
  return data
}

export async function generateAIVideo(payload: {
  prompt: string
  seconds: number
  aspect_ratio: string
  first_frame?: string
  last_frame?: string
  images?: string[]
  save_to_library?: boolean
  character_profile_id?: string
  scene_profile_id?: string
  style_profile_id?: string
  transition?: string
}): Promise<{ task_id: string; mode?: string }> {
  const { data } = await http.post<{ task_id: string }>('/ai/media/videos', payload, { timeout: 60000 })
  return data
}

export async function generateAIVideoBatch(payload: {
  shots: Array<{ prompt: string; seconds?: number; transition?: string; first_frame?: string; last_frame?: string }>
  character_profile_id?: string
  scene_profile_id?: string
  style_profile_id?: string
  aspect_ratio?: string
}): Promise<{ tasks: Array<{ task_id: string }>; count: number }> {
  const { data } = await http.post('/ai/media/batches', payload, { timeout: 60000 })
  return data
}

// ================= 系统维护 =================

export interface MaintenanceStorage {
  videos: { count: number; size: number; files_size: number; covers_size: number }
  tasks: { count: number; files: number; size: number }
  temp: { files: number; size: number }
  cache: { files: number; size: number }
  logs: { files: number; size: number }
  checked_at: string
}

export interface MaintenanceSettings {
  enabled: boolean
  interval_hours: number
  retention_days: number
  clean_logs: boolean
  clear_videos: boolean
  last_run_at: string
  next_run_at: string
  last_result: Record<string, unknown> | string | null
}

export interface MaintenanceStatus {
  storage: MaintenanceStorage
  settings: MaintenanceSettings
}

export async function fetchMaintenanceStatus(): Promise<MaintenanceStatus> {
  const { data } = await http.get<MaintenanceStatus>('/maintenance/status', { timeout: 30000 })
  return data
}

export async function clearAllVideos(force = false): Promise<{ ok: boolean; videos: number; bytes: number }> {
  const { data } = await http.post<{ ok: boolean; videos: number; bytes: number }>('/maintenance/videos/clear', { force }, { timeout: 120000 })
  return data
}

export async function cleanMaintenanceLogs(retentionDays = 7): Promise<{ ok: boolean; files: number; bytes: number }> {
  const { data } = await http.post<{ ok: boolean; files: number; bytes: number }>('/maintenance/logs/clean', { retention_days: retentionDays }, { timeout: 120000 })
  return data
}

export async function saveMaintenanceSettings(payload: Omit<MaintenanceSettings, 'last_run_at' | 'next_run_at' | 'last_result'>): Promise<MaintenanceSettings> {
  const { data } = await http.put<MaintenanceSettings>('/maintenance/settings', payload)
  return data
}

// ================= 视频库 =================

export async function fetchVideos(): Promise<Video[]> {
  const { data } = await http.get<Video[]>('/videos')
  return data
}

export interface VideoUnderstanding {
  video_id: number
  duration: number
  tracks: {
    speech: Array<{ start: number; end: number; text: string; speaker?: string }>
    visual: Array<{ start: number; end: number; text: string; subject_box?: { x: number; y: number; width: number; height: number } }>
    audio: Array<{ start: number; end: number; kind: 'silence' | 'beat' }>
  }
  emotion_curve?: Array<{ timestamp: number; score: number; label?: string }>
}

export async function fetchUnderstanding(videoId: number): Promise<VideoUnderstanding> {
  const { data } = await http.get<VideoUnderstanding>(`/understanding/${videoId}`, { timeout: 30000 })
  return data
}

export async function uploadVideo(
  file: File,
  title: string | undefined,
  onProgress: (percent: number) => void,
): Promise<Video> {
  const form = new FormData()
  form.append('file', file)
  if (title) form.append('title', title)
  const { data } = await http.post<Video>('/videos/upload', form, {
    timeout: 0, // 上传不限时
    onUploadProgress: (e) => {
      if (e.total) onProgress(Math.round((e.loaded * 100) / e.total))
    },
  })
  return data
}

export async function deleteVideo(id: number): Promise<{ ok: boolean }> {
  const { data } = await http.delete<{ ok: boolean }>(`/videos/${id}`)
  return data
}

/** 重命名视频标题 */
export async function renameVideo(id: number, title: string): Promise<Video> {
  const { data } = await http.patch<Video>(`/videos/${id}`, { title })
  return data
}

/** 无损截取视频片段，生成新视频入库（原视频不动） */
export async function trimVideo(id: number, start: number, end: number): Promise<Video> {
  const { data } = await http.post<Video>(`/videos/${id}/trim`, { start, end })
  return data
}

// ================= 剪辑拼接 =================

export interface EditClip {
  video_id: number
  start: number
  end: number
  speed?: number
  freeze?: number
  kind?: 'video' | 'image'
  image_id?: number
  image_name?: string
  video_name?: string
  overlay?: boolean
  title?: string
}

export class ApiError extends Error {
  code: string
  action: string
  status: number

  constructor(message: string, code = 'http', action = '', status = 0) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.action = action
    this.status = status
  }
}

function raiseApi(error: unknown): never {
  if (axios.isAxiosError(error)) {
    const detail = (error.response?.data as { detail?: unknown } | undefined)?.detail
    if (detail && typeof detail === 'object') {
      const record = detail as { message?: string; code?: string; action?: string }
      throw new ApiError(record.message || '请求失败', record.code || 'http', record.action || '', error.response?.status || 0)
    }
    if (typeof detail === 'string' && detail) throw new ApiError(detail, 'http', '', error.response?.status || 0)
  }
  throw error instanceof Error ? error : new ApiError('请求失败')
}

export type SuggestionKind = 'silence' | 'black' | 'pause' | 'filler' | 'scene'

export interface Suggestion {
  id: string
  video_id: number
  start: number
  end: number
  kind: SuggestionKind
  action: 'drop' | 'split'
  score: number
  reason: string
  evidence: string
  recommended: boolean
  llm_action?: 'drop' | 'keep' | null
  text?: string
}

export interface DouyinShort {
  index: number
  title: string
  hashtags: string[]
  description: string
  hook?: string
  reason?: string
  start: number
  end: number
  duration: number
  framing?: string
  captions_burned?: boolean
  file_url: string
  cover_url?: string
  video_id?: number | null
}

export function editTaskPath(task: { id: string; kind?: string }): string {
  if (task.kind === 'edit.douyin') return `/edit/douyin/${encodeURIComponent(task.id)}`
  return `/edit/${encodeURIComponent(task.id)}`
}

export interface EditJob {
  id: string
  kind: string
  status: TaskStatus
  progress: number
  stage: string
  label: string
  logs: string[]
  error: TaskError | null
  result: Task['result']
  input?: Task['input']
}

export interface TranscriptSegment {
  id: string
  start: number
  end: number
  text: string
}

export interface NarrateFrame {
  time: number
  image: string
  caption: string
  caption_error: string
  brightness: number | null
}

export interface NarrateBeat {
  id: string
  start: number
  end: number
  narration: string
  purpose: 'hook' | 'explain' | 'evidence' | 'turn' | 'close'
  pause?: boolean
  speed?: number
  freeze?: number
  insert_kind?: 'none' | 'image' | 'video'
  insert_mode?: 'overlay' | 'fullscreen'
  image_id?: number
  image_name?: string
  video_id?: number
  video_name?: string
}

export interface NarrateVoice {
  engine: 'auto' | 'edge' | 'openai' | 'espeak' | 'tone'
  voice: string
  rate: number
  original_volume: number
  subtitles: boolean
  font_size: number
  margin_v: number
}

export interface SignalNarrateTask {
  id: string
  video_id: number
  title: string
  duration: number
  width: number
  height: number
  transcript: { source: string; warning: string; segments: TranscriptSegment[]; error?: TaskError }
  frames: NarrateFrame[]
  script: { beats: NarrateBeat[]; warnings: string[]; mode?: string; instruction?: string; tone?: string; target_duration?: number } | null
  voice: NarrateVoice
  output: { file: string; duration: number; tts_engine: string; warnings: string[]; video_id?: number } | null
  warnings?: string[]
  step: string
  instruction?: string
  tone?: string
}

export interface NarrateEngines {
  engines: { id: string; available: boolean; detail: string }[]
  voices: { engine: string; id: string; label: string }[]
}

export async function fetchJob(id: string): Promise<EditJob> {
  try {
    const { data } = await http.get<EditJob>(`/jobs/${id}`)
    return data
  } catch (error) {
    raiseApi(error)
  }
}

export async function cancelJob(id: string): Promise<EditJob> {
  try {
    const { data } = await http.post<EditJob>(`/jobs/${id}/cancel`)
    return data
  } catch (error) {
    raiseApi(error)
  }
}

export async function startDouyinProduce(videoId: number, instruction = '', maxClips = 0): Promise<EditJob> {
  try {
    const { data } = await http.post<EditJob>('/edit/douyin', {
      video_id: videoId,
      instruction,
      max_clips: maxClips,
    }, { timeout: 0 })
    return data
  } catch (error) {
    raiseApi(error)
  }
}

export async function saveDouyinToLibrary(jobId: string, indexes: number[] = []): Promise<{
  videos: Video[]
  shorts: DouyinShort[]
  warnings: string[]
}> {
  try {
    const { data } = await http.post(`/edit/douyin/${jobId}/library`, { indexes })
    return data
  } catch (error) {
    raiseApi(error)
  }
}

export async function analyzeEdit(videoIds: number[]): Promise<EditJob> {
  try {
    const { data } = await http.post<EditJob>('/edit/analyze', { video_ids: videoIds }, { timeout: 0 })
    return data
  } catch (error) {
    raiseApi(error)
  }
}

export async function planEditSuggestions(
  videoId: number,
  suggestions: Suggestion[],
  useLlm: boolean,
  instruction = '',
): Promise<{ source: string; suggestions: Suggestion[]; warnings: string[]; drop_threshold: number }> {
  try {
    const { data } = await http.post('/edit/plan', {
      video_id: videoId,
      suggestions,
      use_llm: useLlm,
      instruction,
    })
    return data
  } catch (error) {
    raiseApi(error)
  }
}

export type HighlightSource = {
  video_id: number
  duration?: number
  title?: string
  segments?: unknown[]
  scenes?: unknown[]
  silence?: unknown[]
  black?: unknown[]
  frames?: unknown[]
  suggestions?: unknown[]
}

export type HighlightPlan = {
  source: string
  clips: Array<{ video_id: number; start: number; end: number; speed?: number; freeze?: number; reason?: string }>
  warnings: string[]
}

export async function highlightEdit(
  videos: HighlightSource[],
  instruction = '',
  useLlm = true,
): Promise<HighlightPlan> {
  try {
    const { data } = await http.post<HighlightPlan>('/edit/highlight', {
      videos,
      instruction,
      use_llm: useLlm,
    })
    return data
  } catch (error) {
    raiseApi(error)
  }
}

export async function exportEdit(title: string, clips: EditClip[]): Promise<EditJob> {
  try {
    const { data } = await http.post<EditJob>('/edit/export', { title, clips }, { timeout: 0 })
    return data
  } catch (error) {
    raiseApi(error)
  }
}

export async function fetchNarrateVoices(): Promise<NarrateEngines> {
  try {
    const { data } = await http.get<NarrateEngines>('/narrate/voices')
    return data
  } catch (error) {
    raiseApi(error)
  }
}

export async function fetchSignalNarrateTasks(limit = 20): Promise<SignalNarrateTask[]> {
  try {
    const { data } = await http.get<SignalNarrateTask[]>('/narrate/tasks', { params: { limit } })
    return data
  } catch (error) {
    raiseApi(error)
  }
}

export async function fetchSignalNarrateTask(taskId: string): Promise<SignalNarrateTask> {
  try {
    const { data } = await http.get<SignalNarrateTask>(`/narrate/tasks/${taskId}`)
    return data
  } catch (error) {
    raiseApi(error)
  }
}

export async function startNarrateUnderstand(
  videoId: number,
  opts?: { instruction?: string; tone?: string; framesPerSecond?: number },
): Promise<{ job: EditJob; task: SignalNarrateTask | null }> {
  try {
    const { data } = await http.post(
      '/narrate/tasks',
      {
        video_id: videoId,
        frames_per_second: opts?.framesPerSecond ?? 2,
        instruction: opts?.instruction || '',
        tone: opts?.tone || '',
      },
      { timeout: 0 },
    )
    return data
  } catch (error) {
    raiseApi(error)
  }
}

export async function saveNarrateTranscript(taskId: string, segments: TranscriptSegment[]): Promise<SignalNarrateTask> {
  try {
    const { data } = await http.put<SignalNarrateTask>(`/narrate/tasks/${taskId}/transcript`, { segments })
    return data
  } catch (error) {
    raiseApi(error)
  }
}

export async function draftNarrateScript(
  taskId: string,
  body: { mode: 'llm' | 'skeleton' | 'manual'; instruction: string; tone: string; target_duration: number },
): Promise<{ job: EditJob; task: SignalNarrateTask | null }> {
  try {
    const { data } = await http.post(`/narrate/tasks/${taskId}/script`, body, { timeout: 0 })
    return data
  } catch (error) {
    raiseApi(error)
  }
}

export async function saveNarrateScript(taskId: string, beats: NarrateBeat[], voice: NarrateVoice): Promise<SignalNarrateTask> {
  try {
    const { data } = await http.put<SignalNarrateTask>(`/narrate/tasks/${taskId}/script`, { beats, voice })
    return data
  } catch (error) {
    raiseApi(error)
  }
}

export async function renderNarrate(taskId: string): Promise<{ job: EditJob; task: SignalNarrateTask | null }> {
  try {
    const { data } = await http.post(`/narrate/tasks/${taskId}/render`, {}, { timeout: 0 })
    return data
  } catch (error) {
    raiseApi(error)
  }
}

export async function importNarrateOutput(taskId: string): Promise<{ video: Video; task: SignalNarrateTask }> {
  try {
    const { data } = await http.post(`/narrate/tasks/${taskId}/import`)
    return data
  } catch (error) {
    raiseApi(error)
  }
}

export async function previewNarrateVoice(text: string, voice: NarrateVoice): Promise<Blob> {
  try {
    const { data } = await http.post<Blob>(
      '/narrate/tts-preview',
      { text, engine: voice.engine, voice: voice.voice, rate: voice.rate },
      { responseType: 'blob', timeout: 60000 },
    )
    return data
  } catch (error) {
    raiseApi(error)
  }
}

// ================= 仪表盘统计 =================

export interface Stats {
  videos: { count: number; size: number; analyzed: number; high_quality: number }
  recordings: { total: number; active: number }
  tasks: { running: number; today: number }
}

export async function fetchStats(): Promise<Stats> {
  const { data } = await http.get<Stats>('/stats')
  return data
}

// ================= 下载 =================

export async function parseDownload(text: string): Promise<ParseResult> {
  const { data } = await http.post<ParseResult>('/download/parse', { text })
  return data
}

export interface Inspiration {
  id: number
  url: string
  note: string
  created_at: string
}

export async function fetchInspirations(): Promise<Inspiration[]> {
  const { data } = await http.get<Inspiration[]>('/inspirations')
  return data
}

export async function addInspiration(url: string, note: string): Promise<Inspiration> {
  const { data } = await http.post<Inspiration>('/inspirations', { url, note })
  return data
}

export async function deleteInspiration(id: number): Promise<void> {
  await http.delete(`/inspirations/${id}`)
}

export async function createDownload(text: string): Promise<{ task_id: string }> {
  const { data } = await http.post<{ task_id: string }>('/download', { text })
  return data
}

/** 批量下载：每条文本一个任务，后端串行排队执行 */
export async function createBatchDownload(
  texts: string[],
): Promise<{ task_ids: string[]; count: number }> {
  const { data } = await http.post<{ task_ids: string[]; count: number }>('/download/batch', { texts })
  return data
}

export async function fetchTask(taskId: string): Promise<Task> {
  const { data } = await http.get<Task>(`/tasks/${taskId}`)
  return data
}

export async function cancelTask(taskId: string): Promise<{ ok: boolean }> {
  try {
    const { data } = await http.post<{ ok: boolean }>(`/tasks/${taskId}/cancel`)
    return data
  } catch (error) {
    raiseApi(error)
  }
}

/** 任务列表（不含日志，按创建时间倒序） */
export type TaskSummary = Omit<Task, 'logs'>

export async function fetchTasks(): Promise<TaskSummary[]> {
  const { data } = await http.get<TaskSummary[]>('/tasks')
  return data
}

// ================= 直播录制 =================

export async function createRecording(
  url: string,
  title?: string,
  quality = 'best',
  fps = 0,
): Promise<Recording> {
  // 启动时会同步阻塞数秒解析流地址
  const { data } = await http.post<Recording>('/recordings', { url, title, quality, fps })
  return data
}

export async function fetchRecordings(): Promise<Recording[]> {
  const { data } = await http.get<Recording[]>('/recordings')
  return data
}

export async function fetchRecording(id: number): Promise<Recording> {
  const { data } = await http.get<Recording>(`/recordings/${id}`)
  return data
}

export async function stopRecording(id: number): Promise<Recording> {
  const { data } = await http.post<Recording>(`/recordings/${id}/stop`)
  return data
}

/** 改录制标题（已入库的会同步视频库标题） */
export async function renameRecording(id: number, title: string): Promise<Recording> {
  const { data } = await http.patch<Recording>(`/recordings/${id}`, { title })
  return data
}

export async function deleteRecording(id: number, deleteFile: boolean): Promise<{ ok: boolean }> {
  const { data } = await http.delete<{ ok: boolean }>(`/recordings/${id}`, {
    params: { delete_file: deleteFile },
  })
  return data
}

/** 边录边看预览流地址 */
export function recordingPreviewUrl(id: number): string {
  return `/api/recordings/${id}/preview`
}

// ================= 工具函数 =================

export interface Problem {
  code: string
  message: string
  action: string
}

export function errorDetail(e: unknown): Problem {
  if (e instanceof ApiError) return { code: e.code || 'http', message: e.message, action: e.action || '' }
  if (axios.isAxiosError(e)) {
    const detail = (e.response?.data as { detail?: unknown } | undefined)?.detail
    if (typeof detail === 'string' && detail) return { code: 'http', message: detail, action: '' }
    if (detail && typeof detail === 'object') {
      const record = detail as { code?: string; message?: string; action?: string }
      return { code: record.code || 'http', message: record.message || '请求失败', action: record.action || '' }
    }
    return { code: 'http', message: e.message || '请求失败', action: '' }
  }
  if (e instanceof Error) return { code: 'error', message: e.message, action: '' }
  return { code: 'error', message: '请求失败', action: '' }
}

export function errorMessage(e: unknown): string {
  const problem = errorDetail(e)
  return problem.action ? `${problem.message} ${problem.action}` : problem.message
}

/** 秒 → mm:ss */
export function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null || Number.isNaN(seconds)) return '--:--'
  const s = Math.round(seconds)
  const m = Math.floor(s / 60)
  const r = s % 60
  return `${String(m).padStart(2, '0')}:${String(r).padStart(2, '0')}`
}

/** 秒 → hh:mm:ss（录制时长） */
export function formatElapsed(seconds: number | null | undefined): string {
  if (seconds == null || Number.isNaN(seconds)) return '00:00:00'
  const s = Math.max(0, Math.round(seconds))
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const r = s % 60
  return [h, m, r].map((n) => String(n).padStart(2, '0')).join(':')
}

/** 字节 → MB / GB（自动进位） */
export function formatSize(bytes: number): string {
  const mb = bytes / 1024 / 1024
  if (mb >= 1024) return `${(mb / 1024).toFixed(2)} GB`
  return `${mb.toFixed(1)} MB`
}

export function formatTime(iso: string | null | undefined): string {
  if (!iso) return '-'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString('zh-CN', { hour12: false })
}

/** 相对时间：刚刚 / N 分钟前 / N 小时前 / N 天前 */
export function formatRelative(iso: string | null | undefined): string {
  if (!iso) return '-'
  const t = new Date(iso).getTime()
  if (Number.isNaN(t)) return iso
  const diff = Math.max(0, Date.now() - t)
  const m = Math.floor(diff / 60000)
  if (m < 1) return '刚刚'
  if (m < 60) return `${m} 分钟前`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h} 小时前`
  const d = Math.floor(h / 24)
  if (d < 30) return `${d} 天前`
  return formatTime(iso)
}


export const PLATFORMS: { key: string; label: string }[] = [
  { key: 'douyin', label: '抖音' },
  { key: 'xiaohongshu', label: '小红书' },
  { key: 'kuaishou', label: '快手' },
  { key: 'bilibili', label: '哔哩哔哩' },
]

export function platformLabel(key: string | null | undefined): string {
  if (!key) return '本地'
  if (key === 'direct') return '直链'
  if (key === 'unknown') return '未知'
  if (key === 'edited') return '剪辑合成'
  if (key === 'douyin_short') return '抖音成片'
  if (key === 'narrato' || key === 'narration') return '历史解说'
  if (key === 'ai_edit') return 'AI 剪辑'
  if (key === 'legacy') return '历史任务'
  // 直播录制入库的来源：live_douyin / live_bilibili / live_direct …
  if (key.startsWith('live_')) return `直播·${platformLabel(key.slice(5))}`
  return PLATFORMS.find((p) => p.key === key)?.label ?? key
}

// ================= AI 短剧 =================

export interface DramaShotMedia {
  name: string
  url: string
  status: string
  attempts: number
  quality?: { score: number; passed: boolean; reasons?: string[] } | null
  revision?: number
}

export interface DramaShotQuality {
  stage: string
  attempt: number
  time: string
  score: number
  passed: boolean
  reasons?: string[]
  effective_prompt?: string
  error?: string
}

export interface DramaShotBoard {
  beat: number
  label: string
  name: string
  url: string
  status: string
  prompt?: string
  quality?: { score: number; passed: boolean; reasons?: string[] } | null
  attempts?: number
  revision?: number
}

export interface DramaShot {
  id: string
  index: number
  scene: string
  action: string
  expression: string
  camera: string
  seconds: number
  dialogue: string
  characters: string[]
  prompt?: string
  frame?: string
  frame_url?: string
  // 后端实际返回的是对象，不是字符串：image/video 都是 {name,url,status,attempts,quality}
  image?: DramaShotMedia
  video?: DramaShotMedia
  video_url?: string
  storyboard?: DramaShotBoard[]
  attempts: number
  status: string
  error?: string
  recoverable?: boolean
  revision?: number
  quality_history?: DramaShotQuality[]
}

export interface DramaEpisode {
  index: number
  title: string
  summary: string
  source_excerpt?: string
  shots: DramaShot[]
  video: string
  video_url?: string
  srt: string
  srt_url?: string
  duration?: number
  status: string
}

export interface DramaCharacter {
  id: string
  name: string
  age: number | string | null
  gender: string
  appearance: string
  clothing: string
  personality: string
  profile_id: string
  anchor: string
  anchor_url?: string
  status: string
}

export interface DramaOptions {
  episodes_count: number
  shots_per_episode: number
  seconds_per_shot: number
  aspect_ratio: string
  style: string
  burn_subtitles: boolean
  max_attempts: number
  storyboard_frames?: number
}

export interface DramaEpisodeSummary {
  index: number
  title: string
  status: string
  video: string
  video_url?: string
  shots_done: number
  shots_total: number
}

export interface DramaProjectSummary {
  id: string
  title: string
  status: 'pending' | 'running' | 'success' | 'failed' | 'cancelled' | 'planned'
  stage: string
  progress: number
  error: string
  created_at: string
  updated_at: string
  options: DramaOptions
  novel_chars: number
  episodes: DramaEpisodeSummary[]
}

export interface DramaProject extends Omit<DramaProjectSummary, 'episodes'> {
  logline: string
  characters: DramaCharacter[]
  episodes: DramaEpisode[]
  logs: string[]
}

export async function fetchDramaProjects(): Promise<DramaProjectSummary[]> {
  const { data } = await http.get<DramaProjectSummary[]>('/drama/projects', { timeout: 30000 })
  return data
}

export async function createDramaProject(
  payload: { novel: string; title: string } & Partial<DramaOptions>,
): Promise<DramaProjectSummary> {
  const { data } = await http.post<DramaProjectSummary>('/drama/projects', payload, { timeout: 60000 })
  return data
}

export async function startDramaProject(id: string): Promise<DramaProjectSummary> {
  const { data } = await http.post<DramaProjectSummary>(`/drama/projects/${id}/start`, {}, { timeout: 30000 })
  return data
}

export async function fetchDramaProject(id: string): Promise<DramaProject> {
  const { data } = await http.get<DramaProject>(`/drama/projects/${id}`, { timeout: 30000 })
  return data
}

export async function resumeDramaProject(id: string): Promise<DramaProjectSummary> {
  const { data } = await http.post<DramaProjectSummary>(`/drama/projects/${id}/resume`, {}, { timeout: 30000 })
  return data
}

export async function cancelDramaProject(id: string): Promise<DramaProjectSummary> {
  const { data } = await http.post<DramaProjectSummary>(`/drama/projects/${id}/cancel`, {}, { timeout: 30000 })
  return data
}

export async function deleteDramaProject(id: string): Promise<void> {
  await http.delete(`/drama/projects/${id}`, { timeout: 30000 })
}

export type DramaShotRetryStage = 'image' | 'video' | 'all' | 'board'

export async function retryDramaShot(id: string, shotId: string, stage: DramaShotRetryStage): Promise<void> {
  await http.post(`/drama/projects/${id}/shots/${shotId}/retry`, { stage }, { timeout: 30000 })
}

export async function generateDramaStoryboard(id: string): Promise<{ started: number; skipped: string[]; panels_per_shot: number }> {
  const { data } = await http.post<{ started: number; skipped: string[]; panels_per_shot: number }>(`/drama/projects/${id}/storyboard`, {}, { timeout: 30000 })
  return data
}

export async function generateShotStoryboard(id: string, shotId: string): Promise<void> {
  await http.post(`/drama/projects/${id}/shots/${shotId}/storyboard`, {}, { timeout: 30000 })
}
