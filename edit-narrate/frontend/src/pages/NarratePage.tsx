import { useEffect, useState } from 'react'
import { FailBanner } from '../components/FailBanner.tsx'
import { Dialog } from '../components/ui/dialog.tsx'
import { Badge } from '../components/ui/badge.tsx'
import { Button } from '../components/ui/button.tsx'
import { Input } from '../components/ui/input.tsx'
import { Label } from '../components/ui/label.tsx'
import { Progress } from '../components/ui/progress.tsx'
import { Textarea } from '../components/ui/textarea.tsx'
import { ApiError, api } from '../lib/api.ts'
import type { Beat, EngineInfo, Job, NarrateTask, Video, VoiceSettings } from '../lib/types.ts'
import { formatTime, sleep } from '../lib/utils.ts'

const TASK_KEY = 'video-hub:narrate-task'
const STEPS = ['理解', '文稿', '审阅', '成片'] as const
const PURPOSES: Beat['purpose'][] = ['hook', 'explain', 'evidence', 'turn', 'close']
const PURPOSE_LABEL: Record<Beat['purpose'], string> = {
  hook: '开头',
  explain: '说明',
  evidence: '依据',
  turn: '转折',
  close: '收束',
}
const DEFAULT_VOICE: VoiceSettings = {
  engine: 'auto',
  voice: 'zh-CN-XiaoxiaoNeural',
  rate: 1,
  original_volume: 0.18,
  subtitles: true,
  font_size: 46,
  margin_v: 48,
}

export default function NarratePage() {
  const [videos, setVideos] = useState<Video[]>([])
  const [videoId, setVideoId] = useState<number | null>(null)
  const [pendingVideoId, setPendingVideoId] = useState<number | null>(null)
  const [maxFrames, setMaxFrames] = useState(6)
  const [step, setStep] = useState(0)
  const [task, setTask] = useState<NarrateTask | null>(null)
  const [beats, setBeats] = useState<Beat[]>([])
  const [voice, setVoice] = useState<VoiceSettings>(DEFAULT_VOICE)
  const [instruction, setInstruction] = useState('按口播里已经说过的事实写，不要加没有出现的数字。')
  const [tone, setTone] = useState('清楚、具体')
  const [target, setTarget] = useState(30)
  const [job, setJob] = useState<Job | null>(null)
  const [banner, setBanner] = useState<{ message: string; action?: string } | null>(null)
  const [notes, setNotes] = useState<string[]>([])
  const [engines, setEngines] = useState<EngineInfo | null>(null)
  const [libraryError, setLibraryError] = useState<string | null>(null)

  useEffect(() => {
    api.videos().then((list) => {
      setVideos(list)
      setVideoId((current) => current ?? list[0]?.id ?? null)
    }).catch((error: Error) => setLibraryError(error.message))
    api.voices().then(setEngines).catch(() => setEngines(null))
    const saved = localStorage.getItem(TASK_KEY)
    if (!saved) return
    api.narration(saved).then((loaded) => {
      adopt(loaded)
    }).catch(() => localStorage.removeItem(TASK_KEY))
  }, [])

  function adopt(next: NarrateTask) {
    setTask(next)
    setVideoId(next.video_id)
    setBeats(next.script?.beats || [])
    setVoice(next.voice || DEFAULT_VOICE)
    setNotes(next.script?.warnings || next.warnings || [])
    localStorage.setItem(TASK_KEY, next.id)
  }

  async function follow(initial: Job): Promise<Job> {
    let current = initial
    setJob(current)
    while (current.status === 'pending' || current.status === 'running') {
      await sleep(800)
      current = await api.job(current.id)
      setJob(current)
    }
    return current
  }

  function showJob(done: Job) {
    if (done.error) setBanner({ message: done.error.message, action: done.error.action })
    else if (done.status === 'failed') setBanner({ message: done.label || '这一步失败', action: '看任务日志最后一行。' })
    else setBanner(null)
  }

  async function understand() {
    if (videoId == null) return
    setBanner(null)
    try {
      const started = await api.createNarration(videoId, maxFrames)
      const done = await follow(started.job)
      const loaded = done.result?.task_id ? await api.narration(String(done.result.task_id)) : started.task
      if (loaded) adopt(loaded)
      showJob(done)
      if (loaded?.transcript.segments.length) setStep(1)
    } catch (error) {
      setBanner(error instanceof ApiError ? { message: error.message, action: error.action } : { message: '理解失败' })
    }
  }

  async function saveTranscript() {
    if (!task) return
    try {
      adopt(await api.saveTranscript(task.id, task.transcript.segments))
      setBanner(null)
    } catch (error) {
      setBanner(error instanceof ApiError ? { message: error.message, action: error.action } : { message: '转写没保存' })
    }
  }

  async function draft(mode: 'llm' | 'skeleton') {
    if (!task) return
    setBanner(null)
    try {
      adopt(await api.saveTranscript(task.id, task.transcript.segments))
      const started = await api.draftScript(task.id, { mode, instruction, tone, target_duration: target })
      const done = await follow(started.job)
      const loaded = done.result?.task_id ? await api.narration(String(done.result.task_id)) : started.task
      if (loaded) {
        adopt(loaded)
        setStep(2)
      }
      showJob(done)
    } catch (error) {
      setBanner(error instanceof ApiError ? { message: error.message, action: error.action } : { message: '文稿失败' })
    }
  }

  async function saveReview() {
    if (!task) return null
    const saved = await api.saveScript(task.id, beats, voice)
    adopt(saved)
    return saved
  }

  async function render() {
    if (!task) return
    setBanner(null)
    try {
      await saveReview()
      const started = await api.renderNarration(task.id)
      const done = await follow(started.job)
      const loaded = done.result?.task_id ? await api.narration(String(done.result.task_id)) : started.task
      if (loaded) adopt(loaded)
      showJob(done)
      if (done.status === 'success') setStep(3)
    } catch (error) {
      setBanner(error instanceof ApiError ? { message: error.message, action: error.action } : { message: '渲染失败' })
    }
  }

  async function previewLine(text: string) {
    try {
      const blob = await api.previewVoice(text.slice(0, 80), voice)
      const url = URL.createObjectURL(blob)
      const audio = new Audio(url)
      await audio.play()
    } catch (error) {
      setBanner(error instanceof ApiError ? { message: error.message, action: error.action } : { message: '试听失败' })
    }
  }

  async function importResult() {
    if (!task) return
    try {
      const result = await api.importNarration(task.id)
      adopt(result.task)
      setBanner(null)
      setNotes([`已入库：${result.video.title}`])
    } catch (error) {
      setBanner(error instanceof ApiError ? { message: error.message, action: error.action } : { message: '入库失败' })
    }
  }

  const busy = job?.status === 'pending' || job?.status === 'running'
  const outputUrl = task?.output ? `/api/narrate/tasks/${task.id}/output` : ''

  return (
    <div className="grid h-full min-h-0 lg:grid-cols-[220px_minmax(0,1fr)]">
      <aside className="border-b border-line p-3 lg:border-b-0 lg:border-r">
        <p className="px-2 text-xs text-faint">四步都看得到中间结果</p>
        <ol className="mt-3 space-y-1">
          {STEPS.map((label, index) => (
            <li key={label}>
              <button
                type="button"
                className={`flex w-full items-center gap-2 rounded-md px-2 py-2 text-left text-sm ${step === index ? 'bg-panel-2 text-ink' : 'text-muted'}`}
                onClick={() => setStep(index)}
              >
                <span className="tnum w-4 text-xs text-faint">{index + 1}</span>
                {label}
              </button>
            </li>
          ))}
        </ol>
        {task ? (
          <div className="mt-4 space-y-1 px-2 text-xs text-faint">
            <p>转写 {task.transcript.segments.length} 句 · {task.transcript.source}</p>
            <p>画面 {task.frames.length} 帧</p>
            <p>文稿 {beats.length} 段</p>
            <p>成片 {task.output ? formatTime(task.output.duration) : '未渲染'}</p>
          </div>
        ) : null}
      </aside>
      <section className="min-h-0 space-y-4 overflow-auto p-4">
        {banner ? <FailBanner message={banner.message} action={banner.action} /> : null}
        {notes[0] ? <p className="text-sm text-amber">{notes[0]}</p> : null}
        {job ? (
          <div className="space-y-1">
            <div className="flex justify-between text-xs text-muted"><span>{job.label}</span><span className="tnum">{Math.round(job.progress)}%</span></div>
            <Progress value={job.progress} />
            {job.logs.at(-1) ? <p className="text-[11px] text-faint">{job.logs.at(-1)}</p> : null}
          </div>
        ) : null}

        {step === 0 ? (
          <div className="space-y-4">
            <div className="flex flex-wrap items-end gap-3">
              <div className="min-w-56 flex-1">
                <Label>素材</Label>
                <select
                  className="mt-1 h-9 w-full rounded-md border border-line bg-bg px-2 text-sm"
                  value={videoId ?? ''}
                  onChange={(event) => {
                    const next = Number(event.target.value)
                    if (task && task.video_id !== next) setPendingVideoId(next)
                    else setVideoId(next)
                  }}
                >
                  {videos.map((video) => (
                    <option key={video.id} value={video.id}>{video.title}</option>
                  ))}
                </select>
                {libraryError ? <p className="mt-1 text-xs text-drop">{libraryError}</p> : null}
              </div>
              <div>
                <Label>抽帧上限</Label>
                <Input className="mt-1 w-24" type="number" min={3} max={12} value={maxFrames} onChange={(event) => setMaxFrames(Number(event.target.value))} />
              </div>
              <Button disabled={busy || videoId == null} onClick={() => void understand()}>开始理解</Button>
              <Button variant="secondary" onClick={() => void api.seedDemo().then(() => api.videos().then(setVideos))}>演示素材</Button>
            </div>
            {task?.transcript.warning ? <FailBanner message={task.transcript.warning} action="这不是失败。配好 ASR 后重新理解即可换成真实转写。" /> : null}
            <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_280px]">
              <div className="space-y-2">
                {(task?.transcript.segments || []).map((segment, index) => (
                  <div key={segment.id} className="grid grid-cols-[88px_88px_minmax(0,1fr)] gap-2">
                    <Input className="tnum" type="number" step="0.1" value={segment.start} onChange={(event) => updateSegment(index, { start: Number(event.target.value) })} />
                    <Input className="tnum" type="number" step="0.1" value={segment.end} onChange={(event) => updateSegment(index, { end: Number(event.target.value) })} />
                    <Input value={segment.text} onChange={(event) => updateSegment(index, { text: event.target.value })} />
                  </div>
                ))}
                {task && task.transcript.segments.length === 0 ? (
                  <p className="text-sm text-muted">转写是空的。把口播按「开始秒、结束秒、文本」贴进来，保存后仍可写文稿。</p>
                ) : null}
                {task ? (
                  <Button size="sm" variant="secondary" onClick={() => {
                    const segments = [...task.transcript.segments, { id: `t${task.transcript.segments.length + 1}`, start: 0, end: 1, text: '' }]
                    setTask({ ...task, transcript: { ...task.transcript, segments } })
                  }}>加一句</Button>
                ) : null}
                {task ? <Button size="sm" variant="secondary" disabled={busy} onClick={() => void saveTranscript()}>保存转写</Button> : null}
              </div>
              <div className="space-y-2">
                {(task?.frames || []).map((frame) => (
                  <figure key={frame.image} className="overflow-hidden rounded-md border border-line">
                    <img src={`/api/narrate/tasks/${task?.id}/frames/${frame.image}`} alt="" className="aspect-video w-full object-cover" />
                    <figcaption className="space-y-1 p-2 text-xs">
                      <span className="tnum text-faint">{formatTime(frame.time)}</span>
                      <p>{frame.caption || '没有画面说明'}</p>
                      {frame.caption_error ? <p className="text-drop">{frame.caption_error}</p> : null}
                    </figcaption>
                  </figure>
                ))}
              </div>
            </div>
          </div>
        ) : null}

        {step === 1 ? (
          <div className="max-w-2xl space-y-3">
            <Label>要求</Label>
            <Textarea value={instruction} onChange={(event) => setInstruction(event.target.value)} />
            <div className="grid grid-cols-2 gap-3">
              <div>
                <Label>语气</Label>
                <Input className="mt-1" value={tone} onChange={(event) => setTone(event.target.value)} />
              </div>
              <div>
                <Label>目标时长（秒）</Label>
                <Input className="mt-1" type="number" min={5} value={target} onChange={(event) => setTarget(Number(event.target.value))} />
              </div>
            </div>
            <div className="flex flex-wrap gap-2">
              <Button disabled={busy || !task} onClick={() => void draft('llm')}>用模型写</Button>
              <Button variant="secondary" disabled={busy || !task} onClick={() => void draft('skeleton')}>逐句骨架</Button>
            </div>
            <p className="text-sm text-muted">模型不可用时会直接说明原因，不会交回一份空稿。骨架只切转写，不润色。</p>
          </div>
        ) : null}

        {step === 2 ? (
          <div className="space-y-3">
            {beats.map((beat, index) => {
              const picture = Math.max(0.1, beat.end - beat.start)
              const crowded = beat.narration.length > picture * 4.3 * 1.35
              return (
                <div key={beat.id} className="rounded-lg border border-line bg-panel p-3">
                  <div className="mb-2 flex flex-wrap items-center gap-2">
                    <Badge>{beat.id}</Badge>
                    <select className="h-8 rounded-md border border-line bg-bg px-2 text-xs" value={beat.purpose} onChange={(event) => updateBeat(index, { purpose: event.target.value as Beat['purpose'] })}>
                      {PURPOSES.map((purpose) => <option key={purpose} value={purpose}>{PURPOSE_LABEL[purpose]}</option>)}
                    </select>
                    <Input className="tnum w-24" type="number" step="0.1" value={beat.start} onChange={(event) => updateBeat(index, { start: Number(event.target.value) })} />
                    <Input className="tnum w-24" type="number" step="0.1" value={beat.end} onChange={(event) => updateBeat(index, { end: Number(event.target.value) })} />
                    <span className="tnum text-xs text-faint">{picture.toFixed(1)}s · {beat.narration.length} 字</span>
                    <Button size="sm" variant="ghost" onClick={() => void previewLine(beat.narration || '试听')}>试听</Button>
                    <Button size="sm" variant="ghost" onClick={() => setBeats(beats.filter((_, item) => item !== index))}>删除</Button>
                  </div>
                  <Textarea value={beat.narration} onChange={(event) => updateBeat(index, { narration: event.target.value })} />
                  {crowded ? <p className="mt-1 text-xs text-drop">这句偏长，渲染时可能被拒绝。缩短文字或拉长结束时间。</p> : null}
                </div>
              )
            })}
            <Button size="sm" variant="secondary" onClick={() => setBeats([...beats, { id: `b${beats.length + 1}`, start: beats.at(-1)?.end || 0, end: (beats.at(-1)?.end || 0) + 3, narration: '补一句', purpose: 'explain' }])}>加一段</Button>
            <div className="grid gap-3 rounded-lg border border-line p-3 md:grid-cols-3">
              <div>
                <Label>语音引擎</Label>
                <select className="mt-1 h-9 w-full rounded-md border border-line bg-bg px-2 text-sm" value={voice.engine} onChange={(event) => setVoice({ ...voice, engine: event.target.value as VoiceSettings['engine'] })}>
                  <option value="auto">自动</option>
                  {(engines?.engines || []).map((engine) => (
                    <option key={engine.id} value={engine.id}>{engine.id} · {engine.available ? '可用' : '不可用'}</option>
                  ))}
                </select>
              </div>
              <div>
                <Label>音色</Label>
                <select className="mt-1 h-9 w-full rounded-md border border-line bg-bg px-2 text-sm" value={voice.voice} onChange={(event) => setVoice({ ...voice, voice: event.target.value })}>
                  {(engines?.voices || []).map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
                </select>
              </div>
              <div>
                <Label>语速 {voice.rate.toFixed(2)}</Label>
                <input className="mt-3 w-full" type="range" min={0.7} max={1.4} step={0.05} value={voice.rate} onChange={(event) => setVoice({ ...voice, rate: Number(event.target.value) })} />
              </div>
              <div>
                <Label>原声音量 {voice.original_volume.toFixed(2)}</Label>
                <input className="mt-3 w-full" type="range" min={0} max={1} step={0.02} value={voice.original_volume} onChange={(event) => setVoice({ ...voice, original_volume: Number(event.target.value) })} />
              </div>
              <label className="flex items-center gap-2 text-sm text-muted">
                <input type="checkbox" checked={voice.subtitles} onChange={(event) => setVoice({ ...voice, subtitles: event.target.checked })} />
                烧录字幕
              </label>
              <div>
                <Label>字号</Label>
                <Input className="mt-1" type="number" min={18} max={96} value={voice.font_size} onChange={(event) => setVoice({ ...voice, font_size: Number(event.target.value) })} />
              </div>
            </div>
            <div className="flex gap-2">
              <Button variant="secondary" disabled={busy || !task} onClick={() => void saveReview().catch((error: Error) => setBanner({ message: error.message }))}>保存审阅</Button>
              <Button disabled={busy || !task || beats.length === 0} onClick={() => void render()}>开始渲染</Button>
            </div>
          </div>
        ) : null}

        {step === 3 ? (
          <div className="space-y-3">
            {task?.output ? (
              <>
                <video key={outputUrl} className="max-h-[480px] w-full rounded-lg bg-black" src={outputUrl} controls />
                <p className="text-sm text-muted">
                  引擎：{task.output.tts_engine} · {formatTime(task.output.duration)}
                  {task.output.tts_engine === 'tone' ? ' · 这是测试音，不是人声' : ''}
                </p>
                {(task.output.warnings || []).map((warning) => <p key={warning} className="text-sm text-amber">{warning}</p>)}
                <div className="flex gap-2">
                  <a className="inline-flex h-9 items-center rounded-md bg-amber px-3 text-sm font-medium text-bg" href={outputUrl}>下载成片</a>
                  <Button variant="secondary" onClick={() => void importResult()}>入库</Button>
                </div>
              </>
            ) : (
              <div className="space-y-3">
                <p className="text-sm text-muted">还没有成片。确认审阅里的每句都能放进对应画面，然后渲染。</p>
                <Button disabled={busy || !task} onClick={() => void render()}>开始渲染</Button>
              </div>
            )}
          </div>
        ) : null}
      </section>
      <Dialog open={pendingVideoId != null} onOpenChange={(open) => { if (!open) setPendingVideoId(null) }} title="换一条素材">
        <p>当前解说任务还在。换素材会从理解重新开始，已写的稿留在原来的任务里。</p>
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="secondary" onClick={() => setPendingVideoId(null)}>留下</Button>
          <Button onClick={() => {
            setVideoId(pendingVideoId)
            setTask(null)
            setBeats([])
            localStorage.removeItem(TASK_KEY)
            setPendingVideoId(null)
            setStep(0)
          }}>换素材</Button>
        </div>
      </Dialog>
    </div>
  )

  function updateSegment(index: number, patch: Partial<NarrateTask['transcript']['segments'][number]>) {
    if (!task) return
    const segments = task.transcript.segments.map((segment, item) => (item === index ? { ...segment, ...patch } : segment))
    setTask({ ...task, transcript: { ...task.transcript, segments } })
  }

  function updateBeat(index: number, patch: Partial<Beat>) {
    setBeats(beats.map((beat, item) => (item === index ? { ...beat, ...patch } : beat)))
  }
}
