import { useCallback, useEffect, useState } from 'react'
import {
  ArrowClockwise,
  Broom,
  ClockCounterClockwise,
  Database,
  HardDrives,
  WarningCircle,
} from '@phosphor-icons/react'
import {
  cleanMaintenanceLogs,
  clearAllVideos,
  errorMessage,
  fetchHealth,
  fetchMaintenanceStatus,
  fetchSystemStatus,
  formatSize,
  saveMaintenanceSettings,
  type HealthReport,
  type MaintenanceSettings,
  type MaintenanceStatus,
  type SystemStatus,
} from '../api'
import {
  Alert,
  Button,
  Input,
  PageHeader,
  Panel,
  Progress,
  Select,
  Switch,
  confirmDialog,
  toast,
} from '../ui'

const INTERVALS = [
  { value: '6', label: '每 6 小时' },
  { value: '12', label: '每 12 小时' },
  { value: '24', label: '每天' },
  { value: '72', label: '每 3 天' },
  { value: '168', label: '每周' },
]

function percent(used: number, total: number): number {
  return total > 0 ? Math.min(1, used / total) : 0
}

function safeResult(value: MaintenanceSettings['last_result']): string {
  if (!value) return ''
  if (typeof value === 'string') return value.slice(0, 500)
  const hidden = /key|token|secret|password|cookie|authorization/i
  return Object.entries(value)
    .filter(([key]) => !hidden.test(key))
    .map(([key, item]) => `${key}: ${typeof item === 'object' ? JSON.stringify(item) : String(item)}`)
    .join('\n') || '上次清理没有可展示的明细'
}

export default function MaintenancePage() {
  const [status, setStatus] = useState<MaintenanceStatus | null>(null)
  const [settings, setSettings] = useState<MaintenanceSettings | null>(null)
  const [system, setSystem] = useState<SystemStatus | null>(null)
  const [health, setHealth] = useState<HealthReport | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [cleaning, setCleaning] = useState<'logs' | 'videos' | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [data, systemStatus, healthStatus] = await Promise.all([
        fetchMaintenanceStatus(),
        fetchSystemStatus().catch(() => null),
        fetchHealth().catch(() => null),
      ])
      setStatus(data)
      setSettings(data.settings)
      setSystem(systemStatus)
      setHealth(healthStatus)
    } catch (e) {
      toast.error('读取维护状态失败：' + errorMessage(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const cleanLogs = async () => {
    if (!settings) return
    const ok = await confirmDialog({
      title: '清理日志与临时文件？',
      content: `会删除超过 ${settings.retention_days} 天的日志和临时文件，不删除视频库。`,
      okText: '开始清理',
    })
    if (!ok) return
    setCleaning('logs')
    try {
      const result = await cleanMaintenanceLogs(settings.retention_days)
      toast.success('已清理 ' + result.files + ' 个文件，释放 ' + formatSize(result.bytes))
      await load()
    } catch (e) {
      toast.error('清理日志失败：' + errorMessage(e))
    } finally {
      setCleaning(null)
    }
  }

  const removeVideos = async () => {
    const ok = await confirmDialog({
      title: '清空所有视频？',
      content: '会删除视频库全部视频、封面和导入台账，不可恢复。正在进行的任务或录制会阻止操作。',
      okText: '确认清空',
    })
    if (!ok) return
    setCleaning('videos')
    try {
      const result = await clearAllVideos(false)
      toast.success('已删除 ' + result.videos + ' 个视频，释放 ' + formatSize(result.bytes))
      await load()
    } catch (e) {
      toast.error('清空失败：' + errorMessage(e))
    } finally {
      setCleaning(null)
    }
  }

  const save = async () => {
    if (!settings) return
    if (settings.clear_videos) {
      const ok = await confirmDialog({
        title: '定时任务会清空视频库',
        content: '已勾选「同时清空视频」。到点后会删除全部视频，不能恢复。',
        okText: '仍然保存',
        danger: true,
      })
      if (!ok) return
    }
    setSaving(true)
    try {
      const next = await saveMaintenanceSettings({
        enabled: settings.enabled,
        interval_hours: settings.interval_hours,
        retention_days: settings.retention_days,
        clean_logs: settings.clean_logs,
        clear_videos: settings.clear_videos,
      })
      setSettings(next)
      toast.success(next.enabled ? '定时清理已开启' : '定时清理已关闭')
      await load()
    } catch (e) {
      toast.error('保存失败：' + errorMessage(e))
    } finally {
      setSaving(false)
    }
  }

  const storage = status?.storage
  const totalMedia = (storage?.videos.files_size ?? 0) + (storage?.videos.covers_size ?? 0)

  return (
    <div>
      <PageHeader
        title="系统维护"
        desc="查看存储占用，清理日志和临时文件，或设置定时自动清理。"
        extra={<Button variant="ghost" icon={<ArrowClockwise size={15} />} loading={loading} onClick={() => void load()}>刷新</Button>}
      />

      {!status && loading ? (
        <div className="grid gap-4 md:grid-cols-2">
          {[0, 1, 2, 3].map((item) => <div key={item} className="vh-card h-32 p-5 skeleton" />)}
        </div>
      ) : !status || !storage ? (
        <Alert type="error">无法读取维护状态，请稍后重试。</Alert>
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          <Panel title="运行状态" extra={<Database size={18} className="text-ink-3" />} className="lg:col-span-2">
            <div className="grid gap-3 md:grid-cols-3">
              <div className="rounded-xl bg-fill px-3 py-3 text-[12px] text-ink-3">
                <div className="text-ink-4">内存</div>
                <div className="mt-1 text-ink-1">{system?.memory.percent != null ? `${system.memory.percent}%` : '未知'}{system?.memory.used_mb != null ? ` · ${system.memory.used_mb} / ${system.memory.total_mb} MB` : ''}</div>
              </div>
              <div className="rounded-xl bg-fill px-3 py-3 text-[12px] text-ink-3">
                <div className="text-ink-4">磁盘</div>
                <div className="mt-1 text-ink-1">{system?.disk.percent != null ? `${system.disk.percent}%` : '未知'}{system?.disk.free_gb != null ? ` · 剩余 ${system.disk.free_gb} GB` : ''}</div>
              </div>
              <div className="rounded-xl bg-fill px-3 py-3 text-[12px] text-ink-3">
                <div className="text-ink-4">创作能力</div>
                <div className="mt-1 text-ink-1">{health ? `ffmpeg ${health.ffmpeg ? '可用' : '缺失'} · 文本模型 ${health.llm ? '已配置' : '未配置'} · ASR ${health.asr}` : '健康检查未返回'}</div>
              </div>
            </div>
            {!!system?.services.length && (
              <ul className="mt-3 grid gap-2 sm:grid-cols-2">
                {system.services.map((service) => (
                  <li key={service.key} className="flex items-center gap-2 rounded-lg border border-line-soft px-3 py-2 text-[12px]">
                    <span className={service.active ? 'status-dot' : 'status-dot is-off'} />
                    <span className="text-ink-2">{service.name}</span>
                    <span className="ml-auto text-ink-4">{service.active ? '在线' : '未连接'}</span>
                  </li>
                ))}
              </ul>
            )}
            <p className="mt-3 text-[11px] leading-5 text-ink-4">转写在后端进程里按需进行，没有单独的 ASR 或解说引擎。这里不显示密钥、进程号和内网地址。</p>
          </Panel>

          <Panel title="存储占用" extra={<HardDrives size={18} className="text-ink-3" />}>
            <div className="space-y-4">
              <div>
                <div className="mb-1.5 flex items-center justify-between text-[12px]">
                  <span className="text-ink-2">视频与封面</span>
                  <span className="tnum text-ink-3">{formatSize(totalMedia)}</span>
                </div>
                <Progress percent={percent(totalMedia, totalMedia + storage.temp.size + storage.cache.size + storage.logs.size)} />
                <p className="mt-1 text-[11px] text-ink-4">{storage.videos.count} 个视频 · {formatSize(storage.videos.files_size)} 媒体 · {formatSize(storage.videos.covers_size)} 封面</p>
              </div>
              <div className="grid grid-cols-2 gap-2 text-[12px]">
                <div className="rounded-lg bg-fill px-3 py-2">
                  <div className="text-ink-4">任务目录</div>
                  <div className="tnum mt-1 text-ink-2">{formatSize(storage.tasks.size)}</div>
                  <div className="text-[10px] text-ink-4">{storage.tasks.files} 个文件</div>
                </div>
                <div className="rounded-lg bg-fill px-3 py-2">
                  <div className="text-ink-4">临时文件</div>
                  <div className="tnum mt-1 text-ink-2">{formatSize(storage.temp.size)}</div>
                  <div className="text-[10px] text-ink-4">{storage.temp.files} 个文件</div>
                </div>
                <div className="rounded-lg bg-fill px-3 py-2">
                  <div className="text-ink-4">分析缓存</div>
                  <div className="tnum mt-1 text-ink-2">{formatSize(storage.cache.size)}</div>
                  <div className="text-[10px] text-ink-4">{storage.cache.files} 个文件</div>
                </div>
                <div className="rounded-lg bg-fill px-3 py-2">
                  <div className="text-ink-4">日志文件</div>
                  <div className="tnum mt-1 text-ink-2">{formatSize(storage.logs.size)}</div>
                  <div className="text-[10px] text-ink-4">{storage.logs.files} 个文件</div>
                </div>
              </div>
            </div>
          </Panel>

          <Panel title="一键清理" extra={<Broom size={18} className="text-ink-3" />}>
            <div className="space-y-3">
              <div className="rounded-xl border border-line-soft bg-fill p-3">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <div className="text-[13px] font-medium text-ink-1">清理日志与临时文件</div>
                    <p className="mt-1 text-[11px] leading-5 text-ink-4">删除超过保留天数的 worker 日志、临时抽帧/上传文件和旧发布日志；不影响视频库。</p>
                  </div>
                  <Button size="sm" variant="ghost" loading={cleaning === 'logs'} onClick={() => void cleanLogs()}>清理</Button>
                </div>
              </div>
              <div className="rounded-xl border border-danger/20 bg-danger-soft p-3">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <div className="flex items-center gap-1.5 text-[13px] font-medium text-danger"><WarningCircle size={15} />清空所有视频</div>
                    <p className="mt-1 text-[11px] leading-5 text-ink-3">删除视频库全部视频，包括历史解说成片和 AI 生成视频；同时删除 videos 行、封面和导入台账，并解除录制记录里的 video_id。直播录制原片和 AI 图片不会删除。</p>
                  </div>
                  <Button size="sm" variant="danger" loading={cleaning === 'videos'} onClick={() => void removeVideos()}>清空</Button>
                </div>
              </div>
            </div>
          </Panel>

          <Panel title="定时清理" extra={<ClockCounterClockwise size={18} className="text-ink-3" />} className="lg:col-span-2">
            {settings && (
              <div className="space-y-4">
                <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-line-soft bg-fill px-3 py-2.5">
                  <div>
                    <div className="text-[13px] text-ink-1">启用定时清理</div>
                    <p className="mt-0.5 text-[11px] text-ink-4">后台每分钟检查一次，到点后自动执行。</p>
                  </div>
                  <Switch checked={settings.enabled} onChange={(value) => setSettings({ ...settings, enabled: value })} label="启用定时清理" />
                </div>
                <div className="grid gap-3 sm:grid-cols-2">
                  <label className="block">
                    <span className="mb-1.5 block text-xs text-ink-3">执行频率</span>
                    <Select value={String(settings.interval_hours)} onChange={(value) => setSettings({ ...settings, interval_hours: Number(value) || 24 })} options={INTERVALS} />
                  </label>
                  <label className="block">
                    <span className="mb-1.5 block text-xs text-ink-3">日志保留天数</span>
                    <Input type="number" min={1} max={365} value={String(settings.retention_days)} onChange={(value) => setSettings({ ...settings, retention_days: Math.max(1, Math.min(365, Number(value) || 7)) })} />
                  </label>
                </div>
                <div className="grid gap-2 sm:grid-cols-2">
                  <div className="flex items-center justify-between gap-3 rounded-xl border border-line-soft bg-fill px-3 py-2.5">
                    <div>
                      <div className="text-[12px] text-ink-2">清理日志</div>
                      <div className="text-[11px] text-ink-4">包含 worker 日志和旧发布日志</div>
                    </div>
                    <Switch checked={settings.clean_logs} onChange={(value) => setSettings({ ...settings, clean_logs: value })} label="清理日志" />
                  </div>
                  <div className="flex items-center justify-between gap-3 rounded-xl border border-line-soft bg-fill px-3 py-2.5">
                    <div>
                      <div className="text-[12px] text-ink-2">同时清空视频</div>
                      <div className="text-[11px] text-danger">危险：会删除整个视频库</div>
                    </div>
                    <Switch checked={settings.clear_videos} onChange={(value) => setSettings({ ...settings, clear_videos: value })} label="同时清空视频" />
                  </div>
                </div>
                <div className="flex flex-wrap items-center justify-between gap-2 border-t border-line-soft pt-3 text-[11px] text-ink-4">
                  <span>上次运行：{settings.last_run_at || '从未'}</span>
                  <span>下次运行：{settings.next_run_at || '未启用'}</span>
                </div>
                <div className="flex justify-end">
                  <Button variant="primary" loading={saving} onClick={() => void save()}>保存清理设置</Button>
                </div>
              </div>
            )}
          </Panel>

          {status.settings.last_result && (
            <Panel title="上次清理结果" extra={<Database size={18} className="text-ink-3" />} className="lg:col-span-2">
              <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-all rounded-xl bg-fill p-3 text-[11px] leading-5 text-ink-3">{safeResult(status.settings.last_result)}</pre>
            </Panel>
          )}
        </div>
      )}
    </div>
  )
}
