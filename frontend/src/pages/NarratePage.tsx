import { useCallback, useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import {
  editTaskPath,
  errorMessage,
  fetchVideos,
  startDouyinProduce,
  type Video,
} from '../api'
import { clock, composeDurationInstruction } from '../features/edit/studio'
import { toast } from '../ui'

export default function NarratePage() {
  const navigate = useNavigate()
  const [videos, setVideos] = useState<Video[]>([])
  const [loadError, setLoadError] = useState('')
  const [selectedVideoId, setSelectedVideoId] = useState(0)
  const [instruction, setInstruction] = useState('只生成1条')
  const [maxSeconds, setMaxSeconds] = useState('15')
  const [starting, setStarting] = useState(false)

  const load = useCallback(async () => {
    try {
      setVideos(await fetchVideos())
      setLoadError('')
    } catch (error) {
      setLoadError(errorMessage(error))
    }
  }, [])

  useEffect(() => { void load() }, [load])

  const selected = videos.find((video) => video.id === selectedVideoId) || null

  async function startNarration() {
    if (!selectedVideoId || starting) {
      if (!selectedVideoId) toast.error('先选一条素材库里的原片')
      return
    }
    let prompt = ''
    try {
      prompt = composeDurationInstruction(instruction, maxSeconds)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '最长时长无效')
      return
    }
    if (!/不超过\s*\d+/.test(prompt)) {
      toast.error('口播解说要先写明单条最长秒数')
      return
    }
    setStarting(true)
    try {
      const job = await startDouyinProduce(selectedVideoId, prompt, 1, 'podcast')
      toast.success('已开始按原片口播出片')
      navigate(editTaskPath(job))
    } catch (error) {
      toast.error(errorMessage(error))
    } finally {
      setStarting(false)
    }
  }

  return (
    <div className="studio">
      <header className="studio-hero">
        <p className="studio-kicker">口播解说</p>
        <h1 className="studio-title">用原片里的人声，<br />切一条有时长上限的短片。</h1>
        <p className="studio-lead">
          这里不再合成新的旁白，也不连接已经停用的解说引擎。成片保留原片口播。写了最长秒数却切不出来时，任务会失败，不会把整段原片当成成功。
        </p>
      </header>

      {loadError && <p className="studio-error">{loadError}</p>}

      <section className="studio-board" aria-label="用原片口播生成一条短片">
        <form
          className="studio-compose"
          onSubmit={(event) => {
            event.preventDefault()
            void startNarration()
          }}
        >
          <label className="studio-limit" htmlFor="narrate-video">
            <span>原片</span>
            <select
              id="narrate-video"
              value={selectedVideoId || ''}
              onChange={(event) => setSelectedVideoId(Number(event.target.value))}
              aria-label="选择原片"
            >
              <option value="">选择素材库视频</option>
              {videos.map((video) => (
                <option key={video.id} value={video.id}>
                  {video.title} · {clock(video.duration)}
                </option>
              ))}
            </select>
          </label>
          {videos.length === 0 && (
            <p className="studio-empty">
              库里还没有原片。<Link to="/videos">去素材库</Link>
            </p>
          )}
          <p className="studio-picked">{selected ? selected.title : '还没有选择原片'}</p>
          <input
            className="studio-prompt"
            value={instruction}
            maxLength={400}
            onChange={(event) => setInstruction(event.target.value)}
            placeholder="只生成1条"
            aria-label="解说要求"
          />
          <label className="studio-limit">
            <span>单条最长秒数</span>
            <input
              value={maxSeconds}
              inputMode="numeric"
              maxLength={2}
              onChange={(event) => setMaxSeconds(event.target.value.replace(/[^\d]/g, '').slice(0, 2))}
              aria-label="单条最长秒数"
              required
            />
          </label>
          <button className="studio-cta" type="submit" disabled={starting || !selectedVideoId} aria-busy={starting}>
            {starting ? '正在提交…' : '生成口播短片'}
          </button>
          <p className="studio-fine">
            默认满屏播客式，只出 1 条。需要访谈裁切时去<Link to="/edit">智能剪辑</Link>。
          </p>
        </form>
      </section>
    </div>
  )
}
