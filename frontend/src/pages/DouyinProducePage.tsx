import { useCallback, useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { ArrowLeft } from '@phosphor-icons/react'
import {
  cancelJob,
  errorMessage,
  fetchJob,
  retryDouyinProduce,
  reburnDouyinShort,
  fetchVideos,
  saveDouyinToLibrary,
  type ChenzeReviewCandidate,
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

const CHENZE_REVIEW: Record<string, string> = {
  NO_FIT: '放不进时长 NO_FIT',
  context_required: '缺语境 context_required',
  pending: '待复核 pending',
}

function reviewLabel(status: string) {
  return CHENZE_REVIEW[status] || status
}

function ChenzePanel({ job }: { job: EditJob }) {
  const info = job.result?.chenze
  const requested = Boolean(job.input?.chenze_style || info?.requested)
  if (!requested) return null
  const running = job.status === 'pending' || job.status === 'running'
  const reviews = info?.review_candidates || []
  const appliedRules = info?.applied_rule_ids || []
  let headline = '已选择陈泽剪辑方式'
  let detail = '正在自动整理事件单元。'
  if (!running && info?.applied) {
    headline = '已自动跑完陈泽剪辑流程'
    detail = '按事件单元挑选候选。个人风格尚未人工验收，下面都不是定稿。'
  } else if (!running && info && info.applied === false) {
    headline = '没有按陈泽本人剪辑'
    detail = info.identity?.reason || '账号没有对上已核本人，所以没有套用本人风格。'
  } else if (!running && !info) {
    detail = '这次没有留下陈泽流程结果。'
  }
  return (
    <section className="studio-review" aria-label="陈泽剪辑方式">
      <div className="studio-reel-head">
        <div>
          <h2>{headline}</h2>
          <p>{detail}</p>
        </div>
      </div>
      {info?.applied && (
        <p className="studio-fine">
          知识包 {info.pack_id || 'v1'} · {info.column || '通用保真'}
          {appliedRules.length ? ` · 已执行规则 ${appliedRules.join('、')}` : ' · 没有自动启用风格规则'}
          {info.publication_approved ? '' : ' · 未批准发布'}
        </p>
      )}
      {reviews.length > 0 && (
        <ul className="studio-review-list">
          {reviews.map((item) => (
            <ReviewRow key={`${item.status}-${item.start}-${item.end}-${item.reason}`} item={item} />
          ))}
        </ul>
      )}
    </section>
  )
}

function TextReview({
  job,
  active,
  onUpdated,
}: {
  job: EditJob
  active: DouyinShort | null
  onUpdated: (short: DouyinShort) => void
}) {
  const status = job.result?.text_enhance
  const enabled = Boolean(job.input?.text_enhance?.enabled || status?.enabled)
  const [cues, setCues] = useState(active?.quote_cues || [])
  const [title, setTitle] = useState(active?.title_text || '')
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    setCues(active?.quote_cues || [])
    setTitle(active?.title_text || '')
  }, [active?.index, active?.quote_cues, active?.title_text])
  if (!enabled) return null
  const effectNames: Record<string, string> = {
    captions: '口播字幕',
    title: '旁注',
    fade: '画面淡入淡出',
    emphasis_fade: '字的淡入淡出',
    emphasis_pop: '字的弹出',
  }
  const effects = (status?.applied_effects || active?.text_effects || []).map((name) => effectNames[name] || name)
  async function reburn() {
    if (!active || busy) return
    setBusy(true)
    try {
      const saved = await reburnDouyinShort(job.id, active.index, cues.map((cue) => ({
        start: Number(cue.start),
        end: Number(cue.end),
        text: cue.text,
      })), title)
      onUpdated(saved.short)
      toast.success('已按校对字幕重新烧录')
    } catch (cause) {
      toast.error(errorMessage(cause))
    } finally {
      setBusy(false)
    }
  }
  return (
    <section className="studio-review" aria-label="文字与轻效果">
      <div className="studio-reel-head">
        <div>
          <h2>文字与轻效果</h2>
          <p>
            {effects.length ? `已烧录：${effects.join('、')}。` : '这次打开了文字效果。'}
            口播字幕是转写草稿，旁注是编辑说明，都不是已验收的陈泽风格。原声保留，没有另加音乐。
          </p>
        </div>
      </div>
      {status?.typography_note && <p className="studio-fine">{status.typography_note}</p>}
      {active && (
        <div className="studio-chenze-fields">
          <label className="studio-chenze-wide">
            旁注
            <input value={title} maxLength={40} onChange={(event) => setTitle(event.target.value)} />
          </label>
          {cues.map((cue, index) => (
            <label key={`${active.index}-${index}`} className="studio-chenze-wide">
              {`口播 ${Number(cue.start).toFixed(1)}–${Number(cue.end).toFixed(1)} 秒`}
              <input
                value={cue.text}
                onChange={(event) => {
                  const next = cues.slice()
                  next[index] = { ...cue, text: event.target.value }
                  setCues(next)
                }}
              />
            </label>
          ))}
          <button type="button" className="studio-pill" disabled={busy} onClick={() => void reburn()}>
            {busy ? '正在重烧…' : '按校对字幕重新烧录'}
          </button>
          {active.srt_url && (
            <button type="button" className="studio-quiet" onClick={() => downloadFile(active.srt_url || '')}>
              下载字幕稿
            </button>
          )}
        </div>
      )}
    </section>
  )
}

function ReviewRow({ item }: { item: ChenzeReviewCandidate }) {
  const notes = item.rights_notes || []
  return (
    <li>
      <b>{reviewLabel(item.status)}</b>
      <span className="tnum">{clock(item.start)}–{clock(item.end)}</span>
      {item.preview && <em>可预览</em>}
      {item.fit_label && <em>{item.fit_label}</em>}
      <p>{item.reason}</p>
      {item.text && <p className="studio-fine">{item.text}</p>}
      {notes.map((note) => <p key={note} className="studio-fine">{note}</p>)}
    </li>
  )
}

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

  async function retry() {
    if (!job || running) return
    try {
      const next = await retryDouyinProduce(job.id)
      toast.success('已按同样的原片和提示重新生成')
      navigate(`/edit/douyin/${encodeURIComponent(next.id)}`)
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
      {job && !running && (job.status === 'failed' || job.status === 'cancelled') && (job.logs || []).length > 0 && (
        <ul className="studio-fine">
          {(job.logs || []).slice(-4).map((line) => <li key={line}>{line}</li>)}
        </ul>
      )}
      {job && running && <button type="button" className="studio-quiet studio-cancel" onClick={() => void cancel()}>取消这次生成</button>}
      {job && !running && (job.status === 'failed' || job.status === 'cancelled') && (
        <button type="button" className="studio-cta" onClick={() => void retry()}>用同样的原片再试一次</button>
      )}

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

      {job && <ChenzePanel job={job} />}
      {job && (
        <TextReview
          job={job}
          active={active}
          onUpdated={(short) => {
            setJob((current) => {
              if (!current?.result?.shorts) return current
              return {
                ...current,
                result: {
                  ...current.result,
                  shorts: current.result.shorts.map((item) => item.index === short.index ? { ...item, ...short } : item),
                },
              }
            })
          }}
        />
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
                {active.review_status === 'pending' ? ' · 待复核，不是定稿' : ''}
                {active.publication_approved === false && active.review_status ? ' · 未批准发布' : ''}
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
