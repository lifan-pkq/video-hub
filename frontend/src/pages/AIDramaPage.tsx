import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { FilmStrip, Plus, Trash, ArrowClockwise, Stop, WarningCircle } from '@phosphor-icons/react'
import {
  cancelDramaProject,
  createDramaProject,
  deleteDramaProject,
  errorMessage,
  fetchDramaProjects,
  resumeDramaProject,
  type DramaProjectSummary,
} from '../api'
import { Alert, Button, Input, PageHeader, Panel, Progress, Select, Switch, TextArea, toast } from '../ui'

const RATIOS = [
  { value: '9:16', label: '9:16 竖屏（抖音）' },
  { value: '16:9', label: '16:9 横屏' },
  { value: '3:4', label: '3:4 竖版' },
  { value: '1:1', label: '1:1 方形' },
]
const STYLE_OPTIONS = [
  { value: 'realistic', label: '写实 · 电影感' },
  { value: 'semi', label: '半写实 · 插画' },
  { value: 'anime', label: '日系动漫' },
  { value: 'guofeng', label: '国风工笔' },
]
const ATTEMPT_OPTIONS = [5, 8, 12, 20].map((n) => ({ value: String(n), label: `${n} 次` }))
const BOARD_OPTIONS = [
  { value: '1', label: '1 张（仅首帧）' },
  { value: '2', label: '2 张分镜图' },
  { value: '3', label: '3 张分镜图' },
  { value: '4', label: '4 张分镜图' },
]

const STATUS_LABEL: Record<string, string> = {
  pending: '排队中',
  running: '生成中',
  planned: '待开始',
  success: '已完成',
  failed: '已中断',
  cancelled: '已取消',
}

export function StatusTag({ status }: { status: string }) {
  const color = status === 'success'
    ? 'text-success bg-success/10'
    : status === 'failed'
      ? 'text-danger bg-danger/10'
      : status === 'running' || status === 'planned'
        ? 'text-accent bg-accent/10'
        : 'text-ink-3 bg-fill-deep'
  return <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs ${color}`}>{STATUS_LABEL[status] || status}</span>
}

export default function AIDramaPage() {
  const navigate = useNavigate()
  const [title, setTitle] = useState('')
  const [novel, setNovel] = useState('')
  const [aspectRatio, setAspectRatio] = useState('9:16')
  const [style, setStyle] = useState('realistic')
  const [burnSubtitles, setBurnSubtitles] = useState(false)
  const [maxAttempts, setMaxAttempts] = useState('8')
  const [storyboardFrames, setStoryboardFrames] = useState('3')
  const [creating, setCreating] = useState(false)
  const [projects, setProjects] = useState<DramaProjectSummary[]>([])
  const [loadError, setLoadError] = useState('')
  // 列表里展开查看的失败原因（默认只显示一个失败 icon，点 icon 才展开）
  const [errorOpenId, setErrorOpenId] = useState('')
  const timerRef = useRef(0)

  const loadProjects = useCallback(async () => {
    try {
      setProjects(await fetchDramaProjects())
      setLoadError('')
    } catch (error) {
      setLoadError(errorMessage(error))
    }
  }, [])

  useEffect(() => {
    loadProjects()
    timerRef.current = window.setInterval(() => {
      loadProjects()
    }, 5000)
    return () => window.clearInterval(timerRef.current)
  }, [loadProjects])

  const submit = async () => {
    if (novel.trim().length < 200) {
      toast.error('请粘贴完整小说内容（至少 200 字）')
      return
    }
    setCreating(true)
    try {
      // 集数、每集镜头数、每镜时长都由 AI 规划，这里只传画幅/画风等生成偏好。
      const project = await createDramaProject({
        novel,
        title,
        aspect_ratio: aspectRatio,
        style,
        burn_subtitles: burnSubtitles,
        max_attempts: Number(maxAttempts),
        storyboard_frames: Number(storyboardFrames),
      })
      toast.success('短剧项目已创建，AI 正在规划分集分镜')
      setNovel('')
      setTitle('')
      // 创建成功后跳到结果页（全屏、无左侧导航）
      navigate(`/ai-drama/${project.id}`)
    } catch (error) {
      toast.error(errorMessage(error))
    } finally {
      setCreating(false)
    }
  }

  const action = async (kind: 'resume' | 'cancel' | 'delete', id: string) => {
    try {
      if (kind === 'resume') {
        await resumeDramaProject(id)
        toast.success('已从断点继续生成')
        // 续跑后直接跳到结果页看进度
        navigate(`/ai-drama/${id}`)
        return
      } else if (kind === 'cancel') {
        await cancelDramaProject(id)
        toast.success('已请求取消，产物保留可续跑')
      } else {
        if (!window.confirm('确定删除该项目？项目目录与成片都会被删除。')) return
        await deleteDramaProject(id)
        toast.success('项目已删除')
      }
      loadProjects()
    } catch (error) {
      toast.error(errorMessage(error))
    }
  }

  return (
    <div>
      <PageHeader
        title="AI 短剧"
        desc="粘贴小说，自动生成短剧。"
      />

      {loadError && <Alert type="error" className="mb-4">{loadError}</Alert>}

      <div className="grid items-start gap-6 lg:grid-cols-[360px_minmax(0,1fr)]">
        {/* 左侧：项目列表 */}
        <div className="flex flex-col gap-3">
          <h2 className="section-title">短剧项目（{projects.length}）</h2>
          {projects.length === 0 && <p className="text-sm text-ink-3">还没有项目，先在右侧粘贴小说创建第一部短剧。</p>}
          {projects.map((project) => (
            <Panel key={project.id} pad="sm" className="cursor-pointer hover:border-accent">
              <div
                role="button"
                tabIndex={0}
                onClick={() => navigate(`/ai-drama/${project.id}`)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault()
                    navigate(`/ai-drama/${project.id}`)
                  }
                }}
              >
                {/* 第 1 行：图标 + 剧名 + 状态 + 失败 icon（悬停看全文，点开展开） */}
                <div className="flex items-center gap-2">
                  <FilmStrip size={20} className="shrink-0 text-accent" />
                  <strong className="min-w-0 flex-1 truncate text-sm">{project.title || '未命名短剧'}</strong>
                  {project.error && (
                    <button
                      type="button"
                      className="shrink-0 text-danger"
                      title={project.error}
                      aria-label="查看失败原因"
                      onClick={(event) => {
                        event.stopPropagation()
                        setErrorOpenId(errorOpenId === project.id ? '' : project.id)
                      }}
                    >
                      <WarningCircle size={16} weight="fill" />
                    </button>
                  )}
                  <span className="shrink-0"><StatusTag status={project.status} /></span>
                </div>
                {/* 第 2 行：集数 / 总镜头 / 创建时间，单行截断 */}
                <p className="mt-1.5 truncate text-xs text-ink-3">
                  {project.options.episodes_count} 集 · {project.episodes.reduce((sum, ep) => sum + (ep.shots_total || 0), 0)} 镜 · {project.created_at}
                </p>
                {/* 第 3 行：进度条通栏 + 百分比 */}
                <div className="mt-2.5 flex items-center gap-2">
                  <div className="min-w-0 flex-1">
                    <Progress percent={(project.progress || 0) / 100} status={project.status === 'failed' ? 'error' : project.status === 'success' ? 'success' : 'normal'} />
                  </div>
                  <span className="shrink-0 text-xs text-ink-3">{Math.round(project.progress || 0)}%</span>
                </div>
                {/* 第 4 行：操作按钮，点卡片本身就是查看结果 */}
                <div className="mt-2.5 flex items-center gap-1.5" onClick={(event) => event.stopPropagation()}>
                  {(project.status === 'failed' || project.status === 'cancelled') && (
                    <Button size="sm" icon={<ArrowClockwise size={14} />} onClick={() => action('resume', project.id)}>继续</Button>
                  )}
                  {(project.status === 'running' || project.status === 'pending') && (
                    <Button size="sm" variant="ghost" icon={<Stop size={14} />} onClick={() => action('cancel', project.id)}>取消</Button>
                  )}
                  <Button size="sm" variant="text" icon={<Trash size={14} />} aria-label="删除项目" className="ml-auto" onClick={() => action('delete', project.id)} />
                </div>
              </div>
              {/* 失败原因默认不占地方：只有点了上面那个红色 icon 才展开显示全文 */}
              {project.error && errorOpenId === project.id && (
                <p className="mt-2 rounded-lg bg-danger/5 p-2 text-xs leading-relaxed text-danger">{project.error}</p>
              )}
            </Panel>
          ))}
        </div>

        {/* 中间：开始部署 */}
        <Panel title="开始部署">
          <div className="flex flex-col gap-4">
            <label className="text-xs text-ink-3">剧名（可选，留空由 AI 命名）
              <Input value={title} onChange={setTitle} placeholder="例如：重生之我在京城当首富" className="mt-1" />
            </label>
            <label className="text-xs text-ink-3">完整小说内容（至少 200 字，当前 {novel.trim().length} 字）
              <TextArea
                value={novel}
                onChange={setNovel}
                rows={10}
                className="mt-1"
                placeholder="把完整小说粘贴到这里。AI 会自动提取主要角色并生成固定外貌/服装档案，再拆成每集分镜。"
              />
            </label>
            <div className="flex flex-wrap items-end gap-4">
              <label className="text-xs text-ink-3">画幅
                <Select value={aspectRatio} onChange={setAspectRatio} options={RATIOS} className="mt-1" width={160} />
              </label>
              <label className="text-xs text-ink-3">画风
                <Select value={style} onChange={setStyle} options={STYLE_OPTIONS} className="mt-1" width={160} />
              </label>
              <label className="text-xs text-ink-3" title="单个生成步骤失败后的最大换 Key 重试次数，超限可断点续跑">
                单步重试上限
                <Select value={maxAttempts} onChange={setMaxAttempts} options={ATTEMPT_OPTIONS} className="mt-1" width={110} />
              </label>
              <label className="text-xs text-ink-3" title="每镜按剧情出几张连环画分镜图，第 1 张就是首帧图">
                每镜分镜图
                <Select value={storyboardFrames} onChange={setStoryboardFrames} options={BOARD_OPTIONS} className="mt-1" width={150} />
              </label>
              <label className="flex items-center gap-2 text-xs text-ink-3">
                <Switch checked={burnSubtitles} onChange={setBurnSubtitles} label="烧录台词字幕" />
                烧录台词字幕
              </label>
              <Button variant="primary" icon={<Plus size={16} />} loading={creating} onClick={submit} className="ml-auto">
                开始生成短剧
              </Button>
            </div>
            <p className="text-xs text-ink-4">
              集数、每集镜头数、每镜时长都由 AI 根据小说自主决定。开始后先在结果页展示全部镜头规划，确认后点「开始生成」才正式生成，中断后点「继续」可断点续跑。
            </p>
          </div>
        </Panel>
      </div>
    </div>
  )
}
