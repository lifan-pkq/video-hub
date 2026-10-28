import { useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  ArrowClockwise,
  ArrowLeft,
  CheckCircle,
  Clock,
  DownloadSimple,
  Heart,
  Link as LinkIcon,
} from '@phosphor-icons/react'
import {
  createBatchDownload,
  createDownload,
  errorMessage,
  formatDuration,
  parseAuthorWorks,
  parseDownload,
  platformLabel,
  type AuthorWork,
  type AuthorWorksResult,
  type ParseResult,
} from '../api'
import {
  Alert,
  Button,
  Checkbox,
  Input,
  PageHeader,
  Panel,
  PlatformIcon,
  Segment,
  Spinner,
  TextArea,
  toast,
} from '../ui'
import WizardSteps from '../components/WizardSteps'

/** 批量下载单次上限（与后端 /download/batch 一致） */
const MAX_BATCH_SELECT = 100

const STEPS = [
  { key: 'source', label: '选择方式' },
  { key: 'parse', label: '解析选择' },
  { key: 'confirm', label: '确认下载' },
]

type Mode = 'single' | 'batch' | 'author'

const MODE_LABEL: Record<Mode, string> = {
  single: '单条视频',
  batch: '批量链接',
  author: '作者主页',
}

/** 点赞数 → 1.2万 风格 */
function formatLikeCount(n: number | null): string {
  if (n == null) return '-'
  if (n >= 10000) return `${(n / 10000).toFixed(1)}万`
  return String(n)
}

export default function ImportWizardPage() {
  const [step, setStep] = useState(0)
  const [mode, setMode] = useState<Mode>('single')

  // 单条
  const [text, setText] = useState('')
  const [parsing, setParsing] = useState(false)
  const [parseError, setParseError] = useState<string | null>(null)
  const [preview, setPreview] = useState<ParseResult | null>(null)

  // 批量
  const [batchText, setBatchText] = useState('')

  // 作者主页
  const [authorText, setAuthorText] = useState('')
  const [authorParsing, setAuthorParsing] = useState(false)
  const [authorError, setAuthorError] = useState<string | null>(null)
  const [authorResult, setAuthorResult] = useState<AuthorWorksResult | null>(null)
  const [checkedIds, setCheckedIds] = useState<Set<string>>(new Set())

  // 提交 / 成功页
  const [submitting, setSubmitting] = useState(false)
  const [doneCount, setDoneCount] = useState<number | null>(null)

  // 组件卸载后置 false，阻断在途请求的 setState
  const aliveRef = useRef(true)
  useEffect(() => {
    aliveRef.current = true
    return () => {
      aliveRef.current = false
    }
  }, [])

  /** 批量链接：trim 去空行 */
  const rawLines = useMemo(
    () =>
      batchText
        .split('\n')
        .map((s) => s.trim())
        .filter(Boolean),
    [batchText],
  )
  /** 去重后的链接清单（保序） */
  const batchLines = useMemo(() => [...new Set(rawLines)], [rawLines])
  const dupCount = rawLines.length - batchLines.length

  const selectedWorks = useMemo(
    () => (authorResult ? authorResult.works.filter((w) => checkedIds.has(w.id)) : []),
    [authorResult, checkedIds],
  )

  // ---- 解析动作 ----

  const runParseSingle = async () => {
    const q = text.trim()
    if (!q) return
    setParsing(true)
    setParseError(null)
    setPreview(null)
    try {
      const result = await parseDownload(q)
      if (aliveRef.current) setPreview(result)
    } catch (e) {
      if (aliveRef.current) setParseError(errorMessage(e))
    } finally {
      if (aliveRef.current) setParsing(false)
    }
  }

  const runParseAuthor = async () => {
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

  /** 第①步 → 第②步 */
  const goNext = () => {
    if (mode === 'single') {
      if (!text.trim()) return
      setStep(1)
      runParseSingle()
    } else if (mode === 'batch') {
      if (batchLines.length === 0) return
      setStep(1)
    } else {
      if (!authorText.trim()) return
      setStep(1)
      runParseAuthor()
    }
  }

  /** 第②步单条/作者解析是否就绪，可进入第③步 */
  const step2Ready =
    mode === 'batch'
      ? batchLines.length > 0
      : mode === 'single'
        ? preview !== null
        : selectedWorks.length > 0

  // ---- 作者作品多选 ----

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
    if (works.length > MAX_BATCH_SELECT)
      toast.warning(`最多选择 ${MAX_BATCH_SELECT} 条，已选中前 ${MAX_BATCH_SELECT} 条`)
    setCheckedIds(new Set(works.slice(0, MAX_BATCH_SELECT).map((w) => w.id)))
  }

  const clearWorks = () => setCheckedIds(new Set())

  // ---- 第③步：提交下载 ----

  const handleSubmit = async () => {
    setSubmitting(true)
    try {
      if (mode === 'single') {
        await createDownload(text.trim())
        setDoneCount(1)
      } else if (mode === 'batch') {
        const { task_ids } = await createBatchDownload(batchLines)
        setDoneCount(task_ids.length)
      } else {
        const urls = selectedWorks.map((w) => w.url)
        if (urls.length === 0) return
        const { task_ids } = await createBatchDownload(urls)
        setDoneCount(task_ids.length)
      }
      if (aliveRef.current) toast.success('下载任务已创建，进度请在任务中心查看')
    } catch (e) {
      if (aliveRef.current) toast.error(`创建下载任务失败：${errorMessage(e)}`)
    } finally {
      if (aliveRef.current) setSubmitting(false)
    }
  }

  /** 成功页 → 重新开始新一轮导入 */
  const restart = () => {
    setDoneCount(null)
    setStep(0)
    setPreview(null)
    setParseError(null)
    setAuthorResult(null)
    setCheckedIds(new Set())
    setAuthorError(null)
  }

  // ---- 成功页 ----
  if (doneCount !== null) {
    return (
      <div>
        <PageHeader title="导入素材" desc="粘贴分享链接，解析后一键入库" />
        <div className="mx-auto max-w-2xl">
          <Panel>
            <div className="flex flex-col items-center py-10 text-center">
              <CheckCircle size={48} weight="fill" className="text-success" />
              <div className="mt-4 text-[16px] font-medium text-ink-1">
                已创建 {doneCount} 个下载任务
              </div>
              <p className="mt-1.5 text-[13px] text-ink-3">
                任务正在后台排队执行，下载进度与结果请留意通知中心
              </p>
              <div className="mt-6 flex flex-wrap items-center justify-center gap-3">
                <Link to="/tasks">
                  <Button variant="primary">查看任务中心</Button>
                </Link>
                <Link to="/library">
                  <Button>返回素材库</Button>
                </Link>
              </div>
              <Button variant="text" size="sm" className="mt-3" onClick={restart}>
                继续导入更多 →
              </Button>
            </div>
          </Panel>
        </div>
      </div>
    )
  }

  return (
    <div>
      <PageHeader title="导入素材" desc="单条 / 批量链接 / 作者主页三种方式，解析后一键入库" />

      <div className={step === 1 && mode === 'author' && authorResult ? '' : 'mx-auto max-w-2xl'}>
        <div className="vh-card mb-4 p-4 md:p-5">
          <WizardSteps steps={STEPS} current={step} />
        </div>

        {/* ============ 第①步：选择方式 + 粘贴链接 ============ */}
        {step === 0 && (
          <Panel title="选择导入方式">
            <Segment
              value={mode}
              onChange={(v) => setMode(v as Mode)}
              options={[
                { value: 'single', label: '单条视频' },
                { value: 'batch', label: '批量链接' },
                { value: 'author', label: '作者主页' },
              ]}
              className="mb-4"
            />

            {mode === 'single' && (
              <TextArea
                value={text}
                onChange={setText}
                rows={4}
                placeholder={'粘贴抖音 / 快手 / 小红书 / B站 分享口令或链接，例如：\n7.94 复制打开抖音，看看【xxx的作品】... https://v.douyin.com/xxxx/'}
              />
            )}
            {mode === 'batch' && (
              <>
                <TextArea
                  value={batchText}
                  onChange={setBatchText}
                  rows={7}
                  placeholder="每行粘贴一条分享口令或链接，最多 100 条"
                />
                <div className="mt-2 text-[13px] text-ink-3">
                  已识别 <span className="tnum text-ink-1">{batchLines.length}</span> 条链接
                  {dupCount > 0 && <span className="text-warning">（已自动去重 {dupCount} 条）</span>}
                </div>
              </>
            )}
            {mode === 'author' && (
              <>
                <Input
                  value={authorText}
                  onChange={setAuthorText}
                  allowClear
                  prefix={<LinkIcon size={15} />}
                  placeholder="粘贴作者主页分享链接，例如：https://v.douyin.com/xxx"
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') goNext()
                  }}
                />
                <p className="mt-2 text-xs text-ink-4">
                  将抓取该作者的公开作品列表，勾选后可批量下载
                </p>
              </>
            )}

            <div className="mt-4 flex justify-end">
              <Button
                variant="primary"
                disabled={
                  mode === 'single'
                    ? !text.trim()
                    : mode === 'batch'
                      ? batchLines.length === 0
                      : !authorText.trim()
                }
                onClick={goNext}
              >
                下一步
              </Button>
            </div>
          </Panel>
        )}

        {/* ============ 第②步：解析与选择 ============ */}
        {step === 1 && mode === 'single' && (
          <Panel title="解析结果">
            {parsing ? (
              <div className="flex items-center gap-3 py-8 text-[13px] text-ink-3">
                <Spinner /> 正在解析视频信息…
              </div>
            ) : parseError ? (
              <>
                <Alert type="error">解析失败：{parseError}</Alert>
                <div className="mt-4 flex justify-between">
                  <Button variant="ghost" icon={<ArrowLeft size={15} />} onClick={() => setStep(0)}>
                    上一步
                  </Button>
                  <Button variant="primary" loading={parsing} onClick={runParseSingle}>
                    重试解析
                  </Button>
                </div>
              </>
            ) : preview ? (
              <>
                <div className="flex flex-col gap-4 sm:flex-row">
                  {preview.cover_url ? (
                    <img
                      src={preview.cover_url}
                      alt={preview.title}
                      className="h-32 w-24 shrink-0 rounded-lg object-cover"
                    />
                  ) : (
                    <div className="flex h-32 w-24 shrink-0 items-center justify-center rounded-lg bg-fill">
                      <PlatformIcon platform={preview.platform} size={32} />
                    </div>
                  )}
                  <div className="min-w-0 flex-1">
                    <div className="line-clamp-2 font-medium leading-snug text-ink-1" title={preview.title}>
                      {preview.title}
                    </div>
                    <dl className="mt-2 space-y-1.5">
                      <div className="flex gap-3 text-[13px]">
                        <dt className="shrink-0 text-xs leading-5 text-ink-3">平台</dt>
                        <dd className="flex items-center gap-1.5 text-ink-2">
                          <PlatformIcon platform={preview.platform} size={16} />
                          {platformLabel(preview.platform)}
                        </dd>
                      </div>
                      <div className="flex gap-3 text-[13px]">
                        <dt className="shrink-0 text-xs leading-5 text-ink-3">作者</dt>
                        <dd className="min-w-0 truncate text-ink-2">{preview.author || '未知'}</dd>
                      </div>
                      <div className="flex gap-3 text-[13px]">
                        <dt className="shrink-0 text-xs leading-5 text-ink-3">时长</dt>
                        <dd className="tnum text-ink-2">{formatDuration(preview.duration)}</dd>
                      </div>
                    </dl>
                  </div>
                </div>
                <div className="mt-5 flex justify-between">
                  <Button variant="ghost" icon={<ArrowLeft size={15} />} onClick={() => setStep(0)}>
                    上一步
                  </Button>
                  <Button variant="primary" onClick={() => setStep(2)}>
                    下一步
                  </Button>
                </div>
              </>
            ) : null}
          </Panel>
        )}

        {step === 1 && mode === 'batch' && (
          <Panel
            title="链接清单"
            extra={
              <span className="text-[13px] text-ink-3">
                共 <span className="tnum text-ink-1">{batchLines.length}</span> 条
                {dupCount > 0 && <span className="text-warning">（去重 {dupCount} 条）</span>}
              </span>
            }
          >
            <ol className="max-h-[320px] space-y-1.5 overflow-y-auto pr-1">
              {batchLines.map((line, i) => (
                <li
                  key={i}
                  className="flex items-center gap-2 rounded-lg bg-fill px-3 py-2 text-[13px] text-ink-2"
                >
                  <span className="tnum w-6 shrink-0 text-ink-4">{i + 1}.</span>
                  <span className="min-w-0 flex-1 truncate" title={line}>
                    {line}
                  </span>
                </li>
              ))}
            </ol>
            <div className="mt-5 flex justify-between">
              <Button variant="ghost" icon={<ArrowLeft size={15} />} onClick={() => setStep(0)}>
                上一步
              </Button>
              <Button variant="primary" onClick={() => setStep(2)}>
                下一步
              </Button>
            </div>
          </Panel>
        )}

        {step === 1 && mode === 'author' && (
          <>
            {/* 加载态 */}
            {authorParsing && (
              <Panel>
                <div className="flex flex-col items-center gap-3 py-12 text-center">
                  <Spinner size={26} />
                  <div className="text-[14px] font-medium text-ink-1">正在抓取作者作品列表…</div>
                  <p className="text-[13px] text-ink-3">
                    浏览器模拟抓取耗时较长，可能需要 1-2 分钟，请勿关闭页面
                  </p>
                </div>
              </Panel>
            )}

            {/* 失败态：明确报错 + 重试 */}
            {!authorParsing && authorError && (
              <Panel>
                <Alert type="error">
                  获取作者作品失败：{authorError}
                  {authorError.includes('平台账号') && (
                    <Link to="/accounts" className="ml-2 text-accent hover:underline">
                      去平台账号页登录 →
                    </Link>
                  )}
                </Alert>
                <div className="mt-4 flex justify-between">
                  <Button variant="ghost" icon={<ArrowLeft size={15} />} onClick={() => setStep(0)}>
                    上一步
                  </Button>
                  <Button
                    variant="primary"
                    icon={<ArrowClockwise size={15} />}
                    loading={authorParsing}
                    onClick={runParseAuthor}
                  >
                    重试
                  </Button>
                </div>
              </Panel>
            )}

            {/* 成功态：作者信息条 + 作品网格多选 */}
            {!authorParsing && !authorError && authorResult && (
              <>
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
                      icon={<ArrowLeft size={14} />}
                      onClick={() => setStep(0)}
                    >
                      上一步
                    </Button>
                  </div>
                </Panel>

                {authorResult.works.length > 0 ? (
                  <>
                    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4 md:gap-4 xl:grid-cols-5">
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

                    {/* 底部粘性选择栏 */}
                    <div className="sticky bottom-3 z-10 mt-4 flex flex-wrap items-center gap-x-2 gap-y-2 rounded-[14px] border border-line bg-card px-3 py-2 shadow-pop">
                      <span className="text-[13px] text-ink-2">
                        已选 <span className="tnum font-medium text-accent">{checkedIds.size}</span>
                        <span className="text-ink-3"> / 共 {authorResult.works.length}</span>
                      </span>
                      <Button size="sm" variant="text" onClick={selectAllWorks}>
                        全选
                      </Button>
                      <Button size="sm" variant="text" onClick={clearWorks}>
                        清空
                      </Button>
                      <Button
                        variant="primary"
                        size="sm"
                        className="ml-auto"
                        disabled={checkedIds.size === 0}
                        onClick={() => setStep(2)}
                      >
                        下一步{checkedIds.size > 0 ? `（${checkedIds.size}）` : ''}
                      </Button>
                    </div>
                  </>
                ) : (
                  <Panel>
                    <div className="py-8 text-center text-[13px] text-ink-3">该作者暂无公开作品</div>
                    <div className="flex justify-center">
                      <Button variant="ghost" icon={<ArrowLeft size={15} />} onClick={() => setStep(0)}>
                        上一步
                      </Button>
                    </div>
                  </Panel>
                )}
              </>
            )}
          </>
        )}

        {/* ============ 第③步：确认下载 ============ */}
        {step === 2 && (
          <Panel title="确认下载">
            <dl className="space-y-3">
              <div className="flex gap-3 text-[13px]">
                <dt className="w-20 shrink-0 text-ink-3">导入方式</dt>
                <dd className="text-ink-1">{MODE_LABEL[mode]}</dd>
              </div>
              <div className="flex gap-3 text-[13px]">
                <dt className="w-20 shrink-0 text-ink-3">数量</dt>
                <dd className="tnum text-ink-1">
                  {mode === 'single' ? 1 : mode === 'batch' ? batchLines.length : selectedWorks.length} 条
                </dd>
              </div>
              <div className="flex gap-3 text-[13px]">
                <dt className="w-20 shrink-0 text-ink-3">目标</dt>
                <dd className="min-w-0 flex-1 break-all text-ink-2">
                  {mode === 'single' && (preview?.title || text.trim())}
                  {mode === 'batch' && `${batchLines[0] ?? ''}${batchLines.length > 1 ? ` 等 ${batchLines.length} 条` : ''}`}
                  {mode === 'author' && authorResult && `${authorResult.author.name} 的 ${selectedWorks.length} 个作品`}
                </dd>
              </div>
              <div className="flex gap-3 text-[13px]">
                <dt className="w-20 shrink-0 text-ink-3">说明</dt>
                <dd className="text-ink-3">任务创建后在后台串行执行，进度由通知中心与任务中心展示</dd>
              </div>
            </dl>
            <div className="mt-6 flex justify-between">
              <Button variant="ghost" icon={<ArrowLeft size={15} />} onClick={() => setStep(1)}>
                上一步
              </Button>
              <Button
                variant="primary"
                icon={<DownloadSimple size={15} />}
                loading={submitting}
                disabled={!step2Ready}
                onClick={handleSubmit}
              >
                开始下载
              </Button>
            </div>
          </Panel>
        )}
      </div>
    </div>
  )
}
