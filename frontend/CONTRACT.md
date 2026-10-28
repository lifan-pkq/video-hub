# Video Hub 前端契约（feat/ux-skeleton）

> 面向各页面重写 agent 的地基文档。所有签名均如实抄自代码，改动前请先阅读对应源文件。
> 技术栈：React 18 + Vite 5 + Tailwind 3 + react-router-dom 6 + @phosphor-icons/react + axios。**不要引入新依赖、新 UI 库。**

## 1. 路由表（src/App.tsx）

| 路径 | 页面组件 | 说明 |
| --- | --- | --- |
| `/` | `pages/WorkbenchPage.tsx` | 工作台（占位，待重写） |
| `/library` | `pages/VideosPage.tsx` | 素材库（现复用旧视频库组件占位）。支持 `?q=关键词` 搜索参数 |
| `/library/import` | `pages/ImportWizardPage.tsx` | 素材导入向导（占位，待重写） |
| `/edit` | `pages/EditorPage.tsx` | 视频剪辑，`lazy()` + `<Suspense>` 懒加载，保留 |
| `/publish` | `pages/PublishPage.tsx` | 发布（占位，待重写） |
| `/tasks` | `pages/TasksPage.tsx` | 任务中心 |
| `/accounts` | `pages/AccountsPage.tsx` | 平台账号（设置区） |
| `/videos` | `<Navigate to="/library" replace>` | 旧路由重定向 |
| `/download` | `<Navigate to="/library/import" replace>` | 旧路由重定向 |
| `*` | `pages/NotFoundPage.tsx` | 404 |

旧页面 `DashboardPage / DownloadPage / RecordPage` 已不再挂载（文件保留，勿删）。命令面板中视频搜索跳转为 `/library?q=<title>`。

## 2. 导航结构（src/nav.ts）

```ts
export interface NavItem {
  to: string
  label: string
  icon: Icon            // @phosphor-icons/react 的 Icon 类型
  keywords: string      // 命令面板搜索关键词
  mobileTab: boolean    // 是否出现在移动端底部 Tab（≤768px）
  section: 'main' | 'settings'  // 侧边栏分区
}

export const NAV_ITEMS: NavItem[]           // 全部 6 项
export const MAIN_NAV_ITEMS: NavItem[]      // section==='main'：工作台/素材库/视频剪辑/发布/任务中心
export const SETTINGS_NAV_ITEMS: NavItem[]  // section==='settings'：平台账号（侧边栏底部）
export const MOBILE_TAB_ITEMS: NavItem[]    // mobileTab===true 的 5 项
```

布局：`md`（768px）以上 = 左侧边栏（224px，主导航 + 底部设置区）+ 顶栏 + 内容区；`md` 以下 = 精简顶栏 + 固定底部 Tab 栏。任务中心项带"进行中任务数"角标（`runningTasks`，数据源见 §3）。

## 3. 实时事件（src/events.ts）

```ts
import { useEvents, type EventMsg } from '../events'
import type { TaskSummary } from '../api'

export type EventMsg =
  | { type: 'snapshot'; tasks: TaskSummary[] }
  | { type: 'task_updated'; task: TaskSummary }
  | { type: 'ping' }

export function useEvents(handler: (msg: EventMsg) => void): void
```

- 连接 `/api/ws`（URL 由 `getWsUrl()` 生成，ws/wss 自适应）；JSON 解析失败或非上述三种 type 的帧会被丢弃。
- 断线自动重连：指数退避 1s → 2s → 4s … 30s 封顶。
- **连续 3 次连接失败后降级**：15s 轮询 `fetchTasks()`，diff 合成 `snapshot`（首轮）/ `task_updated`（变更项）事件，页面无需关心传输方式。
- 组件卸载自动清理（关 WS、清定时器）。handler 每次渲染都会更新引用，闭包安全。

用法（App.tsx 实例，可直接抄）：

```tsx
const [latest, setLatest] = useState<EventMsg | null>(null)
const [tasks, setTasks] = useState<TaskSummary[]>([])
const onEvent = useCallback((msg: EventMsg) => {
  setLatest(msg)
  if (msg.type === 'snapshot') setTasks(msg.tasks)
  else if (msg.type === 'task_updated')
    setTasks((prev) => { /* 按 task.id upsert */ return prev })
}, [])
useEvents(onEvent)
```

页面级组件如只需任务列表，也允许直接 `useEvents` 自己订阅；注意每个 hook 实例独立连接，**优先在页面顶层挂一个**。

## 4. 通知中心（src/components/NotifyCenter.tsx）

### `useNotifications(latest)` — App 层状态管理

```ts
export interface AppNotification {
  id: string
  taskId: string
  title: string                       // = 任务标题
  kind: 'success' | 'failed' | 'info' // success/partial→success；failed→failed；其它终态→info
  time: string                        // ISO，进入终态时间
  read: boolean
}

export interface NotificationsApi {
  notifications: AppNotification[]
  unreadCount: number
  markAllRead: () => void
  clear: () => void
}

export function useNotifications(latest: EventMsg | null): NotificationsApi
```

- 把 `task_updated` 中**进入终态**（status 非 pending/running）的任务转为通知；`snapshot` 只建基线不产生通知。
- localStorage 持久化（key `video-hub-notifications`），上限 50 条，新通知在前。

### `<NotifyCenter />` — 顶栏受控组件

```tsx
interface Props {
  notifications: AppNotification[]
  onRead: () => void   // 全部已读
  onClear: () => void  // 清空
}
```

铃铛 + 未读红点 + 下拉面板（点外部/Esc 关闭）。已挂在顶栏，页面 agent 一般不用直接使用。

## 5. 向导步骤条（src/components/WizardSteps.tsx）

```tsx
export interface WizardStep { key: string; label: string }

interface Props {
  steps: WizardStep[]
  current: number  // 当前步骤下标，0 起
}
```

桌面端（≥768px）横向、移动端竖向；已完成打勾（实心 accent）、当前高亮（accent-soft）、未来灰色。用法：

```tsx
<WizardSteps
  steps={[{ key: 'source', label: '粘贴链接' }, { key: 'parse', label: '解析预览' }, { key: 'confirm', label: '确认导入' }]}
  current={1}
/>
```

## 6. 视频播放弹窗（src/components/VideoPlayerModal.tsx）

```tsx
interface Props {
  videoId: number | null  // null = 关闭
  title?: string          // 默认 '视频预览'
  onClose: () => void
}
```

视频源 `/api/videos/${videoId}/file`（后端支持 Range 流式），`<video controls autoPlay playsInline>`；加载失败显示错误态。基于 `ui/Modal`（portal 到 body，Esc/遮罩关闭）。用法：

```tsx
const [playId, setPlayId] = useState<number | null>(null)
<VideoPlayerModal videoId={playId} title={current?.title} onClose={() => setPlayId(null)} />
```

## 7. API 层（src/api.ts）

axios 实例：`export default http`（`baseURL: '/api'`，timeout 120s）。所有函数直接返回 `data`。

### WebSocket

```ts
export function getWsUrl(): string  // 由 baseURL + location 推导 ws(s)://host/api/ws
```

### 类型

```ts
export interface Video {
  id: number; title: string; filename: string; size: number
  duration: number | null; width: number | null; height: number | null
  cover: string | null; source_platform: string | null; source_url: string | null
  created_at: string; file_url: string; cover_url: string | null
}
export interface ParseResult { url: string; title: string; author: string; cover_url: string; duration: number; platform: string; video_id: string }
export type TaskStatus = 'pending' | 'running' | 'success' | 'failed' | 'partial'
export interface Task {
  id: string; type: 'download' | 'publish' | 'edit'; title: string
  status: TaskStatus; progress: number; needs_verify: boolean
  result: { video?: Video; targets?: Record<string, { status: 'success' | 'failed'; message: string }>; [key: string]: unknown }
  created_at: string; finished_at: string | null; logs: string[]
}
export type TaskSummary = Omit<Task, 'logs'>  // 任务列表项，不含日志
export interface PublishTarget { platform: string; account: string }
export interface PublishPayload { video_id: number; targets: PublishTarget[]; title: string; desc: string; tags: string[]; schedule: string | null }
export interface PublishRecord { id: number; task_id: string; video_id: number; video_title: string; platform: string; account: string; title: string; desc: string; tags: string; status: string; log: string; created_at: string; schedule: string | null }
export interface Account { platform: string; account: string; cookie_exists: boolean; updated_at: string | null; valid?: boolean; detail?: string }
export interface CheckResult { platform: string; account: string; valid: boolean; detail: string }
export type LoginState = 'starting' | 'qrcode_ready' | 'success' | 'failed' | 'timeout'
export interface LoginSession { session_id: string; platform: string; account: string; state: LoginState; message: string; has_qr: boolean; qr_version: number; done: boolean }
export type RecordingStatus = 'recording' | 'completed' | 'stopped' | 'failed'
export interface Recording { id: number; url: string; platform: string; title: string; filename: string; status: RecordingStatus; size: number; duration: number | null; error: string | null; video_id: number | null; quality: string; fps: number; created_at: string; finished_at: string | null; elapsed?: number }
export interface Watcher { id: number; platform: string; url: string; name: string; quality: string; fps: number; enabled: number; live: number; active_recording_id: number | null; last_checked_at: string | null; created_at: string }
export interface EditClip { video_id: number; start: number; end: number }
export interface AiVideoAnalysis { video_id: number; title: string; duration: number; waste: { start: number; end: number; kind: 'intro' | 'outro' | 'ad'; reason: string }[]; keep: { start: number; end: number }[] }
export interface Stats { videos: { count: number; size: number }; accounts: { total: number; with_cookie: number }; recordings: { total: number; active: number }; publish: { total: number; today: number; success: number }; tasks: { running: number; needs_verify: number } }
export interface AuthorWork { id: string; title: string; cover_url: string | null; url: string; duration: number | null; like_count: number | null; create_time: number | null }
export interface AuthorWorksResult { author: { name: string; avatar: string | null; url: string; platform: string }; works: AuthorWork[] }
export interface CookieExportResult { platform: string; account: string; cookie_string: string }
```

### 视频库

```ts
export async function fetchVideos(): Promise<Video[]>
export async function uploadVideo(file: File, title: string | undefined, onProgress: (percent: number) => void): Promise<Video>  // 上传不限时
export async function deleteVideo(id: number): Promise<{ ok: boolean }>
export async function renameVideo(id: number, title: string): Promise<Video>
export async function trimVideo(id: number, start: number, end: number): Promise<Video>  // 无损截取，生成新视频入库
```

### 剪辑拼接

```ts
export async function concatVideos(title: string, clips: EditClip[], splitParts = 1): Promise<{ task_id: string }>
export async function analyzeVideos(videoIds: number[], kinds: string[], customPrompt = ''): Promise<{ task_id: string }>
```

### 仪表盘统计

```ts
export async function fetchStats(): Promise<Stats>
```

### 下载 / 任务

```ts
export async function parseDownload(text: string): Promise<ParseResult>
export async function createDownload(text: string): Promise<{ task_id: string }>
export async function createBatchDownload(texts: string[]): Promise<{ task_ids: string[]; count: number }>  // 后端串行排队
export async function fetchTask(taskId: string): Promise<Task>          // 含日志
export async function fetchTasks(): Promise<TaskSummary[]>              // 按创建时间倒序
export async function submitVerifyCode(taskId: string, code: string): Promise<{ ok: boolean; message?: string }>  // 抖音短信验证码
export async function parseAuthorWorks(text: string): Promise<AuthorWorksResult>  // 慢（30-90s，timeout 130s；429=已有抓取进行中）
```

### 发布

```ts
export async function publishVideo(payload: PublishPayload): Promise<{ task_id: string }>
export async function fetchPublishRecords(): Promise<PublishRecord[]>
```

### 平台账号 / 扫码登录

```ts
export async function fetchAccounts(check = false): Promise<Account[]>  // check=true 时带 valid/detail
export async function checkAccount(platform: string, account: string): Promise<CheckResult>  // 慢（10-90s）
export async function uploadCookie(platform: string, account: string, file: File): Promise<{ ok: boolean; path: string; message: string }>
export async function importCookieString(platform: string, account: string, content: string): Promise<{ ok: boolean; count: number }>
export async function exportCookieString(platform: string, account: string): Promise<CookieExportResult>  // 无 cookie 时 404
export async function deleteAccount(platform: string, account: string): Promise<{ ok: boolean; message: string }>
export async function startLogin(platform: string, account: string): Promise<LoginSession>
export async function fetchLoginStatus(sessionId: string): Promise<LoginSession>
export async function cancelLogin(sessionId: string): Promise<{ ok: boolean }>
```

### 直播录制 / 主播监控

```ts
export async function createRecording(url: string, title?: string, quality = 'best', fps = 0): Promise<Recording>
export async function fetchRecordings(): Promise<Recording[]>
export async function fetchRecording(id: number): Promise<Recording>
export async function stopRecording(id: number): Promise<Recording>
export async function renameRecording(id: number, title: string): Promise<Recording>
export async function deleteRecording(id: number, deleteFile: boolean): Promise<{ ok: boolean }>
export function recordingPreviewUrl(id: number): string  // /api/recordings/:id/preview
export async function fetchWatchers(): Promise<Watcher[]>
export async function createWatcher(url: string, name: string, quality: string, fps: number): Promise<Watcher>
export async function updateWatcher(id: number, patch: { enabled?: boolean; name?: string; quality?: string; fps?: number }): Promise<Watcher>
export async function deleteWatcher(id: number): Promise<{ ok: boolean }>
```

### 常量与工具

```ts
export const QUALITY_OPTIONS: { value: string; label: string }[]  // best/high/medium/low
export function qualityLabel(key: string): string
export const FPS_OPTIONS: { value: number; label: string }[]      // 0=原始
export function fpsLabel(fps: number): string
export function errorMessage(e: unknown): string                  // axios 错误 → detail 字符串
export function formatDuration(seconds: number | null | undefined): string  // mm:ss
export function formatElapsed(seconds: number | null | undefined): string   // hh:mm:ss
export function formatSize(bytes: number): string                 // MB / GB
export function formatTime(iso: string | null | undefined): string      // zh-CN 本地化
export function formatRelative(iso: string | null | undefined): string  // 刚刚/N分钟前/N小时前/N天前
export const PLATFORMS: { key: string; label: string }[]          // douyin/xiaohongshu/kuaishou/bilibili
export const QR_LOGIN_PLATFORMS: string[]
export function platformLabel(key: string | null | undefined): string  // 含 live_* 来源前缀处理
```

## 8. UI 组件库（src/ui/index.ts 导出）

```
Alert Button Checkbox DateTimeInput Dropzone EmptyState Input
Modal ConfirmHost confirmDialog  PageHeader Panel PlatformIcon
Progress Segment Select Spinner StatCard Switch Tag TextArea
toast ToastHost
```

常用约定：
- `PageHeader`：`{ title, desc?, extra? }`（extra 为右上角操作区）。
- `Panel`：`{ title?, extra?, pad?: 'none'|'sm'|'md' }`，卡片容器（`.vh-card`）。
- `Modal`：`{ open, onClose, title?, footer?, width?, maskClosable? }`，**必须经由 portal 挂在 body**——不要把弹窗渲染在 `.page-in` 容器内（transform 会形成包含块困住 fixed 弹层）。
- `confirmDialog({ title, content, okText?, cancelText?, danger? }): Promise<boolean>` 命令式确认。
- `toast(...)`（见 `ui/Toast.tsx`）命令式提示。
- `EmptyState`：`{ icon?, title, desc?, action? }`。

## 9. 主题 Token 速查

### CSS 变量（src/index.css）

| 变量 | 深色（默认，html.dark） | 浅色 |
| --- | --- | --- |
| `--bg` | `#0e1013` | `#f4f3f1` |
| `--surface` | `#15181d` | `#ffffff` |
| `--raise` | `#1c2027` | `#ffffff` |
| `--border` | `rgba(255,255,255,.09)` | `rgba(29,33,41,.1)` |
| `--border-soft` | `rgba(255,255,255,.055)` | `rgba(29,33,41,.06)` |
| `--fill` | `rgba(255,255,255,.055)` | `rgba(29,33,41,.045)` |
| `--fill-deep` | `rgba(255,255,255,.1)` | `rgba(29,33,41,.08)` |
| `--ink-1/2/3/4` | `#f2f3f5 / #c9cdd4 / #8b919c / #5c626d` | `#1d2129 / #4e5969 / #86909c / #a9aeb8` |
| `--shadow-card` | `0 1px 2px rgba(0,0,0,.25)` | `0 1px 2px rgba(29,33,41,.05)` |
| `--shadow-pop` | `0 16px 48px rgba(0,0,0,.45)` | `0 16px 48px rgba(29,33,41,.14)` |
| `--mask` | `rgba(0,0,0,.62)` | `rgba(29,33,41,.42)` |

双主题共享（`:root`）：`--accent: #f77234`、`--accent-hover: #fa8b4e`、`--accent-active: #e25b1e`、`--accent-soft: rgba(247,114,52,.12)`；`--danger #f53f3f` / `--success #23c343` / `--warning #ff7d00`（各带 `-soft` 12% 透明版）。

主题切换：`useTheme()`（src/theme.ts），`html.dark` class 为唯一数据源，localStorage key `video-hub-theme`，默认深色。

### Tailwind 映射（tailwind.config.js）

颜色：`bg-base`(--bg)、`bg-card`(--surface)、`bg-raise`、`border-line` / `border-line-soft`、`bg-fill` / `bg-fill-deep`、`text-ink-1..4`、`accent`(.hover/.active/.soft)、`danger`、`success`、`warning`（各带 soft）。
字体：`font-sans`（Noto Sans SC）、`font-display`（Outfit，标题/数字）、`font-mono`。
阴影：`shadow-card`、`shadow-pop`。

### 实用组件类（index.css @layer components）

`.vh-card`（卡片）、`.page-title` / `.page-desc` / `.section-title`、`.tnum`（等宽数字）、`.skeleton`、`.spin`、`.pulse-dot`、`.vh-table`、`.log-box`、`.enter-item` / `.page-in` / `.fade-in` / `.modal-in`、`.poster-card`（海报墙）、`.bento-grid`、`.cmdk-item`、`[data-tip]`（悬停提示）、`.skip-link`。

**注意**：自定义类都在 `@layer components`，Tailwind 工具类可覆盖；给卡片等加 `hidden/md:flex` 不会被自定义类吃掉。

## 10. 移动端适配约定（硬需求）

- **断点：768px（Tailwind `md`）**。`md:` 以上桌面布局（侧边栏）；以下移动布局（底部 Tab）。更细的适配可用 `sm:`（640px）。禁止再用旧的 `lg` 断点做布局级切换。
- 底部 Tab 栏 `fixed inset-x-0 bottom-0 z-40`，含 `pb-[env(safe-area-inset-bottom)]`；页面内容底部预留 `pb-24`（桌面 `md:pb-10`），防止被 Tab 遮挡。
- **≤480px 不允许横向滚动**：表格用 `overflow-x-auto` 容器包裹，卡片网格移动端单列（参考 `.bento-grid` 的 560px 断点），长文本 `truncate` / `break-all`。
- 弹窗/下拉最大宽 `max-w-[calc(100vw-2rem)]`。
- 触屏目标 ≥32px 高；hover 才显现的操作（如 `.poster-actions`）在移动端要有常驻替代入口。
- 图标统一 `@phosphor-icons/react`，导航激活态 `weight="fill"`。
