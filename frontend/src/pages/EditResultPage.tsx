import { useEffect, useRef, useState } from 'react'
import { useLocation, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { ArrowDown, ArrowLeft, ArrowUp, DownloadSimple, Scissors, Sparkle, Trash, ArrowCounterClockwise, ArrowClockwise } from '@phosphor-icons/react'
import {
  ApiError,
  analyzeEdit,
  errorMessage,
  exportEdit,
  fetchJob,
  fetchTask,
  fetchVideos,
  highlightEdit,
  planEditSuggestions,
  type EditJob,
  type Suggestion,
  type Video,
} from '../api'
import { InsertPicker, type PickedImage, type PickedVideo } from '../features/edit/InsertPicker'
import { Timeline } from '../features/edit/Timeline'
import {
  clipAt,
  clipFreeze,
  clipSpeed,
  exportRanges,
  insertAfter,
  isImageClip,
  keptDuration,
  moveClip,
  newClip,
  newImageClip,
  newInsertedVideoClip,
  pack,
  removeClip,
  splitClip,
  timelineDuration,
  timelineToSource,
  trimClip,
  updateClip,
  type Clip,
  type Decision,
  type EditDoc,
} from '../features/edit/model'
import { useHistory } from '../features/edit/useHistory'
import { Alert, Button, EmptyState, Input, PageHeader, Panel, Progress, Tag } from '../ui'

const DRAFT_KEY = 'video-hub:edit-doc:v2'
const KIND_LABEL: Record<Suggestion['kind'], string> = {
  silence: '静音',
  black: '黑场',
  pause: '句间停顿',
  filler: '口头禅',
  scene: '镜头',
}

function formatTime(seconds: number): string {
  const safe = Number.isFinite(seconds) ? Math.max(0, seconds) : 0
  const minutes = Math.floor(safe / 60)
  const rest = safe - minutes * 60
  return `${String(minutes).padStart(2, '0')}:${rest.toFixed(1).padStart(4, '0')}`
}

function sleep(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms))
}

function clipsFor(videos: Video[], ids: number[]): Clip[] {
  const byId = new Map(videos.map((video) => [video.id, video]))
  return pack(ids.flatMap((id) => {
    const video = byId.get(id)
    const clip = video ? newClip(video) : null
    return clip ? [clip] : []
  }))
}

export default function EditResultPage() {
  const { taskId } = useParams()
  const navigate = useNavigate()
  const location = useLocation()
  const [params] = useSearchParams()
  const { doc, commit, replace, undo, redo, reset, canUndo, canRedo } = useHistory()
  const [videos, setVideos] = useState<Video[]>([])
  const [libraryError, setLibraryError] = useState<string | null>(null)
  const [suggestions, setSuggestions] = useState<Suggestion[]>([])
  const [planSource, setPlanSource] = useState('尚未分析')
  const [warnings, setWarnings] = useState<string[]>([])
  const [banner, setBanner] = useState<{ message: string; action?: string } | null>(null)
  const [job, setJob] = useState<EditJob | null>(null)
  const [selectedClipId, setSelectedClipId] = useState<string | null>(null)
  const [selectedSuggestionId, setSelectedSuggestionId] = useState<string | null>(null)
  const [time, setTime] = useState(0)
  const [resultUrl, setResultUrl] = useState<string | null>(null)
  const [highlightPrompt, setHighlightPrompt] = useState('')
  const [analysisClips, setAnalysisClips] = useState<Array<Record<string, unknown>>>([])
  const [pickerOpen, setPickerOpen] = useState(false)
  const [pickerTab, setPickerTab] = useState<'image' | 'video'>('image')
  const videoRef = useRef<HTMLVideoElement>(null)
  const playing = useRef(false)
  const trimBase = useRef<EditDoc | null>(null)
  const restored = useRef(false)
  const docRef = useRef(doc)
  docRef.current = doc

  useEffect(() => {
    let dead = false
    fetchVideos()
      .then((list) => {
        if (dead) return
        setVideos(list)
        setLibraryError(null)
      })
      .catch((error: unknown) => {
        if (!dead) setLibraryError(errorMessage(error))
      })
    return () => {
      dead = true
    }
  }, [])

  useEffect(() => {
    if (restored.current || videos.length === 0) return
    restored.current = true
    const preset = (location.state as { videoIds?: number[] } | null)?.videoIds
    const queryId = Number(params.get('video') || '')
    const presetIds = preset?.length ? preset : Number.isFinite(queryId) && queryId > 0 ? [queryId] : []

    const applyDraft = () => {
      if (taskId || presetIds.length) return
      const raw = localStorage.getItem(DRAFT_KEY)
      if (!raw) return
      try {
        const saved = JSON.parse(raw) as EditDoc & { suggestions?: Suggestion[]; planSource?: string }
        const byId = new Map(videos.map((video) => [video.id, video]))
        const clips = pack((saved.clips || []).flatMap((clip) => {
          if (clip.kind === 'image') {
            return [{
              ...clip,
              kind: 'image' as const,
              fileUrl: clip.imageUrl || clip.fileUrl,
              imageUrl: clip.imageUrl || clip.fileUrl,
            }]
          }
          const video = byId.get(clip.videoId)
          if (!video && !(clip.videoName)) return []
          if (!video) {
            return [{ ...clip, kind: 'video' as const, speed: clip.speed || 1, freeze: clip.freeze || 0 }]
          }
          return [{ ...clip, title: video.title, fileUrl: video.file_url, srcDuration: Number(video.duration || clip.srcDuration) }]
        }))
        reset({ title: saved.title || '未命名成片', clips, decisions: saved.decisions || {} })
        setSuggestions(saved.suggestions || [])
        if (saved.planSource) setPlanSource(saved.planSource)
      } catch {
        localStorage.removeItem(DRAFT_KEY)
      }
    }

    if (!taskId) {
      if (presetIds.length) reset({ title: '未命名成片', clips: clipsFor(videos, presetIds), decisions: {} })
      else applyDraft()
      return
    }

    fetchTask(taskId)
      .then((loaded) => {
        if (loaded.kind === 'edit.douyin') {
          navigate(`/edit/douyin/${encodeURIComponent(loaded.id)}`, { replace: true })
          return
        }
        const state = location.state as { title?: string; highlightPrompt?: string } | null
        if (state?.highlightPrompt) setHighlightPrompt(state.highlightPrompt)
        const analyzed = (loaded.result?.clips || []) as Array<{ video_id: number; suggestions?: Suggestion[] }>
        if (analyzed.length) {
          reset({
            title: String(state?.title || loaded.input?.title || '未命名成片'),
            clips: clipsFor(videos, analyzed.map((item) => item.video_id)),
            decisions: {},
          })
          setSuggestions(analyzed.flatMap((item) => item.suggestions || []))
          setPlanSource(loaded.kind === 'edit.analyze' ? '信号' : planSource)
          if (loaded.error?.message) setBanner({ message: loaded.error.message, action: loaded.error.action })
          return
        }
        const ranges = loaded.input?.clips || []
        if (ranges.length) {
          const byId = new Map(videos.map((video) => [video.id, video]))
          const clips = pack(ranges.flatMap((range) => {
            const video = byId.get(range.video_id)
            const clip = video ? newClip(video) : null
            if (!clip) return []
            return [{ ...clip, trimStart: range.start, trimEnd: Math.min(range.end, clip.srcDuration) }]
          }))
          reset({ title: loaded.input?.title || '未命名成片', clips, decisions: {} })
          return
        }
        const ids = loaded.input?.video_ids || []
        if (ids.length) reset({ title: loaded.input?.title || '未命名成片', clips: clipsFor(videos, ids), decisions: {} })
        if (loaded.error?.message) setBanner({ message: loaded.error.message, action: loaded.error.action })
      })
      .catch((error: unknown) => setBanner({ message: errorMessage(error), action: '回到素材库重新发起分析。' }))
  }, [params, location.state, planSource, reset, taskId, videos])

  useEffect(() => {
    if (!restored.current || taskId) return
    localStorage.setItem(DRAFT_KEY, JSON.stringify({ ...doc, suggestions, planSource }))
  }, [doc, planSource, suggestions, taskId])

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const tag = (event.target as HTMLElement | null)?.tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'z') {
        event.preventDefault()
        if (event.shiftKey) redo()
        else undo()
      } else if (event.code === 'Space') {
        event.preventDefault()
        togglePlay()
      } else if ((event.key === 'Delete' || event.key === 'Backspace') && selectedClipId) {
        event.preventDefault()
        commit({ ...docRef.current, clips: removeClip(docRef.current.clips, selectedClipId) })
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [commit, redo, selectedClipId, undo])

  async function follow(initial: EditJob): Promise<EditJob> {
    let current = initial
    setJob(current)
    while (current.status === 'pending' || current.status === 'running') {
      await sleep(700)
      current = await fetchJob(current.id)
      setJob(current)
    }
    return current
  }

  function addVideo(video: Video) {
    const clip = newClip(video)
    if (!clip) {
      setBanner({ message: `「${video.title}」没有可用时长`, action: '换一条能读出时长的素材。' })
      return
    }
    commit({ ...doc, clips: pack([...doc.clips, clip]) })
    setSelectedClipId(clip.id)
    setBanner(null)
  }

  async function analyze() {
    const ids = [...new Set(doc.clips.filter((clip) => !isImageClip(clip) && clip.videoId > 0).map((clip) => clip.videoId))]
    if (!ids.length) return
    setBanner(null)
    try {
      const done = await follow(await analyzeEdit(ids))
      const clips = (done.result?.clips || []) as Array<Record<string, unknown> & { suggestions?: Suggestion[]; transcript?: { warning?: string }; video_id?: number }>
      setAnalysisClips(clips)
      setSuggestions(clips.flatMap((clip) => clip.suggestions || []))
      setPlanSource('信号')
      setWarnings(clips.map((clip) => clip.transcript?.warning).filter((item): item is string => !!item))
      if (done.error) setBanner({ message: done.error.message || '分析失败', action: done.error.action })
      else if (done.status === 'failed') setBanner({ message: '分析失败', action: '打开任务中心查看日志。' })
    } catch (error) {
      setBanner(error instanceof ApiError ? { message: error.message, action: error.action } : { message: errorMessage(error) })
    }
  }

  function highlightSources(rows: Array<Record<string, unknown>>) {
    return rows.map((row) => {
      const transcript = (row.transcript || {}) as { segments?: unknown[] }
      const signals = (row.signals || {}) as { scenes?: unknown[]; silence?: unknown[]; black?: unknown[] }
      return {
        video_id: Number(row.video_id || 0),
        duration: Number(row.duration || 0),
        title: String(row.title || ''),
        segments: transcript.segments || [],
        scenes: signals.scenes || [],
        silence: signals.silence || [],
        black: signals.black || [],
        frames: (row.frames as unknown[]) || [],
        suggestions: (row.suggestions as unknown[]) || [],
      }
    })
  }

  async function highlightKeep() {
    const ids = [...new Set(doc.clips.filter((clip) => !isImageClip(clip)).map((clip) => clip.videoId))]
    if (!ids.length) {
      setBanner({ message: '时间线上还没有原片', action: '先从左侧把素材加进来。' })
      return
    }
    setBanner(null)
    try {
      const known = new Set(analysisClips.map((item) => Number(item.video_id || 0)))
      let rows = analysisClips
      const notes: string[] = []
      if (!ids.every((id) => known.has(id))) {
        const done = await follow(await analyzeEdit(ids))
        rows = ((done.result?.clips || []) as Array<Record<string, unknown>>)
        setAnalysisClips(rows)
        setSuggestions(rows.flatMap((clip) => ((clip.suggestions as Suggestion[]) || [])))
        const warnings = rows.map((clip) => ((clip.transcript as { warning?: string } | undefined)?.warning)).filter((item): item is string => !!item)
        setWarnings(warnings)
        if (done.status === 'failed' || !rows.length) {
          setBanner({
            message: done.error?.message || '分析失败，还不能出精彩方案',
            action: done.error?.action || '打开任务中心查看日志。',
          })
          return
        }
        if (done.error?.message) notes.push(done.error.message)
      }
      const plan = await highlightEdit(highlightSources(rows), highlightPrompt, true)
      const byId = new Map(videos.map((video) => [video.id, video]))
      const nextClips = pack(
        (plan.clips || []).flatMap((item) => {
          const video = byId.get(item.video_id)
          const base = video ? newClip(video) : null
          if (!base) return []
          const duration = Number(video?.duration || base.srcDuration)
          return [{
            ...base,
            trimStart: Math.max(0, item.start),
            trimEnd: Math.min(duration, Math.max(item.start + 0.3, item.end)),
            speed: item.speed || 1,
            freeze: item.freeze || 0,
          }]
        }),
      )
      if (!nextClips.length) {
        setBanner({ message: '没有排出精彩片段', action: '先确认分析里有口播或镜头。' })
        return
      }
      commit({ ...doc, clips: nextClips, decisions: {} })
      setPlanSource(plan.source === 'llm' ? '精彩·模型' : '精彩·信号')
      setWarnings([...(plan.warnings || []), ...notes])
      setSelectedClipId(nextClips[0]?.id || null)
    } catch (error) {
      setBanner(error instanceof ApiError ? { message: error.message, action: error.action } : { message: errorMessage(error) })
    }
  }

  async function rankWithModel() {
    const ids = [...new Set(suggestions.map((item) => item.video_id))]
    if (!ids.length) {
      setBanner({ message: '还没有切点建议', action: '先点「分析切点」。' })
      return
    }
    setBanner(null)
    const next: Suggestion[] = []
    const notes: string[] = []
    try {
      for (const videoId of ids) {
        const owned = suggestions.filter((item) => item.video_id === videoId)
        const plan = await planEditSuggestions(videoId, owned, true, '')
        next.push(...plan.suggestions)
        notes.push(...(plan.warnings || []))
      }
      setSuggestions(next)
      setPlanSource('模型')
      setWarnings(notes)
    } catch (error) {
      setPlanSource('信号')
      setBanner(error instanceof ApiError ? { message: error.message, action: error.action } : { message: errorMessage(error) })
    }
  }

  function acceptRecommended() {
    const decisions: Record<string, Decision> = { ...doc.decisions }
    for (const item of suggestions) {
      if (item.action === 'drop' && item.recommended && decisions[item.id] !== 'rejected') decisions[item.id] = 'accepted'
    }
    commit({ ...doc, decisions })
  }

  async function exportFilm() {
    const clips = exportRanges(doc.clips, suggestions, doc.decisions)
    if (!clips.length) {
      setBanner({ message: '没有可导出的保留段', action: '时间线至少留 0.3 秒，或取消一些删除。' })
      return
    }
    setBanner(null)
    try {
      const done = await follow(await exportEdit(doc.title || '剪辑成片', clips))
      const video = done.result?.video
      setResultUrl(video?.file_url || done.result?.download_url || null)
      if (done.error) setBanner({ message: done.error.message || '导出失败', action: done.error.action })
      if (video) {
        const list = await fetchVideos()
        setVideos(list)
      }
    } catch (error) {
      setBanner(error instanceof ApiError ? { message: error.message, action: error.action } : { message: errorMessage(error) })
    }
  }

  function seek(next: number) {
    const clip = clipAt(doc.clips, next)
    setTime(next)
    const node = videoRef.current
    if (!node || !clip) return
    if (isImageClip(clip)) {
      node.pause()
      playing.current = false
      return
    }
    if (node.getAttribute('data-src') !== clip.fileUrl) {
      node.src = clip.fileUrl
      node.setAttribute('data-src', clip.fileUrl)
    }
    node.currentTime = timelineToSource(clip, next)
  }

  function togglePlay() {
    const node = videoRef.current
    const clip = clipAt(docRef.current.clips, time)
    if (!node || !clip || isImageClip(clip)) return
    if (playing.current) {
      node.pause()
      playing.current = false
      return
    }
    seek(time)
    playing.current = true
    void node.play()
  }

  function onTimeUpdate() {
    const node = videoRef.current
    const current = docRef.current
    if (!node || !playing.current) return
    const clip = current.clips.find((item) => item.fileUrl === node.getAttribute('data-src'))
    if (!clip) return
    let source = node.currentTime
    const drop = suggestions.find((item) =>
      item.video_id === clip.videoId &&
      item.action === 'drop' &&
      current.decisions[item.id] === 'accepted' &&
      source >= item.start &&
      source < item.end - 0.05)
    if (drop) source = Math.min(drop.end, clip.trimEnd)
    if (source >= clip.trimEnd - 0.08) {
      let next = current.clips.find((item) => item.start >= clip.end - 0.01)
      while (next && isImageClip(next) && next.overlay) {
        next = current.clips.find((item) => item.start >= next!.end - 0.01)
      }
      if (!next) {
        node.pause()
        playing.current = false
        setTime(clip.end)
        return
      }
      if (isImageClip(next)) {
        node.pause()
        playing.current = false
        setTime(next.start)
        return
      }
      node.src = next.fileUrl
      node.setAttribute('data-src', next.fileUrl)
      node.currentTime = next.trimStart
      void node.play()
      setTime(next.start)
      return
    }
    if (Math.abs(node.currentTime - source) > 0.05) node.currentTime = source
    setTime(clip.start + (source - clip.trimStart) / clipSpeed(clip))
  }

  function onTrim(clipId: string, trimStart: number, trimEnd: number, phase: 'move' | 'up') {
    if (!trimBase.current) trimBase.current = docRef.current
    const base = trimBase.current
    const next = { ...base, clips: trimClip(base.clips, clipId, trimStart, trimEnd) }
    if (phase === 'move') {
      replace(next)
      return
    }
    commit(next)
    trimBase.current = null
  }

  function openPicker(tab: 'image' | 'video' = 'image') {
    setPickerTab(tab)
    setPickerOpen(true)
  }

  function onPickImage(picked: PickedImage) {
    const clip = newImageClip(picked)
    commit({ ...doc, clips: insertAfter(doc.clips, selectedClipId, clip) })
    setSelectedClipId(clip.id)
  }

  function onPickVideo(picked: PickedVideo) {
    const clip = newInsertedVideoClip(picked)
    if (!clip) {
      setBanner({ message: '这条视频没有可用时长', action: '换一条能读出时长的素材，或等 AI 视频生成完再插入。' })
      return
    }
    commit({ ...doc, clips: insertAfter(doc.clips, selectedClipId, clip) })
    setSelectedClipId(clip.id)
  }

  const selectedSuggestion = suggestions.find((item) => item.id === selectedSuggestionId) || null
  const selectedClip = doc.clips.find((clip) => clip.id === selectedClipId) || null
  const overlayImage = doc.clips.find((clip) => isImageClip(clip) && clip.overlay && clip.start <= time + 0.05)
  const fullImage = selectedClip && isImageClip(selectedClip) && !selectedClip.overlay
    ? selectedClip
    : doc.clips.find((clip) => isImageClip(clip) && !clip.overlay && time >= clip.start && time < clip.end - 1e-4)
  const busy = job?.status === 'pending' || job?.status === 'running'
  const total = timelineDuration(doc.clips)
  const kept = keptDuration(doc.clips, suggestions, doc.decisions)

  return (
    <div>
      <div className="mb-3">
        <Button variant="ghost" icon={<ArrowLeft size={16} />} onClick={() => navigate('/edit')}>返回智能剪辑</Button>
      </div>
      <PageHeader
        title="手动时间线"
        desc="这是以前的单条时间线，用来打开旧的切点任务。抖音短视频请回到智能剪辑，一键出片。"
        extra={<span className="tnum text-xs text-ink-3">原片 {formatTime(total)} · 成片 {formatTime(kept)} · {planSource}</span>}
      />
      {banner ? (
        <Alert type="error" className="mb-4">
          <span className="font-medium">{banner.message}</span>
          {banner.action ? <span className="mt-1 block text-ink-3">{banner.action}</span> : null}
        </Alert>
      ) : null}
      {warnings[0] ? <Alert type="warning" className="mb-4">{warnings[0]}</Alert> : null}
      {job ? (
        <div className="mb-4">
          <div className="mb-1 flex items-center justify-between text-xs text-ink-3">
            <span>{job.label}</span>
            <span className="tnum">{Math.round(job.progress)}%</span>
          </div>
          <Progress percent={job.progress / 100} status={job.status === 'failed' ? 'error' : job.status === 'success' || job.status === 'partial' ? 'success' : 'normal'} />
          {job.logs.length > 0 ? <p className="mt-1 truncate text-[11px] text-ink-4">{job.logs[job.logs.length - 1]}</p> : null}
        </div>
      ) : null}
      <div className="grid gap-4 xl:grid-cols-[240px_minmax(0,1fr)_300px]">
        <Panel title="素材库" pad="sm" className="min-h-64">
          {libraryError ? <Alert type="error" className="mb-3">{libraryError}</Alert> : null}
          {videos.length === 0 ? (
            <EmptyState title="库是空的" desc="先去素材库上传或下载一条原片，再加到时间线。" />
          ) : (
            <div className="max-h-[540px] space-y-1 overflow-auto">
              {videos.map((video) => (
                <button
                  key={video.id}
                  type="button"
                  className="flex w-full items-center gap-2 rounded-lg px-2 py-2 text-left hover:bg-fill"
                  onClick={() => addVideo(video)}
                >
                  <span className="min-w-0 flex-1 truncate text-[13px] text-ink-1">{video.title}</span>
                  <span className="tnum text-[11px] text-ink-4">{formatTime(Number(video.duration || 0))}</span>
                </button>
              ))}
            </div>
          )}
        </Panel>

        <div className="flex min-w-0 flex-col gap-3">
          <div className="flex flex-wrap items-center gap-2">
            <Input className="max-w-56" value={doc.title} onChange={(value) => replace({ ...doc, title: value.slice(0, 80) })} aria-label="成片标题" />
            <Button size="sm" variant="ghost" aria-label="撤销" disabled={!canUndo} icon={<ArrowCounterClockwise size={15} />} onClick={undo} />
            <Button size="sm" variant="ghost" aria-label="重做" disabled={!canRedo} icon={<ArrowClockwise size={15} />} onClick={redo} />
            <Button variant="secondary" disabled={busy || doc.clips.length === 0} icon={<Scissors size={15} />} onClick={() => void analyze()}>分析切点</Button>
            <Button variant="secondary" disabled={busy || doc.clips.length === 0} icon={<Sparkle size={15} />} onClick={() => void highlightKeep()}>AI 精彩剪辑</Button>
            <Button variant="secondary" disabled={busy || suggestions.length === 0} icon={<Sparkle size={15} />} onClick={() => void rankWithModel()}>模型精排</Button>
            <Button variant="primary" disabled={busy || doc.clips.length === 0} icon={<DownloadSimple size={15} />} onClick={() => void exportFilm()}>导出</Button>
            <Input className="max-w-64" value={highlightPrompt} onChange={setHighlightPrompt} placeholder="精彩剪辑提示，可空" aria-label="精彩剪辑提示" />
          </div>
          <div className="relative min-h-48 overflow-hidden rounded-xl bg-black">
            <video
              ref={videoRef}
              className="h-full max-h-[360px] w-full bg-black object-contain"
              onTimeUpdate={onTimeUpdate}
              onPause={() => { playing.current = false }}
              onPlay={() => { playing.current = true }}
            />
            {fullImage ? <img src={fullImage.imageUrl || fullImage.fileUrl} alt="" className="absolute inset-0 h-full w-full object-contain" /> : null}
            {overlayImage ? <img src={overlayImage.imageUrl || overlayImage.fileUrl} alt="" className="absolute bottom-12 right-3 h-24 w-24 rounded-md border border-white/30 object-cover shadow-lg" /> : null}
            {doc.clips.length === 0 ? <p className="absolute inset-0 grid place-items-center text-sm text-ink-4">预览会跟时间线走</p> : null}
            <button type="button" className="absolute bottom-3 left-3 rounded-lg bg-black/60 px-3 py-1 text-xs text-white" onClick={togglePlay}>空格播放</button>
          </div>
          <Timeline
            clips={doc.clips}
            suggestions={suggestions}
            decisions={doc.decisions}
            time={Math.min(time, total)}
            selectedClipId={selectedClipId}
            selectedSuggestionId={selectedSuggestionId}
            onSeek={seek}
            onSelectClip={setSelectedClipId}
            onSelectSuggestion={setSelectedSuggestionId}
            onTrim={onTrim}
          />
          {resultUrl ? <a className="text-sm text-accent" href={resultUrl}>成片已可播放 / 下载</a> : null}
        </div>

        <Panel
          title="切点"
          pad="sm"
          extra={<Button size="sm" variant="ghost" disabled={!suggestions.some((item) => item.recommended)} onClick={acceptRecommended}>接受建议删除</Button>}
        >
          {selectedSuggestion ? (
            <div className="space-y-3 rounded-xl border border-line-soft bg-fill p-3">
              <div className="flex items-center gap-2">
                <Tag color={selectedSuggestion.action === 'drop' ? 'red' : 'blue'}>{KIND_LABEL[selectedSuggestion.kind]}</Tag>
                <span className="tnum text-xs text-ink-3">{formatTime(selectedSuggestion.start)}–{formatTime(selectedSuggestion.end)}</span>
                <span className="tnum ml-auto text-xs text-ink-2">{Math.round(selectedSuggestion.score * 100)}</span>
              </div>
              <p className="text-[13px] text-ink-1">{selectedSuggestion.reason}</p>
              <p className="text-xs text-ink-3">{selectedSuggestion.evidence}</p>
              {selectedSuggestion.text ? <p className="text-xs text-ink-4">附近口播：{selectedSuggestion.text}</p> : null}
              {selectedSuggestion.llm_action ? <p className="text-xs text-accent">模型：{selectedSuggestion.llm_action === 'drop' ? '删' : '留'}</p> : null}
              <div className="flex flex-wrap gap-2">
                {selectedSuggestion.action === 'drop' ? (
                  <Button size="sm" variant="primary" onClick={() => commit({ ...doc, decisions: { ...doc.decisions, [selectedSuggestion.id]: 'accepted' } })}>接受删除</Button>
                ) : (
                  <Button
                    size="sm"
                    variant="primary"
                    onClick={() => {
                      const clip = doc.clips.find((item) => item.videoId === selectedSuggestion.video_id && selectedSuggestion.start >= item.trimStart && selectedSuggestion.start <= item.trimEnd)
                      if (!clip) return
                      commit({
                        ...doc,
                        clips: splitClip(doc.clips, clip.id, selectedSuggestion.start),
                        decisions: { ...doc.decisions, [selectedSuggestion.id]: 'accepted' },
                      })
                    }}
                  >
                    在这里切开
                  </Button>
                )}
                <Button size="sm" variant="secondary" onClick={() => commit({ ...doc, decisions: { ...doc.decisions, [selectedSuggestion.id]: 'rejected' } })}>保留</Button>
              </div>
            </div>
          ) : (
            <p className="text-[13px] leading-6 text-ink-3">色带是建议删掉的静音、黑场、停顿和口头禅。蓝线是镜头切换，只标记，不自动切开。先看测量值，再决定要不要让模型改裁决。</p>
          )}
          {selectedClip ? (
            <div className="mt-4 space-y-2 text-[13px]">
              <p className="text-ink-3">选中片段</p>
              <p className="text-ink-1">{selectedClip.title}</p>
              <p className="tnum text-xs text-ink-4">
                {isImageClip(selectedClip)
                  ? (selectedClip.overlay ? '叠图' : `静帧 ${(selectedClip.hold ?? 2.5).toFixed(1)}s`)
                  : `源 ${formatTime(selectedClip.trimStart)}–${formatTime(selectedClip.trimEnd)}`}
              </p>
              {!isImageClip(selectedClip) ? (
                <div className="space-y-2 rounded-lg border border-line-soft p-2">
                  <label className="flex items-center gap-2 text-xs text-ink-2">
                    播放速度 {clipSpeed(selectedClip).toFixed(2)}
                    <input
                      className="flex-1"
                      type="range"
                      min={0.5}
                      max={2}
                      step={0.05}
                      value={clipSpeed(selectedClip)}
                      onChange={(event) => commit({ ...doc, clips: updateClip(doc.clips, selectedClip.id, { speed: Number(event.target.value) }) })}
                      aria-label="播放速度"
                    />
                  </label>
                  <label className="flex items-center gap-2 text-xs text-ink-2">
                    静止秒数
                    <input
                      className="h-7 w-16 rounded-lg border border-line bg-fill px-2 text-xs"
                      type="number"
                      min={0}
                      max={30}
                      step={0.5}
                      value={clipFreeze(selectedClip)}
                      onChange={(event) => commit({ ...doc, clips: updateClip(doc.clips, selectedClip.id, { freeze: Number(event.target.value) }) })}
                      aria-label="静止秒数"
                    />
                  </label>
                </div>
              ) : (
                <label className="flex items-center gap-2 text-xs text-ink-2">
                  静帧秒数
                  <input
                    className="h-7 w-16 rounded-lg border border-line bg-fill px-2 text-xs"
                    type="number"
                    min={0.3}
                    max={30}
                    step={0.5}
                    value={selectedClip.hold ?? 2.5}
                    onChange={(event) => commit({ ...doc, clips: updateClip(doc.clips, selectedClip.id, { hold: Number(event.target.value) }) })}
                    aria-label="静帧秒数"
                  />
                </label>
              )}
              <div className="flex flex-wrap gap-2">
                <Button size="sm" variant="secondary" onClick={() => openPicker('image')}>加图片</Button>
                <Button size="sm" variant="secondary" onClick={() => openPicker('video')}>加视频</Button>
                <Button size="sm" variant="secondary" icon={<ArrowUp size={14} />} onClick={() => commit({ ...doc, clips: moveClip(doc.clips, selectedClip.id, -1) })}>前移</Button>
                <Button size="sm" variant="secondary" icon={<ArrowDown size={14} />} onClick={() => commit({ ...doc, clips: moveClip(doc.clips, selectedClip.id, 1) })}>后移</Button>
                <Button size="sm" variant="danger" icon={<Trash size={14} />} onClick={() => commit({ ...doc, clips: removeClip(doc.clips, selectedClip.id) })}>移除</Button>
              </div>
            </div>
          ) : null}
          <ul className="mt-4 max-h-72 space-y-1 overflow-auto">
            {suggestions.filter((item) => item.action === 'drop').map((item) => (
              <li key={item.id}>
                <button type="button" className="flex w-full items-center gap-2 rounded-lg px-1 py-1 text-left text-xs hover:bg-fill" onClick={() => setSelectedSuggestionId(item.id)}>
                  <span className="w-14 text-ink-3">{KIND_LABEL[item.kind]}</span>
                  <span className="tnum text-ink-4">{formatTime(item.start)}</span>
                  <span className="truncate text-ink-2">{item.reason}</span>
                  <span className="ml-auto text-ink-4">{doc.decisions[item.id] === 'accepted' ? '删' : doc.decisions[item.id] === 'rejected' ? '留' : item.recommended ? '建议' : ''}</span>
                </button>
              </li>
            ))}
          </ul>
        </Panel>
      </div>
      <InsertPicker
        open={pickerOpen}
        onClose={() => setPickerOpen(false)}
        videos={videos}
        initialTab={pickerTab}
        onPickImage={onPickImage}
        onPickVideo={onPickVideo}
      />
    </div>
  )
}
