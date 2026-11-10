import { useCallback, useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { ArrowLeft } from '@phosphor-icons/react'
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
import {
  STUDIO_STEPS,
  clock,
  copyBlock,
  downloadFile,
  framingLabel,
  layoutName,
  studioStepFill,
  studioStepIndex,
  taskSourceTitle,
} from '../features/edit/studio'
import { toast } from '../ui'

export default function DouyinProducePage() {
  const { jobId = '' } = useParams()
  const navigate = useNavigate()
  const [job, setJob] = useState<EditJob | null>(null)
  const [videos, setVideos] = useState<Video[]>([])
  const [error, setError] = useState('')
  const [picked, setPicked] = useState<number | null>(null)
  const [saving, setSaving] = useState<number | 'export' | null>(null)

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
  const active = shorts.find((short) => short.index === picked) || shorts[0] || null
  const step = studioStepIndex(job?.stage, job?.status)
  const layout = active?.layout || job?.result?.layout || job?.input?.layout
  const sourceTitle = job
    ? taskSourceTitle({ title: job.label, input: job.input, result: job.result }, videos)
    : '成片'

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
    setSaving(thenExport ? 'export' : indexes[0] ?? null)
    try {
      const saved = await saveDouyinToLibrary(job.id, indexes)
      if (saved.warnings?.length) toast.error(saved.warnings[0])
      else toast.success(thenExport ? '已入库，去封面与导出' : '已放入素材库')
      setJob((current) => current?.result ? {
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

  const statusLine = !job
    ? '正在读取'
    : job.status === 'success'
      ? `已生成 ${shorts.length} 条`
      : job.status === 'partial'
        ? `生成了 ${shorts.length} 条，有的没做成`
        : job.status === 'failed'
          ? '没有生成成片'
          : job.status === 'cancelled'
            ? '已取消'
            : (job.label || '处理中')

  return (
    <div className="studio studio-job">
      <button type="button" className="studio-back" onClick={() => navigate('/edit')}>
        <ArrowLeft size={16} /> 返回选片
      </button>

      {error && <p className="studio-error">{error}</p>}

      <header className="studio-job-head">
        <div>
          <p className="studio-kicker">一键出片</p>
          <h1 className="studio-job-title">{sourceTitle}</h1>
          <p className="studio-job-meta">{layoutName(layout)} · {statusLine}</p>
        </div>
        {job && running && (
          <p className="studio-percent" aria-label={`进度 ${Math.round(job.progress || 0)}%`}>
            <span className="tnum">{Math.round(job.progress || 0)}</span>
            <small>%</small>
          </p>
        )}
      </header>

      {job && (
        <ol className="studio-steps" aria-label="出片进度">
          {STUDIO_STEPS.map((item, index) => {
            const state = index < step ? 'is-done' : index === step ? 'is-now' : ''
            const fill = studioStepFill(index, step, job.progress || 0, job.status)
            return (
              <li
                key={item.id}
                className={`studio-step ${state}`}
                style={{ ['--step' as string]: `${fill}%` }}
                aria-current={index === step ? 'step' : undefined}
              >
                <i />
                <b>{item.label}</b>
              </li>
            )
          })}
        </ol>
      )}

      {job && running && <p className="studio-now" aria-live="polite">{job.label || '正在处理'}</p>}
      {job && running && <button type="button" className="studio-quiet studio-cancel" onClick={() => void cancel()}>取消这次生成</button>}

      {job?.error?.message && (
        <p className={shorts.length ? 'studio-fine' : 'studio-error'}>
          {job.error.message}{job.error.action ? ` ${job.error.action}` : ''}
        </p>
      )}
      {(job?.result?.warnings || []).slice(0, 2).map((warning) => (
        <p key={warning} className="studio-fine">{warning}</p>
      ))}

      {!running && job && shorts.length === 0 && (job.status === 'success' || job.status === 'partial') && (
        <p className="studio-empty">这次没有可预览的成片。换一条口播更集中的原片，或把想留的内容写具体一点。</p>
      )}

      {running && shorts.length > 0 && (
        <p className="studio-fine">已经做好的可以先看。其余还在渲染。</p>
      )}

      {shorts.length > 0 && active && (
        <div className="studio-screen">
          <div className="studio-gallery" role="listbox" aria-label="成片">
            {shorts.map((short) => {
              const on = short.index === active.index
              return (
                <button
                  key={short.index}
                  type="button"
                  role="option"
                  aria-selected={on}
                  className={`studio-frame ${on ? 'is-on' : ''}`}
                  onClick={() => setPicked(short.index)}
                >
                  <span className="studio-still studio-still-tall">
                    {short.cover_url ? <img src={short.cover_url} alt="" /> : <b>{short.index}</b>}
                    <i className="tnum">{clock(short.duration || short.end - short.start)}</i>
                    {typeof short.score === 'number' && short.score > 0 && <em className="tnum">{short.score}</em>}
                  </span>
                  <strong>{short.title}</strong>
                </button>
              )
            })}
          </div>

          <div className="studio-watch">
            <div className="studio-phone">
              <div className="studio-screen-glass">
                {active.file_url ? (
                  <video
                    key={active.index}
                    src={active.file_url}
                    poster={active.cover_url || undefined}
                    controls
                    playsInline
                    preload="metadata"
                  />
                ) : (
                  active.cover_url && <img src={active.cover_url} alt={active.title} />
                )}
              </div>
            </div>

            <div className="studio-copy">
              <h2>{active.title}</h2>
              {(active.hashtags || []).length > 0 && <p className="studio-tags">{active.hashtags.join(' ')}</p>}
              {active.description && <p className="studio-desc">{active.description}</p>}
              <p className="studio-fine">
                {clock(active.duration || active.end - active.start)}
                {typeof active.score === 'number' && active.score > 0 ? ` · 评分 ${active.score}` : ''}
                {' · '}{framingLabel(active)}
                {active.captions_burned === false ? ' · 字幕没烧进画面' : ' · 字幕已烧录'}
              </p>
              <div className="studio-actions">
                <button
                  type="button"
                  className="studio-cta"
                  disabled={!active.file_url}
                  onClick={() => downloadFile(`${active.file_url}?download=1`)}
                >
                  下载视频
                </button>
                <div className="studio-action-row">
                  {active.kit_url && (
                    <button type="button" className="studio-pill" onClick={() => downloadFile(active.kit_url || '')}>
                      下载这条发布包
                    </button>
                  )}
                  <button type="button" className="studio-pill" onClick={() => void copyText(active)}>复制文案</button>
                </div>
                <div className="studio-action-row">
                  <button
                    type="button"
                    className="studio-quiet"
                    disabled={Boolean(active.video_id) || saving === active.index || running}
                    onClick={() => void save([active.index])}
                  >
                    {active.video_id ? '已入库' : saving === active.index ? '入库中…' : '入库'}
                  </button>
                  <button
                    type="button"
                    className="studio-quiet"
                    disabled={saving === 'export' || running}
                    onClick={() => void save([active.index], true)}
                  >
                    {saving === 'export' ? '正在入库…' : '封面与导出'}
                  </button>
                  {job?.result?.pack_url && (
                    <button type="button" className="studio-quiet" onClick={() => downloadFile(job.result?.pack_url || '')}>
                      下载全部
                    </button>
                  )}
                </div>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
