import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Broadcast, Play, StopCircle, Trash } from '@phosphor-icons/react'
import {
  createRecording,
  deleteRecording,
  errorDetail,
  errorMessage,
  fetchRecordings,
  formatDuration,
  formatElapsed,
  formatSize,
  formatTime,
  fpsLabel,
  platformLabel,
  qualityLabel,
  stopRecording,
  FPS_OPTIONS,
  QUALITY_OPTIONS,
  type Problem,
  type Recording,
} from '../api'
import { Button, EmptyState, Input, Modal, PageHeader, Panel, ProblemAlert, Select, confirmDialog, toast } from '../ui'
import RequestState from '../ui/RequestState'
import StatusBadge from '../components/StatusBadge'

function ingestText(record: Recording): string {
  if (record.status === 'recording') {
    return record.starting ? '正在连接直播流，连上后开始计时，停止后自动分段入库' : '录制中，停止后每段都会入库并生成素材卡'
  }
  if (record.segments?.length) {
    const pending = record.segments.filter((item) => item.summary_status === 'running').length
    return pending ? `已入库 ${record.segments.length} 段，${pending} 段素材卡还在生成` : `已入库 ${record.segments.length} 段`
  }
  if (record.video_id) return '已入库到视频素材库'
  if (record.status === 'failed') return '没有入库'
  if (record.status === 'stopped' || record.status === 'completed') return '已停止，还没有可入库的分段'
  return ''
}

export default function RecordPage() {
  const [url, setUrl] = useState('')
  const [title, setTitle] = useState('')
  const [quality, setQuality] = useState('best')
  const [fps, setFps] = useState(0)
  const [records, setRecords] = useState<Recording[]>([])
  const [loading, setLoading] = useState(true)
  const [hasData, setHasData] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [problem, setProblem] = useState<Problem | null>(null)
  const [starting, setStarting] = useState(false)
  const [previewing, setPreviewing] = useState<Recording | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setRecords(await fetchRecordings())
      setHasData(true)
      setLoadError(null)
    } catch (error) {
      setLoadError(errorMessage(error))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load() }, [load])
  const active = records.some((record) => record.status === 'recording')
  useEffect(() => {
    if (!active) return
    const timer = window.setInterval(() => void load(), 3000)
    return () => window.clearInterval(timer)
  }, [active, load])

  const start = async () => {
    const target = url.trim()
    if (!target) {
      setProblem({ code: 'url_missing', message: '还没有填写直播地址。', action: '粘贴直播间、m3u8 或 flv 链接后再开始。' })
      return
    }
    if (!QUALITY_OPTIONS.some((item) => item.value === quality)) {
      setProblem({ code: 'quality_invalid', message: '画质档位不合法。', action: '请选择原画、高清、标清或流畅。' })
      return
    }
    if (!FPS_OPTIONS.some((item) => item.value === fps)) {
      setProblem({ code: 'fps_invalid', message: '帧率不合法。', action: '请选择原始帧率、24、30 或 60。' })
      return
    }
    setStarting(true)
    setProblem(null)
    try {
      const record = await createRecording(target, title.trim(), quality, fps)
      setRecords((current) => [record, ...current.filter((item) => item.id !== record.id)])
      setUrl('')
      setHasData(true)
      toast.success('已开始录制。停止后会自动分段入库')
    } catch (error) {
      setProblem(errorDetail(error))
    } finally {
      setStarting(false)
    }
  }

  const stop = async (record: Recording) => {
    if (!await confirmDialog({ title: '停止录制', content: '停止后每个已写出的分段都会入库，并开始生成质量分和素材卡。', okText: '停止并入库' })) return
    try {
      await stopRecording(record.id)
      toast.success('已停止，正在入库')
      await load()
    } catch (error) {
      toast.error('停止失败：' + errorMessage(error))
    }
  }

  const remove = async (record: Recording) => {
    if (!await confirmDialog({ title: '删除录制记录', content: '只删除这条记录，不删除已经入库的视频分段。', danger: true, okText: '删除记录' })) return
    try {
      await deleteRecording(record.id, false)
      await load()
    } catch (error) {
      toast.error('删除失败：' + errorMessage(error))
    }
  }

  return (
    <section>
      <PageHeader title="直播录制" desc="手动开始、手动停止。每 10 分钟一段，停止后自动入库并生成素材卡。" />
      <Panel>
        <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_220px_140px_140px_auto]">
          <Input value={url} onChange={setUrl} placeholder="直播间 / m3u8 / flv 链接" />
          <Input value={title} onChange={setTitle} placeholder="录制标题（可选）" />
          <Select value={quality} onChange={setQuality} options={QUALITY_OPTIONS} />
          <Select value={String(fps)} onChange={(value) => setFps(Number(value))} options={FPS_OPTIONS.map((item) => ({ value: String(item.value), label: item.label }))} />
          <Button variant="primary" icon={<Broadcast size={15} />} loading={starting} onClick={() => void start()}>开始录制</Button>
        </div>
        {problem && <ProblemAlert message={problem.message} action={problem.action} className="mt-3" />}
      </Panel>
      <Panel className="mt-5" title="录制记录" extra={<Button size="sm" variant="ghost" loading={loading} onClick={() => void load()}>刷新</Button>}>
        <RequestState error={loadError} hasData={hasData} loading={loading} onRetry={load} className="mb-3" />
        {loading && !hasData ? (
          <div className="space-y-3">{[0, 1].map((item) => <div key={item} className="skeleton h-16 rounded-xl" />)}</div>
        ) : !hasData ? null : records.length === 0 ? (
          <EmptyState icon={<Broadcast size={26} />} title="还没有录制" desc="填写直播地址后手动开始。这里不会自动监控主播。" />
        ) : (
          <div className="space-y-3">
            {records.map((record) => (
              <div key={record.id} className="rounded-xl border border-line-soft bg-fill p-3">
                <div className="flex flex-wrap items-center gap-2">
                  <StatusBadge status={record.status} />
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-[13px] text-ink-1">{record.title || '未命名录制'}</p>
                    <p className="mt-1 text-[11px] text-ink-4">
                      {platformLabel(record.platform)} · {qualityLabel(record.quality)} · {fpsLabel(record.fps)} · {formatSize(record.size)} · {formatTime(record.created_at)}
                      {record.status === 'recording' ? ` · 已录 ${formatElapsed(record.elapsed)}` : ` · ${formatDuration(record.duration)}`}
                    </p>
                    <p className="mt-1 text-[11px] text-ink-3">{ingestText(record)}</p>
                  </div>
                  {record.status === 'recording' && <Button size="sm" variant="ghost" onClick={() => setPreviewing(record)}>边录边看</Button>}
                  {record.status === 'recording' && <Button size="sm" variant="danger" icon={<StopCircle size={14} />} onClick={() => void stop(record)}>停止</Button>}
                  {record.video_id ? <Link to={`/videos?q=${encodeURIComponent(record.title)}`} className="text-[12px] text-accent">打开素材</Link> : null}
                  <Button size="sm" variant="text" icon={<Trash size={13} />} onClick={() => void remove(record)}>删除记录</Button>
                </div>
                {!!record.segments?.length && (
                  <div className="mt-3 flex flex-wrap gap-2 border-t border-line-soft pt-3">
                    {record.segments.map((segment) => (
                      <a key={segment.video_id} href={`/api/videos/${segment.video_id}/file`} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 rounded-lg bg-surface px-2.5 py-1.5 text-[11px] text-accent">
                        <Play size={11} />第 {segment.index} 段 · {formatDuration(segment.duration)} · 质量 {Math.round(segment.quality_score || 0)}
                        {segment.summary_status === 'running' ? ' · 素材卡生成中' : ''}
                      </a>
                    ))}
                  </div>
                )}
                {record.error && <ProblemAlert className="mt-2" message={record.error} action="检查地址是否仍在直播，或换一个画质档位再试。" />}
              </div>
            ))}
          </div>
        )}
      </Panel>
      <Modal open={!!previewing} onClose={() => setPreviewing(null)} title={previewing?.title || '边录边看'} width={900}>
        {previewing && <video src={`/api/recordings/${previewing.id}/preview`} controls autoPlay className="max-h-[70vh] w-full rounded-lg bg-black" />}
      </Modal>
    </section>
  )
}
