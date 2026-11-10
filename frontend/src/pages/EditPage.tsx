import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ClockCounterClockwise, Plus, Scissors, Stop } from '@phosphor-icons/react'
import {
  analyzeEdit,
  cancelJob,
  errorMessage,
  fetchTasks,
  fetchVideos,
  type TaskSummary,
  type Video,
} from '../api'
import { Alert, Button, Input, PageHeader, Panel, Progress, toast } from '../ui'

const KIND_LABEL: Record<string, string> = {
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

export default function EditPage() {
  const navigate = useNavigate()
  const [tasks, setTasks] = useState<TaskSummary[]>([])
  const [videos, setVideos] = useState<Video[]>([])
  const [loadError, setLoadError] = useState('')
  const [selectedVideoId, setSelectedVideoId] = useState(0)
  const [title, setTitle] = useState('')
  const [prompt, setPrompt] = useState('')
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

  async function startAnalyze() {
    if (!selectedVideoId) {
      toast.error('先在左侧选一条原片')
      return
    }
    setStarting(true)
    try {
      const job = await analyzeEdit([selectedVideoId])
      toast.success('已开始分析切点')
      navigate(`/edit/${job.id}`, { state: { title: title || undefined, highlightPrompt: prompt || undefined } })
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
        desc="选一条原片，分析切点、AI 精彩剪辑，再导出成片。"
      />

      {loadError && <Alert type="error" className="mb-4">{loadError}</Alert>}

      <div className="grid items-start gap-6 lg:grid-cols-[360px_minmax(0,1fr)]">
        {/* 左侧：剪辑任务列表 */}
        <div className="flex flex-col gap-3">
          <h2 className="section-title">剪辑任务（{tasks.length}）</h2>
          {tasks.length === 0 && <p className="text-sm text-ink-3">还没有剪辑任务，先在右侧选一条原片开始分析。</p>}
          {tasks.map((task) => (
            <Panel key={task.id} pad="sm" className="cursor-pointer hover:border-accent">
              <div
                role="button"
                tabIndex={0}
                onClick={() => navigate(`/edit/${task.id}`)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault()
                    navigate(`/edit/${task.id}`)
                  }
                }}
              >
                <div className="flex items-center gap-2">
                  <Scissors size={18} className="shrink-0 text-accent" />
                  <strong className="min-w-0 flex-1 truncate text-sm">{KIND_LABEL[task.kind || ''] || task.title || '剪辑任务'}</strong>
                  <span className="shrink-0"><StatusTag status={task.status} /></span>
                </div>
                <p className="mt-1.5 truncate text-xs text-ink-3">
                  {(task.input?.video_ids || []).map((id) => videos.find((video) => video.id === id)?.title || `#${id}`).join('、') || task.title} · {task.created_at}
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

        {/* 中间：开始剪辑 */}
        <Panel title="开始剪辑">
          <div className="flex flex-col gap-4">
            <div>
              <p className="mb-2 text-xs text-ink-3">选一条原片（{videos.length} 条）</p>
              {videos.length === 0 ? (
                <p className="text-sm text-ink-3">库是空的，先去素材库上传或下载一条原片。</p>
              ) : (
                <div className="max-h-64 space-y-1 overflow-auto">
                  {videos.map((video) => (
                    <button
                      key={video.id}
                      type="button"
                      onClick={() => {
                        setSelectedVideoId(video.id)
                        if (!title) setTitle(`${video.title} · 精彩剪辑`)
                      }}
                      className={`flex w-full items-center gap-2 rounded-lg border px-3 py-2 text-left ${selectedVideoId === video.id ? 'border-accent bg-accent/5' : 'border-line-soft hover:bg-fill'}`}
                    >
                      <span className="min-w-0 flex-1 truncate text-[13px] text-ink-1">{video.title}</span>
                      <span className="tnum shrink-0 text-[11px] text-ink-4">{formatTime(Number(video.duration || 0))}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
            <label className="text-xs text-ink-3">成片标题
              <Input value={title} onChange={setTitle} placeholder="例如：陈泽直播精彩合集" className="mt-1" />
            </label>
            <label className="text-xs text-ink-3">精彩剪辑提示（可选，告诉 AI 想留什么样的片段）
              <Input value={prompt} onChange={setPrompt} placeholder="例如：只留连麦 PK 和嘉年华名场面" className="mt-1" />
            </label>
            <div className="flex flex-wrap items-center gap-2">
              <Button variant="primary" icon={<Scissors size={15} />} disabled={starting || !selectedVideoId} onClick={() => void startAnalyze()}>
                {starting ? '开始分析…' : '分析切点'}
              </Button>
              <Button variant="secondary" icon={<Plus size={15} />} onClick={() => navigate('/edit/new')}>空白时间线</Button>
            </div>
            <p className="text-xs text-ink-4">
              分析会先做转写和信号检测（静音、黑场、镜头切换），完成后自动跳到结果页，在那里看切点、跑 AI 精彩剪辑、导出成片。
              {selectedVideo ? `当前选中：${selectedVideo.title}` : ''}
            </p>
          </div>
        </Panel>
      </div>

      <p className="mt-4 flex items-center gap-1.5 text-xs text-ink-4">
        <ClockCounterClockwise size={14} /> 历史任务点一下就能回到当时的结果页；分析或导出进行中也会在这里显示进度。
      </p>
    </div>
  )
}
