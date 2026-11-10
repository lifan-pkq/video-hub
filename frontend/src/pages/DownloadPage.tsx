import { useCallback, useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { DownloadSimple, Link as LinkIcon, Trash } from '@phosphor-icons/react'
import {
  addInspiration,
  createBatchDownload,
  deleteInspiration,
  errorDetail,
  errorMessage,
  fetchInspirations,
  fetchTask,
  parseDownload,
  type Inspiration,
  type ParseResult,
  type Problem,
  type Task,
} from '../api'
import { Alert, Button, EmptyState, Input, PageHeader, Panel, ProblemAlert, Progress, TextArea, toast } from '../ui'
import StatusBadge from '../components/StatusBadge'

export default function DownloadPage() {
  const [searchParams] = useSearchParams()
  const [text, setText] = useState('')
  const [parsed, setParsed] = useState<ParseResult | null>(null)
  const [parsing, setParsing] = useState(false)
  const [tasks, setTasks] = useState<Task[]>([])
  const [downloading, setDownloading] = useState(false)
  const [problem, setProblem] = useState<Problem | null>(null)
  const [inspirations, setInspirations] = useState<Inspiration[]>([])
  const [inspirationUrl, setInspirationUrl] = useState('')
  const [note, setNote] = useState('')
  const [notesOpen, setNotesOpen] = useState(false)

  const loadInspirations = useCallback(async () => {
    try {
      setInspirations(await fetchInspirations())
    } catch (error) {
      toast.error('灵感库加载失败：' + errorMessage(error))
    }
  }, [])

  useEffect(() => { void loadInspirations() }, [loadInspirations])

  useEffect(() => {
    const focus = searchParams.get('task')
    if (!focus) return
    void fetchTask(focus).then((task) => {
      setTasks((current) => current.some((item) => item.id === task.id) ? current : [task, ...current])
    }).catch((error) => setProblem(errorDetail(error)))
  }, [searchParams])

  useEffect(() => {
    if (!tasks.some((task) => task.status === 'pending' || task.status === 'running')) return
    const timer = window.setInterval(() => {
      void Promise.all(tasks.map((task) => fetchTask(task.id).catch(() => task))).then((rows) => setTasks(rows))
    }, 2500)
    return () => window.clearInterval(timer)
  }, [tasks])

  const parse = async () => {
    const first = text.split('\n').map((line) => line.trim()).find(Boolean)
    if (!first) {
      setProblem({ code: 'url_missing', message: '还没有可解析的链接。', action: '先粘贴一条以 http 开头的视频链接。' })
      return
    }
    setParsing(true)
    setProblem(null)
    try {
      setParsed(await parseDownload(first))
    } catch (error) {
      setParsed(null)
      setProblem(errorDetail(error))
    } finally {
      setParsing(false)
    }
  }

  const download = async () => {
    const links = text.split('\n').map((line) => line.trim()).filter(Boolean)
    if (!links.length) {
      setProblem({ code: 'url_missing', message: '还没有可下载的链接。', action: '每行粘贴一条视频链接，再开始下载。' })
      return
    }
    setDownloading(true)
    setProblem(null)
    try {
      const result = await createBatchDownload(links)
      const rows = await Promise.all(result.task_ids.map((id) => fetchTask(id)))
      setTasks(rows)
      toast.success(`已提交 ${result.count} 个下载，进度在本页和任务中心同步`)
    } catch (error) {
      setProblem(errorDetail(error))
    } finally {
      setDownloading(false)
    }
  }

  const saveInspiration = async () => {
    if (!inspirationUrl.trim()) {
      toast.warning('先填写要记下来的链接')
      return
    }
    try {
      await addInspiration(inspirationUrl.trim(), note.trim())
      setInspirationUrl('')
      setNote('')
      await loadInspirations()
      toast.success('已保存笔记，没有下载作者视频')
    } catch (error) {
      toast.error('保存失败：' + errorMessage(error))
    }
  }

  return (
    <section>
      <PageHeader
        title="视频下载"
        desc="只通过 yt-dlp 下载。Cookie 失效或画质拿不到时，会直接告诉你下一步。"
        extra={<Link to="/tasks"><Button variant="ghost">任务中心</Button></Link>}
      />
      <div className="grid gap-5 xl:grid-cols-[minmax(0,1.2fr)_minmax(300px,0.8fr)]">
        <div className="space-y-5">
          <Panel title="解析并下载">
            <TextArea value={text} onChange={setText} rows={5} placeholder="每行一个视频链接" />
            <div className="mt-3 flex flex-wrap gap-2">
              <Button variant="ghost" loading={parsing} onClick={() => void parse()}>解析最高画质</Button>
              <Button variant="primary" icon={<DownloadSimple size={15} />} loading={downloading} onClick={() => void download()}>开始下载</Button>
              <Link to="/library/import"><Button variant="ghost">改用导入向导</Button></Link>
            </div>
            {problem && <ProblemAlert message={problem.message} action={problem.action} className="mt-3" />}
            {parsed && (
              <div className="mt-4 rounded-xl border border-line-soft bg-fill p-3 text-[12px] text-ink-3">
                <p className="text-ink-1">{parsed.title}</p>
                <p className="mt-1">{parsed.author || '未知作者'} · {parsed.platform}</p>
                {parsed.available_best && <p className="mt-1">可用最高：{parsed.available_best.width ?? '?'}×{parsed.available_best.height ?? '?'} · {parsed.available_best.format_note || parsed.available_best.vcodec || '格式未知'}</p>}
              </div>
            )}
          </Panel>
          <Panel title="这次提交的下载" extra={<Link to="/tasks" className="text-xs text-accent">全部任务 →</Link>}>
            {tasks.length === 0 ? (
              <EmptyState title="还没有下载任务" desc="提交后可以离开页面。失败会带上原因，例如 Cookie 需要更新。" />
            ) : (
              <div className="space-y-3">
                {tasks.map((task) => (
                  <div key={task.id} className="rounded-xl border border-line-soft bg-fill p-3">
                    <div className="flex items-center gap-2">
                      <span className="min-w-0 flex-1 truncate text-[13px] text-ink-1">{task.title}</span>
                      <StatusBadge status={task.status} />
                    </div>
                    <Progress percent={(task.progress || 0) / 100} className="mt-2" status={task.status === 'failed' ? 'error' : 'normal'} />
                    {task.status === 'failed' && task.error?.message ? (
                      <ProblemAlert className="mt-2" message={task.error.message} action={task.error.action} />
                    ) : (
                      <p className="mt-1 truncate text-[11px] text-ink-4">{task.logs?.[task.logs.length - 1] || '等待开始'}</p>
                    )}
                    {task.result?.video && (
                      <Link to={`/videos?q=${encodeURIComponent(task.result.video.title)}`} className="mt-2 inline-block text-[12px] text-accent">已入库，去素材库查看</Link>
                    )}
                  </div>
                ))}
              </div>
            )}
          </Panel>
        </div>
        <Panel title="灵感笔记" extra={<Button size="sm" variant="ghost" onClick={() => setNotesOpen((open) => !open)}>{notesOpen || inspirations.length === 0 ? '收起表单' : '记一条'}</Button>}>
          <Alert type="info">只保存链接和你的笔记，不会抓取作者视频，也不会进剪辑。</Alert>
          {(notesOpen || inspirations.length === 0) && (
            <div className="mt-3 space-y-3">
              <Input value={inspirationUrl} onChange={setInspirationUrl} placeholder="https://…" />
              <TextArea value={note} onChange={setNote} rows={3} placeholder="这个开头或转场为什么值得记？" />
              <Button icon={<LinkIcon size={14} />} onClick={() => void saveInspiration()}>保存笔记</Button>
            </div>
          )}
          <div className="mt-3 max-h-[420px] space-y-2 overflow-auto">
            {inspirations.length === 0 ? (
              <EmptyState title="还没有笔记" desc="看到值得参考的成片时，把链接记在这里即可。" />
            ) : inspirations.map((item) => (
              <div key={item.id} className="rounded-xl border border-line-soft bg-fill p-3">
                <div className="flex items-start gap-2">
                  <a href={item.url} target="_blank" rel="noreferrer" className="min-w-0 flex-1 break-all text-[12px] text-accent">{item.url}</a>
                  <Button size="sm" variant="text" icon={<Trash size={12} />} onClick={() => void deleteInspiration(item.id).then(loadInspirations).catch((error) => toast.error(errorMessage(error)))}>删除</Button>
                </div>
                {item.note && <p className="mt-2 text-[11px] leading-5 text-ink-3">{item.note}</p>}
              </div>
            ))}
          </div>
        </Panel>
      </div>
    </section>
  )
}
