import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowClockwise, Clock, DownloadSimple, Heart } from '@phosphor-icons/react'
import {
  createBatchDownload,
  createDownload,
  errorMessage,
  fetchTask,
  fetchTasks,
  formatDuration,
  parseAuthorWorks,
  parseDownload,
  platformLabel,
  type AuthorWork,
  type AuthorWorksResult,
  type ParseResult,
  type Task,
  type TaskSummary,
} from '../api'
import { Alert, Button, Checkbox, PageHeader, Panel, PlatformIcon, Progress, Segment, TextArea, toast } from '../ui'
import TaskProgress from '../components/TaskProgress'
import StatusBadge from '../components/StatusBadge'

const FINISHED = ['success', 'failed', 'partial']

/** 批量下载单次上限（与后端 /download/batch 一致） */
const MAX_BATCH_SELECT = 100

/** 点赞数 → 1.2万 风格 */
function formatLikeCount(n: number | null): string {
  if (n == null) return '-'
  if (n >= 10000) return `${(n / 10000).toFixed(1)}万`
  return String(n)
}

export default function DownloadPage() {
  const [mode, setMode] = useState<'single' | 'batch' | 'author'>('single')

  const [text, setText] = useState('')
  const [parsing, setParsing] = useState(false)
  const [parseError, setParseError] = useState<string | null>(null)
  const [preview, setPreview] = useState<ParseResult | null>(null)

  const [task, setTask] = useState<Task | null>(null)
  const [starting, setStarting] = useState(false)
  const timerRef = useRef<number | null>(null)

  // 批量队列
  const [batchText, setBatchText] = useState('')
  const [batching, setBatching] = useState(false)
  const [queue, setQueue] = useState<TaskSummary[]>([])
  const batchTimerRef = useRef<number | null>(null)
  const queueIdsRef = useRef<string[]>([])

  // 作者作品
  const [authorText, setAuthorText] = useState('')
  const [authorParsing, setAuthorParsing] = useState(false)
  const [authorError, setAuthorError] = useState<string | null>(null)
  const [authorResult, setAuthorResult] = useState<AuthorWorksResult | null>(null)
  const [checkedIds, setCheckedIds] = useState<Set<string>>(new Set())
  const [authorSubmitting, setAuthorSubmitting] = useState(false)
  // 组件卸载后置 false，阻断在途请求的 setState
  const aliveRef = useRef(true)

  const batchLines = batchText
    .split('\n')
    .map((s) => s.trim())
    .filter(Boolean)

  // 卸载时清理轮询，并阻断在途请求的副作用
  useEffect(() => {
    aliveRef.current = true
    return () => {
      aliveRef.current = false
      if (timerRef.current) window.clearInterval(timerRef.current)
      if (batchTimerRef.current) window.clearInterval(batchTimerRef.current)
    }
  }, [])

  const startPolling = (taskId: string) => {
    if (timerRef.current) window.clearInterval(timerRef.current)
    timerRef.current = window.setInterval(async () => {
      try {
        const t = await fetchTask(taskId)
        setTask(t)
        if (FINISHED.includes(t.status)) {
          if (timerRef.current) window.clearInterval(timerRef.current)
          timerRef.current = null
          if (t.status === 'success') toast.success('下载完成，已加入视频库')
          else if (t.status === 'failed') toast.error('下载失败，请查看日志')
        }
      } catch {
        // 轮询失败静默重试
      }
    }, 1500)
  }

  const startQueuePolling = () => {
    if (batchTimerRef.current) window.clearInterval(batchTimerRef.current)
    batchTimerRef.current = window.setInterval(async () => {
      try {
        const ids = queueIdsRef.current
        if (ids.length === 0) return
        const all = await fetchTasks()
        const byId = new Map(all.map((t) => [t.id, t]))
        const mine = ids.map((id) => byId.get(id)).filter((t): t is TaskSummary => !!t)
        setQueue(mine)
        if (mine.length === ids.length && mine.every((t) => FINISHED.includes(t.status))) {
          if (batchTimerRef.current) window.clearInterval(batchTimerRef.current)
          batchTimerRef.current = null
          const done = mine.filter((t) => t.status === 'success').length
          const failed = mine.length - done
          if (failed === 0) toast.success(`批量下载完成：${done} 条全部成功`)
          else toast.success(`批量下载结束：完成 ${done} 条，失败 ${failed} 条`)
        }
      } catch {
        // 轮询失败静默重试
      }
    }, 2000)
  }

  const handleParse = async () => {
    if (!text.trim()) return
    setParsing(true)
    setParseError(null)
    setPreview(null)
    setTask(null)
    try {
      setPreview(await parseDownload(text.trim()))
    } catch (e) {
      setParseError(errorMessage(e))
    } finally {
      setParsing(false)
    }
  }

  const handleDownload = async () => {
    if (!text.trim()) return
    setStarting(true)
    try {
      const { task_id } = await createDownload(text.trim())
      const t = await fetchTask(task_id)
      setTask(t)
      if (!FINISHED.includes(t.status)) startPolling(task_id)
      else if (t.status === 'success') toast.success('下载完成，已加入视频库')
    } catch (e) {
      toast.error(`创建下载任务失败：${errorMessage(e)}`)
    } finally {
      setStarting(false)
    }
  }

  const handleBatchSubmit = async () => {
    const lines = batchLines
    if (lines.length === 0) return
    setBatching(true)
    try {
      const { task_ids } = await createBatchDownload(lines)
      queueIdsRef.current = task_ids
      setQueue([])
      toast.success(`已创建 ${task_ids.length} 个下载任务，正在排队执行`)
      startQueuePolling()
    } catch (e) {
      toast.error(`创建批量下载失败：${errorMessage(e)}`)
    } finally {
      setBatching(false)
    }
  }

  /** 把一批 task_ids 接入现有队列轮询（保留仍在执行中的旧任务，已结束的丢弃） */
  const appendQueueTasks = (taskIds: string[]) => {
    const activeOld = queueIdsRef.current.filter((id) => {
      const t = queue.find((q) => q.id === id)
      return t !== undefined && !FINISHED.includes(t.status)
    })
    queueIdsRef.current = [...activeOld, ...taskIds]
    setQueue([])
    startQueuePolling()
  }

  const handleAuthorParse = async () => {
    const q = authorText.trim()
    if (!q) return
    setAuthorParsing(true)
    setAuthorError(null)
    try {
      const result = await parseAuthorWorks(q)
      if (!aliveRef.current) return
      setAuthorResult(result)
      setCheckedIds(new Set())
      if (result.works.length === 0) toast.info('未解析到作品，可能该作者暂无公开作品')
    } catch (e) {
      if (!aliveRef.current) return
      setAuthorResult(null)
      setAuthorError(errorMessage(e))
    } finally {
      if (aliveRef.current) setAuthorParsing(false)
    }
  }

  const toggleWork = (id: string) => {
    setCheckedIds((prev) => {
      if (prev.has(id)) {
        const next = new Set(prev)
        next.delete(id)
        return next
      }
      if (prev.size >= MAX_BATCH_SELECT) {
        toast.warning(`最多选择 ${MAX_BATCH_SELECT} 条`)
        return prev
      }
      const next = new Set(prev)
      next.add(id)
      return next
    })
  }

  const selectAllWorks = () => {
    if (!authorResult) return
    const works = authorResult.works
    if (works.length > MAX_BATCH_SELECT) toast.warning(`最多选择 ${MAX_BATCH_SELECT} 条，已选中前 ${MAX_BATCH_SELECT} 条`)
    setCheckedIds(new Set(works.slice(0, MAX_BATCH_SELECT).map((w) => w.id)))
  }

  const invertWorks = () => {
    if (!authorResult) return
    setCheckedIds((prev) => {
      const next = new Set<string>()
      for (const w of authorResult.works) {
        if (prev.has(w.id)) continue
        if (next.size >= MAX_BATCH_SELECT) {
          toast.warning(`最多选择 ${MAX_BATCH_SELECT} 条`)
          break
        }
        next.add(w.id)
      }
      return next
    })
  }

  const resetAuthor = () => {
    setAuthorResult(null)
    setCheckedIds(new Set())
    setAuthorError(null)
  }

  const handleAuthorDownload = async () => {
    if (!authorResult) return
    const urls = authorResult.works.filter((w) => checkedIds.has(w.id)).map((w) => w.url)
    if (urls.length === 0) return
    setAuthorSubmitting(true)
    try {
      const { task_ids } = await createBatchDownload(urls)
      if (!aliveRef.current) return
      toast.success(`已创建 ${task_ids.length} 个下载任务，正在排队执行`)
      appendQueueTasks(task_ids)
      setMode('batch')
    } catch (e) {
      if (aliveRef.current) toast.error(`创建批量下载失败：${errorMessage(e)}`)
    } finally {
      if (aliveRef.current) setAuthorSubmitting(false)
    }
  }

  const busy = task !== null && ['pending', 'running'].includes(task.status)

  return (
    <div>
      <PageHeader
        title="视频下载"
        desc="粘贴分享口令或链接，自动解析并无水印入库；支持多行批量"
      />

      {/* 输入区 */}
      <div className="max-w-2xl">
        <Panel title="分享链接">
          <Segment
            value={mode}
            onChange={(v) => setMode(v as 'single' | 'batch' | 'author')}
            options={[
              { value: 'single', label: '单条解析' },
              { value: 'batch', label: '批量队列' },
              { value: 'author', label: '作者作品' },
            ]}
            className="mb-3"
          />

          {mode === 'single' ? (
            <>
              <TextArea
                value={text}
                onChange={setText}
                rows={4}
                placeholder={'粘贴抖音分享口令 / 链接，例如：\n7.94 复制打开抖音，看看【xxx的作品】... https://v.douyin.com/xxxx/'}
              />
              <div className="mt-3 flex justify-end">
                <Button variant="primary" loading={parsing} disabled={!text.trim()} onClick={handleParse}>
                  解析视频
                </Button>
              </div>
              {parseError && (
                <Alert type="error" className="mt-3">
                  解析失败：{parseError}
                </Alert>
              )}
            </>
          ) : mode === 'batch' ? (
            <>
              <TextArea
                value={batchText}
                onChange={setBatchText}
                rows={6}
                placeholder={'每行粘贴一条分享口令或链接，最多 100 条'}
              />
              <div className="mt-3 flex items-center justify-between">
                <span className="text-[13px] text-ink-3">已识别 {batchLines.length} 条链接</span>
                <Button
                  variant="primary"
                  loading={batching}
                  disabled={batchLines.length === 0}
                  onClick={handleBatchSubmit}
                >
                  加入下载队列
                </Button>
              </div>

              {/* 队列进度列表 */}
              {queue.length > 0 && (
                <div className="mt-4 space-y-2">
                  {queue.map((t, i) => (
                    <div key={t.id} className="vh-card p-3">
                      <div className="flex items-center gap-2">
                        <span className="tnum w-5 shrink-0 text-[13px] text-ink-3">{i + 1}.</span>
                        <StatusBadge status={t.status} />
                        <span className="min-w-0 flex-1 truncate text-[13px] text-ink-1" title={t.title}>
                          {t.title}
                        </span>
                      </div>
                      <Progress percent={t.progress / 100} className="mt-2" />
                    </div>
                  ))}
                  <div className="pt-1 text-right">
                    <Link to="/tasks" className="text-[13px] text-accent hover:underline">
                      去任务中心查看全部 →
                    </Link>
                  </div>
                </div>
              )}
            </>
          ) : (
            <>
              {/* 作者作品：输入态 */}
              <TextArea
                value={authorText}
                onChange={setAuthorText}
                rows={3}
                placeholder={'粘贴作者主页分享链接 / 口令，例如：\nhttps://v.douyin.com/xxx 或 https://www.xiaohongshu.com/user/profile/xxx'}
              />
              <div className="mt-3 flex items-center justify-between gap-3">
                <span className="text-[13px] text-ink-3">
                  {authorParsing ? '浏览器抓取中，可能需要 30-90 秒…' : '解析作者主页，勾选作品后可批量下载'}
                </span>
                <Button
                  variant="primary"
                  loading={authorParsing}
                  disabled={!authorText.trim()}
                  onClick={handleAuthorParse}
                >
                  获取作品列表
                </Button>
              </div>
              {authorError && (
                <Alert type="error" className="mt-3">
                  {authorError}
                  {authorError.includes('平台账号') && (
                    <Link to="/accounts" className="ml-2 text-accent hover:underline">
                      去平台账号页登录 →
                    </Link>
                  )}
                </Alert>
              )}
            </>
          )}
        </Panel>

        {/* 解析预览卡 */}
        {mode === 'single' && preview && (
          <Panel className="mt-4">
            <div className="flex gap-4">
              {preview.cover_url ? (
                <img
                  src={preview.cover_url}
                  alt={preview.title}
                  className="h-28 w-20 shrink-0 rounded object-cover"
                />
              ) : (
                <div className="flex h-28 w-20 shrink-0 items-center justify-center rounded bg-fill">
                  <PlatformIcon platform={null} size={32} />
                </div>
              )}
              <div className="min-w-0 flex-1">
                <div className="line-clamp-2 font-medium leading-snug text-ink-1" title={preview.title}>
                  {preview.title}
                </div>
                <dl className="mt-2 space-y-1">
                  <div className="flex gap-3 text-[13px]">
                    <dt className="shrink-0 text-xs text-ink-3">作者</dt>
                    <dd className="min-w-0 truncate text-ink-2">{preview.author || '未知'}</dd>
                  </div>
                  <div className="flex gap-3 text-[13px]">
                    <dt className="shrink-0 text-xs text-ink-3">时长</dt>
                    <dd className="tnum text-ink-2">{formatDuration(preview.duration)}</dd>
                  </div>
                </dl>
              </div>
            </div>
            <div className="mt-4 flex justify-end">
              <Button
                variant="primary"
                icon={<DownloadSimple size={15} />}
                loading={starting}
                disabled={busy}
                onClick={handleDownload}
              >
                下载到视频库
              </Button>
            </div>
          </Panel>
        )}

        {/* 任务进度 */}
        {mode === 'single' && task && (
          <Panel title="下载进度" className="mt-4">
            <TaskProgress task={task} />
            {task.status === 'success' && (
              <Alert type="success" className="mt-3">
                已加入视频库{task.result.video ? `：${task.result.video.title}` : ''}
                <Link to="/videos" className="ml-2 text-accent hover:underline">
                  去查看 →
                </Link>
              </Alert>
            )}
            {task.status === 'failed' && (
              <Alert type="error" className="mt-3">
                下载失败，请查看上方日志
              </Alert>
            )}
          </Panel>
        )}
      </div>

      {/* 作者作品：结果区（全宽） */}
      {mode === 'author' && authorResult && (
        <div className="mt-4">
          {/* 作者信息条 */}
          <Panel pad="sm" className="mb-3">
            <div className="flex items-center gap-3">
              {authorResult.author.avatar ? (
                <img
                  src={authorResult.author.avatar}
                  alt={authorResult.author.name}
                  className="h-10 w-10 shrink-0 rounded-full object-cover"
                />
              ) : (
                <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-fill-deep text-sm font-medium text-ink-2">
                  {platformLabel(authorResult.author.platform)[0] ?? '?'}
                </span>
              )}
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="truncate font-medium text-ink-1">{authorResult.author.name}</span>
                  <PlatformIcon platform={authorResult.author.platform} size={18} />
                  <span className="shrink-0 text-xs text-ink-3">
                    {platformLabel(authorResult.author.platform)}
                  </span>
                </div>
                <div className="tnum mt-0.5 text-xs text-ink-3">
                  共 {authorResult.works.length} 个作品
                </div>
              </div>
              <a
                href={authorResult.author.url}
                target="_blank"
                rel="noreferrer"
                className="hidden shrink-0 text-[13px] text-accent hover:underline sm:inline"
              >
                作者主页 →
              </a>
              <Button
                variant="ghost"
                size="sm"
                icon={<ArrowClockwise size={14} />}
                disabled={authorParsing}
                onClick={resetAuthor}
              >
                重新解析
              </Button>
            </div>
          </Panel>

          {/* 操作条（吸顶） */}
          <div className="sticky top-3 z-10 mb-3 flex flex-wrap items-center gap-x-2 gap-y-2 rounded-[14px] border border-line bg-card px-3 py-2 shadow-card">
            <span className="text-[13px] text-ink-2">
              已选 <span className="tnum font-medium text-accent">{checkedIds.size}</span>
              <span className="text-ink-3"> / 共 {authorResult.works.length}</span>
            </span>
            <Button size="sm" variant="text" onClick={selectAllWorks}>
              全选
            </Button>
            <Button size="sm" variant="text" onClick={invertWorks}>
              反选
            </Button>
            <Button
              variant="primary"
              size="sm"
              className="ml-auto"
              icon={<DownloadSimple size={14} />}
              loading={authorSubmitting}
              disabled={checkedIds.size === 0}
              onClick={handleAuthorDownload}
            >
              下载选中{checkedIds.size > 0 ? `（${checkedIds.size}）` : ''}
            </Button>
          </div>

          {/* 作品网格 */}
          {authorResult.works.length > 0 ? (
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 md:grid-cols-4 xl:grid-cols-5">
              {authorResult.works.map((w: AuthorWork) => {
                const selected = checkedIds.has(w.id)
                return (
                  <div
                    key={w.id}
                    onClick={() => toggleWork(w.id)}
                    className={`vh-card cursor-pointer select-none overflow-hidden ${
                      selected ? 'border-accent ring-2 ring-accent' : 'hover:border-line'
                    }`}
                  >
                    <div className="relative">
                      {w.cover_url ? (
                        <img
                          src={w.cover_url}
                          alt={w.title}
                          loading="lazy"
                          className="aspect-[3/4] w-full object-cover"
                        />
                      ) : (
                        <div className="flex aspect-[3/4] w-full items-center justify-center bg-fill-deep text-3xl font-medium text-ink-3">
                          {(w.title[0] ?? '影').toUpperCase()}
                        </div>
                      )}
                      <span
                        className="absolute left-2 top-2 rounded-md bg-black/45 p-1 backdrop-blur-sm"
                        onClick={(e) => e.stopPropagation()}
                      >
                        <Checkbox checked={selected} onChange={() => toggleWork(w.id)} />
                      </span>
                      {w.duration != null && (
                        <span className="tnum absolute bottom-2 right-2 rounded bg-black/55 px-1.5 py-0.5 text-[11px] text-white">
                          {formatDuration(w.duration)}
                        </span>
                      )}
                    </div>
                    <div className="p-2.5">
                      <div
                        className="line-clamp-2 text-[13px] font-medium leading-snug text-ink-1"
                        title={w.title}
                      >
                        {w.title}
                      </div>
                      <div className="tnum mt-1 flex items-center gap-3 text-xs text-ink-3">
                        <span className="inline-flex items-center gap-1">
                          <Heart size={12} />
                          {formatLikeCount(w.like_count)}
                        </span>
                        {w.duration != null && (
                          <span className="inline-flex items-center gap-1">
                            <Clock size={12} />
                            {formatDuration(w.duration)}
                          </span>
                        )}
                      </div>
                    </div>
                  </div>
                )
              })}
            </div>
          ) : (
            <Panel>
              <div className="py-8 text-center text-[13px] text-ink-3">
                该作者暂无公开作品
              </div>
            </Panel>
          )}
        </div>
      )}
    </div>
  )
}
