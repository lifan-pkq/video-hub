import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import {
  ArrowRight,
  CloudArrowDown,
  DownloadSimple,
  FilmStrip,
  FolderOpen,
  PaperPlaneTilt,
  Play,
  Scissors,
  VideoCamera,
} from '@phosphor-icons/react'
import {
  createDownload,
  errorMessage,
  fetchStats,
  fetchTasks,
  fetchVideos,
  formatDuration,
  formatRelative,
  formatSize,
  parseDownload,
  platformLabel,
  type ParseResult,
  type Stats,
  type TaskSummary,
  type Video,
} from '../api'
import {
  Alert,
  Button,
  EmptyState,
  Input,
  PageHeader,
  Panel,
  PlatformIcon,
  Progress,
  StatCard,
  Tag,
  toast,
} from '../ui'
import { useEvents, type EventMsg } from '../events'
import StatusBadge from '../components/StatusBadge'
import VideoPlayerModal from '../components/VideoPlayerModal'

/** 日期问候：8月22日 星期六 */
function todayText(): string {
  const d = new Date()
  const weekday = d.toLocaleDateString('zh-CN', { weekday: 'long' })
  return `${d.getMonth() + 1}月${d.getDate()}日 ${weekday}`
}

const TASK_TYPE_TAG: Record<TaskSummary['type'], { label: string; color: 'accent' | 'blue' | 'orange' }> = {
  download: { label: '下载', color: 'accent' },
  publish: { label: '发布', color: 'blue' },
  edit: { label: '剪辑', color: 'orange' },
}

export default function WorkbenchPage() {
  const navigate = useNavigate()

  // ---- 数据：统计 + 视频 ----
  const [stats, setStats] = useState<Stats | null>(null)
  const [videos, setVideos] = useState<Video[]>([])
  const [loading, setLoading] = useState(true)

  // ---- 实时任务 ----
  const [tasks, setTasks] = useState<TaskSummary[]>([])
  const onEvent = useCallback((msg: EventMsg) => {
    if (msg.type === 'snapshot') {
      setTasks(msg.tasks)
    } else if (msg.type === 'task_updated') {
      setTasks((prev) => {
        const i = prev.findIndex((t) => t.id === msg.task.id)
        if (i === -1) return [msg.task, ...prev]
        const next = prev.slice()
        next[i] = msg.task
        return next
      })
    }
  }, [])
  useEvents(onEvent)

  // ---- 快捷下载 ----
  const [link, setLink] = useState('')
  const [parsing, setParsing] = useState(false)
  const [parseError, setParseError] = useState<string | null>(null)
  const [preview, setPreview] = useState<ParseResult | null>(null)
  const [starting, setStarting] = useState(false)

  // ---- 最近视频预览 ----
  const [playId, setPlayId] = useState<number | null>(null)

  const load = useCallback(async () => {
    try {
      const [s, v] = await Promise.all([fetchStats(), fetchVideos()])
      setStats(s)
      setVideos(v)
    } catch (e) {
      toast.error(`加载失败：${errorMessage(e)}`)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const activeTasks = useMemo(
    () => tasks.filter((t) => t.status === 'pending' || t.status === 'running'),
    [tasks],
  )
  const recentVideos = videos.slice(0, 8)
  const playVideo = videos.find((v) => v.id === playId)

  const handleParse = async () => {
    const q = link.trim()
    if (!q) {
      toast.warning('请先粘贴分享链接')
      return
    }
    setParsing(true)
    setParseError(null)
    setPreview(null)
    try {
      setPreview(await parseDownload(q))
    } catch (e) {
      setParseError(errorMessage(e))
    } finally {
      setParsing(false)
    }
  }

  const handleDownload = async () => {
    const q = link.trim()
    if (!q) return
    setStarting(true)
    try {
      await createDownload(q)
      toast.success('下载任务已创建，可在下方跟踪进度')
      setPreview(null)
      setLink('')
      // 立即刷新一次任务列表，让进度卡片马上出现（WS 推送随后接管）
      fetchTasks()
        .then(setTasks)
        .catch(() => {})
    } catch (e) {
      toast.error(`创建下载任务失败：${errorMessage(e)}`)
    } finally {
      setStarting(false)
    }
  }

  const workflowSteps = [
    {
      key: 'import',
      title: '找素材',
      desc: '下载或导入视频',
      icon: <FolderOpen size={20} />,
      to: '/library/import',
      highlight: !loading && videos.length === 0,
    },
    {
      key: 'edit',
      title: '剪辑',
      desc: '裁剪拼接与 AI 分析',
      icon: <Scissors size={20} />,
      to: '/edit',
      highlight: false,
    },
    {
      key: 'publish',
      title: '发布',
      desc: '一键分发多平台',
      icon: <PaperPlaneTilt size={20} />,
      to: '/publish',
      highlight: false,
    },
  ]

  const statCards = stats
    ? [
        {
          label: '视频总数',
          value: <span className="tnum">{stats.videos.count}</span>,
          sub: `占用 ${formatSize(stats.videos.size)}`,
          icon: <VideoCamera size={18} />,
          to: '/library',
        },
        {
          label: '进行中任务',
          value: (
            <span className={`tnum ${stats.tasks.running > 0 ? 'text-accent' : ''}`}>
              {stats.tasks.running}
            </span>
          ),
          sub: stats.tasks.needs_verify > 0 ? `${stats.tasks.needs_verify} 个待验证` : '暂无待验证',
          icon: <DownloadSimple size={18} />,
          to: '/tasks',
        },
        {
          label: '今日发布',
          value: <span className="tnum">{stats.publish.today}</span>,
          sub: `累计 ${stats.publish.total} 次`,
          icon: <PaperPlaneTilt size={18} />,
          to: '/publish',
        },
        {
          label: '发布成功率',
          value: (
            <span className="tnum">
              {stats.publish.total > 0
                ? `${Math.round((stats.publish.success / stats.publish.total) * 100)}%`
                : '-'}
            </span>
          ),
          sub: `成功 ${stats.publish.success} 次`,
          icon: <FilmStrip size={18} />,
          to: '/publish',
        },
      ]
    : []

  return (
    <div>
      <PageHeader title="工作台" desc={`${todayText()} · 今天已发布 ${stats?.publish.today ?? 0} 个视频`} />

      {/* 1. 快捷下载大卡 */}
      <Panel className="enter-item" pad="md">
        <div className="flex items-center gap-2">
          <CloudArrowDown size={22} className="text-accent" weight="fill" />
          <h2 className="section-title">快捷下载</h2>
        </div>
        <p className="mt-1 text-[13px] text-ink-3">
          粘贴抖音 / 快手 / 小红书 / B站 分享链接，解析后一键无水印入库
        </p>
        <div className="mt-4 flex flex-col gap-2 sm:flex-row">
          <Input
            value={link}
            onChange={(v) => {
              setLink(v)
              if (parseError) setParseError(null)
            }}
            allowClear
            placeholder="粘贴分享链接或口令，例如：https://v.douyin.com/xxxx/"
            className="h-11 flex-1 text-sm"
            onKeyDown={(e) => {
              if (e.key === 'Enter') handleParse()
            }}
          />
          <Button
            variant="primary"
            size="md"
            className="h-11 shrink-0 px-5"
            loading={parsing}
            disabled={!link.trim()}
            onClick={handleParse}
          >
            解析下载
          </Button>
        </div>

        {parseError && (
          <Alert type="error" className="mt-3">
            解析失败：{parseError}
          </Alert>
        )}

        {preview && (
          <div className="fade-in mt-4 flex flex-col gap-4 rounded-xl border border-line-soft bg-fill p-4 sm:flex-row">
            {preview.cover_url ? (
              <img
                src={preview.cover_url}
                alt={preview.title}
                className="h-28 w-20 shrink-0 rounded-lg object-cover"
              />
            ) : (
              <div className="flex h-28 w-20 shrink-0 items-center justify-center rounded-lg bg-fill-deep">
                <PlatformIcon platform={preview.platform} size={30} />
              </div>
            )}
            <div className="min-w-0 flex-1">
              <div className="line-clamp-2 font-medium leading-snug text-ink-1" title={preview.title}>
                {preview.title}
              </div>
              <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-[13px] text-ink-3">
                <span className="inline-flex items-center gap-1.5">
                  <PlatformIcon platform={preview.platform} size={16} />
                  {platformLabel(preview.platform)}
                </span>
                <span>作者：{preview.author || '未知'}</span>
                <span className="tnum">时长 {formatDuration(preview.duration)}</span>
              </div>
            </div>
            <div className="flex shrink-0 items-end">
              <Button
                variant="primary"
                icon={<DownloadSimple size={15} />}
                loading={starting}
                onClick={handleDownload}
              >
                立即下载
              </Button>
            </div>
          </div>
        )}
      </Panel>

      {/* 2. 进行中任务（实时） */}
      <div className="mt-6">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="section-title">进行中任务</h2>
          <Link to="/tasks" className="text-[13px] text-accent hover:underline">
            任务中心 →
          </Link>
        </div>
        {activeTasks.length === 0 ? (
          <p className="rounded-xl border border-dashed border-line px-4 py-3 text-[13px] text-ink-4">
            暂无进行中的任务，下载或发布后会在这里实时显示进度
          </p>
        ) : (
          <div className="grid gap-3 md:grid-cols-2">
            {activeTasks.map((t, i) => {
              const typeTag = TASK_TYPE_TAG[t.type]
              return (
                <div
                  key={t.id}
                  className="vh-card enter-item p-4"
                  style={{ animationDelay: `${Math.min(i * 40, 200)}ms` }}
                >
                  <div className="flex items-center gap-2">
                    <Tag color={typeTag.color}>{typeTag.label}</Tag>
                    <span className="min-w-0 flex-1 truncate text-[13px] font-medium text-ink-1" title={t.title}>
                      {t.title}
                    </span>
                    <StatusBadge status={t.status} />
                  </div>
                  <Progress percent={t.progress / 100} className="mt-3" />
                  <div className="mt-2 flex items-center justify-between text-xs text-ink-4">
                    {t.needs_verify ? (
                      <span className="text-accent">等待短信验证码</span>
                    ) : (
                      <span className="tnum">{Math.round(t.progress)}%</span>
                    )}
                    <span>{formatRelative(t.created_at)}</span>
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>

      {/* 3. 工作流引导条 */}
      <div className="mt-6">
        <h2 className="section-title mb-3">创作流程</h2>
        <div className="grid gap-3 md:grid-cols-3">
          {workflowSteps.map((s, i) => (
            <Link
              key={s.key}
              to={s.to}
              className={`vh-card group flex items-center gap-3 p-4 transition-[border-color] hover:border-accent ${
                s.highlight ? 'border-accent bg-accent-soft' : ''
              }`}
            >
              <span
                className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-xl ${
                  s.highlight ? 'bg-accent text-white' : 'bg-fill text-ink-3 group-hover:text-accent'
                }`}
              >
                {s.icon}
              </span>
              <span className="min-w-0 flex-1">
                <span className="flex items-center gap-1.5 text-[14px] font-medium text-ink-1">
                  <span className="tnum text-xs text-ink-4">{i + 1}</span>
                  {s.title}
                </span>
                <span className="mt-0.5 block truncate text-xs text-ink-3">{s.desc}</span>
              </span>
              <ArrowRight size={16} className="shrink-0 text-ink-4 transition-transform group-hover:translate-x-0.5 group-hover:text-accent" />
            </Link>
          ))}
        </div>
      </div>

      {/* 4. 最近视频 */}
      <div className="mt-6">
        <Panel
          title="最近视频"
          extra={
            <Link to="/library" className="text-[13px] text-accent hover:underline">
              全部素材 →
            </Link>
          }
        >
          {loading ? (
            <div className="flex gap-3 overflow-hidden">
              {[0, 1, 2, 3].map((i) => (
                <div key={i} className="w-40 shrink-0">
                  <div className="skeleton aspect-video w-full rounded-lg" />
                  <div className="skeleton mt-2 h-3 w-3/4" />
                </div>
              ))}
            </div>
          ) : recentVideos.length === 0 ? (
            <EmptyState
              icon={<VideoCamera size={26} />}
              title="素材库还是空的"
              desc="用上方快捷下载粘贴链接，或进入导入向导批量下载"
              action={
                <Button variant="primary" onClick={() => navigate('/library/import')}>
                  去导入素材
                </Button>
              }
            />
          ) : (
            <div className="flex gap-3 overflow-x-auto pb-2">
              {recentVideos.map((v, i) => (
                <div
                  key={v.id}
                  className="enter-item group w-40 shrink-0"
                  style={{ animationDelay: `${Math.min(i * 40, 240)}ms` }}
                >
                  <button
                    type="button"
                    onClick={() => setPlayId(v.id)}
                    className="relative block aspect-video w-full overflow-hidden rounded-lg bg-fill"
                    aria-label={`预览 ${v.title}`}
                  >
                    {v.cover_url ? (
                      <img
                        src={v.cover_url}
                        alt={v.title}
                        loading="lazy"
                        className="h-full w-full object-cover transition-transform duration-300 group-hover:scale-105"
                      />
                    ) : (
                      <div className="flex h-full w-full items-center justify-center">
                        <PlatformIcon platform={v.source_platform} size={30} />
                      </div>
                    )}
                    <span className="absolute inset-0 flex items-center justify-center bg-black/0 transition-colors group-hover:bg-black/35">
                      <Play
                        size={26}
                        weight="fill"
                        className="text-white opacity-0 transition-opacity group-hover:opacity-100"
                      />
                    </span>
                    {v.duration != null && (
                      <span className="tnum absolute bottom-1.5 right-1.5 rounded bg-black/55 px-1.5 py-0.5 text-[11px] text-white">
                        {formatDuration(v.duration)}
                      </span>
                    )}
                  </button>
                  <div className="mt-2 truncate text-[13px] text-ink-1" title={v.title}>
                    {v.title}
                  </div>
                  <div className="mt-1 flex items-center gap-1">
                    <Button
                      size="sm"
                      variant="text"
                      className="h-7 px-1.5"
                      icon={<Scissors size={13} />}
                      onClick={() => navigate(`/edit?video=${v.id}`)}
                    >
                      去剪辑
                    </Button>
                    <Button
                      size="sm"
                      variant="text"
                      className="h-7 px-1.5"
                      icon={<PaperPlaneTilt size={13} />}
                      onClick={() => navigate(`/publish?video=${v.id}`)}
                    >
                      去发布
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </Panel>
      </div>

      {/* 5. 统计卡片（一行 4 小卡） */}
      <div className="mt-6 grid grid-cols-2 gap-3 md:grid-cols-4">
        {loading || !stats
          ? [0, 1, 2, 3].map((i) => (
              <div key={i} className="vh-card p-5">
                <div className="skeleton" style={{ width: '40%', height: 13 }} />
                <div className="skeleton mt-3" style={{ width: '70%', height: 26 }} />
              </div>
            ))
          : statCards.map((c) => (
              <Link key={c.label} to={c.to} className="transition-transform hover:-translate-y-0.5">
                <StatCard label={c.label} value={c.value} sub={c.sub} icon={c.icon} />
              </Link>
            ))}
      </div>

      <VideoPlayerModal videoId={playId} title={playVideo?.title} onClose={() => setPlayId(null)} />
    </div>
  )
}
