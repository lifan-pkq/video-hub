import { useCallback, useEffect, useMemo, useState, type KeyboardEvent, type ReactNode } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import {
  Broadcast,
  ChartLine,
  DownloadSimple,
  PaperPlaneTilt,
  UserCircle,
  VideoCamera,
} from '@phosphor-icons/react'
import {
  errorMessage,
  fetchPublishRecords,
  fetchStats,
  fetchTasks,
  fetchVideos,
  formatRelative,
  formatSize,
  type PublishRecord,
  type Stats,
  type TaskSummary,
  type Video,
} from '../api'
import {
  Button,
  EmptyState,
  PageHeader,
  Panel,
  PlatformIcon,
  Progress,
  StatCard,
} from '../ui'
import StatusBadge from '../components/StatusBadge'
import RequestState from '../ui/RequestState'

/** 日期问候：8月22日 星期六 */
function todayText(): string {
  const d = new Date()
  const weekday = d.toLocaleDateString('zh-CN', { weekday: 'long' })
  return `${d.getMonth() + 1}月${d.getDate()}日 ${weekday}`
}

/** 面板加载骨架：4 行，末行 60% 宽 */
function PanelSkeleton() {
  return (
    <div className="space-y-3 py-1">
      {['100%', '100%', '100%', '60%'].map((w, i) => (
        <div key={i} className="skeleton" style={{ width: w, height: 14 }} />
      ))}
    </div>
  )
}

interface TrendDay {
  label: string
  count: number
}

/** 近 14 天发布次数按日聚合 */
function buildTrend(records: PublishRecord[]): TrendDay[] {
  const now = new Date()
  const days: (TrendDay & { key: string })[] = []
  for (let i = 13; i >= 0; i--) {
    const d = new Date(now.getFullYear(), now.getMonth(), now.getDate() - i)
    days.push({
      key: `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`,
      label: `${d.getMonth() + 1}/${d.getDate()}`,
      count: 0,
    })
  }
  const index = new Map(days.map((d, i) => [d.key, i]))
  for (const r of records) {
    const d = new Date(r.created_at)
    if (Number.isNaN(d.getTime())) continue
    const i = index.get(`${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`)
    if (i != null) days[i].count++
  }
  return days
}

/** Catmull-Rom 转三次贝塞尔，生成平滑折线路径 */
function smoothPath(pts: { x: number; y: number }[]): string {
  if (pts.length < 2) return ''
  let d = `M ${pts[0].x} ${pts[0].y}`
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[Math.max(0, i - 1)]
    const p1 = pts[i]
    const p2 = pts[i + 1]
    const p3 = pts[Math.min(pts.length - 1, i + 2)]
    const c1x = p1.x + (p2.x - p0.x) / 6
    const c1y = p1.y + (p2.y - p0.y) / 6
    const c2x = p2.x - (p3.x - p1.x) / 6
    const c2y = p2.y - (p3.y - p1.y) / 6
    d += ` C ${c1x} ${c1y}, ${c2x} ${c2y}, ${p2.x} ${p2.y}`
  }
  return d
}

/** 发布趋势面积图（纯 SVG，无依赖） */
function TrendChart({ days }: { days: TrendDay[] }) {
  const W = 560
  const H = 190
  const PAD_X = 12
  const PAD_TOP = 28 // 顶部留给最高点标注
  const PAD_BOTTOM = 24 // 底部留给日期刻度
  const max = Math.max(...days.map((d) => d.count), 1)
  const innerH = H - PAD_TOP - PAD_BOTTOM
  const pts = days.map((d, i) => ({
    x: PAD_X + (i * (W - PAD_X * 2)) / (days.length - 1),
    y: PAD_TOP + (1 - d.count / max) * innerH,
  }))
  const line = smoothPath(pts)
  const base = H - PAD_BOTTOM
  const area = `${line} L ${pts[pts.length - 1].x} ${base} L ${pts[0].x} ${base} Z`
  const peakIdx = days.findIndex((d) => d.count === max && max > 0)
  const tickIdx = [0, 4, 9, 13]

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label="近 14 天发布趋势">
      <defs>
        <linearGradient id="trendFill" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" style={{ stopColor: 'var(--accent)' }} stopOpacity={0.35} />
          <stop offset="100%" style={{ stopColor: 'var(--accent)' }} stopOpacity={0} />
        </linearGradient>
      </defs>
      <line
        x1={PAD_X}
        y1={base}
        x2={W - PAD_X}
        y2={base}
        strokeWidth={1}
        style={{ stroke: 'var(--border)' }}
      />
      <path d={area} fill="url(#trendFill)" />
      <path
        d={line}
        fill="none"
        strokeWidth={2}
        strokeLinecap="round"
        style={{ stroke: 'var(--accent)' }}
      />
      {peakIdx >= 0 && (
        <g>
          <circle
            cx={pts[peakIdx].x}
            cy={pts[peakIdx].y}
            r={3.5}
            style={{ fill: 'var(--accent)' }}
          />
          <text
            x={Math.min(Math.max(pts[peakIdx].x, 24), W - 24)}
            y={pts[peakIdx].y - 10}
            textAnchor="middle"
            fontSize={11}
            fontWeight={600}
            style={{ fill: 'var(--ink-1)' }}
          >
            {days[peakIdx].count}
          </text>
        </g>
      )}
      {tickIdx.map((i) => (
        <text
          key={i}
          x={pts[i].x}
          y={H - 6}
          textAnchor={i === 0 ? 'start' : i === 13 ? 'end' : 'middle'}
          fontSize={10}
          style={{ fill: 'var(--ink-4)' }}
        >
          {days[i].label}
        </text>
      ))}
    </svg>
  )
}

/** 近 14 天发布结果占比环形图 */
function PublishDonut({ records }: { records: PublishRecord[] }) {
  const since = Date.now() - 14 * 86400000
  let success = 0
  let partial = 0
  let failed = 0
  for (const r of records) {
    const t = new Date(r.created_at).getTime()
    if (Number.isNaN(t) || t < since) continue
    if (r.status === 'success') success++
    else if (r.status === 'partial') partial++
    else if (r.status === 'failed') failed++
  }
  const total = success + partial + failed
  if (total === 0) {
    return <p className="py-8 text-center text-[13px] text-ink-3">近 14 天暂无发布记录</p>
  }
  const rate = Math.round((success / total) * 100)
  const R = 54
  const C = 2 * Math.PI * R
  let offset = 0
  const segs = [
    { label: '成功', count: success, color: 'var(--success)' },
    { label: '部分成功', count: partial, color: 'var(--warning)' },
    { label: '失败', count: failed, color: 'var(--danger)' },
  ].filter((s) => s.count > 0)

  return (
    <div className="flex items-center gap-6">
      <div className="relative h-32 w-32 shrink-0">
        <svg viewBox="0 0 132 132" className="h-full w-full -rotate-90">
          <circle
            cx={66}
            cy={66}
            r={R}
            fill="none"
            strokeWidth={12}
            style={{ stroke: 'var(--fill-deep)' }}
          />
          {segs.map((s) => {
            const len = (s.count / total) * C
            const el = (
              <circle
                key={s.label}
                cx={66}
                cy={66}
                r={R}
                fill="none"
                strokeWidth={12}
                strokeDasharray={`${len} ${C - len}`}
                strokeDashoffset={-offset}
                style={{ stroke: s.color }}
              />
            )
            offset += len
            return el
          })}
        </svg>
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          <div className="stat-value tnum text-[26px] leading-none text-ink-1">{rate}%</div>
          <div className="mt-1 text-xs text-ink-3">成功率</div>
        </div>
      </div>
      <div className="space-y-2.5 text-[13px]">
        {segs.map((s) => (
          <div key={s.label} className="flex items-center gap-2 text-ink-2">
            <span className="h-2 w-2 rounded-full" style={{ background: s.color }} />
            {s.label}
            <span className="tnum text-ink-1">{s.count}</span>
          </div>
        ))}
        <div className="text-xs text-ink-4">
          近 14 天共 <span className="tnum">{total}</span> 次
        </div>
      </div>
    </div>
  )
}

export default function DashboardPage() {
  const navigate = useNavigate()
  const [stats, setStats] = useState<Stats | null>(null)
  const [videos, setVideos] = useState<Video[]>([])
  const [tasks, setTasks] = useState<TaskSummary[]>([])
  const [records, setRecords] = useState<PublishRecord[]>([])
  const [loading, setLoading] = useState(true)
  const [hasData, setHasData] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [s, v, t, r] = await Promise.all([
        fetchStats(),
        fetchVideos(),
        fetchTasks(),
        fetchPublishRecords(),
      ])
      setStats(s)
      setVideos(v)
      setTasks(t)
      setRecords(r)
      setHasData(true)
      setLoadError(null)
    } catch (e) {
      setLoadError(errorMessage(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  // 有进行中任务/录制时每 5 秒轮询，全部结束后自动停止
  const polling = !!stats && (stats.tasks.running > 0 || stats.recordings.active > 0)
  useEffect(() => {
    if (!polling) return
    const timer = window.setInterval(() => load(), 5000)
    return () => window.clearInterval(timer)
  }, [polling, load])

  const activeTasks = tasks.filter((t) => t.status === 'pending' || t.status === 'running')
  const recentVideos = videos.slice(0, 8)
  const finishedRecords = records.filter(
    (r) => r.status === 'success' || r.status === 'partial' || r.status === 'failed',
  )
  const publishSuccesses = finishedRecords.filter((r) => r.status === 'success').length
  const successRate = finishedRecords.length > 0
    ? `${Math.round((publishSuccesses / finishedRecords.length) * 100)}%`
    : '-'

  const trend = useMemo(() => buildTrend(records), [records])
  const trendTotal = trend.reduce((sum, d) => sum + d.count, 0)

  const statCards: {
    label: string
    value: ReactNode
    sub: ReactNode
    icon: ReactNode
    to: string
  }[] = stats
    ? [
        {
          label: '视频库',
          value: stats.videos.count,
          sub: <>共占用 <span className="tnum">{formatSize(stats.videos.size)}</span></>,
          icon: <VideoCamera size={18} />,
          to: '/videos',
        },
        {
          label: '平台账号',
          value: (
            <>
              <span className="tnum">{stats.accounts.with_cookie}</span>
              <span className="tnum text-[16px] text-ink-4">/{stats.accounts.total}</span>
            </>
          ),
          sub: '已配置登录态 / 总账号数',
          icon: <UserCircle size={18} />,
          to: '/accounts',
        },
        {
          label: '录制中',
          value: (
            <span className={`tnum ${stats.recordings.active > 0 ? 'text-accent' : ''}`}>
              {stats.recordings.active}
            </span>
          ),
          sub: <>共 <span className="tnum">{stats.recordings.total}</span> 条录制记录</>,
          icon: <Broadcast size={18} />,
          to: '/record',
        },
        {
          label: '发布成功率',
          value: <span className="tnum">{successRate}</span>,
          sub: (
            <>
              累计 <span className="tnum">{finishedRecords.length}</span> 次
            </>
          ),
          icon: <PaperPlaneTilt size={18} />,
          to: '/publish',
        },
      ]
    : []

  return (
    <div>
      <PageHeader
        title="仪表盘"
        desc={`${todayText()} · 今天已发布 ${stats?.publish.today ?? 0} 个视频`}
        extra={
          <>
            <Button
              variant="primary"
              icon={<DownloadSimple size={15} />}
              onClick={() => navigate('/download')}
            >
              下载视频
            </Button>
            <Button
              variant="ghost"
              icon={<Broadcast size={15} />}
              onClick={() => navigate('/record')}
            >
              开始录制
            </Button>
          </>
        }
      />

      <RequestState error={loadError} hasData={hasData} loading={loading} onRetry={load} className="mb-4" />
      {hasData || loading ? (
        <div className="bento-grid">
        {/* 发布趋势（大格） */}
        <div className="bento-2x2 enter-item">
          <Panel
            className="flex h-full flex-col"
            title={
              <>
                发布趋势
                <span className="ml-2 text-xs font-normal text-ink-4">
                  近 14 天 · 共 <span className="tnum">{trendTotal}</span> 次
                </span>
              </>
            }
          >
            {loading && !hasData ? (
              <PanelSkeleton />
            ) : trendTotal === 0 ? (
              <div className="flex flex-1 items-center justify-center">
                <EmptyState
                  icon={<ChartLine size={26} />}
                  title="暂无发布数据"
                  desc="近 14 天还没有发布记录"
                />
              </div>
            ) : (
              <div className="flex flex-1 items-center">
                <TrendChart days={trend} />
              </div>
            )}
          </Panel>
        </div>

        {/* 统计卡 ×4 */}
        {(loading && !hasData) || !stats
          ? [0, 1, 2, 3].map((i) => (
              <div key={i} className="vh-card p-5">
                <div className="skeleton" style={{ width: '40%', height: 13 }} />
                <div className="skeleton mt-3" style={{ width: '70%', height: 26 }} />
              </div>
            ))
          : statCards.map((c, i) => (
              <div
                key={c.label}
                role="button"
                tabIndex={0}
                onClick={() => navigate(c.to)}
                onKeyDown={(e: KeyboardEvent<HTMLDivElement>) => {
                  if (e.key === 'Enter') navigate(c.to)
                }}
                className="enter-item cursor-pointer transition-[border-color] hover:border-accent"
                style={{ animationDelay: `${Math.min(40 + i * 40, 200)}ms` }}
              >
                <StatCard label={c.label} value={c.value} sub={c.sub} icon={c.icon} />
              </div>
            ))}

        {/* 发布占比 */}
        <div className="bento-2x1 enter-item" style={{ animationDelay: '220ms' }}>
          <Panel
            className="h-full"
            title={
              <>
                发布成功率
                <span className="ml-2 text-xs font-normal text-ink-4">近 14 天</span>
              </>
            }
          >
            {loading && !hasData ? <PanelSkeleton /> : <PublishDonut records={records} />}
          </Panel>
        </div>

        {/* 进行中的任务 */}
        <div className="bento-2x1 enter-item" style={{ animationDelay: '260ms' }}>
          <Panel
            title="进行中的任务"
            className="h-full"
            extra={
              <Link to="/tasks" className="text-[13px] text-accent hover:underline">
                任务中心 →
              </Link>
            }
          >
            {loading && !hasData ? (
              <PanelSkeleton />
            ) : activeTasks.length === 0 ? (
              <p className="py-6 text-center text-[13px] text-ink-3">暂无进行中的任务</p>
            ) : (
              <div className="space-y-3">
                {activeTasks.map((t, i) => (
                  <div
                    key={t.id}
                    className="enter-item"
                    style={{ animationDelay: `${Math.min(i * 40, 240)}ms` }}
                  >
                    <div className="flex items-center gap-2">
                      <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-fill text-ink-3">
                        {t.type === 'download' ? (
                          <DownloadSimple size={13} />
                        ) : (
                          <PaperPlaneTilt size={13} />
                        )}
                      </span>
                      <span className="min-w-0 flex-1 truncate text-[13px] text-ink-1">{t.title}</span>
                      <StatusBadge status={t.status} />
                    </div>
                    <div className="mt-1.5 pl-8">
                      {t.type === 'download' ? (
                        <Progress
                          percent={t.progress / 100}
                          status={t.status === 'failed' ? 'error' : 'normal'}
                        />
                      ) : (
                        <span className="text-xs text-ink-4">
                          {t.status === 'pending' ? '排队等待中' : '发布执行中'}
                        </span>
                      )}
                      <div className="mt-1 flex items-center justify-between text-xs text-ink-4">
                        {t.needs_verify ? (
                          <span className="text-accent">等待短信验证码</span>
                        ) : (
                          <span className="tnum">
                            {t.type === 'download' ? `${Math.round(t.progress)}%` : ''}
                          </span>
                        )}
                        <span>{formatRelative(t.created_at)}</span>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </Panel>
        </div>

        {/* 最近视频（横向滚动） */}
        <div className="bento-2x1 enter-item" style={{ animationDelay: '300ms' }}>
          <Panel
            title="最近视频"
            className="h-full"
            extra={
              <Link to="/videos" className="text-[13px] text-accent hover:underline">
                全部视频 →
              </Link>
            }
          >
            {loading && !hasData ? (
              <PanelSkeleton />
            ) : recentVideos.length === 0 ? (
              <EmptyState
                icon={<VideoCamera size={26} />}
                title="视频库还是空的"
                desc="去下载抖音视频，或上传本地视频"
                action={
                  <>
                    <Button variant="primary" onClick={() => navigate('/download')}>
                      去下载
                    </Button>
                    <Button onClick={() => navigate('/videos')}>上传视频</Button>
                  </>
                }
              />
            ) : (
              <div className="flex gap-3 overflow-x-auto pb-2">
                {recentVideos.map((v, i) => (
                  <Link
                    key={v.id}
                    to="/videos"
                    className="enter-item group w-36 shrink-0"
                    style={{ animationDelay: `${Math.min(i * 40, 240)}ms` }}
                  >
                    <div className="aspect-video overflow-hidden rounded-lg bg-fill">
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
                    </div>
                    <div className="mt-2 truncate text-[13px] text-ink-1">{v.title}</div>
                    <div className="mt-0.5 text-xs text-ink-4">{formatRelative(v.created_at)}</div>
                  </Link>
                ))}
              </div>
            )}
          </Panel>
        </div>
        </div>
      ) : null}
    </div>
  )
}
