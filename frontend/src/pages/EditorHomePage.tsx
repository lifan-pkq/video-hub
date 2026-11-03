import { useCallback, useEffect, useMemo, useState } from 'react'
import { Navigate, useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import {
  ArrowRight,
  Check,
  ClockCounterClockwise,
  FilmSlate,
  MagicWand,
  Plus,
  Sparkle,
} from '@phosphor-icons/react'
import {
  analyzeVideos,
  errorMessage,
  fetchVideos,
  formatDuration,
  formatRelative,
  planEdit,
  type AiSuggestionKind,
  type Video,
} from '../api'
import { useTaskCenter } from '../context/TaskCenter'
import StatusBadge from '../components/StatusBadge'
import { taskModeLabel, taskVideoIds } from '../features/editor/taskRestore'
import { Button, Checkbox, EmptyState, Input, PageHeader, Progress, TextArea, toast } from '../ui'
import RequestState from '../ui/RequestState'

const ANALYSIS_KINDS: { value: AiSuggestionKind; label: string }[] = [
  { value: 'intro', label: '去开头废话' },
  { value: 'outro', label: '去结尾废话' },
  { value: 'ad', label: '去中间广告' },
  { value: 'silence', label: '去静音停顿' },
  { value: 'repeat', label: '去重复表达' },
]

type EditorMode = 'plan' | 'analyze'

export default function EditorHomePage() {
  const navigate = useNavigate()
  const location = useLocation()
  const [searchParams] = useSearchParams()
  const { summaries, loading: tasksLoading, error: tasksError, refreshTasks } = useTaskCenter()
  const [videos, setVideos] = useState<Video[]>([])
  const [selected, setSelected] = useState<Set<number>>(() => new Set())
  const [mode, setMode] = useState<EditorMode>('plan')
  const [target, setTarget] = useState('60')
  const [kinds, setKinds] = useState<Set<AiSuggestionKind>>(() => new Set(['intro', 'outro', 'ad']))
  const [prompt, setPrompt] = useState('')
  const [starting, setStarting] = useState<EditorMode | null>(null)
  const [libLoading, setLibLoading] = useState(true)
  const [libError, setLibError] = useState<string | null>(null)

  const loadVideos = useCallback(async () => {
    setLibLoading(true)
    try {
      setVideos(await fetchVideos())
      setLibError(null)
    } catch (error) {
      setLibError(errorMessage(error))
    } finally {
      setLibLoading(false)
    }
  }, [])

  useEffect(() => {
    void loadVideos()
  }, [loadVideos])

  useEffect(() => {
    const state = location.state as { videoIds?: number[]; mode?: EditorMode } | null
    if (!state?.videoIds?.length) return
    const nextMode = state.mode ?? 'analyze'
    const ids = state.videoIds.filter((id) => Number.isInteger(id) && id > 0)
    setMode(nextMode)
    setSelected(new Set(ids.slice(0, nextMode === 'plan' ? 1 : 20)))
    navigate(location.pathname, { replace: true, state: null })
  }, [location, navigate])

  const history = useMemo(() => summaries.filter((task) => task.type === 'edit'), [summaries])
  const videosById = useMemo(() => new Map(videos.map((video) => [video.id, video])), [videos])
  const legacyTaskId = searchParams.get('task')

  const changeMode = (nextMode: EditorMode) => {
    setMode(nextMode)
    if (nextMode === 'plan') {
      setSelected((current) => new Set(Array.from(current).slice(0, 1)))
    }
  }

  const toggleVideo = (id: number) => {
    setSelected((current) => {
      if (mode === 'plan') return current.has(id) ? new Set() : new Set([id])
      const next = new Set(current)
      if (next.has(id)) next.delete(id)
      else if (next.size >= 20) {
        toast.warning('一次最多分析 20 个视频')
        return current
      } else next.add(id)
      return next
    })
  }

  const toggleKind = (kind: AiSuggestionKind) => {
    setKinds((current) => {
      const next = new Set(current)
      if (next.has(kind)) next.delete(kind)
      else next.add(kind)
      return next
    })
  }

  const start = async () => {
    if (selected.size === 0) {
      toast.warning('请先选择要剪辑的视频')
      return
    }
    if (mode === 'plan' && selected.size !== 1) {
      toast.warning('AI 智能成片一次选择一个视频')
      return
    }
    if (mode === 'analyze' && kinds.size === 0) {
      toast.warning('至少选择一种分析类型')
      return
    }
    setStarting(mode)
    try {
      const { task_id } = mode === 'plan'
        ? await planEdit(
            Array.from(selected)[0],
            Math.max(5, Math.min(1800, Number(target) || 60)),
            prompt.trim(),
          )
        : await analyzeVideos(Array.from(selected), Array.from(kinds), prompt.trim())
      navigate(`/edit/${task_id}`)
    } catch (error) {
      toast.error(`创建智能剪辑任务失败：${errorMessage(error)}`)
      setStarting(null)
    }
  }

  if (legacyTaskId) return <Navigate to={`/edit/${encodeURIComponent(legacyTaskId)}`} replace />

  return (
    <div>
      <PageHeader
        title="智能剪辑"
        desc="选择素材并描述你的剪辑需求，历史任务会保留在这里，随时可以继续。"
        extra={
          <Button variant="ghost" icon={<Plus size={15} />} onClick={() => navigate('/edit/new')}>
            空白剪辑
          </Button>
        }
      />

      <section className="overflow-hidden rounded-3xl border border-line-soft bg-card shadow-card">
        <div className="border-b border-line-soft bg-fill/60 px-5 py-6 sm:px-8 sm:py-8">
          <div className="mx-auto max-w-3xl">
            <div className="text-center">
              <h2 className="text-lg font-semibold tracking-tight text-ink-1">这次想怎么剪？</h2>
              <p className="mt-1 text-xs leading-relaxed text-ink-3">选择一种 AI 模式，配置好后在下方选择素材。</p>
            </div>
            <div className="mt-5 grid gap-3 sm:grid-cols-2" role="tablist" aria-label="智能剪辑模式">
              <button
                type="button"
                role="tab"
                aria-selected={mode === 'plan'}
                onClick={() => changeMode('plan')}
                className={`rounded-2xl border p-4 text-left transition-colors ${
                  mode === 'plan' ? 'border-accent bg-accent-soft' : 'border-line bg-card hover:border-accent/50'
                }`}
              >
                <span className="flex items-center gap-2 text-sm font-medium text-ink-1"><Sparkle size={17} className="text-accent" />AI 智能成片</span>
                <span className="mt-1.5 block text-[11px] leading-relaxed text-ink-3">按目标时长挑选高光片段，直接组成新版时间线。</span>
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={mode === 'analyze'}
                onClick={() => changeMode('analyze')}
                className={`rounded-2xl border p-4 text-left transition-colors ${
                  mode === 'analyze' ? 'border-accent bg-accent-soft' : 'border-line bg-card hover:border-accent/50'
                }`}
              >
                <span className="flex items-center gap-2 text-sm font-medium text-ink-1"><MagicWand size={17} className="text-accent" />AI 智能分析</span>
                <span className="mt-1.5 block text-[11px] leading-relaxed text-ink-3">找出废话、广告、静音和重复表达，逐条确认后剪除。</span>
              </button>
            </div>

            {mode === 'plan' ? (
              <div className="mt-5 grid items-start gap-3 sm:grid-cols-[130px_minmax(0,1fr)]">
                <label className="block">
                  <span className="mb-1.5 block text-[11px] text-ink-3">目标时长（秒）</span>
                  <Input
                    value={target}
                    onChange={setTarget}
                    type="number"
                    min={5}
                    max={1800}
                    disabled={starting !== null}
                    aria-label="目标时长（秒）"
                  />
                </label>
                <label className="block">
                  <span className="mb-1.5 block text-[11px] text-ink-3">选段要求（可留空）</span>
                  <TextArea
                    value={prompt}
                    onChange={setPrompt}
                    rows={2}
                    maxLength={200}
                    disabled={starting !== null}
                    aria-label="选段要求"
                    placeholder="例如：只保留干货，开头要抓人，结尾回扣主题"
                    className="bg-card"
                  />
                </label>
              </div>
            ) : (
              <div className="mt-5">
                <div className="flex flex-wrap gap-x-4 gap-y-2">
                  {ANALYSIS_KINDS.map((kind) => (
                    <Checkbox
                      key={kind.value}
                      checked={kinds.has(kind.value)}
                      disabled={starting !== null}
                      onChange={() => toggleKind(kind.value)}
                      label={kind.label}
                    />
                  ))}
                </div>
                <TextArea
                  value={prompt}
                  onChange={setPrompt}
                  rows={2}
                  maxLength={200}
                  disabled={starting !== null}
                  aria-label="分析要求"
                  placeholder="补充剪辑要求，例如：只保留产品介绍，重复结论只留一次"
                  className="mt-3 bg-card"
                />
              </div>
            )}
          </div>
        </div>

        <div className="p-5 sm:p-6">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <div>
              <h3 className="text-[13px] font-medium text-ink-1">选择素材</h3>
              <p className="mt-0.5 text-[11px] text-ink-4">
                {mode === 'plan' ? '智能成片选择 1 个视频' : `已选 ${selected.size} 个，最多 20 个`}
              </p>
            </div>
            {selected.size > 0 && (
              <Button size="sm" variant="text" onClick={() => setSelected(new Set())}>清空选择</Button>
            )}
          </div>
          <RequestState error={libError} hasData={videos.length > 0} loading={libLoading} onRetry={loadVideos} className="mb-3" />
          {libLoading && videos.length === 0 ? (
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
              {[0, 1, 2, 3].map((item) => <div key={item} className="skeleton aspect-[16/9] rounded-xl" />)}
            </div>
          ) : videos.length === 0 && !libError ? (
            <EmptyState title="暂无视频素材" desc="先到视频素材库下载或上传视频，再回来开始剪辑。" />
          ) : (
            <div className="grid max-h-[330px] grid-cols-2 gap-3 overflow-y-auto pr-1 sm:grid-cols-3 lg:grid-cols-4">
              {videos.map((video) => {
                const checked = selected.has(video.id)
                return (
                  <button
                    key={video.id}
                    type="button"
                    aria-pressed={checked}
                    onClick={() => toggleVideo(video.id)}
                    className={`group relative overflow-hidden rounded-xl border text-left transition-all ${
                      checked ? 'border-accent ring-2 ring-accent/15' : 'border-line-soft hover:border-line'
                    }`}
                  >
                    <div className="aspect-[16/9] bg-fill-deep">
                      {video.cover_url && <img src={video.cover_url} alt="" className="h-full w-full object-cover" />}
                    </div>
                    <div className="p-2.5">
                      <div className="truncate text-xs font-medium text-ink-1">{video.title}</div>
                      <div className="tnum mt-1 text-[11px] text-ink-4">{formatDuration(video.duration)}</div>
                    </div>
                    <span className={`absolute right-2 top-2 flex h-5 w-5 items-center justify-center rounded-full border ${
                      checked ? 'border-accent bg-accent text-white' : 'border-white/60 bg-black/35 text-transparent'
                    }`}>
                      <Check size={12} weight="bold" />
                    </span>
                  </button>
                )
              })}
            </div>
          )}
          <div className="mt-5 flex justify-center">
            <Button
              variant="primary"
              loading={starting === mode}
              disabled={selected.size === 0 || starting !== null || (mode === 'analyze' && kinds.size === 0)}
              icon={<ArrowRight size={15} />}
              onClick={() => void start()}
              className="h-11 px-6"
            >
              {mode === 'plan' ? '开始智能成片' : '开始智能分析'}
            </Button>
          </div>
        </div>
      </section>

      <section className="mt-8">
        <div className="mb-3 flex items-end justify-between gap-3">
          <div>
            <h2 className="text-base font-semibold text-ink-1">历史记录</h2>
            <p className="mt-1 text-xs text-ink-3">点击记录回到对应的剪辑详情。</p>
          </div>
          <span className="tnum text-xs text-ink-4">{history.length} 条</span>
        </div>
        <RequestState error={tasksError} hasData={history.length > 0} loading={tasksLoading} onRetry={refreshTasks} className="mb-3" />
        {tasksLoading && history.length === 0 ? (
          <div className="space-y-3">{[0, 1, 2].map((item) => <div key={item} className="skeleton h-24 rounded-2xl" />)}</div>
        ) : history.length === 0 ? (
          <div className="vh-card">
            <EmptyState
              icon={<ClockCounterClockwise size={25} />}
              title="还没有剪辑记录"
              desc="从上方选择素材并创建任务后，就可以在这里继续。"
            />
          </div>
        ) : (
          <div className="space-y-3">
            {history.map((task) => {
              const ids = taskVideoIds(task)
              const cover = ids.length > 0 ? videosById.get(ids[0])?.cover_url : null
              return (
                <button
                  key={task.id}
                  type="button"
                  onClick={() => navigate(`/edit/${task.id}`)}
                  className="vh-card flex w-full items-center gap-3 p-3 text-left sm:gap-4 sm:p-4"
                >
                  <div className="flex h-16 w-14 shrink-0 items-center justify-center overflow-hidden rounded-xl bg-fill text-ink-3 sm:h-[72px] sm:w-16">
                    {cover ? <img src={cover} alt="" className="h-full w-full object-cover" /> : <FilmSlate size={22} />}
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-[11px] font-medium text-accent">{taskModeLabel(task)}</span>
                      <StatusBadge status={task.status} />
                    </div>
                    <div className="mt-1.5 truncate text-[13px] font-medium text-ink-1">{task.title}</div>
                    <div className="mt-1 text-[11px] text-ink-4">
                      {formatRelative(task.created_at)}{ids.length > 0 ? ` · ${ids.length} 个素材` : ''}
                    </div>
                    {(task.status === 'pending' || task.status === 'running') && (
                      <Progress percent={task.progress / 100} className="mt-2 max-w-sm" />
                    )}
                  </div>
                  <ArrowRight size={17} className="shrink-0 text-ink-4" />
                </button>
              )
            })}
          </div>
        )}
      </section>
    </div>
  )
}
