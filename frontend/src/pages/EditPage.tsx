import { useCallback, useEffect, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { ClockCounterClockwise, Plus, Scissors, Stop } from '@phosphor-icons/react'
import {
  analyzeEdit,
  cancelJob,
  editTaskPath,
  errorMessage,
  fetchTasks,
  fetchVideos,
  startDouyinProduce,
  type TaskSummary,
  type Video,
} from '../api'
import { Alert, Button, PageHeader, Panel, Progress, Select, TextArea, toast } from '../ui'

const KIND_LABEL: Record<string, string> = {
  'edit.douyin': '抖音成片',
  'edit.analyze': '分析切点',
  'edit.export': '导出成片',
}

const STATUS_LABEL: Record<string, string> = {
  pending: '排队中',
  running: '处理中',
  success: '已完成',
  partial: '部分完成',
  failed: '已失败',
  cancelled: '已取消',
}

const CLIP_OPTIONS = [
  { value: '0', label: '自动（长片最多约 10 条）' },
  { value: '1', label: '1 条' },
  { value: '3', label: '最多 3 条' },
  { value: '5', label: '最多 5 条' },
  { value: '10', label: '最多 10 条' },
]

function StatusTag({ status }: { status: string }) {
  const color = status === 'success'
    ? 'text-success bg-success/10'
    : status === 'failed'
      ? 'text-danger bg-danger/10'
      : status === 'running'
        ? 'text-accent bg-accent/10'
        : 'text-ink-3 bg-fill-deep'
  return <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs ${color}`}>{STATUS_LABEL[status] || status}</span>
}

function formatTime(seconds: number): string {
  const safe = Number.isFinite(seconds) ? Math.max(0, seconds) : 0
  const minutes = Math.floor(safe / 60)
  const rest = safe - minutes * 60
  return `${String(minutes).padStart(2, '0')}:${rest.toFixed(1).padStart(4, '0')}`
}

function taskVideoLabel(task: TaskSummary, videos: Video[]): string {
  const single = task.input?.video_id
  if (single) return videos.find((video) => video.id === single)?.title || `#${single}`
  const ids = task.input?.video_ids || []
  return ids.map((id) => videos.find((video) => video.id === id)?.title || `#${id}`).join('、') || task.title
}

export default function EditPage() {
  const navigate = useNavigate()
  const location = useLocation()
  const [tasks, setTasks] = useState<TaskSummary[]>([])
  const [videos, setVideos] = useState<Video[]>([])
  const [loadError, setLoadError] = useState('')
  const [selectedVideoId, setSelectedVideoId] = useState(0)
  const [instruction, setInstruction] = useState('')
  const [maxClips, setMaxClips] = useState('0')
  const [starting, setStarting] = useState(false)

  const load = useCallback(async () => {
    try {
      const [taskList, videoList] = await Promise.all([fetchTasks(), fetchVideos()])
      setTasks(taskList.filter((task) => (task.kind || '').startsWith('edit.')))
      setVideos(videoList)
      setLoadError('')
    } catch (error) {
      setLoadError(errorMessage(error))
    }
  }, [])

  useEffect(() => {
    void load()
    const timer = window.setInterval(load, 4000)
    return () => window.clearInterval(timer)
  }, [load])

  useEffect(() => {
    const ids = (location.state as { videoIds?: number[] } | null)?.videoIds
    const first = ids?.find((id) => id > 0)
    if (first) setSelectedVideoId(first)
  }, [location.state])

  async function startProduce() {
    if (!selectedVideoId) {
      toast.error('先选一条素材库里的原片')
      return
    }
    setStarting(true)
    try {
      const job = await startDouyinProduce(selectedVideoId, instruction.trim(), Number(maxClips) || 0)
      toast.success('已开始生成抖音短视频')
      navigate(editTaskPath(job))
    } catch (error) {
      toast.error(errorMessage(error))
    } finally {
      setStarting(false)
    }
  }

  async function startAnalyze() {
    if (!selectedVideoId) {
      toast.error('先选一条原片')
      return
    }
    setStarting(true)
    try {
      const job = await analyzeEdit([selectedVideoId])
      toast.success('已开始分析切点')
      navigate(`/edit/${job.id}`)
    } catch (error) {
      toast.error(errorMessage(error))
    } finally {
      setStarting(false)
    }
  }

  async function cancel(taskId: string) {
    try {
      await cancelJob(taskId)
      toast.success('已请求取消')
      void load()
    } catch (error) {
      toast.error(errorMessage(error))
    }
  }

  const selectedVideo = videos.find((video) => video.id === selectedVideoId) || null

  return (
    <div>
      <PageHeader
        title="智能剪辑"
        desc="选一条已经在素材库里的原片，转写后抽出多条抖音竖屏短视频。带字幕、封面和文案，你再自己发布。"
      />

      {loadError && <Alert type="error" className="mb-4">{loadError}</Alert>}

      <div className="grid items-start gap-6 lg:grid-cols-[360px_minmax(0,1fr)]">
        <div className="flex flex-col gap-3">
          <h2 className="section-title">剪辑任务（{tasks.length}）</h2>
          {tasks.length === 0 && <p className="text-sm text-ink-3">还没有剪辑任务。右边选一条原片，就可以生成抖音短视频。</p>}
          {tasks.map((task) => (
            <Panel key={task.id} pad="sm" className="cursor-pointer hover:border-accent">
              <div
                role="button"
                tabIndex={0}
                onClick={() => navigate(editTaskPath(task))}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault()
                    navigate(editTaskPath(task))
                  }
                }}
              >
                <div className="flex items-center gap-2">
                  <Scissors size={18} className="shrink-0 text-accent" />
                  <strong className="min-w-0 flex-1 truncate text-sm">{KIND_LABEL[task.kind || ''] || task.title || '剪辑任务'}</strong>
                  <span className="shrink-0"><StatusTag status={task.status} /></span>
                </div>
                <p className="mt-1.5 truncate text-xs text-ink-3">
                  {taskVideoLabel(task, videos)} · {task.created_at}
                </p>
                <div className="mt-2.5 flex items-center gap-2">
                  <div className="min-w-0 flex-1">
                    <Progress percent={(task.progress || 0) / 100} status={task.status === 'failed' ? 'error' : task.status === 'success' ? 'success' : 'normal'} />
                  </div>
                  <span className="shrink-0 text-xs text-ink-3">{Math.round(task.progress || 0)}%</span>
                </div>
                {(task.status === 'running' || task.status === 'pending') && (
                  <div className="mt-2.5 flex items-center gap-1.5" onClick={(event) => event.stopPropagation()}>
                    <Button size="sm" variant="ghost" icon={<Stop size={14} />} onClick={() => void cancel(task.id)}>取消</Button>
                  </div>
                )}
                {task.error?.message && (
                  <p className="mt-2 truncate text-xs text-danger" title={task.error.message}>{task.error.message}</p>
                )}
              </div>
            </Panel>
          ))}
        </div>

        <Panel title="生成抖音短视频">
          <div className="flex flex-col gap-4">
            <div>
              <p className="mb-2 text-xs text-ink-3">选一条原片（{videos.length} 条）</p>
              {videos.length === 0 ? (
                <p className="text-sm text-ink-3">库是空的。先去素材库上传、下载或录一段，再回来生成。</p>
              ) : (
                <div className="max-h-64 space-y-1 overflow-auto">
                  {videos.map((video) => (
                    <button
                      key={video.id}
                      type="button"
                      onClick={() => setSelectedVideoId(video.id)}
                      className={`flex w-full items-center gap-2 rounded-lg border px-3 py-2 text-left ${selectedVideoId === video.id ? 'border-accent bg-accent/5' : 'border-line-soft hover:bg-fill'}`}
                    >
                      <span className="min-w-0 flex-1 truncate text-[13px] text-ink-1">{video.title}</span>
                      <span className="tnum shrink-0 text-[11px] text-ink-4">{formatTime(Number(video.duration || 0))}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
            <label className="text-xs text-ink-3">想留什么样的片段（可选）
              <TextArea value={instruction} onChange={setInstruction} rows={3} placeholder="例如：只留观点冲突和金句，不要寒暄和广告" className="mt-1" />
            </label>
            <label className="text-xs text-ink-3">条数
              <div className="mt-1">
                <Select value={maxClips} onChange={setMaxClips} options={CLIP_OPTIONS} />
              </div>
            </label>
            <div className="flex flex-wrap items-center gap-2">
              <Button variant="primary" icon={<Scissors size={15} />} disabled={starting || !selectedVideoId} onClick={() => void startProduce()}>
                {starting ? '正在提交…' : '生成抖音短视频'}
              </Button>
            </div>
            <p className="text-xs text-ink-4">
              会复用现有转写（共享 ASR，或本机分段 whisper），再让模型挑出适合竖屏 9:16 的几条口播。每条带烧录字幕、封面、标题、话题和简介。不会登录或发布到抖音。
              {selectedVideo ? ` 当前选中：${selectedVideo.title}` : ''}
            </p>
            <details className="rounded-lg border border-line-soft px-3 py-2 text-sm">
              <summary className="cursor-pointer text-ink-2">高级：手动切点和单条时间线</summary>
              <p className="mt-2 text-xs text-ink-4">适合自己删静音、广告，或把多段拼成一条成片。解说配音仍走 AI 解说。</p>
              <div className="mt-3 flex flex-wrap gap-2">
                <Button variant="secondary" icon={<Scissors size={15} />} disabled={starting || !selectedVideoId} onClick={() => void startAnalyze()}>
                  分析切点
                </Button>
                <Button variant="secondary" icon={<Plus size={15} />} onClick={() => navigate('/edit/new')}>空白时间线</Button>
              </div>
            </details>
          </div>
        </Panel>
      </div>

      <p className="mt-4 flex items-center gap-1.5 text-xs text-ink-4">
        <ClockCounterClockwise size={14} /> 任务也会出现在任务中心。点开可以预览、下载，或把成片送进素材库和封面导出。
      </p>
    </div>
  )
}
