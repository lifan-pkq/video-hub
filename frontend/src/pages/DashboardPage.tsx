import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Broadcast, DownloadSimple, FilmSlate, MagicWand, Package, SquaresFour } from '@phosphor-icons/react'
import {
  errorMessage,
  fetchHealth,
  fetchStats,
  fetchTasks,
  formatRelative,
  formatSize,
  type HealthReport,
  type Stats,
  type TaskSummary,
} from '../api'
import { Button, EmptyState, PageHeader, Panel, StatCard } from '../ui'
import RequestState from '../ui/RequestState'
import StatusBadge from '../components/StatusBadge'

function healthRows(health: HealthReport): Array<{ label: string; ok: boolean; detail: string }> {
  const asrLabel = health.asr === 'remote' ? '使用外部转写接口' : health.asr === 'whisper' ? '本机按需转写，长音频分段' : '未安装 faster-whisper，智能剪辑无法转写'
  return [
    { label: 'ffmpeg', ok: !!health.ffmpeg, detail: health.ffmpeg ? '剪辑和导出可用' : '未安装，剪辑与导出无法出片' },
    { label: '文本模型', ok: !!health.llm, detail: health.llm ? '抖音选段和标题可用' : '未配置 VIDEO_HUB_AI_*，选段会用本地规则' },
    { label: '画面模型', ok: !!health.vision, detail: health.vision ? '抽帧说明可用' : '未配置视觉模型，抽帧仍会保留' },
    { label: '语音识别', ok: health.asr === 'remote' || health.asr === 'whisper', detail: asrLabel },
    {
      label: '磁盘剩余',
      ok: (health.disk_free_mb ?? 1024) >= 512,
      detail: health.disk_free_mb == null
        ? '还没读到剩余空间'
        : health.disk_free_mb < 512
          ? `只剩 ${health.disk_free_mb} MB，渲染和导出会停下来`
          : `约 ${health.disk_free_mb} MB`,
    },
  ]
}

export default function DashboardPage() {
  const [stats, setStats] = useState<Stats | null>(null)
  const [tasks, setTasks] = useState<TaskSummary[]>([])
  const [health, setHealth] = useState<HealthReport | null>(null)
  const [loading, setLoading] = useState(true)
  const [hasData, setHasData] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [statsValue, taskRows, healthValue] = await Promise.all([fetchStats(), fetchTasks(), fetchHealth()])
      setStats(statsValue)
      setTasks(taskRows)
      setHealth(healthValue)
      setHasData(true)
      setError(null)
    } catch (cause) {
      setError(errorMessage(cause))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load() }, [load])
  const recentTasks = tasks.slice(0, 6)

  return (
    <div>
      <PageHeader
        title="工作台概览"
        desc="看库存、任务和本机能力。要处理具体素材，去创作工作台。"
        extra={<Link to="/workbench"><Button variant="primary" icon={<SquaresFour size={15} />}>打开工作台</Button></Link>}
      />
      <div className="vh-quick-actions" aria-label="快捷入口">
        <Link to="/workbench">创作工作台</Link>
        <Link to="/edit">智能剪辑</Link>
        <Link to="/download">下载素材</Link>
        <Link to="/tasks">任务中心</Link>
      </div>
      <RequestState error={error} hasData={hasData} loading={loading} onRetry={load} className="mb-4" />
      {stats && (
        <div className="mb-5 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <Link to="/videos"><StatCard label="视频素材" value={stats.videos.count} sub={`${stats.videos.high_quality} 条高质 · ${formatSize(stats.videos.size)}`} icon={<FilmSlate size={18} />} /></Link>
          <Link to="/videos"><StatCard label="已理解素材" value={stats.videos.analyzed} sub="可直接进剪辑或导出" icon={<MagicWand size={18} />} /></Link>
          <Link to="/record"><StatCard label="录制中" value={stats.recordings.active} sub={`共 ${stats.recordings.total} 条记录`} icon={<Broadcast size={18} />} /></Link>
          <Link to="/tasks"><StatCard label="进行中任务" value={stats.tasks.running} sub={`今天新增 ${stats.tasks.today} 个`} icon={<DownloadSimple size={18} />} /></Link>
        </div>
      )}
      <div className="grid gap-5 xl:grid-cols-2">
        <Panel title="最近任务" extra={<Link to="/tasks" className="text-xs text-accent">任务中心 →</Link>}>
          {recentTasks.length === 0 ? (
            <EmptyState icon={<DownloadSimple size={24} />} title="还没有任务" desc="下载或剪辑开始后，进度会出现在这里。" action={<Link to="/download"><Button size="sm">去下载</Button></Link>} />
          ) : (
            <ul className="divide-y divide-line-soft">
              {recentTasks.map((task) => (
                <li key={task.id} className="flex items-center gap-2 py-2.5">
                  <span className="min-w-0 flex-1 truncate text-[13px] text-ink-1">{task.title}</span>
                  <StatusBadge status={task.status} />
                  <span className="shrink-0 text-[11px] text-ink-4">{formatRelative(task.created_at)}</span>
                </li>
              ))}
            </ul>
          )}
        </Panel>
        <Panel title="本机能力" extra={<Link to="/settings/system" className="text-xs text-accent">系统维护 →</Link>}>
          {!health ? (
            <p className="py-8 text-center text-[13px] text-ink-3">还没有读到健康状态</p>
          ) : (
            <ul className="space-y-2">
              {healthRows(health).map((row) => (
                <li key={row.label} className="flex items-start gap-2 rounded-lg bg-fill px-3 py-2">
                  <span className={row.ok ? 'status-dot mt-1' : 'status-dot is-off mt-1'} />
                  <span className="min-w-0">
                    <strong className="block text-[13px] text-ink-1">{row.label}</strong>
                    <span className="text-[11px] text-ink-4">{row.detail}</span>
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>
      <div className="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Link to="/download" className="vh-card flex items-center gap-3 p-4 hover:border-accent"><DownloadSimple size={18} /><span className="text-sm text-ink-1">获取视频</span></Link>
        <Link to="/record" className="vh-card flex items-center gap-3 p-4 hover:border-accent"><Broadcast size={18} /><span className="text-sm text-ink-1">直播录制</span></Link>
        <Link to="/edit" className="vh-card flex items-center gap-3 p-4 hover:border-accent"><FilmSlate size={18} /><span className="text-sm text-ink-1">智能剪辑</span></Link>
        <Link to="/export" className="vh-card flex items-center gap-3 p-4 hover:border-accent"><Package size={18} /><span className="text-sm text-ink-1">封面与导出</span></Link>
      </div>
    </div>
  )
}
