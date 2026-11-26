import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  createNarration,
  errorMessage,
  fetchHealth,
  fetchVideos,
  formatDuration,
  renderNarration,
  saveNarrationScript,
  type CapabilityStatus,
  type NarrationBeat,
  type Video,
} from '../api'
import { Button, EmptyState, PageHeader, toast } from '../ui'
import RequestState from '../ui/RequestState'

export default function NarratePage() {
  const [videos, setVideos] = useState<Video[]>([])
  const [videoId, setVideoId] = useState<number | null>(null)
  const [target, setTarget] = useState('30')
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [taskId, setTaskId] = useState('')
  const [beats, setBeats] = useState<NarrationBeat[]>([])
  const [reviewed, setReviewed] = useState(false)
  const [rendered, setRendered] = useState(false)
  const [busy, setBusy] = useState('')
  const [note, setNote] = useState('')
  const [asrNote, setAsrNote] = useState<CapabilityStatus | null>(null)

  const load = useCallback(() => {
    setLoading(true)
    void Promise.all([fetchVideos(), fetchHealth().catch(() => null)])
      .then(([list, health]) => {
        setVideos(list)
        setVideoId((current) => current ?? list[0]?.id ?? null)
        setAsrNote(health?.capabilities?.find((item) => item.id === 'asr') || null)
        setLoadError(null)
      })
      .catch((error) => setLoadError(errorMessage(error)))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => { load() }, [load])

  const seconds = Number(target)
  const valid = Number.isInteger(seconds) && seconds >= 5 && seconds <= 180

  async function draft() {
    if (!videoId || !valid) {
      toast.error(videoId ? '目标时长请填 5 到 180 的整数秒' : '先选一条素材库里的原片')
      return
    }
    setBusy('draft')
    try {
      const task = await createNarration(videoId, seconds)
      setTaskId(task.id)
      setBeats(task.result?.beats || [])
      setReviewed(false)
      setRendered(false)
      setNote(task.message || '请核对短句后再渲染。')
    } catch (error) {
      toast.error(errorMessage(error))
    } finally {
      setBusy('')
    }
  }

  async function approve() {
    if (!taskId) return
    setBusy('review')
    try {
      const task = await saveNarrationScript(taskId, beats)
      setBeats(task.result?.beats || [])
      setReviewed(true)
      setNote(task.message || '文稿已审核。')
    } catch (error) {
      toast.error(errorMessage(error))
    } finally {
      setBusy('')
    }
  }

  async function render() {
    if (!reviewed) {
      toast.error('先审核文稿，再渲染原声')
      return
    }
    setBusy('render')
    try {
      const task = await renderNarration(taskId)
      setRendered(true)
      setNote(task.message || '原声成片已生成。')
    } catch (error) {
      toast.error(errorMessage(error))
    } finally {
      setBusy('')
    }
  }

  return (
    <div>
      <PageHeader
        title="给已有原片写解说"
        desc="只使用素材库里的原声。先出短句文稿，审核后才渲染，不重新下载，也不走智能剪辑。"
      />
      <RequestState
        error={loadError}
        hasData={videos.length > 0}
        loading={loading}
        onRetry={load}
        className="mb-4"
      />
      {!loading && videos.length === 0 && !loadError && (
        <EmptyState title="素材库还是空的" desc="先把原片放进素材库，再回来写解说。" action={<Link to="/videos">去素材库</Link>} />
      )}
      {videos.length > 0 && (
        <section className="studio-board" aria-label="解说设置">
          {asrNote && <p className="studio-fine">{asrNote.summary} {asrNote.next}</p>}
          <label className="studio-limit">
            <span>原片</span>
            <select value={videoId ?? ''} onChange={(event) => setVideoId(Number(event.target.value))} aria-label="选择原片">
              {videos.map((video) => (
                <option key={video.id} value={video.id}>{video.title} · {formatDuration(video.duration)}</option>
              ))}
            </select>
          </label>
          <label className="studio-limit">
            <span>目标时长（秒）</span>
            <input
              value={target}
              inputMode="numeric"
              maxLength={3}
              aria-label="解说目标时长"
              onChange={(event) => setTarget(event.target.value.replace(/[^\d]/g, '').slice(0, 3))}
            />
          </label>
          <Button variant="primary" loading={busy === 'draft'} onClick={() => void draft()}>生成文稿</Button>
          {note && <p className="text-sm text-ink-3">{note}</p>}
          {beats.length > 0 && (
            <div className="studio-chenze">
              {beats.map((beat, index) => (
                <label key={beat.id || index} className="studio-limit">
                  <span>{index + 1}</span>
                  <input
                    value={beat.text}
                    aria-label={`第${index + 1}句`}
                    onChange={(event) => {
                      const text = event.target.value
                      setReviewed(false)
                      setBeats((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, text } : item))
                    }}
                  />
                </label>
              ))}
              <div className="flex gap-2">
                <Button variant="secondary" loading={busy === 'review'} onClick={() => void approve()}>确认文稿</Button>
                <Button variant="primary" loading={busy === 'render'} disabled={!reviewed} onClick={() => void render()}>渲染原声</Button>
                {rendered && taskId && (
                  <a className="text-sm text-accent" href={`/api/narrate/tasks/${taskId}/file`}>下载</a>
                )}
              </div>
            </div>
          )}
        </section>
      )}
    </div>
  )
}
