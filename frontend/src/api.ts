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
}

export type TaskStatus = 'pending' | 'running' | 'success' | 'failed' | 'partial'

export interface Task {
  id: string
  type: 'download' | 'publish' | 'edit'
  title: string
  status: TaskStatus
  progress: number
  /** 抖音发布触发短信验证时为 true（引擎已自动请求验证码） */
  needs_verify: boolean
  result: {
    video?: Video
    /** 发布结果，key 格式 "平台:账号"，如 "douyin:default" */
    targets?: Record<string, { status: 'success' | 'failed'; message: string }>
    [key: string]: unknown
  }
  created_at: string
  finished_at: string | null
  logs: string[]
}

export interface PublishTarget {
  platform: string
  account: string
}

export interface PublishPayload {
  video_id: number
  targets: PublishTarget[]
  title: string
  desc: string
  tags: string[]
  schedule: string | null
}

export interface PublishRecord {
  id: number
  task_id: string
  video_id: number
  video_title: string
  platform: string
  account: string
  title: string
  desc: string
  tags: string
  status: string
  log: string
  created_at: string
  /** 定时发布时间（"YYYY-MM-DD HH:mm"），立即发布为 null */
  schedule: string | null
}

export interface Account {
  platform: string
  account: string
  cookie_exists: boolean
  updated_at: string | null
  /** 仅 check=true 时返回 */
  valid?: boolean
  detail?: string
}

export interface CheckResult {
  platform: string
  account: string
  valid: boolean
  detail: string
}

export type LoginState = 'starting' | 'qrcode_ready' | 'success' | 'failed' | 'timeout'

export interface LoginSession {
  session_id: string
  platform: string
  account: string
  state: LoginState
  message: string
  has_qr: boolean
  qr_version: number
  done: boolean
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
  finished_at: string | null
  /** 仅 recording 状态时返回（秒） */
  elapsed?: number
}

/** 主播监控（自动录制） */
export interface Watcher {
  id: number
  platform: string
  url: string
  name: string
  quality: string
  fps: number
  enabled: number
  live: number
  active_recording_id: number | null
  last_checked_at: string | null
  created_at: string
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

// ================= 视频库 =================

export async function fetchVideos(): Promise<Video[]> {
  const { data } = await http.get<Video[]>('/videos')
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
}

/** 创建拼接渲染任务（异步），返回 task_id；split_parts>1 时成片再均分入库 */
export async function concatVideos(
  title: string,
  clips: EditClip[],
  splitParts = 1,
): Promise<{ task_id: string }> {
  const { data } = await http.post<{ task_id: string }>('/edit/concat', {
    title,
    clips,
    split_parts: splitParts,
  })
  return data
}

/** AI 智能分析（可多视频）：whisper 转写 + LLM 标出低价值/广告区间（异步任务） */
export async function analyzeVideos(
  videoIds: number[],
  kinds: string[],
  customPrompt = '',
): Promise<{ task_id: string }> {
  const { data } = await http.post<{ task_id: string }>('/edit/analyze', {
    video_ids: videoIds,
    kinds,
    custom_prompt: customPrompt,
  })
  return data
}

/** 单个视频的 AI 分析结果 */
export interface AiVideoAnalysis {
  video_id: number
  title: string
  duration: number
  /** 建议剪除的区间 */
  waste: { start: number; end: number; kind: 'intro' | 'outro' | 'ad'; reason: string }[]
  /** 保留区间（waste 的补集） */
  keep: { start: number; end: number }[]
}

// ================= 仪表盘统计 =================

export interface Stats {
  videos: { count: number; size: number }
  accounts: { total: number; with_cookie: number }
  recordings: { total: number; active: number }
  publish: { total: number; today: number; success: number }
  tasks: { running: number; needs_verify: number }
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

/** 任务列表（不含日志，按创建时间倒序） */
export type TaskSummary = Omit<Task, 'logs'>

export async function fetchTasks(): Promise<TaskSummary[]> {
  const { data } = await http.get<TaskSummary[]>('/tasks')
  return data
}

/** 提交抖音短信验证码（4-8 位数字；任务已结束会 400） */
export async function submitVerifyCode(
  taskId: string,
  code: string,
): Promise<{ ok: boolean; message?: string }> {
  const { data } = await http.post<{ ok: boolean; message?: string }>(
    `/tasks/${taskId}/verify-code`,
    { code },
  )
  return data
}

// ================= 作者作品 =================

/** 作者主页解析出的单条作品 */
export interface AuthorWork {
  id: string
  title: string
  cover_url: string | null
  url: string
  duration: number | null
  like_count: number | null
  /** 发布时间戳（秒） */
  create_time: number | null
}

/** POST /author/parse 响应 */
export interface AuthorWorksResult {
  author: {
    name: string
    avatar: string | null
    url: string
    platform: string
  }
  works: AuthorWork[]
}

/** 解析作者主页作品列表（浏览器抓取，可能耗时 30-90 秒，超时放宽到 130s；429 = 另一个抓取进行中） */
export async function parseAuthorWorks(text: string): Promise<AuthorWorksResult> {
  const { data } = await http.post<AuthorWorksResult>('/author/parse', { text }, { timeout: 130000 })
  return data
}

// ================= 发布 =================

export async function publishVideo(payload: PublishPayload): Promise<{ task_id: string }> {
  const { data } = await http.post<{ task_id: string }>('/publish', payload)
  return data
}

export async function fetchPublishRecords(): Promise<PublishRecord[]> {
  const { data } = await http.get<PublishRecord[]>('/publish/records')
  return data
}

// ================= 平台账号 =================

export async function fetchAccounts(check = false): Promise<Account[]> {
  const { data } = await http.get<Account[]>('/accounts', {
    params: check ? { check: true } : undefined,
  })
  return data
}

export async function checkAccount(platform: string, account: string): Promise<CheckResult> {
  // 校验较慢（10-90 秒），单独放宽超时
  const { data } = await http.post<CheckResult>(
    `/accounts/${platform}/${account}/check`,
    null,
    { timeout: 120000 },
  )
  return data
}

export async function uploadCookie(
  platform: string,
  account: string,
  file: File,
): Promise<{ ok: boolean; path: string; message: string }> {
  const form = new FormData()
  form.append('file', file)
  const { data } = await http.post<{ ok: boolean; path: string; message: string }>(
    `/accounts/${platform}/${account}/cookie`,
    form,
  )
  return data
}

export async function deleteAccount(
  platform: string,
  account: string,
): Promise<{ ok: boolean; message: string }> {
  const { data } = await http.delete<{ ok: boolean; message: string }>(
    `/accounts/${platform}/${account}`,
  )
  return data
}

// ================= 扫码登录 =================

export async function startLogin(platform: string, account: string): Promise<LoginSession> {
  const { data } = await http.post<LoginSession>('/accounts/login', { platform, account })
  return data
}

export async function fetchLoginStatus(sessionId: string): Promise<LoginSession> {
  const { data } = await http.get<LoginSession>(`/accounts/login/${sessionId}`)
  return data
}

export async function cancelLogin(sessionId: string): Promise<{ ok: boolean }> {
  const { data } = await http.post<{ ok: boolean }>(`/accounts/login/${sessionId}/cancel`)
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

// ================= 主播监控（自动录制） =================

export async function fetchWatchers(): Promise<Watcher[]> {
  const { data } = await http.get<Watcher[]>('/watchers')
  return data
}

export async function createWatcher(
  url: string,
  name: string,
  quality: string,
  fps: number,
): Promise<Watcher> {
  const { data } = await http.post<Watcher>('/watchers', { url, name, quality, fps })
  return data
}

export async function updateWatcher(
  id: number,
  patch: { enabled?: boolean; name?: string; quality?: string; fps?: number },
): Promise<Watcher> {
  const { data } = await http.patch<Watcher>(`/watchers/${id}`, patch)
  return data
}

export async function deleteWatcher(id: number): Promise<{ ok: boolean }> {
  const { data } = await http.delete<{ ok: boolean }>(`/watchers/${id}`)
  return data
}

// ================= 工具函数 =================

export function errorMessage(e: unknown): string {
  if (axios.isAxiosError(e)) {
    const detail = (e.response?.data as { detail?: unknown } | undefined)?.detail
    if (typeof detail === 'string') return detail
    if (detail) return JSON.stringify(detail)
    return e.message
  }
  if (e instanceof Error) return e.message
  return String(e)
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

/** 支持网页扫码登录的平台（B站走 TV 端官方 API 扫码） */
export const QR_LOGIN_PLATFORMS = ['douyin', 'xiaohongshu', 'kuaishou', 'bilibili']

export function platformLabel(key: string | null | undefined): string {
  if (!key) return '本地'
  if (key === 'direct') return '直链'
  if (key === 'unknown') return '未知'
  if (key === 'edited') return '剪辑合成'
  // 直播录制入库的来源：live_douyin / live_bilibili / live_direct …
  if (key.startsWith('live_')) return `直播·${platformLabel(key.slice(5))}`
  return PLATFORMS.find((p) => p.key === key)?.label ?? key
}
