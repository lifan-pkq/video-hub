import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import {
  cancelJob,
  editTaskPath,
  errorMessage,
  fetchTasks,
  fetchVideos,
  formatRelative,
  startDouyinProduce,
  type DouyinLayout,
  type TaskSummary,
  type Video,
} from '../api'
import { clock, historyLabel, taskSourceTitle } from '../features/edit/studio'
import { toast } from '../ui'

const LAYOUTS: Array<{ id: DouyinLayout; title: string; detail: string }> = [
  { id: 'interview', title: '访谈式', detail: '跟着说话人裁成 9:16' },
  { id: 'podcast', title: '满屏播客式', detail: '完整画面，背景铺满' },
]

export default function EditPage() {
  const navigate = useNavigate()
  const location = useLocation()
  const [tasks, setTasks] = useState<TaskSummary[]>([])
  const [videos, setVideos] = useState<Video[]>([])
  const [loadError, setLoadError] = useState('')
  const [selectedVideoId, setSelectedVideoId] = useState(0)
  const [query, setQuery] = useState('')
  const [instruction, setInstruction] = useState('')
  const [layout, setLayout] = useState<DouyinLayout>('interview')
  const [starting, setStarting] = useState(false)

  const load = useCallback(async () => {
    try {
      const [taskList, videoList] = await Promise.all([fetchTasks(), fetchVideos()])
      setTasks(taskList.filter((task) => task.kind === 'edit.douyin'))
      setVideos(videoList)
      setLoadError('')
    } catch (error) {
      setLoadError(errorMessage(error))
    }
  }, [])

  useEffect(() => {
    void load()
    const timer = window.setInterval(() => { void load() }, 4000)
    return () => window.clearInterval(timer)
  }, [load])

  useEffect(() => {
    const ids = (location.state as { videoIds?: number[] } | null)?.videoIds
    const first = ids?.find((id) => id > 0)
    if (first) setSelectedVideoId(first)
  }, [location.state])

  const selected = videos.find((video) => video.id === selectedVideoId) || null
  const needle = query.trim().toLowerCase()
  const visible = useMemo(
    () => videos.filter((video) => !needle || video.title.toLowerCase().includes(needle)),
    [needle, videos],
  )

  async function startProduce() {
    if (!selectedVideoId || starting) {
      if (!selectedVideoId) toast.error('先选一条素材库里的原片')
      return
    }
    setStarting(true)
    try {
      const job = await startDouyinProduce(selectedVideoId, instruction.trim(), 0, layout)
      toast.success('已开始生成抖音短视频')
      navigate(editTaskPath(job))
    } catch (error) {
      toast.error(errorMessage(error))
    } finally {
      setStarting(false)
    }
  }

  async function cancel(taskId: string) {
    try {
      await cancelJob(taskId)
      toast.success('已请求取消')
      void load()
    } catch (error) {
      toast.error(errorMessage(error))
    }
  }

  return (
    <div className="studio">
      <header className="studio-hero">
        <p className="studio-kicker">一键出片</p>
        <h1 className="studio-title">一条长片，<br />一组抖音短视频。</h1>
        <p className="studio-lead">
          从素材库选访谈、直播回放或口播。转写、选段、渲染，交出竖屏成片、封面和文案。发布你自己来。
        </p>
      </header>

      {loadError && <p className="studio-error">{loadError}</p>}

      <section className="studio-board" aria-label="选择原片并生成">
        <div className="studio-source">
          <div className="studio-reel-head">
            <div>
              <h2>原片</h2>
              <p>素材库 · {videos.length}</p>
            </div>
            {videos.length > 0 && (
              <input
                className="studio-search"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="搜索标题"
                aria-label="搜索原片"
                type="search"
              />
            )}
          </div>

          {videos.length === 0 ? (
            <div className="studio-empty">
              <p>库里还没有原片。</p>
              <div className="studio-links">
                <Link to="/videos">去素材库</Link>
                <Link to="/download">去下载</Link>
                <Link to="/record">去录制</Link>
              </div>
            </div>
          ) : visible.length === 0 ? (
            <p className="studio-empty">没有标题匹配的原片。</p>
          ) : (
            <div className="studio-reel" role="listbox" aria-label="素材库原片">
              {visible.map((video) => {
                const on = video.id === selectedVideoId
                return (
                  <button
                    key={video.id}
                    type="button"
                    role="option"
                    aria-selected={on}
                    className={`studio-frame ${on ? 'is-on' : ''}`}
                    onClick={() => setSelectedVideoId(video.id)}
                  >
                    <span className="studio-still">
                      {video.cover_url ? <img src={video.cover_url} alt="" /> : <b>{video.title.slice(0, 1)}</b>}
                      <i className="tnum">{clock(video.duration)}</i>
                    </span>
                    <strong>{video.title}</strong>
                  </button>
                )
              })}
            </div>
          )}
        </div>

        <form
          className="studio-compose"
          onSubmit={(event) => {
            event.preventDefault()
            void startProduce()
          }}
        >
          <div className="studio-feature" aria-hidden="true">
            {selected?.cover_url
              ? <img src={selected.cover_url} alt="" />
              : selected
                ? <b>{selected.title.slice(0, 1)}</b>
                : null}
          </div>
          <div className="studio-picked">
            <h2>{selected ? selected.title : '先选一条原片'}</h2>
            {selected && <span className="tnum">{clock(selected.duration)}</span>}
          </div>
          <input
            className="studio-prompt"
            value={instruction}
            maxLength={400}
            onChange={(event) => setInstruction(event.target.value)}
            placeholder="想留什么，可选。例如只要观点冲突和金句"
            aria-label="想留什么样的片段"
          />
          <div className="studio-layouts" role="radiogroup" aria-label="竖屏版式">
            {LAYOUTS.map((item) => (
              <button
                key={item.id}
                type="button"
                role="radio"
                aria-checked={layout === item.id}
                className={`studio-layout ${layout === item.id ? 'is-on' : ''}`}
                onClick={() => setLayout(item.id)}
              >
                <strong>{item.title}</strong>
                <span>{item.detail}</span>
              </button>
            ))}
          </div>
          <button className="studio-cta" type="submit" disabled={starting || !selectedVideoId} aria-busy={starting}>
            {starting ? '正在提交…' : '生成抖音短视频'}
          </button>
          <p className="studio-fine">
            最多大约 10 条，先出更值得发的。转写在本机按需进行。不会登录抖音，也不会自动发布。
          </p>
        </form>
      </section>

      <section className="studio-history" aria-label="出片记录">
        <div className="studio-reel-head">
          <div>
            <h2>出片记录</h2>
            <p>{tasks.length ? `${tasks.length} 次` : '还没有成片'}</p>
          </div>
        </div>
        {tasks.length === 0 ? (
          <p className="studio-empty">选好原片，点一次生成。完成后可以在这里回来看。</p>
        ) : (
          <div className="studio-reel studio-reel-tall">
            {tasks.map((task) => {
              const cover = task.result?.shorts?.find((short) => short.cover_url)?.cover_url
              const running = task.status === 'pending' || task.status === 'running'
              const title = taskSourceTitle(task, videos)
              return (
                <article key={task.id} className="studio-memory">
                  <button type="button" className="studio-frame" onClick={() => navigate(editTaskPath(task))}>
                    <span className={`studio-still studio-still-tall ${running ? 'is-live' : ''}`}>
                      {cover ? <img src={cover} alt="" /> : <b className="tnum">{Math.round(task.progress || 0)}</b>}
                      <i>{historyLabel(task)}</i>
                    </span>
                    <strong>{title}</strong>
                    <em>{formatRelative(task.created_at)}</em>
                  </button>
                  {running && (
                    <button type="button" className="studio-quiet" onClick={() => void cancel(task.id)}>取消</button>
                  )}
                </article>
              )
            })}
          </div>
        )}
      </section>
    </div>
  )
}
