import { useCallback, useEffect, useRef, useState } from 'react'
import {
  Broadcast,
  Eye,
  NotePencil,
  Play,
  Plus,
  StopCircle,
  Trash,
  WarningCircle,
} from '@phosphor-icons/react'
import {
  createRecording,
  createWatcher,
  deleteRecording,
  deleteWatcher,
  errorMessage,
  fetchRecording,
  fetchRecordings,
  fetchWatchers,
  formatElapsed,
  formatSize,
  formatTime,
  FPS_OPTIONS,
  fpsLabel,
  platformLabel,
  QUALITY_OPTIONS,
  qualityLabel,
  recordingPreviewUrl,
  renameRecording,
  stopRecording,
  updateWatcher,
  type Recording,
  type RecordingStatus,
  type Watcher,
} from '../api'
import {
  Button,
  Checkbox,
  confirmDialog,
  EmptyState,
  Input,
  Modal,
  PageHeader,
  Panel,
  PlatformIcon,
  Select,
  Switch,
  Tag,
  toast,
} from '../ui'
import type { TagColor } from '../ui/Tag'
import RequestState from '../ui/RequestState'

const STATUS_TAG: Record<RecordingStatus, { label: string; color: TagColor }> = {
  recording: { label: '录制中', color: 'red' },
  completed: { label: '已完成', color: 'green' },
  stopped: { label: '已停止', color: 'orange' },
  failed: { label: '失败', color: 'red' },
}

function StatusTag({ status }: { status: RecordingStatus }) {
  const s: { label: string; color: TagColor } = STATUS_TAG[status] ?? {
    label: status,
    color: 'gray',
  }
  return <Tag color={s.color}>{s.label}</Tag>
}

/** 录制参数选择（画质 + 帧率），新建录制和添加监控共用 */
function QualityFpsPicker({
  quality,
  fps,
  onQuality,
  onFps,
}: {
  quality: string
  fps: number
  onQuality: (v: string) => void
  onFps: (v: number) => void
}) {
  return (
    <>
      <Select
        value={quality}
        onChange={onQuality}
        options={QUALITY_OPTIONS.map((q) => ({ value: q.value, label: `画质 · ${q.label}` }))}
        className="w-full sm:w-36"
      />
      <Select
        value={String(fps)}
        onChange={(v: string) => onFps(Number(v))}
        options={FPS_OPTIONS.map((f) => ({ value: String(f.value), label: f.label }))}
        className="w-full sm:w-32"
      />
    </>
  )
}

export default function RecordPage() {
  const [recordings, setRecordings] = useState<Recording[]>([])
  const [watchers, setWatchers] = useState<Watcher[]>([])
  const [loading, setLoading] = useState(true)
  const [hasData, setHasData] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)

  // 新建录制表单
  const [url, setUrl] = useState('')
  const [title, setTitle] = useState('')
  const [quality, setQuality] = useState('best')
  const [fps, setFps] = useState(0)
  const [starting, setStarting] = useState(false)
  const [stoppingIds, setStoppingIds] = useState<Set<number>>(new Set())
  const stopRequestedIdsRef = useRef<Set<number>>(new Set())
  const [formError, setFormError] = useState<string | null>(null)

  // 边录边看
  const [previewing, setPreviewing] = useState<Recording | null>(null)

  // 改名
  const [renaming, setRenaming] = useState<Recording | null>(null)
  const [renameTitle, setRenameTitle] = useState('')
  const [renameSaving, setRenameSaving] = useState(false)

  // 删除
  const [deleting, setDeleting] = useState<Recording | null>(null)
  const [deleteFile, setDeleteFile] = useState(false)
  const [deleteBusy, setDeleteBusy] = useState(false)

  // 添加监控
  const [watchOpen, setWatchOpen] = useState(false)
  const [watchUrl, setWatchUrl] = useState('')
  const [watchName, setWatchName] = useState('')
  const [watchQuality, setWatchQuality] = useState('best')
  const [watchFps, setWatchFps] = useState(0)
  const [watchBusy, setWatchBusy] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [recs, ws] = await Promise.all([fetchRecordings(), fetchWatchers()])
      setRecordings(recs)
      setWatchers(ws)
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

  const active = recordings.filter((r) => r.status === 'recording')
  const history = recordings.filter((r) => r.status !== 'recording')
  const activeIdsKey = active.map((r) => r.id).join(',')

  // 有录制中任务时，每 3 秒轮询各任务详情
  useEffect(() => {
    if (!activeIdsKey) return
    const ids = activeIdsKey.split(',').map(Number)
    const timer = window.setInterval(async () => {
      const updates = await Promise.all(
        ids.map(async (id) => {
          try {
            return await fetchRecording(id)
          } catch {
            return null // 轮询失败静默重试
          }
        }),
      )
      const fresh = updates.filter(
        (r): r is Recording => r !== null && !stopRequestedIdsRef.current.has(r.id),
      )
      if (fresh.length === 0) return
      setRecordings((prev) => {
        const map = new Map(fresh.map((r) => [r.id, r]))
        return prev.map((r) => map.get(r.id) ?? r)
      })
    }, 3000)
    return () => window.clearInterval(timer)
  }, [activeIdsKey])

  // 有启用的监控时，每 10 秒同步 watcher 状态（live 灯、自动录制的关联任务）
  useEffect(() => {
    if (!watchers.some((w) => w.enabled)) return
    const timer = window.setInterval(async () => {
      try {
        setWatchers(await fetchWatchers())
      } catch {
        // 静默重试
      }
    }, 10000)
    return () => window.clearInterval(timer)
  }, [watchers.some((w) => w.enabled)]) // eslint-disable-line react-hooks/exhaustive-deps

  // 自动录制开的新任务也要进列表：有 watcher 关联录制时低频全量刷新
  useEffect(() => {
    if (!watchers.some((w) => w.active_recording_id)) return
    const timer = window.setInterval(async () => {
      try {
        setRecordings(await fetchRecordings())
      } catch {
        // 静默重试
      }
    }, 5000)
    return () => window.clearInterval(timer)
  }, [watchers.some((w) => w.active_recording_id)]) // eslint-disable-line react-hooks/exhaustive-deps

  const handleStart = async () => {
    if (!url.trim() || starting) return
    setFormError(null)
    setStarting(true)
    try {
      const rec = await createRecording(url.trim(), title.trim() || undefined, quality, fps)
      setRecordings((prev) => [rec, ...prev])
      setUrl('')
      setTitle('')
      toast.success(`已开始录制「${rec.title || rec.url}」`)
    } catch (e) {
      const message = errorMessage(e)
      setFormError(message)
      toast.error(message)
    } finally {
      setStarting(false)
    }
  }

  const handleStop = async (r: Recording) => {
    if (stoppingIds.has(r.id)) return
    const ok = await confirmDialog({
      title: '停止录制',
      content: `确定要停止录制「${r.title || r.url}」吗？已录制的内容会保留并入库。`,
      okText: '确认停止',
      cancelText: '取消',
      danger: true,
    })
    if (!ok) return
    stopRequestedIdsRef.current.add(r.id)
    setStoppingIds((prev) => new Set(prev).add(r.id))
    try {
      const rec = await stopRecording(r.id)
      setRecordings((prev) => prev.map((x) => (x.id === rec.id ? rec : x)))
      toast.success('已停止录制')
    } catch (e) {
      stopRequestedIdsRef.current.delete(r.id)
      toast.error(`停止失败：${errorMessage(e)}`)
    } finally {
      setStoppingIds((prev) => {
        const next = new Set(prev)
        next.delete(r.id)
        return next
      })
    }
  }

  const handleRename = async () => {
    if (!renaming || renameSaving) return
    const t = renameTitle.trim()
    if (!t) {
      toast.warning('标题不能为空')
      return
    }
    setRenameSaving(true)
    try {
      const rec = await renameRecording(renaming.id, t)
      setRecordings((prev) => prev.map((x) => (x.id === rec.id ? rec : x)))
      toast.success('已改名')
      setRenaming(null)
    } catch (e) {
      toast.error(`改名失败：${errorMessage(e)}`)
    } finally {
      setRenameSaving(false)
    }
  }

  const handleDelete = async () => {
    if (!deleting) return
    setDeleteBusy(true)
    try {
      await deleteRecording(deleting.id, deleteFile)
      setRecordings((prev) => prev.filter((r) => r.id !== deleting.id))
      toast.success('已删除')
      setDeleting(null)
    } catch (e) {
      toast.error(`删除失败：${errorMessage(e)}`)
    } finally {
      setDeleteBusy(false)
    }
  }

  // ---------- 监控 ----------

  const handleAddWatcher = async () => {
    if (!watchUrl.trim() || watchBusy) return
    setWatchBusy(true)
    try {
      const w = await createWatcher(watchUrl.trim(), watchName.trim(), watchQuality, watchFps)
      setWatchers((prev) => [w, ...prev])
      setWatchOpen(false)
      setWatchUrl('')
      setWatchName('')
      toast.success(`已开始监控「${w.name || w.url}」，开播自动录制`)
    } catch (e) {
      toast.error(`添加失败：${errorMessage(e)}`)
    } finally {
      setWatchBusy(false)
    }
  }

  const handleToggleWatcher = async (w: Watcher, enabled: boolean) => {
    try {
      const updated = await updateWatcher(w.id, { enabled })
      setWatchers((prev) => prev.map((x) => (x.id === updated.id ? updated : x)))
      toast.success(enabled ? `已开启监控「${w.name || w.url}」` : '已暂停监控')
    } catch (e) {
      toast.error(`操作失败：${errorMessage(e)}`)
    }
  }

  const handleDeleteWatcher = async (w: Watcher) => {
    const ok = await confirmDialog({
      title: '删除监控',
      content: `确定要删除对「${w.name || w.url}」的监控吗？正在进行中的自动录制不受影响。`,
      okText: '确认删除',
      danger: true,
    })
    if (!ok) return
    try {
      await deleteWatcher(w.id)
      setWatchers((prev) => prev.filter((x) => x.id !== w.id))
      toast.success('已删除')
    } catch (e) {
      toast.error(`删除失败：${errorMessage(e)}`)
    }
  }

  return (
    <div>
      <PageHeader
        title="直播录制"
        desc="粘贴直播间链接开始录制，完成后自动入库视频库；支持画质/帧率选择与主播开播自动录制"
        extra={
          <Button variant="primary" icon={<Plus size={15} weight="bold" />} onClick={() => setWatchOpen(true)}>
            添加监控
          </Button>
        }
      />

      <RequestState error={loadError} hasData={hasData} loading={loading} onRetry={load} className="mb-4" />

      {/* 新建录制 */}
      <Panel title="新建录制">
        <div className="flex flex-col gap-3 lg:flex-row">
          <Input
            value={url}
            onChange={setUrl}
            placeholder="直播间链接，支持直接粘贴分享文案（自动提取链接）"
            allowClear
            className="flex-1"
          />
          <Input
            value={title}
            onChange={setTitle}
            placeholder="标题（可选，留空自动命名）"
            allowClear
            className="flex-1"
          />
          <QualityFpsPicker quality={quality} fps={fps} onQuality={setQuality} onFps={setFps} />
          <Button
            variant="danger"
            loading={starting}
            disabled={!url.trim()}
            onClick={handleStart}
            className="shrink-0"
          >
            开始录制
          </Button>
        </div>
        {formError && <p className="mt-2 text-[13px] text-danger">启动失败：{formError}</p>}
      </Panel>

      {/* 自动录制（主播监控） */}
      {(watchers.length > 0 || (hasData && !loading)) && (
        <div className="mt-6">
          <Panel
            title="自动录制"
            extra={<span className="text-xs text-ink-4">开播自动按预设参数录制，每分钟检查一次</span>}
          >
            {watchers.length === 0 ? (
              <p className="py-2 text-[13px] text-ink-3">
                还没有监控。点右上角「添加监控」，主播开播时会自动按预设画质和帧率录制。
              </p>
            ) : (
              <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                {watchers.map((w) => {
                  const off = !w.enabled
                  return (
                    <div key={w.id} className={`vh-card p-4 ${off ? 'opacity-55' : ''}`}>
                      <div className="flex items-center gap-2">
                        <PlatformIcon platform={w.platform} size={20} />
                        <span className="min-w-0 flex-1 truncate font-medium text-ink-1" title={w.name || w.url}>
                          {w.name || w.url}
                        </span>
                        {w.active_recording_id ? (
                          <Tag
                            color="red"
                            icon={<span className="pulse-dot inline-block h-1.5 w-1.5 rounded-full bg-current" />}
                          >
                            自动录制中
                          </Tag>
                        ) : w.live ? (
                          <Tag color="green">已开播</Tag>
                        ) : (
                          <Tag color="gray">{off ? '已暂停' : '监控中'}</Tag>
                        )}
                      </div>
                      <div className="mt-1.5 truncate text-xs text-ink-4" title={w.url}>
                        {w.url}
                      </div>
                      <div className="mt-2 flex items-center gap-2 text-xs text-ink-3">
                        <Tag color="accent">{qualityLabel(w.quality)}</Tag>
                        <Tag color="gray">{fpsLabel(w.fps)}</Tag>
                        {w.last_checked_at && (
                          <span className="tnum ml-auto text-ink-4">
                            检查于 {formatTime(w.last_checked_at).slice(5)}
                          </span>
                        )}
                      </div>
                      <div className="mt-3 flex items-center justify-between border-t border-line-soft pt-3">
                        <Switch
                          checked={!!w.enabled}
                          onChange={(v) => handleToggleWatcher(w, v)}
                          label={w.enabled ? '暂停监控' : '开启监控'}
                        />
                        <Button
                          variant="text"
                          size="sm"
                          style={{ color: 'var(--danger)' }}
                          icon={<Trash size={13} />}
                          onClick={() => handleDeleteWatcher(w)}
                        >
                          删除
                        </Button>
                      </div>
                    </div>
                  )
                })}
              </div>
            )}
          </Panel>
        </div>
      )}

      {/* 录制中 */}
      {active.length > 0 && (
        <div className="mt-6">
          <h2 className="section-title mb-3">录制中</h2>
          <div className="space-y-3">
            {active.map((r, i) => (
              <div
                key={r.id}
                className="vh-card enter-item p-4"
                style={{ animationDelay: `${Math.min(i * 50, 200)}ms` }}
              >
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <Tag
                        color="red"
                        icon={
                          <span className="pulse-dot inline-block h-1.5 w-1.5 rounded-full bg-current" />
                        }
                      >
                        LIVE
                      </Tag>
                      <span className="truncate font-medium text-ink-1" title={r.title || r.url}>
                        {r.title || r.url}
                      </span>
                    </div>
                    <div className="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-ink-3">
                      <PlatformIcon platform={r.platform} size={16} />
                      <span>{platformLabel(r.platform)}</span>
                      <span className="mx-1 text-ink-4">|</span>
                      <span>
                        已录 <span className="tnum font-display">{formatElapsed(r.elapsed)}</span>
                      </span>
                      <span className="mx-1 text-ink-4">|</span>
                      <span className="tnum">{formatSize(r.size)}</span>
                      <span className="mx-1 text-ink-4">|</span>
                      <span>
                        {qualityLabel(r.quality)} · {fpsLabel(r.fps)}
                      </span>
                    </div>
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    <Button variant="secondary" icon={<Eye size={15} />} onClick={() => setPreviewing(r)}>
                      边录边看
                    </Button>
                    <Button
                      variant="danger"
                      icon={<StopCircle size={15} />}
                      loading={stoppingIds.has(r.id)}
                      onClick={() => handleStop(r)}
                    >
                      {stoppingIds.has(r.id) ? '停止中…' : '停止录制'}
                    </Button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 录制历史 */}
      <div className="mt-6">
        {loading && !hasData ? (
          <Panel title="录制历史">
            <div className="space-y-2.5">
              {[0, 1, 2, 3].map((i) => (
                <div key={i} className="skeleton h-4" style={{ width: `${88 - i * 12}%` }} />
              ))}
            </div>
          </Panel>
        ) : !hasData ? null : history.length === 0 ? (
          <Panel pad="none">
            <EmptyState
              icon={<Broadcast size={28} />}
              title="暂无录制记录"
              desc="粘贴直播间链接开始你的第一次录制"
            />
          </Panel>
        ) : (
          <Panel title="录制历史" pad="none" className="overflow-x-auto">
            <table className="vh-table">
              <thead>
                <tr>
                  <th>标题</th>
                  <th>平台</th>
                  <th>状态</th>
                  <th>参数</th>
                  <th>时长</th>
                  <th>大小</th>
                  <th>时间</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {history.map((r) => (
                  <tr key={r.id}>
                    <td>
                      <div className="min-w-[200px]">
                        <div className="flex items-center gap-2">
                          <span
                            className="max-w-[300px] truncate text-ink-1"
                            title={r.title || r.url}
                          >
                            {r.title || r.url}
                          </span>
                          {(r.status === 'completed' || r.status === 'stopped') &&
                            r.video_id != null && <Tag color="accent">已入库</Tag>}
                        </div>
                        {r.error && <div className="mt-0.5 text-xs text-danger">{r.error}</div>}
                      </div>
                    </td>
                    <td>
                      <span className="inline-flex items-center gap-1.5">
                        <PlatformIcon platform={r.platform} size={14} />
                        {platformLabel(r.platform)}
                      </span>
                    </td>
                    <td>
                      <span className="flex items-center gap-1">
                        <StatusTag status={r.status} />
                        {r.status === 'failed' && r.error && (
                          <WarningCircle size={14} className="text-danger" data-tip={r.error} />
                        )}
                      </span>
                    </td>
                    <td>
                      <span className="whitespace-nowrap text-xs text-ink-3">
                        {qualityLabel(r.quality)} · {fpsLabel(r.fps)}
                      </span>
                    </td>
                    <td>
                      <span className="tnum font-display">{formatElapsed(r.duration)}</span>
                    </td>
                    <td>
                      <span className="tnum">{formatSize(r.size)}</span>
                    </td>
                    <td>
                      <span className="tnum text-xs text-ink-3">{formatTime(r.created_at)}</span>
                    </td>
                    <td>
                      <span className="flex items-center gap-1">
                        {r.video_id != null && (
                          <Button
                            variant="text"
                            size="sm"
                            icon={<Play size={13} />}
                            onClick={() =>
                              window.open(`/api/videos/${r.video_id}/file`, '_blank')
                            }
                            data-tip="播放（已入库视频）"
                          >
                            播放
                          </Button>
                        )}
                        <Button
                          variant="text"
                          size="sm"
                          icon={<NotePencil size={13} />}
                          onClick={() => {
                            setRenaming(r)
                            setRenameTitle(r.title || '')
                          }}
                        >
                          改名
                        </Button>
                        <Button
                          variant="text"
                          size="sm"
                          style={{ color: 'var(--danger)' }}
                          onClick={() => {
                            setDeleteFile(false)
                            setDeleting(r)
                          }}
                        >
                          删除
                        </Button>
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Panel>
        )}
      </div>

      {/* 边录边看弹窗 */}
      <Modal
        open={!!previewing}
        onClose={() => setPreviewing(null)}
        title={previewing ? `边录边看 · ${previewing.title || previewing.url}` : ''}
        width={860}
      >
        {previewing && (
          <div>
            <video
              src={recordingPreviewUrl(previewing.id)}
              controls
              autoPlay
              className="max-h-[60vh] w-full rounded-lg bg-black"
            />
            <p className="mt-2 text-xs text-ink-4">
              直播画面有数秒延迟属正常；卡住时点播放器的暂停再播放即可刷新到最新。
            </p>
          </div>
        )}
      </Modal>

      {/* 改名弹窗 */}
      <Modal
        open={!!renaming}
        onClose={() => setRenaming(null)}
        title="重命名录制"
        width={420}
        footer={
          <>
            <Button variant="ghost" onClick={() => setRenaming(null)} disabled={renameSaving}>
              取消
            </Button>
            <Button variant="primary" loading={renameSaving} onClick={handleRename}>
              保存
            </Button>
          </>
        }
      >
        <Input
          value={renameTitle}
          onChange={setRenameTitle}
          maxLength={80}
          autoFocus
          placeholder="录制标题"
          onKeyDown={(e) => e.key === 'Enter' && handleRename()}
        />
        <p className="mt-2 text-xs text-ink-4">已入库的视频会同步改名。</p>
      </Modal>

      {/* 添加监控弹窗 */}
      <Modal
        open={watchOpen}
        onClose={() => setWatchOpen(false)}
        title="添加自动录制监控"
        width={440}
        footer={
          <>
            <Button variant="ghost" onClick={() => setWatchOpen(false)} disabled={watchBusy}>
              取消
            </Button>
            <Button
              variant="primary"
              loading={watchBusy}
              disabled={!watchUrl.trim()}
              onClick={handleAddWatcher}
            >
              开始监控
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <div>
            <div className="mb-1.5 text-[13px] text-ink-2">直播间链接</div>
            <Input
              value={watchUrl}
              onChange={setWatchUrl}
              placeholder="粘贴直播间链接或短链，自动识别平台"
              allowClear
            />
          </div>
          <div>
            <div className="mb-1.5 text-[13px] text-ink-2">主播名（可选，用于录制标题）</div>
            <Input value={watchName} onChange={setWatchName} placeholder="如：老罗的直播间" allowClear />
          </div>
          <div>
            <div className="mb-1.5 text-[13px] text-ink-2">录制参数</div>
            <div className="flex gap-3">
              <QualityFpsPicker quality={watchQuality} fps={watchFps} onQuality={setWatchQuality} onFps={setWatchFps} />
            </div>
          </div>
          <p className="text-xs leading-relaxed text-ink-4">
            每分钟检查一次开播状态。开播后自动开始录制，下播自动收尾入库；再次开播会继续录新的一场。
          </p>
        </div>
      </Modal>

      {/* 删除确认弹窗 */}
      <Modal
        open={!!deleting}
        onClose={() => setDeleting(null)}
        title="确认删除"
        width={420}
        footer={
          <>
            <Button variant="ghost" onClick={() => setDeleting(null)} disabled={deleteBusy}>
              取消
            </Button>
            <Button variant="danger" loading={deleteBusy} onClick={handleDelete}>
              确认删除
            </Button>
          </>
        }
      >
        <p className="text-[13px] leading-relaxed text-ink-2">
          确定要删除录制记录「{deleting?.title || deleting?.url}」吗？
        </p>
        <div className="mt-3">
          <Checkbox checked={deleteFile} onChange={setDeleteFile} label="同时删除视频文件" />
        </div>
      </Modal>
    </div>
  )
}
