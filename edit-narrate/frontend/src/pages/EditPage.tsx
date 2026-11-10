import { Download, Redo2, Scissors, Sparkles, Undo2 } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { FailBanner } from '../components/FailBanner.tsx'
import { Badge } from '../components/ui/badge.tsx'
import { Button } from '../components/ui/button.tsx'
import { Input } from '../components/ui/input.tsx'
import { Progress } from '../components/ui/progress.tsx'
import { Timeline } from '../features/edit/Timeline.tsx'
import {
  clipAt,
  exportRanges,
  keptDuration,
  moveClip,
  newClip,
  removeClip,
  splitClip,
  timelineDuration,
  trimClip,
  type EditDoc,
} from '../features/edit/model.ts'
import { useHistory } from '../features/edit/useHistory.ts'
import { ApiError, api, fileUrl } from '../lib/api.ts'
import type { Job, Suggestion, Video } from '../lib/types.ts'
import { formatTime, sleep } from '../lib/utils.ts'

const DRAFT_KEY = 'video-hub:edit-doc:v1'
const KIND_LABEL: Record<Suggestion['kind'], string> = {
  silence: '静音',
  black: '黑场',
  pause: '句间停顿',
  filler: '口头禅',
  scene: '镜头',
}

export default function EditPage() {
  const { doc, commit, replace, undo, redo, reset, canUndo, canRedo } = useHistory()
  const [videos, setVideos] = useState<Video[]>([])
  const [libraryError, setLibraryError] = useState<string | null>(null)
  const [suggestions, setSuggestions] = useState<Suggestion[]>([])
  const [planSource, setPlanSource] = useState('尚未分析')
  const [warnings, setWarnings] = useState<string[]>([])
  const [banner, setBanner] = useState<{ message: string; action?: string } | null>(null)
  const [job, setJob] = useState<Job | null>(null)
  const [selectedClipId, setSelectedClipId] = useState<string | null>(null)
  const [selectedSuggestionId, setSelectedSuggestionId] = useState<string | null>(null)
  const [time, setTime] = useState(0)
  const [resultUrl, setResultUrl] = useState<string | null>(null)
  const videoRef = useRef<HTMLVideoElement>(null)
  const playing = useRef(false)
  const trimBase = useRef<EditDoc | null>(null)
  const restored = useRef(false)
  const docRef = useRef(doc)
  docRef.current = doc

  async function loadVideos() {
    try {
      setVideos(await api.videos())
      setLibraryError(null)
    } catch (error) {
      setLibraryError(error instanceof Error ? error.message : '素材库读取失败')
    }
  }

  useEffect(() => {
    void loadVideos()
  }, [])

  useEffect(() => {
    if (restored.current || videos.length === 0) return
    restored.current = true
    const raw = localStorage.getItem(DRAFT_KEY)
    if (!raw) return
    try {
      const saved = JSON.parse(raw) as EditDoc
      const byId = new Map(videos.map((video) => [video.id, video]))
      const clips = (saved.clips || []).flatMap((clip) => {
        const video = byId.get(clip.videoId)
        if (!video) return []
        return [{ ...clip, title: video.title, fileUrl: fileUrl(video), srcDuration: Number(video.duration || clip.srcDuration) }]
      })
      reset({ title: saved.title || '未命名成片', clips, decisions: saved.decisions || {} })
    } catch {
      localStorage.removeItem(DRAFT_KEY)
    }
  }, [reset, videos])

  useEffect(() => {
    localStorage.setItem(DRAFT_KEY, JSON.stringify(doc))
  }, [doc])

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const tag = (event.target as HTMLElement | null)?.tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA') return
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

  async function follow(initial: Job): Promise<Job> {
    let current = initial
    setJob(current)
    while (current.status === 'pending' || current.status === 'running') {
      await sleep(700)
      current = await api.job(current.id)
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
    const clips = [...doc.clips, clip]
    let cursor = 0
    const packed = clips.map((item) => {
      const start = cursor
      const end = start + (item.trimEnd - item.trimStart)
      cursor = end
      return { ...item, start, end }
    })
    commit({ ...doc, clips: packed })
    setSelectedClipId(clip.id)
    setBanner(null)
  }

  async function analyze() {
    const ids = [...new Set(doc.clips.map((clip) => clip.videoId))]
    if (!ids.length) return
    setBanner(null)
    try {
      const done = await follow(await api.analyze(ids))
      const clips = ((done.result?.clips as Array<{ suggestions: Suggestion[]; transcript?: { warning?: string } }>) || [])
      setSuggestions(clips.flatMap((clip) => clip.suggestions || []))
      setPlanSource('信号')
      const notes = clips.map((clip) => clip.transcript?.warning).filter((item): item is string => !!item)
      setWarnings(notes)
      if (done.error) setBanner({ message: done.error.message, action: done.error.action })
      else if (done.status === 'failed') setBanner({ message: '分析失败', action: '看下方任务日志。' })
    } catch (error) {
      setBanner(error instanceof ApiError ? { message: error.message, action: error.action } : { message: '分析失败' })
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
        const plan = await api.plan(videoId, owned, true, '')
        next.push(...plan.suggestions)
        notes.push(...(plan.warnings || []))
      }
      setSuggestions(next)
      setPlanSource('模型')
      setWarnings(notes)
    } catch (error) {
      setPlanSource('信号')
      setBanner(error instanceof ApiError ? { message: error.message, action: error.action } : { message: '精排失败' })
    }
  }

  function acceptRecommended() {
    const decisions = { ...doc.decisions }
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
      const done = await follow(await api.exportEdit(doc.title || '剪辑成片', clips))
      const video = done.result?.video as Video | undefined
      const download = String(done.result?.download_url || '')
      setResultUrl(video?.file_url || download || null)
      if (done.error) setBanner({ message: done.error.message, action: done.error.action })
      if (video) void loadVideos()
    } catch (error) {
      setBanner(error instanceof ApiError ? { message: error.message, action: error.action } : { message: '导出失败' })
    }
  }

  function seek(next: number) {
    const clip = clipAt(doc.clips, next)
    setTime(next)
    const node = videoRef.current
    if (!node || !clip) return
    if (node.getAttribute('data-src') !== clip.fileUrl) {
      node.src = clip.fileUrl
      node.setAttribute('data-src', clip.fileUrl)
    }
    node.currentTime = clip.trimStart + (next - clip.start)
  }

  function togglePlay() {
    const node = videoRef.current
    const clip = clipAt(docRef.current.clips, time)
    if (!node || !clip) return
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
    const drop = suggestions.find(
      (item) =>
        item.video_id === clip.videoId &&
        item.action === 'drop' &&
        current.decisions[item.id] === 'accepted' &&
        source >= item.start &&
        source < item.end - 0.05,
    )
    if (drop) source = Math.min(drop.end, clip.trimEnd)
    if (source >= clip.trimEnd - 0.08) {
      const next = current.clips.find((item) => item.start >= clip.end - 0.01)
      if (!next) {
        node.pause()
        playing.current = false
        setTime(clip.end)
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
    setTime(clip.start + (source - clip.trimStart))
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

  const selectedSuggestion = suggestions.find((item) => item.id === selectedSuggestionId) || null
  const selectedClip = doc.clips.find((clip) => clip.id === selectedClipId) || null
  const busy = job?.status === 'pending' || job?.status === 'running'
  const total = timelineDuration(doc.clips)
  const kept = keptDuration(doc.clips, suggestions, doc.decisions)

  return (
    <div className="grid h-full min-h-0 lg:grid-cols-[280px_minmax(0,1fr)_320px]">
      <aside className="flex min-h-0 flex-col border-b border-line lg:border-b-0 lg:border-r">
        <div className="flex items-center justify-between px-3 py-3">
          <p className="text-sm">素材库</p>
          <Button size="sm" variant="secondary" onClick={() => void api.seedDemo().then(() => loadVideos()).catch((error: Error) => setLibraryError(error.message))}>
            演示素材
          </Button>
        </div>
        {libraryError ? <p className="px-3 pb-2 text-xs text-drop">{libraryError}</p> : null}
        <div className="min-h-0 flex-1 space-y-1 overflow-auto px-2 pb-3">
          {videos.length === 0 ? <p className="px-2 text-sm text-muted">库是空的。生成两条演示片，或等 Video Hub 素材库接上。</p> : null}
          {videos.map((video) => (
            <button
              key={video.id}
              type="button"
              className="flex w-full items-center gap-2 rounded-md px-2 py-2 text-left hover:bg-panel-2"
              onClick={() => addVideo(video)}
            >
              <span className="min-w-0 flex-1 truncate text-sm">{video.title}</span>
              <span className="tnum text-[11px] text-faint">{formatTime(Number(video.duration || 0))}</span>
            </button>
          ))}
        </div>
      </aside>

      <section className="flex min-h-0 flex-col gap-3 p-3">
        <div className="flex flex-wrap items-center gap-2">
          <Input className="max-w-56" value={doc.title} onChange={(event) => replace({ ...doc, title: event.target.value.slice(0, 80) })} />
          <Button size="icon" variant="ghost" aria-label="撤销" disabled={!canUndo} onClick={undo}><Undo2 size={16} /></Button>
          <Button size="icon" variant="ghost" aria-label="重做" disabled={!canRedo} onClick={redo}><Redo2 size={16} /></Button>
          <Button variant="secondary" disabled={busy || doc.clips.length === 0} onClick={() => void analyze()}>
            <Scissors size={15} /> 分析切点
          </Button>
          <Button variant="secondary" disabled={busy || suggestions.length === 0} onClick={() => void rankWithModel()}>
            <Sparkles size={15} /> 模型精排
          </Button>
          <Button disabled={busy || doc.clips.length === 0} onClick={() => void exportFilm()}>
            <Download size={15} /> 导出
          </Button>
          <span className="tnum ml-auto text-xs text-muted">
            原片 {formatTime(total)} · 成片 {formatTime(kept)} · {planSource}
          </span>
        </div>
        {banner ? <FailBanner message={banner.message} action={banner.action} /> : null}
        {warnings[0] ? <p className="text-xs text-amber">{warnings[0]}</p> : null}
        {job ? (
          <div className="space-y-1">
            <div className="flex items-center justify-between text-xs text-muted">
              <span>{job.label}</span>
              <span className="tnum">{Math.round(job.progress)}%</span>
            </div>
            <Progress value={job.progress} />
            {job.logs.at(-1) ? <p className="truncate text-[11px] text-faint">{job.logs.at(-1)}</p> : null}
          </div>
        ) : null}
        <div className="relative min-h-48 flex-1 overflow-hidden rounded-lg bg-black">
          <video
            ref={videoRef}
            className="h-full w-full bg-black object-contain"
            onTimeUpdate={onTimeUpdate}
            onPause={() => { playing.current = false }}
            onPlay={() => { playing.current = true }}
          />
          {doc.clips.length === 0 ? <p className="absolute inset-0 grid place-items-center text-sm text-muted">预览会跟时间线走</p> : null}
          <button type="button" className="absolute bottom-3 left-3 rounded bg-black/60 px-3 py-1 text-xs" onClick={togglePlay}>
            空格播放
          </button>
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
        {resultUrl ? (
          <a className="text-sm text-amber" href={resultUrl}>成片已可播放 / 下载</a>
        ) : null}
      </section>

      <aside className="min-h-0 overflow-auto border-t border-line p-3 lg:border-l lg:border-t-0">
        <div className="mb-3 flex items-center justify-between">
          <p className="text-sm">切点</p>
          <Button size="sm" variant="ghost" disabled={!suggestions.some((item) => item.recommended)} onClick={acceptRecommended}>
            接受建议删除
          </Button>
        </div>
        {selectedSuggestion ? (
          <div className="space-y-3 rounded-lg border border-line bg-panel p-3">
            <div className="flex items-center gap-2">
              <Badge>{KIND_LABEL[selectedSuggestion.kind]}</Badge>
              <span className="tnum text-xs text-muted">
                {formatTime(selectedSuggestion.start)}–{formatTime(selectedSuggestion.end)}
              </span>
              <span className="tnum ml-auto text-xs">{Math.round(selectedSuggestion.score * 100)}</span>
            </div>
            <p className="text-sm">{selectedSuggestion.reason}</p>
            <p className="text-xs text-muted">{selectedSuggestion.evidence}</p>
            {selectedSuggestion.text ? <p className="text-xs text-faint">附近口播：{selectedSuggestion.text}</p> : null}
            {selectedSuggestion.llm_action ? <p className="text-xs text-scene">模型：{selectedSuggestion.llm_action === 'drop' ? '删' : '留'}</p> : null}
            <div className="flex gap-2">
              {selectedSuggestion.action === 'drop' ? (
                <Button
                  size="sm"
                  onClick={() => commit({ ...doc, decisions: { ...doc.decisions, [selectedSuggestion.id]: 'accepted' } })}
                >
                  接受删除
                </Button>
              ) : (
                <Button
                  size="sm"
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
              <Button
                size="sm"
                variant="secondary"
                onClick={() => commit({ ...doc, decisions: { ...doc.decisions, [selectedSuggestion.id]: 'rejected' } })}
              >
                保留
              </Button>
            </div>
          </div>
        ) : (
          <p className="text-sm leading-6 text-muted">
            色带是建议删掉的静音、黑场、停顿和口头禅。蓝线是镜头切换，只标记，不自动切开。先看测量值，再决定要不要让模型改裁决。
          </p>
        )}
        {selectedClip ? (
          <div className="mt-4 space-y-2 text-sm">
            <p className="text-muted">选中片段</p>
            <p>{selectedClip.title}</p>
            <p className="tnum text-xs text-faint">
              源 {formatTime(selectedClip.trimStart)}–{formatTime(selectedClip.trimEnd)}
            </p>
            <div className="flex gap-2">
              <Button size="sm" variant="secondary" onClick={() => commit({ ...doc, clips: moveClip(doc.clips, selectedClip.id, -1) })}>前移</Button>
              <Button size="sm" variant="secondary" onClick={() => commit({ ...doc, clips: moveClip(doc.clips, selectedClip.id, 1) })}>后移</Button>
              <Button size="sm" variant="destructive" onClick={() => commit({ ...doc, clips: removeClip(doc.clips, selectedClip.id) })}>移除</Button>
            </div>
          </div>
        ) : null}
        <ul className="mt-4 space-y-1">
          {suggestions.filter((item) => item.action === 'drop').map((item) => (
            <li key={item.id}>
              <button
                type="button"
                className="flex w-full items-center gap-2 rounded px-1 py-1 text-left text-xs hover:bg-panel-2"
                onClick={() => setSelectedSuggestionId(item.id)}
              >
                <span className="w-12 text-muted">{KIND_LABEL[item.kind]}</span>
                <span className="tnum text-faint">{formatTime(item.start)}</span>
                <span className="truncate text-ink/80">{item.reason}</span>
                <span className="ml-auto text-faint">{doc.decisions[item.id] === 'accepted' ? '删' : doc.decisions[item.id] === 'rejected' ? '留' : item.recommended ? '建议' : ''}</span>
              </button>
            </li>
          ))}
        </ul>
      </aside>
    </div>
  )
}
