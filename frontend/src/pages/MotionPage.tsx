import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { DownloadSimple, Gif, ImageSquare } from '@phosphor-icons/react'
import {
  errorMessage,
  fetchTask,
  fetchVideos,
  formatDuration,
  formatSize,
  type Task,
  type Video,
} from '../api'
import { createMotion, ingestMotion, uploadMotion } from '../motion'
import { useTaskCenter } from '../context/TaskCenter'
import {
  Alert,
  Button,
  Checkbox,
  Dropzone,
  EmptyState,
  Input,
  PageHeader,
  Panel,
  ProblemAlert,
  Progress,
  Segment,
  Select,
  Switch,
  toast,
} from '../ui'

type SourceMode = 'library' | 'upload'
type MotionFormat = 'gif' | 'webp'

function oneDecimal(value: number): string {
  return String(Math.round(value * 10) / 10)
}

export function MotionStudio({ embedded = false }: { embedded?: boolean }) {
  const [searchParams, setSearchParams] = useSearchParams()
  const { trackTask } = useTaskCenter()
  const [videos, setVideos] = useState<Video[]>([])
  const [mode, setMode] = useState<SourceMode>('library')
  const [videoId, setVideoId] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [fileUrl, setFileUrl] = useState('')
  const [duration, setDuration] = useState<number | null>(null)
  const [start, setStart] = useState('0')
  const [end, setEnd] = useState('5')
  const [format, setFormat] = useState<MotionFormat>('gif')
  const [width, setWidth] = useState('480')
  const [fps, setFps] = useState('12')
  const [colors, setColors] = useState('256')
  const [quality, setQuality] = useState('75')
  const [dither, setDither] = useState(true)
  const [loop, setLoop] = useState(true)
  const [ingest, setIngest] = useState(false)
  const [jobId, setJobId] = useState(searchParams.get('task') || '')
  const [task, setTask] = useState<Task | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [saving, setSaving] = useState(false)
  const videoRef = useRef<HTMLVideoElement>(null)
  const clipPlaying = useRef(false)
  const appliedVideo = useRef(false)

  useEffect(() => {
    void fetchVideos().then(setVideos).catch((error) => toast.error('视频库加载失败：' + errorMessage(error)))
  }, [])

  useEffect(() => {
    if (appliedVideo.current) return
    const requested = searchParams.get('video')
    if (!requested) return
    appliedVideo.current = true
    setMode('library')
    setVideoId(requested)
  }, [searchParams])

  useEffect(() => {
    if (!file) {
      setFileUrl('')
      return
    }
    const url = URL.createObjectURL(file)
    setFileUrl(url)
    setStart('0')
    setEnd('5')
    setDuration(null)
    return () => URL.revokeObjectURL(url)
  }, [file])

  const selected = videos.find((item) => String(item.id) === videoId) || null
  const selectedId = selected?.id
  const selectedDuration = selected?.duration
  useEffect(() => {
    if (mode !== 'library' || selectedId == null) return
    setDuration(selectedDuration ?? null)
    setStart('0')
    setEnd(oneDecimal(selectedDuration != null ? Math.min(selectedDuration, 5) : 5))
  }, [mode, selectedId, selectedDuration])

  useEffect(() => {
    const param = searchParams.get('task')
    if (param) setJobId(param)
  }, [searchParams])

  useEffect(() => {
    if (!jobId) return
    let dead = false
    let timer = 0
    const tick = async () => {
      try {
        const detail = await fetchTask(jobId)
        if (dead) return
        setTask(detail)
        if (detail.status === 'pending' || detail.status === 'running') {
          timer = window.setTimeout(tick, 1000)
        }
      } catch {
        if (!dead) timer = window.setTimeout(tick, 2000)
      }
    }
    void tick()
    return () => {
      dead = true
      window.clearTimeout(timer)
    }
  }, [jobId])

  const previewUrl = mode === 'upload' ? fileUrl : selected?.file_url || ''
  const startNum = Number(start)
  const endNum = Number(end)
  const span = duration && duration > 0 ? duration : 30
  const clipError = useMemo(() => {
    if (!Number.isFinite(startNum) || !Number.isFinite(endNum)) return '请填写起止时间'
    if (startNum < 0) return '开始时间不能小于 0'
    if (endNum - startNum < 0.2) return '片段至少 0.2 秒'
    if (endNum - startNum > 30) return '片段最长 30 秒'
    if (duration != null && endNum > duration + 0.05) return `结束时间超过视频时长（${formatDuration(duration)}）`
    return ''
  }, [startNum, endNum, duration])

  const remember = (id: string) => {
    setJobId(id)
    trackTask(id)
    if (embedded) return
    const next = new URLSearchParams(searchParams)
    next.set('task', id)
    setSearchParams(next, { replace: true })
  }

  const onMetadata = () => {
    const node = videoRef.current
    if (!node || !Number.isFinite(node.duration) || node.duration <= 0) return
    setDuration(node.duration)
    setEnd((current) => {
      const value = Number(current)
      if (!Number.isFinite(value) || value <= 0 || value > node.duration) return oneDecimal(Math.min(node.duration, 5))
      return current
    })
  }

  const seek = (seconds: number) => {
    const node = videoRef.current
    if (!node) return
    node.currentTime = Math.max(0, seconds)
  }

  const previewClip = () => {
    const node = videoRef.current
    if (!node || clipError) return
    clipPlaying.current = true
    node.currentTime = startNum
    void node.play()
  }

  const onTimeUpdate = () => {
    const node = videoRef.current
    if (!node || !clipPlaying.current) return
    if (node.currentTime >= endNum) {
      node.pause()
      clipPlaying.current = false
    }
  }

  const changeStart = (raw: string) => {
    const value = Math.max(0, Number(raw) || 0)
    const next = Math.min(value, Math.max(0, endNum - 0.2), span)
    setStart(oneDecimal(next))
    seek(next)
  }

  const changeEnd = (raw: string) => {
    const value = Math.max(0, Number(raw) || 0)
    const next = Math.min(Math.max(value, startNum + 0.2), span)
    setEnd(oneDecimal(next))
  }

  const running = task?.status === 'pending' || task?.status === 'running'
  const canGenerate = !clipError && !submitting && !running && (mode === 'upload' ? !!file : !!selected)

  const generate = async () => {
    if (!canGenerate) return
    const body = {
      start: startNum,
      duration: Math.round((endNum - startNum) * 1000) / 1000,
      width: Number(width) || 480,
      fps: Number(fps) || 12,
      format,
      loop: loop ? 0 : -1,
      colors: Number(colors) || 256,
      dither,
      quality: Number(quality) || 75,
      ingest,
    }
    setSubmitting(true)
    setTask(null)
    try {
      const created = mode === 'upload' && file
        ? await uploadMotion(file, body)
        : await createMotion({ ...body, video_id: Number(videoId) })
      setTask(created)
      remember(created.id)
      if (created.status === 'failed') toast.error(created.error?.message || '生成失败')
      else toast.success('已开始生成，进度在这里和任务中心都能看到')
    } catch (error) {
      toast.error(errorMessage(error))
    } finally {
      setSubmitting(false)
    }
  }

  const saveToLibrary = async () => {
    if (!task) return
    setSaving(true)
    try {
      const image = await ingestMotion(task.id)
      setTask({ ...task, result: { ...(task.result || {}), image } })
      toast.success('已放入图片素材库')
    } catch (error) {
      toast.error(errorMessage(error))
    } finally {
      setSaving(false)
    }
  }

  const result = task?.result
  const previewImage = result?.preview_url || result?.download_url

  return (
    <div className="space-y-5">
      {!embedded && (
        <PageHeader
          title="视频转动图"
          desc="截一段最多 30 秒的视频，导出 GIF 或体积更小的动态 WebP。生成在后台跑，可以离开页面。"
        />
      )}
      <div className="grid gap-5 lg:grid-cols-[minmax(0,1.15fr)_minmax(280px,0.85fr)]">
        <Panel title="选择片段">
          <Segment
            value={mode}
            onChange={(value) => setMode(value as SourceMode)}
            options={[{ value: 'library', label: '素材库' }, { value: 'upload', label: '本地上传' }]}
          />
          {mode === 'library' ? (
            <div className="mt-4">
              {videos.length === 0 ? (
                <EmptyState
                  icon={<Gif size={24} />}
                  title="素材库还没有视频"
                  desc="先上传或下载一条视频，也可以直接在右边这个页面改用本地文件。"
                  action={<Button size="sm" onClick={() => setMode('upload')}>改为本地上传</Button>}
                />
              ) : (
                <Select
                  value={videoId}
                  onChange={setVideoId}
                  className="w-full"
                  options={[{ value: '', label: '请选择一条视频' }, ...videos.map((video) => ({ value: String(video.id), label: video.title }))]}
                />
              )}
            </div>
          ) : (
            <div className="mt-4">
              <Dropzone accept="video/*" tip="不会写入素材库，只用来生成这一张动图" onFile={setFile} />
              {file && <p className="mt-2 truncate text-xs text-ink-3">{file.name}</p>}
            </div>
          )}

          {previewUrl ? (
            <div className="mt-4">
              <video
                key={previewUrl}
                ref={videoRef}
                src={previewUrl}
                controls
                playsInline
                onLoadedMetadata={onMetadata}
                onTimeUpdate={onTimeUpdate}
                className="max-h-[320px] w-full rounded-xl bg-black"
              />
              <div className="mt-3 flex items-center justify-between text-xs text-ink-3">
                <span>视频时长 <span className="tnum">{formatDuration(duration)}</span></span>
                <span>片段 <span className="tnum">{formatDuration(Math.max(0, endNum - startNum))}</span></span>
              </div>
              <div className="relative mt-3 h-2 overflow-hidden rounded-full bg-fill">
                <div
                  className="absolute inset-y-0 rounded-full bg-accent"
                  style={{
                    left: `${Math.min(100, Math.max(0, (startNum / span) * 100))}%`,
                    width: `${Math.min(100, Math.max(0, ((endNum - startNum) / span) * 100))}%`,
                  }}
                />
              </div>
              <label className="mt-3 block text-xs text-ink-3">
                开始 {oneDecimal(startNum)} 秒
                <input className="mt-1 w-full accent-accent" type="range" min={0} max={span} step={0.1} value={Math.min(startNum || 0, span)} onChange={(event) => changeStart(event.target.value)} />
              </label>
              <label className="mt-2 block text-xs text-ink-3">
                结束 {oneDecimal(endNum)} 秒
                <input className="mt-1 w-full accent-accent" type="range" min={0} max={span} step={0.1} value={Math.min(endNum || 0, span)} onChange={(event) => changeEnd(event.target.value)} />
              </label>
              <div className="mt-3 flex flex-wrap items-end gap-3">
                <div className="w-[120px]">
                  <div className="mb-1 text-xs text-ink-3">开始（秒）</div>
                  <Input type="number" min={0} step={0.1} value={start} onChange={setStart} />
                </div>
                <div className="w-[120px]">
                  <div className="mb-1 text-xs text-ink-3">结束（秒）</div>
                  <Input type="number" min={0} step={0.1} value={end} onChange={setEnd} />
                </div>
                <Button size="sm" onClick={previewClip} disabled={!!clipError}>预览这段</Button>
              </div>
              {clipError && <p className="mt-2 text-xs text-danger">{clipError}</p>}
            </div>
          ) : (
            <p className="mt-4 text-[12px] text-ink-4">选好视频后可以拖动起止时间，并先预览这一段。</p>
          )}
        </Panel>

        <div className="space-y-5">
          <Panel title="输出">
            <div className="space-y-4">
              <div>
                <div className="mb-1.5 text-xs text-ink-3">格式</div>
                <Segment
                  value={format}
                  onChange={(value) => setFormat(value as MotionFormat)}
                  options={[{ value: 'gif', label: 'GIF' }, { value: 'webp', label: 'WebP' }]}
                />
                <p className="mt-2 text-[12px] leading-5 text-ink-4">
                  {format === 'gif'
                    ? 'GIF 用调色板生成，颜色更稳。可调颜色数和抖动。'
                    : '动态 WebP 通常更小。如果这台机器的 FFmpeg 没有 libwebp，会提示改用 GIF。'}
                </p>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <label>
                  <span className="mb-1.5 block text-xs text-ink-3">宽度</span>
                  <Input type="number" min={16} max={1920} step={2} value={width} onChange={setWidth} suffix={<span className="text-xs">px</span>} />
                </label>
                <label>
                  <span className="mb-1.5 block text-xs text-ink-3">帧率</span>
                  <Input type="number" min={5} max={30} step={1} value={fps} onChange={setFps} suffix={<span className="text-xs">fps</span>} />
                </label>
              </div>
              <div className="flex flex-wrap gap-2">
                {['320', '480', '640'].map((value) => (
                  <Button key={value} size="sm" variant={width === value ? 'secondary' : 'ghost'} onClick={() => setWidth(value)}>{value}px</Button>
                ))}
                {['8', '12', '15'].map((value) => (
                  <Button key={value} size="sm" variant={fps === value ? 'secondary' : 'ghost'} onClick={() => setFps(value)}>{value} fps</Button>
                ))}
              </div>
              {format === 'gif' ? (
                <div className="grid grid-cols-[1fr_auto] items-end gap-3">
                  <label>
                    <span className="mb-1.5 block text-xs text-ink-3">颜色数</span>
                    <Select value={colors} onChange={setColors} className="w-full" options={[{ value: '64', label: '64 色' }, { value: '128', label: '128 色' }, { value: '256', label: '256 色' }]} />
                  </label>
                  <div className="flex items-center gap-2 pb-2">
                    <Switch checked={dither} onChange={setDither} label="抖动" />
                    <span className="text-xs text-ink-3">抖动</span>
                  </div>
                </div>
              ) : (
                <label>
                  <span className="mb-1.5 block text-xs text-ink-3">画质</span>
                  <Select value={quality} onChange={setQuality} className="w-full" options={[{ value: '50', label: '50 · 更小' }, { value: '75', label: '75 · 默认' }, { value: '90', label: '90 · 更清晰' }]} />
                </label>
              )}
              <div className="flex items-center justify-between gap-3">
                <span className="text-[13px] text-ink-2">循环播放</span>
                <Switch checked={loop} onChange={setLoop} label="循环播放" />
              </div>
              <div className="flex flex-col items-start gap-3">
                <Checkbox checked={ingest} onChange={setIngest} label="完成后放入图片素材库" />
                <Button variant="primary" icon={<Gif size={16} />} loading={submitting || running} disabled={!canGenerate} onClick={() => void generate()}>
                  生成动图
                </Button>
              </div>
              <p className="text-[12px] text-ink-4">默认宽 480、12 帧，高度按原片比例。同一时间的重编码路数跟其他渲染共用。</p>
            </div>
          </Panel>

          {running && task && (
            <Panel title="正在生成">
              <Alert type="info">{task.title || '处理中'}。可以离开这个页面，任务中心也能取消。</Alert>
              <div className="mt-4"><Progress percent={Math.max(0.04, Math.min(0.98, (task.progress || 4) / 100))} /></div>
              <div className="mt-2 text-xs text-ink-3"><span className="tnum">{Math.round(task.progress)}%</span> · {task.title}</div>
            </Panel>
          )}
          {task?.status === 'failed' && (
            <Panel>
              <ProblemAlert message={task.error?.message || '生成失败'} action={task.error?.action} />
            </Panel>
          )}
          {task?.status === 'cancelled' && (
            <Panel>
              <Alert type="warning">已取消。可以调整片段后再生成。</Alert>
            </Panel>
          )}
          {task?.status === 'success' && previewImage && (
            <Panel title="动图预览">
              <img src={previewImage} alt="生成的动图" className="max-h-[360px] w-full rounded-xl bg-fill object-contain" />
              <div className="mt-3 flex flex-wrap items-center justify-between gap-3 text-sm text-ink-2">
                <span>{(result?.format || format).toUpperCase()} · <span className="tnum">{formatSize(result?.size || 0)}</span></span>
                <div className="flex flex-wrap gap-2">
                  <a href={result?.download_url || previewImage} download={result?.filename || 'motion'}>
                    <Button size="sm" icon={<DownloadSimple size={15} />}>下载</Button>
                  </a>
                  {result?.image ? (
                    <Link to="/images"><Button size="sm" variant="ghost" icon={<ImageSquare size={15} />}>已在图片库</Button></Link>
                  ) : (
                    <Button size="sm" variant="ghost" loading={saving} onClick={() => void saveToLibrary()}>放入图片库</Button>
                  )}
                </div>
              </div>
              {result?.ingest_error?.message && (
                <div className="mt-3"><ProblemAlert message={result.ingest_error.message} action={result.ingest_error.action} /></div>
              )}
            </Panel>
          )}
        </div>
      </div>
    </div>
  )
}

export default function MotionPage() {
  return <MotionStudio />
}
