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
  type ChenzeOptions,
  type Problem,
  type Recording,
} from '../api'
import { Button, EmptyState, Input, Modal, PageHeader, Panel, ProblemAlert, Select, Switch, confirmDialog, toast } from '../ui'
import RequestState from '../ui/RequestState'
import StatusBadge from '../components/StatusBadge'
import AnalysisDocument from '../components/AnalysisDocument'

const CHENZE_COLUMNS: Array<{ id: '' | '综艺' | '评审' | '游戏' | '通用保真'; title: string }> = [
  { id: '', title: '自动识别栏目' },
  { id: '综艺', title: '综艺' },
  { id: '评审', title: '评审' },
  { id: '游戏', title: '游戏' },
  { id: '通用保真', title: '通用保真' },
]

function ingestText(record: Recording): string {
  if (record.status === 'recording') {
    return record.starting
      ? '正在连接直播流，连上后开始计时。停止后收成一条视频入库'
      : '录制中。中断时已写出的内容仍可恢复，停止后收成一条视频'
  }
  if (record.video_id) {
    if (record.analysis_status === 'running' || !record.document_ready) return '已入库为一条视频，内容分析文档生成中'
    if (record.analysis_status === 'failed') return '已入库为一条视频。内容分析没有完成，已录的视频还在'
    return '已入库为一条视频，可以阅读内容分析文档'
  }
  if (record.status === 'failed') return '没有入库。已经写出的捕获会保留，方便之后恢复'
  if (record.status === 'stopped' || record.status === 'completed') return '已停止，没有可入库的媒体'
  return ''
}

export default function RecordPage() {
  const [url, setUrl] = useState('')
  const [title, setTitle] = useState('')
  const [quality, setQuality] = useState('best')
  const [fps, setFps] = useState(0)
  const [chenzeStyle, setChenzeStyle] = useState(false)
  const [chenzeName, setChenzeName] = useState('')
  const [chenzeId, setChenzeId] = useState('')
  const [chenzeHome, setChenzeHome] = useState('')
  const [chenzeColumn, setChenzeColumn] = useState<'' | '综艺' | '评审' | '游戏' | '通用保真'>('')
  const [records, setRecords] = useState<Recording[]>([])
  const [loading, setLoading] = useState(true)
  const [hasData, setHasData] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [problem, setProblem] = useState<Problem | null>(null)
  const [starting, setStarting] = useState(false)
  const [previewing, setPreviewing] = useState<Recording | null>(null)
  const [reading, setReading] = useState<Recording | null>(null)

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
  const active = records.some((record) => (
    record.status === 'recording' ||
    (Boolean(record.video_id) && !record.document_ready && record.analysis_status !== 'failed')
  ))
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
      const chenze: ChenzeOptions | undefined = chenzeStyle
        ? {
            display_name: chenzeName.trim(),
            douyin_id: chenzeId.trim(),
            homepage_url: chenzeHome.trim(),
            column: chenzeColumn,
          }
        : undefined
      const record = await createRecording(target, title.trim(), quality, fps, chenzeStyle, chenze)
      setRecords((current) => [record, ...current.filter((item) => item.id !== record.id)])
      setUrl('')
      setHasData(true)
      toast.success('已开始录制。停止后会把整场收成一条视频')
    } catch (error) {
      setProblem(errorDetail(error))
    } finally {
      setStarting(false)
    }
  }

  const stop = async (record: Recording) => {
    if (!await confirmDialog({ title: '停止录制', content: '停止后会把已经录到的内容收成一条视频并入库，同时开始生成质量分和内容分析文档。手动停止也会保留已录内容。', okText: '停止并入库' })) return
    try {
      await stopRecording(record.id)
      toast.success('已停止，正在把整场收成一条视频')
      await load()
    } catch (error) {
      toast.error('停止失败：' + errorMessage(error))
    }
  }

  const remove = async (record: Recording) => {
    if (!await confirmDialog({ title: '删除录制记录', content: '只删除这条记录，不删除已经入库的那一条视频。', danger: true, okText: '删除记录' })) return
    try {
      await deleteRecording(record.id, false)
      await load()
    } catch (error) {
      toast.error('删除失败：' + errorMessage(error))
    }
  }

  return (
    <section>
      <PageHeader title="直播录制" desc="手动开始、手动停止。整场录成一条视频，停止后入库，并生成可阅读的内容分析文档。这里不会自动监控主播。" />
      <Panel>
        <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_220px_140px_140px_auto]">
          <Input value={url} onChange={setUrl} placeholder="直播间 / m3u8 / flv 链接" />
          <Input value={title} onChange={setTitle} placeholder="录制标题（可选）" />
          <Select value={quality} onChange={setQuality} options={QUALITY_OPTIONS} />
          <Select value={String(fps)} onChange={(value) => setFps(Number(value))} options={FPS_OPTIONS.map((item) => ({ value: String(item.value), label: item.label }))} />
          <Button variant="primary" icon={<Broadcast size={15} />} loading={starting} onClick={() => void start()}>开始录制</Button>
        </div>
        <div className="mt-4 flex items-start gap-3">
          <Switch checked={chenzeStyle} onChange={setChenzeStyle} label="使用陈泽剪辑方式" />
          <button type="button" className="bg-transparent p-0 text-left" onClick={() => setChenzeStyle((checked) => !checked)}>
            <p className="text-[13px] text-ink-1">使用陈泽剪辑方式</p>
            <p className="mt-1 text-[12px] text-ink-4">可选，默认关闭。只记在这条录制上，供内容分析文档和之后剪辑预选。不改变直播捕获。账号要对上已核的「陈泽-」、抖音号 9896719.（含尾点）和本人主页，否则不会当成陈泽本人。</p>
          </button>
        </div>
        {chenzeStyle && (
          <div className="mt-3 grid gap-3 md:grid-cols-2">
            <Input value={chenzeName} onChange={setChenzeName} placeholder="展示名，例如 陈泽-" />
            <Input value={chenzeId} onChange={setChenzeId} placeholder="抖音号，必须带尾点 9896719." />
            <Input value={chenzeHome} onChange={setChenzeHome} placeholder="已核本人主页链接" />
            <Select
              value={chenzeColumn}
              onChange={(value) => setChenzeColumn(value as '' | '综艺' | '评审' | '游戏' | '通用保真')}
              options={CHENZE_COLUMNS.map((item) => ({ value: item.id, label: item.title }))}
            />
          </div>
        )}
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
                  {record.video_id ? <Link to="/edit" state={{ videoIds: [record.video_id] }} className="text-[12px] text-accent">去剪辑</Link> : null}
                  {record.video_id && record.document_ready ? <Button size="sm" variant="ghost" onClick={() => setReading(record)}>阅读分析文档</Button> : null}
                  <Button size="sm" variant="text" icon={<Trash size={13} />} onClick={() => void remove(record)}>删除记录</Button>
                </div>
                {record.video_id && (
                  <div className="mt-3 flex flex-wrap gap-2 border-t border-line-soft pt-3">
                    <a href={`/api/videos/${record.video_id}/file`} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 rounded-lg bg-surface px-2.5 py-1.5 text-[11px] text-accent">
                      <Play size={11} />整场视频 · {formatDuration(record.duration)} · 质量 {Math.round(record.quality_score || record.segments?.[0]?.quality_score || 0)}
                      {record.analysis_status === 'running' ? ' · 分析文档生成中' : ''}
                    </a>
                    {record.chenze_style ? <span className="inline-flex items-center rounded-lg bg-surface px-2.5 py-1.5 text-[11px] text-ink-3">已记下陈泽剪辑方式</span> : null}
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
      <AnalysisDocument videoId={reading?.video_id ?? null} title={reading?.title} onClose={() => setReading(null)} />
    </section>
  )
}
