import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { Link, useLocation, useSearchParams } from 'react-router-dom'
import { CalendarBlank, Clock, MagnifyingGlass, VideoCamera, X } from '@phosphor-icons/react'
import {
  Alert,
  Button,
  Checkbox,
  DateTimeInput,
  EmptyState,
  Input,
  Modal,
  PageHeader,
  Panel,
  PlatformIcon,
  Segment,
  Select,
  Tag,
  TextArea,
  toast,
} from '../ui'
import {
  errorMessage,
  fetchAccounts,
  fetchPublishRecords,
  fetchVideos,
  formatDuration,
  formatTime,
  PLATFORMS,
  platformLabel,
  publishVideo,
  type Account,
  type PublishRecord,
  type Video,
} from '../api'
import StatusBadge from '../components/StatusBadge'
import TaskLogs from '../components/TaskLogs'
import RequestState from '../ui/RequestState'
import { useTaskCenter } from '../context/TaskCenter'

const TITLE_MAX = 55
const FINISHED = ['success', 'failed', 'partial']

/** "douyin:default" → "抖音 / default" */
function targetLabel(key: string): string {
  const i = key.indexOf(':')
  if (i < 0) return key
  return `${platformLabel(key.slice(0, i))} / ${key.slice(i + 1)}`
}

const WEEKDAYS = ['日', '一', '二', '三', '四', '五', '六']

/** 排期分组的日期标题："2026-08-22" → "2026/8/22 星期六" */
function scheduleDayLabel(day: string): string {
  const [y, m, d] = day.split('-').map(Number)
  return `${y}/${m}/${d} 星期${WEEKDAYS[new Date(y, m - 1, d).getDay()]}`
}

/** 定时时间是否已过（schedule 为本地时间 "YYYY-MM-DD HH:mm"） */
function scheduleExpired(schedule: string): boolean {
  return new Date(schedule.replace(' ', 'T')).getTime() < Date.now()
}

export default function PublishPage() {
  const location = useLocation()
  const [searchParams] = useSearchParams()

  const [videos, setVideos] = useState<Video[]>([])
  const [sourceLoading, setSourceLoading] = useState(true)
  const [sourceError, setSourceError] = useState<string | null>(null)
  const [selectedId, setSelectedId] = useState<number | null>(null)
  /** 视频网格搜索关键词（client-side 按标题过滤） */
  const [videoQuery, setVideoQuery] = useState('')

  const [title, setTitle] = useState('')
  const [desc, setDesc] = useState('')
  const [tags, setTags] = useState<string[]>([])
  const [tagInput, setTagInput] = useState('')
  // 选中的发布目标，元素格式 "platform:account"
  const [targets, setTargets] = useState<string[]>([])
  const [accounts, setAccounts] = useState<Account[]>([])
  const [schedule, setSchedule] = useState<string>('')

  const [publishing, setPublishing] = useState(false)
  const { task, trackTask } = useTaskCenter()
  const previousTaskRef = useRef<{ id: string; status: string } | null>(null)

  const [records, setRecords] = useState<PublishRecord[]>([])
  const [logRecord, setLogRecord] = useState<PublishRecord | null>(null)
  // 发布记录筛选（client-side）
  const [filterPlatform, setFilterPlatform] = useState('')
  const [filterStatus, setFilterStatus] = useState('')
  const [filterKeyword, setFilterKeyword] = useState('')
  // 发布记录区视角：records 表格 / schedule 排期时间线
  const [recordView, setRecordView] = useState<'records' | 'schedule'>('records')

  const loadRecords = useCallback(async () => {
    try {
      setRecords(await fetchPublishRecords())
    } catch {
      // 记录加载失败不阻塞主流程
    }
  }, [])

  const loadSources = useCallback(async () => {
    setSourceLoading(true)
    setSourceError(null)
    try {
      const [videoList, accountList] = await Promise.all([fetchVideos(), fetchAccounts()])
      setVideos(videoList)
      setAccounts(accountList.filter((account) => account.cookie_exists))
      const stateId = (location.state as { videoId?: number } | null)?.videoId
      const queryId = searchParams.get('videoId')
      const preselect = stateId ?? (queryId ? Number(queryId) : null)
      const selected = videoList.find((video) => video.id === preselect)
      if (selected) {
        setSelectedId(selected.id)
        setTitle((current) => current || selected.title.slice(0, TITLE_MAX))
      }
    } catch (reason) {
      setSourceError(errorMessage(reason))
    } finally {
      setSourceLoading(false)
    }
  }, [location.state, searchParams])

  useEffect(() => {
    void loadSources()
    void loadRecords()
  }, [loadRecords, loadSources])

  const selectedVideo = videos.find((v) => v.id === selectedId) ?? null
  const taskId = task?.id
  const taskStatus = task?.status

  useEffect(() => {
    if (!taskId || !taskStatus) {
      previousTaskRef.current = null
      return
    }
    const previous = previousTaskRef.current
    if (FINISHED.includes(taskStatus)) {
      void loadRecords()
      if (previous?.id === taskId && previous.status !== taskStatus) {
        if (taskStatus === 'success') toast.success('全部账号发布成功')
        else if (taskStatus === 'partial') toast.warning('部分账号发布失败，请查看结果')
        else toast.error('发布失败，请查看结果与日志')
      }
    }
    previousTaskRef.current = { id: taskId, status: taskStatus }
  }, [loadRecords, taskId, taskStatus])

  const addTag = () => {
    const t = tagInput.trim().replace(/^#/, '')
    if (t && !tags.includes(t)) setTags([...tags, t])
    setTagInput('')
  }

  const handlePlatformTargets = (platform: string, values: string[]) => {
    setTargets((prev) => [...prev.filter((k) => !k.startsWith(`${platform}:`)), ...values])
  }

  const handlePublish = async () => {
    if (!selectedVideo || publishing) return
    if (!title.trim()) {
      toast.warning('标题必填')
      return
    }
    if (title.trim().length > TITLE_MAX) {
      toast.warning(`标题最长 ${TITLE_MAX} 字`)
      return
    }
    if (targets.length === 0) {
      toast.warning('请至少选择一个账号')
      return
    }
    if (targets.some((key) => key.startsWith('xiaohongshu:')) && title.trim().length > 20) {
      toast.warning('选择小红书账号时，标题最长 20 字')
      return
    }
    if (schedule && new Date(schedule).getTime() <= Date.now()) {
      toast.warning('定时发布时间必须晚于当前时间')
      return
    }

    setPublishing(true)
    try {
      const { task_id } = await publishVideo({
        video_id: selectedVideo.id,
        targets: targets.map((key) => {
          const i = key.indexOf(':')
          return { platform: key.slice(0, i), account: key.slice(i + 1) }
        }),
        title: title.trim(),
        desc: desc.trim(),
        tags,
        schedule: schedule || null,
      })
      trackTask(task_id)
    } catch (e) {
      toast.error(`创建发布任务失败：${errorMessage(e)}`)
    } finally {
      setPublishing(false)
    }
  }

  // 每个目标（平台:账号）的状态徽标
  const targetBadge = (key: string) => {
    if (!task) return null
    const result = task.result.targets?.[key]
    if (result) return <StatusBadge status={result.status} />
    if (task.status === 'pending') return <StatusBadge status="pending" />
    if (task.status === 'running') return <StatusBadge status="running" />
    return <StatusBadge status={task.status} />
  }

  // 可用账号按平台分组
  const accountsByPlatform = new Map<string, Account[]>()
  for (const acc of accounts) {
    const list = accountsByPlatform.get(acc.platform) ?? []
    list.push(acc)
    accountsByPlatform.set(acc.platform, list)
  }

  const busy = task !== null && ['pending', 'running'].includes(task.status)

  // 视频网格搜索过滤（不影响预选逻辑，预选基于完整列表）
  const videoKeyword = videoQuery.trim().toLowerCase()
  const filteredVideos = videoKeyword
    ? videos.filter((v) => v.title.toLowerCase().includes(videoKeyword))
    : videos

  // 发布记录筛选：平台 + 状态 + 关键词（匹配视频名或发布标题）
  const recordKeyword = filterKeyword.trim().toLowerCase()
  const filteredRecords = records.filter((r) => {
    if (filterPlatform && r.platform !== filterPlatform) return false
    if (filterStatus && r.status !== filterStatus) return false
    if (
      recordKeyword &&
      !r.video_title.toLowerCase().includes(recordKeyword) &&
      !r.title.toLowerCase().includes(recordKeyword)
    ) {
      return false
    }
    return true
  })

  // 排期视图：筛选后取有定时时间的记录，按时间正序，再按日期（"YYYY-MM-DD"）分组
  const scheduledRecords = filteredRecords
    .filter((r) => r.schedule !== null)
    .sort((a, b) => a.schedule!.localeCompare(b.schedule!))
  const scheduleGroups = new Map<string, PublishRecord[]>()
  for (const r of scheduledRecords) {
    const day = r.schedule!.slice(0, 10)
    const list = scheduleGroups.get(day) ?? []
    list.push(r)
    scheduleGroups.set(day, list)
  }

  return (
    <div>
      <PageHeader title="发布视频" desc="选择视频，填写发布信息，一键分发到多个平台账号" />
      <RequestState error={sourceError} hasData={videos.length > 0} loading={sourceLoading} onRetry={() => void loadSources()} className="mb-4" />

      <div className="grid gap-4 lg:grid-cols-2">
        {/* 左：视频选择 */}
        <Panel
          title="选择视频"
          extra={
            <Input
              allowClear
              prefix={<MagnifyingGlass size={14} />}
              placeholder="搜索视频标题"
              value={videoQuery}
              onChange={setVideoQuery}
              className="w-[180px]"
            />
          }
        >
          {sourceLoading && videos.length === 0 ? (
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">{[0, 1, 2].map((item) => <div key={item} className="skeleton aspect-video" />)}</div>
          ) : videos.length === 0 ? (
            !sourceError && <EmptyState
              icon={<VideoCamera size={28} />}
              title="暂无视频"
              desc="请先到视频库上传或下载视频，再回来选择发布"
            />
          ) : filteredVideos.length === 0 ? (
            <EmptyState icon={<MagnifyingGlass size={28} />} title="没有匹配的视频" desc="换个关键词试试" />
          ) : (
            <div className="grid max-h-[560px] grid-cols-2 gap-3 overflow-y-auto pr-1 sm:grid-cols-3">
              {filteredVideos.map((v, i) => (
                <button
                  key={v.id}
                  type="button"
                  aria-pressed={selectedId === v.id}
                  onClick={() => {
                    setSelectedId(v.id)
                    if (!title) setTitle(v.title.slice(0, TITLE_MAX))
                  }}
                  className={`enter-item cursor-pointer overflow-hidden rounded-lg text-left ring-2 transition-all ${
                    selectedId === v.id ? 'ring-accent' : 'ring-transparent hover:ring-line'
                  }`}
                  style={{ animationDelay: `${Math.min(i * 50, 400)}ms` }}
                >
                  <div className="relative aspect-video bg-fill">
                    {v.cover_url ? (
                      <img src={v.cover_url} alt={v.title} loading="lazy" className="h-full w-full object-cover" />
                    ) : (
                      <div className="flex h-full items-center justify-center text-ink-4">
                        <PlatformIcon platform={v.source_platform} size={32} />
                      </div>
                    )}
                    <span className="tnum absolute bottom-1 right-1 rounded bg-black/70 px-1 text-[10px] text-white">
                      {formatDuration(v.duration)}
                    </span>
                  </div>
                  <span className="block truncate px-1.5 py-1 text-xs text-ink-3" title={v.title}>
                    {v.title}
                  </span>
                </button>
              ))}
            </div>
          )}
        </Panel>

        {/* 右：发布表单 */}
        <Panel title="发布信息">
          <div className="space-y-4">
            <div>
              <label className="mb-1.5 block text-[13px] text-ink-2">
                标题 <span className="text-danger">*</span>
                <span className="ml-2 text-xs text-ink-3">（小红书限制20字以内）</span>
              </label>
              <Input
                value={title}
                onChange={setTitle}
                maxLength={TITLE_MAX}
                placeholder={`必填，最长 ${TITLE_MAX} 字`}
                suffix={
                  <span className="tnum text-xs">
                    {title.length}/{TITLE_MAX}
                  </span>
                }
              />
            </div>

            <div>
              <label className="mb-1.5 block text-[13px] text-ink-2">简介</label>
              <TextArea value={desc} onChange={setDesc} rows={3} placeholder="视频简介（可选）" />
            </div>

            <div>
              <label className="mb-1.5 block text-[13px] text-ink-2">标签（回车添加）</label>
              <Input
                value={tagInput}
                onChange={setTagInput}
                placeholder="输入标签后回车"
                onKeyDown={(e: KeyboardEvent<HTMLInputElement>) => {
                  if (e.key === 'Enter') {
                    e.preventDefault()
                    addTag()
                  }
                }}
              />
              {tags.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-2">
                  {tags.map((t) => (
                    <Tag key={t} color="blue">
                      #{t}
                      <button
                        type="button"
                        aria-label={`删除标签 ${t}`}
                        onClick={() => setTags(tags.filter((x) => x !== t))}
                        className="-mr-0.5 flex items-center opacity-60 transition-opacity hover:opacity-100"
                      >
                        <X size={10} weight="bold" />
                      </button>
                    </Tag>
                  ))}
                </div>
              )}
            </div>

            <div>
              <label className="mb-1.5 block text-[13px] text-ink-2">发布账号</label>
              {accountsByPlatform.size === 0 ? (
                <Alert type="warning">
                  暂无可用账号（需已配置 cookie），请先到
                  <Link to="/accounts" className="mx-1 text-accent hover:underline">
                    平台账号
                  </Link>
                  页添加
                </Alert>
              ) : (
                <div className="space-y-2">
                  {[...accountsByPlatform.entries()].map(([platform, list]) => (
                    <div key={platform} className="flex items-start gap-3">
                      <span className="flex w-16 shrink-0 items-center gap-1.5 pt-[5px]">
                        <PlatformIcon platform={platform} size={16} />
                        <span className="text-xs text-ink-3">{platformLabel(platform)}</span>
                      </span>
                      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
                        {list.map((acc) => {
                          const key = `${acc.platform}:${acc.account}`
                          return (
                            <Checkbox
                              key={key}
                              checked={targets.includes(key)}
                              onChange={(checked: boolean) => {
                                const current = targets.filter((k) => k.startsWith(`${platform}:`))
                                handlePlatformTargets(
                                  platform,
                                  checked ? [...current, key] : current.filter((k) => k !== key),
                                )
                              }}
                              label={acc.account}
                            />
                          )
                        })}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>

            <div>
              <label className="mb-1.5 block text-[13px] text-ink-2">定时发布（留空 = 立即发布）</label>
              <div className="flex items-center gap-2">
                <Clock size={15} className="shrink-0 text-ink-3" />
                <DateTimeInput value={schedule} onChange={setSchedule} className="w-[220px]" />
                {schedule && (
                  <Button size="sm" variant="text" onClick={() => setSchedule('')}>
                    清空
                  </Button>
                )}
              </div>
            </div>
          </div>

          <Button
            variant="primary"
            full
            className="mt-4"
            loading={publishing}
            disabled={!selectedVideo || busy}
            onClick={handlePublish}
          >
            {publishing
              ? '创建任务中…'
              : busy
                ? '发布中…'
                : selectedVideo
                  ? `发布「${selectedVideo.title.slice(0, 12)}${selectedVideo.title.length > 12 ? '…' : ''}」`
                  : '请先选择视频'}
          </Button>
        </Panel>
      </div>

      {/* 任务状态 */}
      {task && (
        <Panel title="发布进度" className="mt-4">
          <div className="mb-3 flex items-center gap-2">
            <StatusBadge status={task.status} />
            <span className="tnum text-[13px] text-ink-3">{task.progress}%</span>
            <div className="ml-4 flex flex-wrap items-center gap-3">
              {targets.map((key) => (
                <span key={key} className="flex items-center gap-1 text-sm">
                  {targetLabel(key)} {targetBadge(key)}
                </span>
              ))}
            </div>
          </div>
          {task.result.targets && (
            <div className="mb-3 space-y-1">
              {Object.entries(task.result.targets).map(([key, r]) => (
                <Alert key={key} type={r.status === 'success' ? 'success' : 'error'}>
                  {`${targetLabel(key)}：${r.message || (r.status === 'success' ? '发布成功' : '发布失败')}`}
                </Alert>
              ))}
            </div>
          )}
          <TaskLogs logs={task.logs} />
        </Panel>
      )}

      {/* 发布记录 */}
      <Panel
        title="发布记录"
        className="mt-4"
        extra={
          <Segment
            value={recordView}
            onChange={(v) => setRecordView(v as 'records' | 'schedule')}
            options={[
              { value: 'records', label: '记录' },
              { value: 'schedule', label: '排期' },
            ]}
          />
        }
      >
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <Select
            width={120}
            value={filterPlatform}
            onChange={setFilterPlatform}
            options={[
              { label: '全部平台', value: '' },
              ...PLATFORMS.map((p) => ({ label: p.label, value: p.key })),
            ]}
          />
          <Select
            width={120}
            value={filterStatus}
            onChange={setFilterStatus}
            options={[
              { label: '全部状态', value: '' },
              { label: '成功', value: 'success' },
              { label: '失败', value: 'failed' },
              { label: '运行中', value: 'running' },
            ]}
          />
          <Input
            allowClear
            prefix={<MagnifyingGlass size={14} />}
            placeholder="搜索视频或发布标题"
            value={filterKeyword}
            onChange={setFilterKeyword}
            className="w-[200px]"
          />
        </div>
        {recordView === 'records' ? (
          <div className="overflow-x-auto">
            <table className="vh-table">
              <thead>
                <tr>
                  <th className="w-[160px]">时间</th>
                  <th className="w-[180px]">视频</th>
                  <th className="w-[96px]">平台</th>
                  <th className="w-[100px]">账号</th>
                  <th className="w-[100px]">状态</th>
                  <th>标题</th>
                  <th className="w-[90px]">操作</th>
                </tr>
              </thead>
              <tbody>
                {filteredRecords.map((r) => (
                  <tr key={r.id}>
                    <td>
                      <span className="tnum text-xs text-ink-3">{formatTime(r.created_at)}</span>
                      {r.schedule && <span className="tnum block text-xs text-accent">定时 {r.schedule}</span>}
                    </td>
                    <td>
                      <span className="block max-w-[170px] truncate text-xs" title={r.video_title}>
                        {r.video_title}
                      </span>
                    </td>
                    <td>
                      <span className="flex items-center gap-1.5">
                        <PlatformIcon platform={r.platform} size={14} />
                        <span>{platformLabel(r.platform)}</span>
                      </span>
                    </td>
                    <td>{r.account || '-'}</td>
                    <td>
                      <StatusBadge status={r.status} />
                    </td>
                    <td>
                      <span className="block max-w-[220px] truncate" title={r.title}>
                        {r.title}
                      </span>
                    </td>
                    <td>
                      <Button size="sm" variant="text" onClick={() => setLogRecord(r)}>
                        查看日志
                      </Button>
                    </td>
                  </tr>
                ))}
                {filteredRecords.length === 0 && (
                  <tr>
                    <td colSpan={7}>
                      {records.length === 0 ? (
                        <EmptyState title="暂无发布记录" desc="发布任务完成后会在这里展示" />
                      ) : (
                        <EmptyState title="没有匹配的记录" desc="调整筛选条件试试" />
                      )}
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        ) : scheduledRecords.length === 0 ? (
          <EmptyState
            icon={<CalendarBlank size={28} />}
            title="暂无排期"
            desc="发布视频时选择定时发布时间，就会出现在这里"
          />
        ) : (
          <div className="space-y-4">
            {[...scheduleGroups.entries()].map(([day, list]) => (
              <div key={day} className="flex gap-4">
                <h3 className="section-title w-[132px] shrink-0 pt-2">{scheduleDayLabel(day)}</h3>
                <div className="min-w-0 flex-1 space-y-3 border-l border-line pl-4">
                  {list.map((r) => (
                    <div
                      key={r.id}
                      className={`vh-card flex items-center gap-3 p-3 ${scheduleExpired(r.schedule!) ? 'opacity-60' : ''}`}
                    >
                      <span className="tnum font-display w-14 shrink-0 text-lg">{r.schedule!.slice(11)}</span>
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-[13px]" title={r.video_title}>
                          {r.video_title}
                        </span>
                        <span className="mt-1 flex items-center gap-1.5 text-xs text-ink-3">
                          <PlatformIcon platform={r.platform} size={14} />
                          <span className="truncate">{r.account || '-'}</span>
                        </span>
                      </span>
                      <StatusBadge status={r.status} />
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </Panel>

      {/* 日志弹窗 */}
      <Modal
        title={`发布日志 - ${logRecord ? platformLabel(logRecord.platform) : ''}${logRecord ? ` / ${logRecord.account}` : ''}`}
        open={!!logRecord}
        onClose={() => setLogRecord(null)}
        footer={null}
        width={720}
      >
        {logRecord && (
          <div className="log-box" style={{ maxHeight: 400 }}>
            {logRecord.log || '（无日志）'}
          </div>
        )}
      </Modal>
    </div>
  )
}
