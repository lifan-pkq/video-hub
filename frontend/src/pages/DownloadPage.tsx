import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { DownloadSimple } from '@phosphor-icons/react'
import {
  createBatchDownload,
  createDownload,
  errorMessage,
  fetchTask,
  fetchTasks,
  formatDuration,
  parseDownload,
  type ParseResult,
  type Task,
  type TaskSummary,
} from '../api'
import { Alert, Button, PageHeader, Panel, PlatformIcon, Progress, Segment, TextArea, toast } from '../ui'
import TaskProgress from '../components/TaskProgress'
import StatusBadge from '../components/StatusBadge'

const FINISHED = ['success', 'failed', 'partial']

export default function DownloadPage() {
  const [mode, setMode] = useState<'single' | 'batch'>('single')

  const [text, setText] = useState('')
  const [parsing, setParsing] = useState(false)
  const [parseError, setParseError] = useState<string | null>(null)
  const [preview, setPreview] = useState<ParseResult | null>(null)
  const [parsedText, setParsedText] = useState<string | null>(null)
  const parseRequestRef = useRef(0)

  const [task, setTask] = useState<Task | null>(null)
  const [starting, setStarting] = useState(false)
  const timerRef = useRef<number | null>(null)

  // 批量队列
  const [batchText, setBatchText] = useState('')
  const [batching, setBatching] = useState(false)
  const [queue, setQueue] = useState<TaskSummary[]>([])
  const batchTimerRef = useRef<number | null>(null)
  const queueIdsRef = useRef<string[]>([])

  const batchLines = batchText
    .split('\n')
    .map((s) => s.trim())
    .filter(Boolean)

  // 卸载时清理轮询
  useEffect(() => {
    return () => {
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
    const input = text.trim()
    if (!input) return
    const requestId = ++parseRequestRef.current
    setParsing(true)
    setParseError(null)
    setPreview(null)
    setParsedText(null)
    setTask(null)
    try {
      const result = await parseDownload(input)
      if (requestId !== parseRequestRef.current) return
      setParsedText(input)
      setPreview(result)
    } catch (e) {
      if (requestId === parseRequestRef.current) setParseError(errorMessage(e))
    } finally {
      if (requestId === parseRequestRef.current) setParsing(false)
    }
  }

  const handleDownload = async () => {
    if (!preview || !parsedText) return
    setStarting(true)
    try {
      const { task_id } = await createDownload(parsedText)
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
            onChange={(v) => setMode(v as 'single' | 'batch')}
            options={[
              { value: 'single', label: '单条解析' },
              { value: 'batch', label: '批量队列' },
            ]}
            className="mb-3"
          />

          {mode === 'single' ? (
            <>
              <TextArea
                value={text}
                onChange={(value) => {
                  parseRequestRef.current += 1
                  setText(value)
                  setParsing(false)
                  setPreview(null)
                  setParsedText(null)
                  setParseError(null)
                }}
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
          ) : (
            <>
              <TextArea
                value={batchText}
                onChange={setBatchText}
                rows={6}
                placeholder={'每行粘贴一条分享口令或链接，最多 20 条'}
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
    </div>
  )
}
