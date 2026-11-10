import { useEffect, useMemo, useRef, useState, type ChangeEvent } from 'react'
import { useSearchParams } from 'react-router-dom'
import { MagnifyingGlass, UploadSimple } from '@phosphor-icons/react'
import {
  ApiError,
  draftNarrateScript,
  errorMessage,
  fetchJob,
  fetchNarrateVoices,
  fetchSignalNarrateTask,
  fetchVideos,
  formatDuration,
  importNarrateOutput,
  platformLabel,
  previewNarrateVoice,
  renderNarrate,
  saveNarrateScript,
  saveNarrateTranscript,
  startNarrateUnderstand,
  uploadVideo,
  type EditJob,
  type NarrateBeat,
  type NarrateEngines,
  type NarrateVoice,
  type SignalNarrateTask,
  type Video,
} from '../api'
import { InsertPicker, type PickedImage, type PickedVideo } from '../features/edit/InsertPicker'
import { Alert, Button, EmptyState, Input, Modal, PageHeader, Panel, Progress, Select, Switch, Tag, TextArea, toast } from '../ui'

const TASK_KEY = 'video-hub:narrate-task'
const STEPS = [
  { label: '理解', desc: '转写和画面' },
  { label: '文稿', desc: 'AI 或手填' },
  { label: '审阅', desc: '改句子和声音' },
  { label: '成片', desc: '渲染并入库' },
] as const
const PURPOSES: NarrateBeat['purpose'][] = ['hook', 'explain', 'evidence', 'turn', 'close']
const PURPOSE_LABEL: Record<NarrateBeat['purpose'], string> = {
  hook: '开头',
  explain: '说明',
  evidence: '依据',
  turn: '转折',
  close: '收束',
}
const DEFAULT_VOICE: NarrateVoice = {
  engine: 'auto',
  voice: 'zh-CN-XiaoxiaoNeural',
  rate: 0.9,
  original_volume: 0.18,
  subtitles: true,
  font_size: 46,
  margin_v: 48,
}
const DEFAULT_INSTRUCTION = '按口播里已经说过的事实写，不要加没有出现的数字。'
const DEFAULT_TONE = '清楚、具体'
const TONE_OPTIONS = [
  { value: '清楚、具体', label: '清楚、具体' },
  { value: '轻松口语', label: '轻松口语' },
  { value: '专业冷静', label: '专业冷静' },
  { value: '悬疑紧张', label: '悬疑紧张' },
  { value: '温暖亲切', label: '温暖亲切' },
]
const QUALITY_BANDS = [
  { value: 'all', label: '全部质量' },
  { value: 'high', label: '≥70' },
  { value: 'mid', label: '40–69' },
  { value: 'low', label: '<40' },
] as const
const SORT_OPTIONS = [
  { value: 'newest', label: '最新优先' },
  { value: 'quality', label: '质量优先' },
] as const

type QualityBand = (typeof QUALITY_BANDS)[number]['value']
type SortKey = (typeof SORT_OPTIONS)[number]['value']

function formatTime(seconds: number): string {
  const safe = Number.isFinite(seconds) ? Math.max(0, seconds) : 0
  const minutes = Math.floor(safe / 60)
  const rest = safe - minutes * 60
  return `${String(minutes).padStart(2, '0')}:${rest.toFixed(1).padStart(4, '0')}`
}

function sleep(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms))
}

function qualityBandOf(score: number | null | undefined): QualityBand {
  if (score == null || Number.isNaN(score)) return 'low'
  if (score >= 70) return 'high'
  if (score >= 40) return 'mid'
  return 'low'
}

function qualityBadgeClass(score: number | null | undefined): string {
  const band = qualityBandOf(score)
  if (band === 'high') return 'bg-emerald-500/90 text-white'
  if (band === 'mid') return 'bg-amber-500/90 text-white'
  return 'bg-black/55 text-white'
}

export default function NarratePage() {
  const [params, setParams] = useSearchParams()
  const [videos, setVideos] = useState<Video[]>([])
  const [videoId, setVideoId] = useState<number | null>(null)
  const [pendingVideoId, setPendingVideoId] = useState<number | null>(null)
  const [search, setSearch] = useState('')
  const [platformFilter, setPlatformFilter] = useState('all')
  const [qualityFilter, setQualityFilter] = useState<QualityBand>('all')
  const [sortBy, setSortBy] = useState<SortKey>('newest')
  const [uploading, setUploading] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [step, setStep] = useState(0)
  const [task, setTask] = useState<SignalNarrateTask | null>(null)
  const [beats, setBeats] = useState<NarrateBeat[]>([])
  const [pickerBeatIndex, setPickerBeatIndex] = useState<number | null>(null)
  const [voice, setVoice] = useState<NarrateVoice>(DEFAULT_VOICE)
  const [instruction, setInstruction] = useState(DEFAULT_INSTRUCTION)
  const [tone, setTone] = useState(DEFAULT_TONE)
  const [target, setTarget] = useState(30)
  const [pasteTranscript, setPasteTranscript] = useState('')
  const [scriptMode, setScriptMode] = useState<'ai' | 'manual'>('ai')
  const [manualScript, setManualScript] = useState('')
  const [job, setJob] = useState<EditJob | null>(null)
  const [banner, setBanner] = useState<{ message: string; action?: string } | null>(null)
  const [notes, setNotes] = useState<string[]>([])
  const [engines, setEngines] = useState<NarrateEngines | null>(null)
  const [libraryError, setLibraryError] = useState<string | null>(null)

  useEffect(() => {
    let dead = false
    fetchVideos()
      .then((list) => {
        if (dead) return
        setVideos(list)
        const preset = Number(params.get('video') || '')
        setVideoId((current) => current ?? (Number.isFinite(preset) && preset > 0 ? preset : list[0]?.id ?? null))
      })
      .catch((error: unknown) => {
        if (!dead) setLibraryError(errorMessage(error))
      })
    fetchNarrateVoices().then((info) => {
      if (!dead) setEngines(info)
    }).catch(() => {
      if (!dead) setEngines(null)
    })
    const saved = params.get('task') || localStorage.getItem(TASK_KEY)
    if (saved) {
      fetchSignalNarrateTask(saved).then((loaded) => {
        if (!dead) adopt(loaded)
      }).catch(() => localStorage.removeItem(TASK_KEY))
    }
    return () => {
      dead = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const platformOptions = useMemo(() => {
    const keys = Array.from(new Set(videos.map((video) => video.source_platform || 'local'))).sort()
    return [{ value: 'all', label: '全部来源' }, ...keys.map((key) => ({ value: key, label: platformLabel(key === 'local' ? null : key) }))]
  }, [videos])

  const filteredVideos = useMemo(() => {
    const query = search.trim().toLowerCase()
    const rows = videos.filter((video) => {
      if (query && !video.title.toLowerCase().includes(query)) return false
      const platform = video.source_platform || 'local'
      if (platformFilter !== 'all' && platform !== platformFilter) return false
      if (qualityFilter !== 'all' && qualityBandOf(video.quality_score) !== qualityFilter) return false
      return true
    })
    rows.sort((a, b) => {
      if (sortBy === 'quality') {
        const qa = a.quality_score ?? -1
        const qb = b.quality_score ?? -1
        if (qb !== qa) return qb - qa
      }
      return new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
    })
    return rows
  }, [videos, search, platformFilter, qualityFilter, sortBy])

  const selectedVideo = useMemo(() => videos.find((video) => video.id === videoId) || null, [videos, videoId])

  function adopt(next: SignalNarrateTask) {
    setTask(next)
    setVideoId(next.video_id)
    setBeats(next.script?.beats || [])
    setVoice(next.voice || DEFAULT_VOICE)
    setNotes(next.script?.warnings || next.warnings || [])
    if (next.instruction) setInstruction(next.instruction)
    else if (next.script?.instruction) setInstruction(next.script.instruction)
    if (next.tone) setTone(next.tone)
    else if (next.script?.tone) setTone(next.script.tone)
    if (next.script?.target_duration) setTarget(Number(next.script.target_duration) || 30)
    localStorage.setItem(TASK_KEY, next.id)
    const stepIndex = next.step === 'render' ? 3 : next.step === 'review' ? 2 : next.step === 'script' ? 2 : next.transcript.segments.length ? 1 : 0
    setStep(stepIndex)
    setParams((current) => {
      const copy = new URLSearchParams(current)
      copy.set('task', next.id)
      copy.set('video', String(next.video_id))
      return copy
    }, { replace: true })
  }

  function requestSelectVideo(next: number) {
    if (task && task.video_id !== next) setPendingVideoId(next)
    else {
      setVideoId(next)
      setParams((current) => {
        const copy = new URLSearchParams(current)
        copy.set('video', String(next))
        return copy
      }, { replace: true })
    }
  }

  async function follow(initial: EditJob): Promise<EditJob> {
    let current = initial
    setJob(current)
    while (current.status === 'pending' || current.status === 'running') {
      await sleep(800)
      current = await fetchJob(current.id)
      setJob(current)
    }
    return current
  }

  function showJob(done: EditJob) {
    if (done.error) setBanner({ message: done.error.message || '这一步失败', action: done.error.action })
    else if (done.status === 'failed') setBanner({ message: done.label || '这一步失败', action: '看任务日志最后一行。' })
    else setBanner(null)
  }

  async function understand() {
    if (videoId == null) return
    setBanner(null)
    try {
      const started = await startNarrateUnderstand(videoId, { instruction, tone, framesPerSecond: 2 })
      const done = await follow(started.job)
      const loaded = done.result?.task_id ? await fetchSignalNarrateTask(String(done.result.task_id)) : started.task
      if (loaded) adopt(loaded)
      if (done.status === 'partial' || Boolean((done.result as Record<string, unknown> | undefined)?.needs_transcript)) {
        setBanner(null)
        setNotes(loaded?.warnings?.length ? loaded.warnings : ['转写为空，请在下方粘贴或手填后再写稿。'])
      } else {
        showJob(done)
      }
      if (loaded?.transcript.segments.length) setStep(1)
    } catch (error) {
      setBanner(error instanceof ApiError ? { message: error.message, action: error.action } : { message: errorMessage(error) })
    }
  }

  async function saveTranscript() {
    if (!task) return
    try {
      adopt(await saveNarrateTranscript(task.id, task.transcript.segments))
      setBanner(null)
    } catch (error) {
      setBanner(error instanceof ApiError ? { message: error.message, action: error.action } : { message: errorMessage(error) })
    }
  }

  async function draft(mode: 'llm' | 'skeleton' | 'manual') {
    if (!task) return
    setBanner(null)
    try {
      if (task.transcript.segments.length) {
        adopt(await saveNarrateTranscript(task.id, task.transcript.segments))
      }
      const started = await draftNarrateScript(task.id, { mode, instruction, tone, target_duration: target })
      const done = await follow(started.job)
      const loaded = done.result?.task_id ? await fetchSignalNarrateTask(String(done.result.task_id)) : started.task
      if (loaded) {
        adopt(loaded)
        setStep(2)
      }
      showJob(done)
    } catch (error) {
      setBanner(error instanceof ApiError ? { message: error.message, action: error.action } : { message: errorMessage(error) })
    }
  }

  function buildSegmentsFromPaste(text: string, duration: number) {
    const lines = text.split(/\n+/).map((line) => line.trim()).filter(Boolean)
    if (!lines.length) return [] as SignalNarrateTask['transcript']['segments']
    if (lines.length === 1) {
      return [{ id: 't1', start: 0, end: Math.max(0.5, duration || 1), text: lines[0].slice(0, 400) }]
    }
    const each = Math.max(0.5, (duration || lines.length) / lines.length)
    return lines.map((line, index) => ({
      id: `t${index + 1}`,
      start: Number((index * each).toFixed(2)),
      end: Number((Math.min(duration || (index + 1) * each, (index + 1) * each)).toFixed(2)),
      text: line.slice(0, 400),
    }))
  }

  async function applyPasteTranscript() {
    if (!task) return
    const segments = buildSegmentsFromPaste(pasteTranscript, Number(task.duration) || 0)
    if (!segments.length) {
      setBanner({ message: '请先粘贴至少一句转写文字' })
      return
    }
    try {
      const next = { ...task, transcript: { ...task.transcript, source: 'edited', segments, warning: '' } }
      setTask(next)
      adopt(await saveNarrateTranscript(task.id, segments))
      setPasteTranscript('')
      setBanner(null)
      setNotes([])
      toast.success(`已保存 ${segments.length} 句转写`)
    } catch (error) {
      setBanner(error instanceof ApiError ? { message: error.message, action: error.action } : { message: errorMessage(error) })
    }
  }

  async function applyManualScript() {
    if (!task) return
    const lines = manualScript.split(/\n+/).map((line) => line.trim()).filter(Boolean)
    if (!lines.length) {
      setBanner({ message: '请先填写至少一句解说词' })
      return
    }
    const duration = Number(task.duration) || Math.max(5, lines.length * 3)
    const each = Math.max(1, duration / lines.length)
    const nextBeats: NarrateBeat[] = lines.map((line, index) => ({
      id: `b${index + 1}`,
      start: Number((index * each).toFixed(2)),
      end: Number((Math.min(duration, (index + 1) * each)).toFixed(2)),
      narration: line.slice(0, 200),
      purpose: (index === 0 ? 'hook' : index === lines.length - 1 ? 'close' : 'explain') as NarrateBeat['purpose'],
      pause: false,
      speed: 1,
      freeze: 0,
      insert_kind: 'none',
      insert_mode: 'fullscreen',
    }))
    setBanner(null)
    try {
      if (task.transcript.segments.length) {
        await saveNarrateTranscript(task.id, task.transcript.segments)
      }
      setBeats(nextBeats)
      adopt(await saveNarrateScript(task.id, nextBeats, voice))
      setStep(2)
      toast.success(`已用手填解说 ${nextBeats.length} 段`)
    } catch (error) {
      setBanner(error instanceof ApiError ? { message: error.message, action: error.action } : { message: errorMessage(error) })
    }
  }

  async function saveReview() {
    if (!task) return null
    const saved = await saveNarrateScript(task.id, beats, voice)
    adopt(saved)
    return saved
  }

  async function render() {
    if (!task) return
    setBanner(null)
    try {
      await saveReview()
      const started = await renderNarrate(task.id)
      const done = await follow(started.job)
      const loaded = done.result?.task_id ? await fetchSignalNarrateTask(String(done.result.task_id)) : started.task
      if (loaded) adopt(loaded)
      showJob(done)
      if (done.status === 'success') setStep(3)
    } catch (error) {
      setBanner(error instanceof ApiError ? { message: error.message, action: error.action } : { message: errorMessage(error) })
    }
  }

  async function previewLine(text: string) {
    try {
      const blob = await previewNarrateVoice(text.slice(0, 80) || '试听', voice)
      const url = URL.createObjectURL(blob)
      const audio = new Audio(url)
      await audio.play()
    } catch (error) {
      setBanner(error instanceof ApiError ? { message: error.message, action: error.action } : { message: errorMessage(error) })
    }
  }

  async function importResult() {
    if (!task) return
    try {
      const result = await importNarrateOutput(task.id)
      adopt(result.task)
      setBanner(null)
      setNotes([`已入库：${result.video.title}`])
    } catch (error) {
      setBanner(error instanceof ApiError ? { message: error.message, action: error.action } : { message: errorMessage(error) })
    }
  }

  async function handleUpload(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return
    setUploading(true)
    try {
      const title = file.name.replace(/\.[^.]+$/, '')
      const uploaded = await uploadVideo(file, title, () => undefined)
      setVideos((prev) => [uploaded, ...prev.filter((item) => item.id !== uploaded.id)])
      requestSelectVideo(uploaded.id)
      toast.success(`已上传「${uploaded.title}」`)
    } catch (error) {
      toast.error(`上传失败：${errorMessage(error)}`)
    } finally {
      setUploading(false)
    }
  }

  function updateSegment(index: number, patch: Partial<SignalNarrateTask['transcript']['segments'][number]>) {
    if (!task) return
    const segments = task.transcript.segments.map((segment, item) => (item === index ? { ...segment, ...patch } : segment))
    setTask({ ...task, transcript: { ...task.transcript, segments } })
  }

  function updateBeat(index: number, patch: Partial<NarrateBeat>) {
    setBeats(beats.map((beat, item) => (item === index ? { ...beat, ...patch } : beat)))
  }

  const busy = job?.status === 'pending' || job?.status === 'running'
  const outputUrl = task?.output ? `/api/narrate/tasks/${task.id}/output` : ''
  const toneSelectOptions = useMemo(() => {
    const base = [...TONE_OPTIONS]
    if (tone && !base.some((item) => item.value === tone)) base.unshift({ value: tone, label: tone })
    return base
  }, [tone])

  const promptFields = (
    <div className="space-y-3 rounded-xl border border-line-soft bg-fill/40 p-3">
      <div className="flex items-center justify-between gap-2">
        <strong className="text-[13px] text-ink-1">创作要求 / 提示词</strong>
        <span className="text-[11px] text-ink-4">理解与写稿共用</span>
      </div>
      <label className="block text-xs text-ink-3">
        提示词
        <TextArea className="mt-1" value={instruction} onChange={setInstruction} rows={3} />
      </label>
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="text-xs text-ink-3">
          语气
          <div className="mt-1">
            <Select value={tone} onChange={setTone} options={toneSelectOptions} />
          </div>
        </label>
        <label className="text-xs text-ink-3">
          目标时长（秒）
          <Input className="mt-1" type="number" min={5} value={String(target)} onChange={(value) => setTarget(Number(value) || 30)} />
        </label>
      </div>
    </div>
  )

  return (
    <div>
      <PageHeader title="AI 解说" desc="理解后写稿；审阅可暂停解说、变速静止，也能插入图片或视频。" />
      <div className="mb-4 grid gap-2 sm:grid-cols-4">
        {STEPS.map((item, index) => (
          <button
            key={item.label}
            type="button"
            onClick={() => setStep(index)}
            className={`rounded-xl border px-3 py-2 text-left ${step === index ? 'border-accent bg-accent-soft' : index < step ? 'border-line bg-fill' : 'border-line-soft bg-transparent'}`}
          >
            <span className="tnum text-[11px] text-ink-4">{index + 1}</span>
            <strong className="mt-0.5 block text-[13px] text-ink-1">{item.label}</strong>
            <small className="text-[11px] text-ink-3">{item.desc}</small>
          </button>
        ))}
      </div>
      {banner ? (
        <Alert type="error" className="mb-4">
          <span className="font-medium">{banner.message}</span>
          {banner.action ? <span className="mt-1 block text-ink-3">{banner.action}</span> : null}
        </Alert>
      ) : null}
      {notes[0] ? <Alert type="warning" className="mb-4">{notes[0]}</Alert> : null}
      {job ? (
        <div className="mb-4">
          <div className="mb-1 flex justify-between text-xs text-ink-3"><span>{job.label}</span><span className="tnum">{Math.round(job.progress)}%</span></div>
          <Progress percent={job.progress / 100} status={job.status === 'failed' ? 'error' : 'normal'} />
          {job.logs.length > 0 ? <p className="mt-1 text-[11px] text-ink-4">{job.logs[job.logs.length - 1]}</p> : null}
        </div>
      ) : null}

      <div className="grid gap-4 lg:grid-cols-[220px_minmax(0,1fr)]">
        <Panel title="进度" pad="sm">
          {task ? (
            <div className="space-y-1 text-xs text-ink-3">
              <p>转写 {task.transcript.segments.length} 句 · {task.transcript.source}</p>
              <p>画面 {task.frames.length} 帧</p>
              <p>文稿 {beats.length} 段</p>
              <p>成片 {task.output ? formatTime(task.output.duration) : '未渲染'}</p>
            </div>
          ) : <p className="text-xs text-ink-4">四步都看得到中间结果。</p>}
          {selectedVideo ? (
            <div className="mt-3 border-t border-line-soft pt-3 text-xs text-ink-3">
              <p className="line-clamp-2 font-medium text-ink-1">{selectedVideo.title}</p>
              <p className="tnum mt-1">{formatDuration(selectedVideo.duration)} · 质量 {selectedVideo.quality_score != null ? Math.round(selectedVideo.quality_score) : '--'}</p>
            </div>
          ) : null}
        </Panel>

        <div className="min-w-0">
          {step === 0 ? (
            <div className="space-y-4">
              <div className="flex flex-wrap items-end gap-3">
                <label className="min-w-48 flex-1 text-xs text-ink-3">
                  搜索标题
                  <div className="mt-1">
                    <Input value={search} onChange={setSearch} placeholder="按标题筛选" prefix={<MagnifyingGlass size={14} />} />
                  </div>
                </label>
                <label className="text-xs text-ink-3">
                  来源
                  <div className="mt-1 w-36"><Select value={platformFilter} onChange={setPlatformFilter} options={platformOptions} /></div>
                </label>
                <label className="text-xs text-ink-3">
                  质量
                  <div className="mt-1 w-32"><Select value={qualityFilter} onChange={(value) => setQualityFilter(value as QualityBand)} options={[...QUALITY_BANDS]} /></div>
                </label>
                <label className="text-xs text-ink-3">
                  排序
                  <div className="mt-1 w-32"><Select value={sortBy} onChange={(value) => setSortBy(value as SortKey)} options={[...SORT_OPTIONS]} /></div>
                </label>
                <input ref={fileInputRef} type="file" accept="video/*" className="hidden" onChange={(event) => void handleUpload(event)} />
                <Button variant="secondary" disabled={uploading || busy} loading={uploading} icon={<UploadSimple size={14} />} onClick={() => fileInputRef.current?.click()}>
                  上传素材
                </Button>
                <Button variant="primary" disabled={busy || videoId == null} onClick={() => void understand()}>开始理解</Button>
              </div>

              {libraryError ? <Alert type="error">{libraryError}</Alert> : null}
              {videos.length === 0 && !libraryError ? <EmptyState title="还没有素材" desc="先到素材库放进一条原片，或点上方上传。" /> : null}
              {videos.length > 0 && filteredVideos.length === 0 ? <EmptyState title="没有匹配的素材" desc="换个关键词或筛选条件试试。" /> : null}

              {filteredVideos.length > 0 ? (
                <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-4">
                  {filteredVideos.map((video) => {
                    const selected = video.id === videoId
                    return (
                      <button
                        key={video.id}
                        type="button"
                        onClick={() => requestSelectVideo(video.id)}
                        className={`overflow-hidden rounded-xl border text-left transition ${selected ? 'border-accent ring-2 ring-accent/30' : 'border-line-soft hover:border-line'}`}
                      >
                        <div className="relative aspect-video bg-fill">
                          {video.cover_url ? (
                            <img src={video.cover_url} alt="" className="h-full w-full object-cover" loading="lazy" />
                          ) : (
                            <div className="flex h-full items-center justify-center text-2xl text-ink-4">{(video.title[0] || '影').toUpperCase()}</div>
                          )}
                          <span className="absolute bottom-1.5 left-1.5 rounded bg-black/60 px-1.5 py-0.5 text-[10px] text-white tnum">{formatDuration(video.duration)}</span>
                          {video.quality_score != null ? (
                            <span className={`absolute right-1.5 top-1.5 rounded px-1.5 py-0.5 text-[10px] tnum ${qualityBadgeClass(video.quality_score)}`}>
                              质量 {Math.round(video.quality_score)}
                            </span>
                          ) : null}
                          {selected ? <span className="absolute left-1.5 top-1.5 rounded bg-accent px-1.5 py-0.5 text-[10px] text-white">已选</span> : null}
                        </div>
                        <div className="space-y-1 p-2.5">
                          <div className="line-clamp-2 text-[13px] font-medium text-ink-1" title={video.title}>{video.title}</div>
                          <div className="text-[11px] text-ink-4">{platformLabel(video.source_platform)}</div>
                        </div>
                      </button>
                    )
                  })}
                </div>
              ) : null}

              {promptFields}

              {task?.transcript.warning ? <Alert type="warning">{task.transcript.warning}</Alert> : null}
              {task && !task.transcript.segments.length ? (
                <div className="space-y-2 rounded-xl border border-amber-500/40 bg-amber-500/10 p-3">
                  <strong className="block text-[13px] text-ink-1">转写为空 — 手动粘贴口播</strong>
                  <p className="text-[12px] text-ink-3">每行一句。保存后可继续用 AI 写稿，或到「文稿」里自己填写解说。</p>
                  <TextArea value={pasteTranscript} onChange={setPasteTranscript} rows={5} placeholder={'第一句口播\n第二句口播'} />
                  <div className="flex flex-wrap gap-2">
                    <Button size="sm" variant="primary" disabled={busy} onClick={() => void applyPasteTranscript()}>保存粘贴的转写</Button>
                    <Button size="sm" variant="secondary" disabled={busy} onClick={() => { setScriptMode('manual'); setStep(1) }}>跳过转写，自己填写解说</Button>
                  </div>
                </div>
              ) : null}
              <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_280px]">
                <div className="space-y-2">
                  {(task?.transcript.segments || []).map((segment, index) => (
                    <div key={segment.id || index} className="rounded-xl border border-line-soft p-3">
                      <div className="mb-2 flex flex-wrap gap-2">
                        <Input className="tnum w-24" type="number" step="0.1" value={String(segment.start)} onChange={(value) => updateSegment(index, { start: Number(value) })} aria-label="开始" />
                        <Input className="tnum w-24" type="number" step="0.1" value={String(segment.end)} onChange={(value) => updateSegment(index, { end: Number(value) })} aria-label="结束" />
                        <Button size="sm" variant="ghost" onClick={() => {
                          if (!task) return
                          setTask({ ...task, transcript: { ...task.transcript, segments: task.transcript.segments.filter((_, item) => item !== index) } })
                        }}>删除</Button>
                      </div>
                      <TextArea value={segment.text} onChange={(value) => updateSegment(index, { text: value })} />
                    </div>
                  ))}
                  {task ? (
                    <div className="flex flex-wrap gap-2">
                      <Button size="sm" variant="secondary" onClick={() => {
                        const segments = [...task.transcript.segments, { id: `t${task.transcript.segments.length + 1}`, start: 0, end: 1, text: '' }]
                        setTask({ ...task, transcript: { ...task.transcript, segments } })
                      }}>加一句</Button>
                      <Button size="sm" variant="secondary" disabled={busy} onClick={() => void saveTranscript()}>保存转写</Button>
                    </div>
                  ) : null}
                </div>
                <div className="space-y-2">
                  {(task?.frames || []).map((frame) => (
                    <figure key={frame.image} className="overflow-hidden rounded-xl border border-line-soft">
                      <img src={`/api/narrate/tasks/${task?.id}/frames/${frame.image}`} alt="" className="aspect-video w-full object-cover" />
                      <figcaption className="space-y-1 p-2 text-xs">
                        <span className="tnum text-ink-4">{formatTime(frame.time)}</span>
                        <p className="text-ink-2">{frame.caption || '没有画面说明'}</p>
                        {frame.caption_error ? <p className="text-danger">{frame.caption_error}</p> : null}
                      </figcaption>
                    </figure>
                  ))}
                </div>
              </div>
            </div>
          ) : null}

          {step === 1 ? (
            <div className="max-w-2xl space-y-3">
              {promptFields}
              <div className="flex flex-wrap gap-2">
                <Button
                  variant={scriptMode === 'ai' ? 'primary' : 'secondary'}
                  disabled={busy || !task}
                  onClick={() => setScriptMode('ai')}
                >AI 生成解说</Button>
                <Button
                  variant={scriptMode === 'manual' ? 'primary' : 'secondary'}
                  disabled={busy || !task}
                  onClick={() => setScriptMode('manual')}
                >自己填写解说</Button>
              </div>
              {scriptMode === 'ai' ? (
                <>
                  <div className="flex flex-wrap gap-2">
                    <Button variant="primary" disabled={busy || !task} onClick={() => void draft('llm')}>用模型写</Button>
                    <Button variant="secondary" disabled={busy || !task || !(task?.transcript.segments.length)} onClick={() => void draft('skeleton')}>逐句骨架</Button>
                  </div>
                  <p className="text-[13px] text-ink-3">上方提示词与语气和第一步共用。没有转写时可用画面说明写稿；也可改选「自己填写解说」。</p>
                </>
              ) : (
                <>
                  <label className="block text-xs text-ink-3">
                    解说词（每行一段）
                    <TextArea className="mt-1" value={manualScript} onChange={setManualScript} rows={8} placeholder={'开头一句\n中间说明\n收束'} />
                  </label>
                  <Button variant="primary" disabled={busy || !task} onClick={() => void applyManualScript()}>用手填稿进入审阅</Button>
                  <p className="text-[13px] text-ink-3">会按片长均分时间轴；到「审阅」里还能改每段起止和声音。</p>
                </>
              )}
            </div>
          ) : null}

          {step === 2 ? (
            <div className="space-y-3">
              {beats.map((beat, index) => {
                const speed = Math.min(2, Math.max(0.5, Number(beat.speed ?? 1) || 1))
                const freeze = Math.min(30, Math.max(0, Number(beat.freeze ?? 0) || 0))
                const picture = Math.max(0.1, (beat.end - beat.start) / speed + freeze)
                const speechRate = Math.min(2, Math.max(0.5, Number(voice.rate ?? 1) || 1))
                const crowded = !beat.pause && beat.narration.length > picture * 4.3 * speechRate * 1.35
                return (
                  <div key={beat.id} className="rounded-xl border border-line-soft bg-card p-3">
                    <div className="mb-2 flex flex-wrap items-center gap-2">
                      <Tag>{beat.id}</Tag>
                      <select className="h-8 rounded-lg border border-line bg-fill px-2 text-xs text-ink-1" value={beat.purpose} onChange={(event) => updateBeat(index, { purpose: event.target.value as NarrateBeat['purpose'] })}>
                        {PURPOSES.map((purpose) => <option key={purpose} value={purpose}>{PURPOSE_LABEL[purpose]}</option>)}
                      </select>
                      <Input className="tnum w-24" type="number" step="0.1" value={String(beat.start)} onChange={(value) => updateBeat(index, { start: Number(value) })} aria-label="段开始" />
                      <Input className="tnum w-24" type="number" step="0.1" value={String(beat.end)} onChange={(value) => updateBeat(index, { end: Number(value) })} aria-label="段结束" />
                      <span className="tnum text-xs text-ink-4">{picture.toFixed(1)}s · {beat.narration.length} 字</span>
                      <Button size="sm" variant="ghost" disabled={!!beat.pause || !beat.narration.trim()} onClick={() => void previewLine(beat.narration || '试听')}>试听</Button>
                      <Button size="sm" variant="ghost" onClick={() => setBeats(beats.filter((_, item) => item !== index))}>删除</Button>
                    </div>
                    <div className="mb-2 flex flex-wrap items-center gap-3">
                      <Switch
                        checked={!!beat.pause}
                        onChange={(checked) => updateBeat(index, { pause: checked, narration: checked ? beat.narration : beat.narration })}
                        label="暂停解说"
                      />
                      <span className="text-xs text-ink-3">暂停解说</span>
                      <label className="flex items-center gap-2 text-xs text-ink-2">
                        速度
                        <input
                          className="w-28"
                          type="range"
                          min={0.5}
                          max={2}
                          step={0.05}
                          value={speed}
                          onChange={(event) => updateBeat(index, { speed: Number(event.target.value) })}
                          aria-label="段落速度"
                        />
                        <span className="tnum">{speed.toFixed(2)}</span>
                      </label>
                      <label className="flex items-center gap-2 text-xs text-ink-2">
                        静止
                        <input
                          className="h-7 w-16 rounded-lg border border-line bg-fill px-2 text-xs"
                          type="number"
                          min={0}
                          max={30}
                          step={0.5}
                          value={freeze}
                          onChange={(event) => updateBeat(index, { freeze: Number(event.target.value) })}
                          aria-label="段落静止秒数"
                        />
                        秒
                      </label>
                      <Button size="sm" variant="secondary" onClick={() => setPickerBeatIndex(index)}>加图片 / 加视频</Button>
                      {beat.insert_kind && beat.insert_kind !== 'none' ? (
                        <Button size="sm" variant="ghost" onClick={() => updateBeat(index, { insert_kind: 'none', image_id: 0, image_name: '', video_id: 0, video_name: '', insert_mode: 'fullscreen' })}>清除插入</Button>
                      ) : null}
                    </div>
                    {beat.pause ? <p className="mb-2 text-xs text-ink-3">这一段不配音，画面按速度、静止和插入继续播放。口播留空也会暂停解说。</p> : null}
                    {beat.insert_kind === 'image' ? (
                      <p className="mb-2 text-xs text-ink-2">已插入图片 · {beat.insert_mode === 'overlay' ? '叠在画面上' : '全屏静帧'}{beat.image_name ? ` · ${beat.image_name}` : beat.image_id ? ` · #${beat.image_id}` : ''}</p>
                    ) : null}
                    {beat.insert_kind === 'video' ? (
                      <p className="mb-2 text-xs text-ink-2">已插入视频{beat.video_id ? ` · 素材 ${beat.video_id}` : beat.video_name ? ` · ${beat.video_name}` : ''}</p>
                    ) : null}
                    <TextArea
                      value={beat.narration}
                      onChange={(value) => updateBeat(index, { narration: value, pause: value.trim() ? beat.pause : true })}
                      placeholder={beat.pause ? '暂停解说，可以留空' : '这一段的口播'}
                    />
                    {crowded ? <p className="mt-1 text-xs text-danger">这句偏长，渲染时可能被拒绝。缩短文字、放慢画面速度、增加静止，或拉长结束时间。</p> : null}
                  </div>
                )
              })}
              <Button size="sm" variant="secondary" onClick={() => {
                const last = beats.length ? beats[beats.length - 1] : undefined
                const start = last?.end || 0
                setBeats([...beats, { id: `b${beats.length + 1}`, start, end: start + 3, narration: '补一句', purpose: 'explain', pause: false, speed: 1, freeze: 0, insert_kind: 'none', insert_mode: 'fullscreen' }])
              }}>加一段</Button>
              <div className="grid gap-3 rounded-xl border border-line-soft p-3 md:grid-cols-3">
                <label className="text-xs text-ink-3">
                  语音引擎
                  <select className="mt-1 h-9 w-full rounded-[10px] border border-line bg-fill px-2 text-[13px] text-ink-1" value={voice.engine} onChange={(event) => setVoice({ ...voice, engine: event.target.value as NarrateVoice['engine'] })}>
                    <option value="auto">自动</option>
                    {(engines?.engines || []).map((engine) => <option key={engine.id} value={engine.id}>{engine.id} · {engine.available ? '可用' : '不可用'}</option>)}
                  </select>
                </label>
                <label className="text-xs text-ink-3">
                  音色
                  <select className="mt-1 h-9 w-full rounded-[10px] border border-line bg-fill px-2 text-[13px] text-ink-1" value={voice.voice} onChange={(event) => setVoice({ ...voice, voice: event.target.value })}>
                    {(engines?.voices || [{ engine: 'edge', id: voice.voice, label: voice.voice }]).map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
                  </select>
                </label>
                <label className="text-xs text-ink-3">
                  语速 {voice.rate.toFixed(2)}
                  <input className="mt-3 w-full" type="range" min={0.7} max={1.4} step={0.05} value={voice.rate} onChange={(event) => setVoice({ ...voice, rate: Number(event.target.value) })} />
                </label>
                <label className="text-xs text-ink-3">
                  原声音量 {voice.original_volume.toFixed(2)}
                  <input className="mt-3 w-full" type="range" min={0} max={1} step={0.02} value={voice.original_volume} onChange={(event) => setVoice({ ...voice, original_volume: Number(event.target.value) })} />
                </label>
                <div className="flex items-center gap-2 text-[13px] text-ink-2">
                  <Switch checked={voice.subtitles} onChange={(checked) => setVoice({ ...voice, subtitles: checked })} label="烧录字幕" />
                  烧录字幕
                </div>
                <label className="text-xs text-ink-3">字号<Input className="mt-1" type="number" min={18} max={96} value={String(voice.font_size)} onChange={(value) => setVoice({ ...voice, font_size: Number(value) || 46 })} /></label>
              </div>
              <div className="flex flex-wrap gap-2">
                <Button variant="secondary" disabled={busy || !task} onClick={() => void saveReview().catch((error: unknown) => setBanner({ message: errorMessage(error) }))}>保存审阅</Button>
                <Button variant="primary" disabled={busy || !task || beats.length === 0} onClick={() => void render()}>开始渲染</Button>
              </div>
            </div>
          ) : null}

          {step === 3 ? (
            <div className="space-y-3">
              {task?.output ? (
                <>
                  <video key={outputUrl} className="max-h-[480px] w-full rounded-xl bg-black" src={outputUrl} controls />
                  <p className="text-[13px] text-ink-3">
                    引擎：{task.output.tts_engine} · {formatTime(task.output.duration)}
                    {task.output.tts_engine === 'tone' ? ' · 这是测试音，不是人声' : ''}
                  </p>
                  {(task.output.warnings || []).map((warning) => <Alert key={warning} type="warning">{warning}</Alert>)}
                  <div className="flex flex-wrap gap-2">
                    <a className="inline-flex h-9 items-center rounded-[10px] bg-accent px-4 text-[13px] font-medium text-white" href={outputUrl}>下载成片</a>
                    <Button variant="secondary" onClick={() => void importResult()}>入库</Button>
                    <Button variant="ghost" disabled>剪映草稿暂未接入</Button>
                  </div>
                </>
              ) : (
                <div className="space-y-3">
                  <p className="text-[13px] text-ink-3">还没有成片。确认审阅里的每句都能放进对应画面，然后渲染。</p>
                  <Button variant="primary" disabled={busy || !task} onClick={() => void render()}>开始渲染</Button>
                  <Button variant="ghost" disabled>剪映草稿暂未接入</Button>
                </div>
              )}
            </div>
          ) : null}
        </div>
      </div>

      <Modal
        open={pendingVideoId != null}
        onClose={() => setPendingVideoId(null)}
        title="换一条素材"
        footer={
          <>
            <Button variant="secondary" onClick={() => setPendingVideoId(null)}>留下</Button>
            <Button variant="primary" onClick={() => {
              setVideoId(pendingVideoId)
              setTask(null)
              setBeats([])
              localStorage.removeItem(TASK_KEY)
              setPendingVideoId(null)
              setStep(0)
              setParams((current) => {
                const copy = new URLSearchParams(current)
                copy.delete('task')
                if (pendingVideoId) copy.set('video', String(pendingVideoId))
                return copy
              }, { replace: true })
            }}>换素材</Button>
          </>
        }
      >
        <p className="text-[13px] text-ink-2">当前解说任务还在。换素材会从理解重新开始，已写的稿留在原来的任务里。</p>
      </Modal>

      <InsertPicker
        open={pickerBeatIndex != null}
        onClose={() => setPickerBeatIndex(null)}
        videos={videos}
        onPickImage={(picked: PickedImage) => {
          if (pickerBeatIndex == null) return
          updateBeat(pickerBeatIndex, {
            insert_kind: 'image',
            insert_mode: picked.overlay ? 'overlay' : 'fullscreen',
            image_id: picked.imageId,
            image_name: picked.imageName,
            video_id: 0,
            video_name: '',
          })
          setPickerBeatIndex(null)
        }}
        onPickVideo={(picked: PickedVideo) => {
          if (pickerBeatIndex == null) return
          updateBeat(pickerBeatIndex, {
            insert_kind: 'video',
            insert_mode: 'fullscreen',
            video_id: picked.videoId,
            video_name: picked.videoName,
            image_id: 0,
            image_name: '',
          })
          setPickerBeatIndex(null)
        }}
      />
    </div>
  )
}
