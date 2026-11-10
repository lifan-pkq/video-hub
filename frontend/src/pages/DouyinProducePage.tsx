import { useCallback, useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { ArrowLeft, Copy, DownloadSimple, Package, Stop } from '@phosphor-icons/react'
import {
  cancelJob,
  errorMessage,
  fetchJob,
  fetchVideos,
  saveDouyinToLibrary,
  type DouyinShort,
  type EditJob,
  type Video,
} from '../api'
import { Alert, Button, PageHeader, Panel, Progress, toast } from '../ui'

function formatTime(seconds: number): string {
  const safe = Number.isFinite(seconds) ? Math.max(0, seconds) : 0
  const minutes = Math.floor(safe / 60)
  const rest = Math.floor(safe - minutes * 60)
  return `${minutes}:${String(rest).padStart(2, '0')}`
}

function framingText(framing?: string): string {
  if (framing === 'subject') return '已按说话人位置裁成 9:16'
  return '没有可靠的说话人位置，已居中裁成 9:16'
}

function copyBlock(short: DouyinShort): string {
  const tags = (short.hashtags || []).join(' ')
  return `标题：${short.title}\n话题：${tags}\n简介：${short.description || ''}`
}

function downloadFile(url: string) {
  const link = document.createElement('a')
  link.href = url
  link.rel = 'noopener'
  document.body.appendChild(link)
  link.click()
  link.remove()
}

export default function DouyinProducePage() {
  const { jobId = '' } = useParams()
  const navigate = useNavigate()
  const [job, setJob] = useState<EditJob | null>(null)
  const [videos, setVideos] = useState<Video[]>([])
  const [error, setError] = useState('')
  const [saving, setSaving] = useState<number | 'all' | null>(null)

  const load = useCallback(async () => {
    if (!jobId) return
    try {
      const [next, library] = await Promise.all([fetchJob(jobId), fetchVideos().catch(() => [] as Video[])])
      setJob(next)
      setVideos(library)
      setError('')
    } catch (cause) {
      setError(errorMessage(cause))
    }
  }, [jobId])

  useEffect(() => {
    void load()
  }, [load])

  useEffect(() => {
    if (!job || !['pending', 'running'].includes(job.status)) return
    const timer = window.setInterval(() => { void load() }, 2000)
    return () => window.clearInterval(timer)
  }, [job, load])

  const shorts = job?.result?.shorts || []
  const running = job?.status === 'pending' || job?.status === 'running'
  const sourceTitle = job?.result?.source_title
    || videos.find((video) => video.id === job?.input?.video_id)?.title
    || (job?.input?.video_id ? `视频 #${job.input.video_id}` : '原片')

  async function cancel() {
    if (!job) return
    try {
      setJob(await cancelJob(job.id))
      toast.success('已请求取消')
    } catch (cause) {
      toast.error(errorMessage(cause))
    }
  }

  async function save(indexes: number[], thenExport = false) {
    if (!job) return
    setSaving(indexes.length === 1 ? indexes[0] : 'all')
    try {
      const saved = await saveDouyinToLibrary(job.id, indexes)
      if (saved.warnings?.length) toast.error(saved.warnings[0])
      else toast.success(thenExport ? '已入库，去封面与导出' : '已放入素材库')
      setJob((current) => current && current.result ? {
        ...current,
        result: { ...current.result, shorts: saved.shorts },
      } : current)
      const videoId = saved.videos?.[0]?.id
      if (thenExport && videoId) navigate(`/export?video=${videoId}`)
    } catch (cause) {
      toast.error(errorMessage(cause))
    } finally {
      setSaving(null)
    }
  }

  async function copyText(short: DouyinShort) {
    try {
      await navigator.clipboard.writeText(copyBlock(short))
      toast.success('文案已复制')
    } catch {
      toast.error('复制失败，请手动选择文案')
    }
  }

  return (
    <div>
      <PageHeader
        title="抖音短视频"
        desc={`${sourceTitle} · 竖屏成片可预览、下载，或送进素材库后再做封面导出。不会自动发布。`}
        extra={<Button variant="ghost" icon={<ArrowLeft size={16} />} onClick={() => navigate('/edit')}>返回智能剪辑</Button>}
      />

      {error && <Alert type="error" className="mb-4">{error}</Alert>}

      {job && (
        <Panel className="mb-4">
          <div className="flex flex-wrap items-center gap-3">
            <div className="min-w-0 flex-1">
              <p className="text-sm text-ink-1">{job.label || '生成中'}</p>
              <p className="mt-1 text-xs text-ink-4">
                {job.status === 'success' && '全部成片已生成'}
                {job.status === 'partial' && '有一部分没完成，已生成的可以先用'}
                {job.status === 'failed' && '生成失败'}
                {job.status === 'cancelled' && '已取消'}
                {running && '转写、选段和渲染都在这条任务里，也可以去任务中心看'}
              </p>
            </div>
            {running && (
              <Button size="sm" variant="ghost" icon={<Stop size={14} />} onClick={() => void cancel()}>取消</Button>
            )}
            {job.result?.pack_url && (
              <Button size="sm" variant="secondary" icon={<Package size={14} />} onClick={() => downloadFile(job.result?.pack_url || '')}>
                下载整包
              </Button>
            )}
            {shorts.length > 0 && (
              <Button size="sm" variant="secondary" loading={saving === 'all'} onClick={() => void save(shorts.map((item) => item.index))}>
                全部入库
              </Button>
            )}
          </div>
          {running && (
            <div className="mt-3">
              <Progress percent={(job.progress || 0) / 100} />
            </div>
          )}
          {job.error?.message && (
            <Alert type={job.status === 'failed' ? 'error' : 'warning'} className="mt-3">
              {job.error.message}{job.error.action ? ` ${job.error.action}` : ''}
            </Alert>
          )}
          {(job.result?.warnings || []).length > 0 && job.status !== 'failed' && (
            <ul className="mt-3 space-y-1 text-xs text-ink-3">
              {job.result?.warnings?.map((warning) => <li key={warning}>{warning}</li>)}
            </ul>
          )}
          {running && (job.logs || []).length > 0 && (
            <p className="mt-3 truncate text-xs text-ink-4">{job.logs[job.logs.length - 1]}</p>
          )}
        </Panel>
      )}

      {!job && !error && <p className="mb-4 text-sm text-ink-3">正在读取任务…</p>}

      {!running && job && shorts.length === 0 && (job.status === 'success' || job.status === 'partial') && (
        <Panel>
          <p className="text-sm text-ink-2">这次没有可预览的成片。看上面的说明，改提示或换一条口播更集中的原片再试。</p>
        </Panel>
      )}

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {shorts.map((short) => (
          <Panel key={short.index} pad="sm" className="overflow-hidden">
            <div className="mx-auto aspect-[9/16] w-full max-w-[280px] overflow-hidden rounded-xl bg-black">
              {short.file_url ? (
                <video
                  src={short.file_url}
                  poster={short.cover_url || undefined}
                  controls
                  playsInline
                  preload="metadata"
                  className="h-full w-full bg-black object-contain"
                />
              ) : (
                short.cover_url && <img src={short.cover_url} alt={short.title} className="h-full w-full object-cover" />
              )}
            </div>
            <div className="mt-3 space-y-2 px-1 pb-1">
              <div className="flex items-start justify-between gap-2">
                <h3 className="text-sm font-medium text-ink-1">{short.title}</h3>
                <span className="tnum shrink-0 text-xs text-ink-4">{formatTime(short.duration || (short.end - short.start))}</span>
              </div>
              <p className="text-xs text-ink-3">{(short.hashtags || []).join(' ')}</p>
              {short.description && <p className="text-xs leading-relaxed text-ink-2">{short.description}</p>}
              {short.hook && <p className="text-xs text-ink-4">钩子：{short.hook}</p>}
              <p className="text-[11px] text-ink-4">
                {framingText(short.framing)}
                {short.captions_burned === false ? ' · 字幕没烧进画面，文案仍可复制' : ' · 字幕已烧录'}
              </p>
              <div className="flex flex-wrap gap-2 pt-1">
                <Button size="sm" variant="secondary" icon={<DownloadSimple size={14} />} onClick={() => downloadFile(`${short.file_url}?download=1`)}>下载</Button>
                <Button size="sm" variant="ghost" icon={<Copy size={14} />} onClick={() => void copyText(short)}>复制文案</Button>
                <Button size="sm" variant="ghost" loading={saving === short.index} onClick={() => void save([short.index])}>
                  {short.video_id ? '已入库' : '入库'}
                </Button>
                <Button size="sm" variant="ghost" icon={<Package size={14} />} onClick={() => void save([short.index], true)}>封面与导出</Button>
              </div>
            </div>
          </Panel>
        ))}
      </div>
    </div>
  )
}
