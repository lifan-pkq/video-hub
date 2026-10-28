import { useCallback, useEffect, useMemo, useRef, useState, type ChangeEvent, type KeyboardEvent } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { Checks, DownloadSimple, FilmSlate, MagnifyingGlass, Plus, Scissors, VideoCamera } from '@phosphor-icons/react'
import {
  deleteVideo,
  errorMessage,
  fetchVideos,
  formatDuration,
  formatSize,
  platformLabel,
  renameVideo,
  trimVideo,
  uploadVideo,
  type Video,
} from '../api'
import VideoCard from '../components/VideoCard'
import RequestState from '../ui/RequestState'
import {
  Button,
  Checkbox,
  Dropzone,
  EmptyState,
  Input,
  Modal,
  PageHeader,
  Panel,
  Select,
  confirmDialog,
  toast,
} from '../ui'

type SortKey = 'newest' | 'oldest' | 'largest' | 'longest'

const SORT_OPTIONS: { value: SortKey; label: string }[] = [
  { value: 'newest', label: '最新优先' },
  { value: 'oldest', label: '最旧优先' },
  { value: 'largest', label: '最大文件' },
  { value: 'longest', label: '最长时长' },
]

/** 海报墙网格 */
const GRID_CLASS = 'grid grid-cols-2 gap-4 sm:grid-cols-3 md:grid-cols-4 xl:grid-cols-5 2xl:grid-cols-6'

/** 截取快捷区间（秒） */
const TRIM_PRESETS = [15, 30, 60]

export default function VideosPage() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const [videos, setVideos] = useState<Video[]>([])
  const [loading, setLoading] = useState(true)
  const [hasData, setHasData] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)

  const [playing, setPlaying] = useState<Video | null>(null)

  const [uploading, setUploading] = useState(false)
  const [uploadProgress, setUploadProgress] = useState(0)
  const fileInputRef = useRef<HTMLInputElement>(null)

  // 搜索 / 筛选 / 排序（命令面板可带 ?q= 预填搜索）
  const [search, setSearch] = useState(() => searchParams.get('q') ?? '')
  const [sourceFilter, setSourceFilter] = useState('all')
  const [sortBy, setSortBy] = useState<SortKey>('newest')

  // SPA 内从命令面板跳到 /videos?q= 时组件不重挂载，需监听参数变化
  useEffect(() => {
    const q = searchParams.get('q')
    if (q !== null) setSearch(q)
  }, [searchParams])

  // 多选
  const [selecting, setSelecting] = useState(false)
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set())

  // 重命名
  const [renaming, setRenaming] = useState<Video | null>(null)
  const [renameTitle, setRenameTitle] = useState('')
  const [renameSaving, setRenameSaving] = useState(false)

  // 截取
  const [trimTarget, setTrimTarget] = useState<Video | null>(null)
  const [trimStart, setTrimStart] = useState('0')
  const [trimEnd, setTrimEnd] = useState('')
  const [trimming, setTrimming] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setVideos(await fetchVideos())
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

  const handleUpload = async (file: File) => {
    setUploading(true)
    setUploadProgress(0)
    try {
      // 文件名（去掉扩展名）作为默认标题
      const title = file.name.replace(/\.[^.]+$/, '')
      const v = await uploadVideo(file, title, setUploadProgress)
      toast.success(`已上传「${v.title}」`)
      await load()
    } catch (e) {
      toast.error(`上传失败：${errorMessage(e)}`)
    } finally {
      setUploading(false)
    }
  }

  const handleDelete = async (video: Video) => {
    const ok = await confirmDialog({
      title: '确认删除',
      content: `确定要删除「${video.title}」吗？此操作不可恢复。`,
      okText: '确认删除',
      cancelText: '取消',
      danger: true,
    })
    if (!ok) return
    try {
      await deleteVideo(video.id)
      toast.success('已删除')
      await load()
    } catch (e) {
      toast.error(`删除失败：${errorMessage(e)}`)
    }
  }

  // ================= 多选 =================

  const exitSelecting = () => {
    setSelecting(false)
    setSelectedIds(new Set())
  }

  const toggleSelect = (video: Video) => {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (next.has(video.id)) next.delete(video.id)
      else next.add(video.id)
      return next
    })
  }

  const handleBatchDelete = async () => {
    const targets = videos.filter((v) => selectedIds.has(v.id))
    if (targets.length === 0) return
    const ok = await confirmDialog({
      title: '批量删除',
      content: `将删除 ${targets.length} 个视频及其封面，不可恢复。`,
      okText: '确认删除',
      cancelText: '取消',
      danger: true,
    })
    if (!ok) return
    const results = await Promise.allSettled(targets.map((v) => deleteVideo(v.id)))
    const succeeded = results.filter((r) => r.status === 'fulfilled').length
    const failed = results.length - succeeded
    if (failed === 0) toast.success(`已删除 ${succeeded} 个视频`)
    else if (succeeded === 0) toast.error(`删除失败：${failed} 个视频未能删除`)
    else toast.warning(`已删除 ${succeeded} 个，失败 ${failed} 个`)
    exitSelecting()
    await load()
  }

  // ================= 重命名 / 下载 =================

  const openRename = (video: Video) => {
    setRenaming(video)
    setRenameTitle(video.title)
  }

  const submitRename = async () => {
    if (!renaming) return
    const title = renameTitle.trim()
    if (!title) {
      toast.warning('标题不能为空')
      return
    }
    setRenameSaving(true)
    try {
      const updated = await renameVideo(renaming.id, title)
      // 本地更新，不必整表刷新
      setVideos((prev) => prev.map((v) => (v.id === updated.id ? { ...v, title: updated.title } : v)))
      toast.success('已重命名')
      setRenaming(null)
    } catch (e) {
      toast.error(`重命名失败：${errorMessage(e)}`)
    } finally {
      setRenameSaving(false)
    }
  }

  const handleDownload = (video: Video) => {
    const a = document.createElement('a')
    a.href = video.file_url
    a.download = `${video.title}.mp4`
    document.body.appendChild(a)
    a.click()
    a.remove()
  }

  // ================= 截取 =================

  // ================= AI 智能剪辑 =================

  const handleAiEdit = (video: Video) => {
    // 跳到剪辑页：该视频自动进时间线并立即开始 AI 分析
    navigate('/edit', { state: { videoIds: [video.id], autoAnalyze: true } })
  }

  const openTrim = (video: Video) => {
    setTrimTarget(video)
    setTrimStart('0')
    setTrimEnd(video.duration != null ? String(Math.round(video.duration * 10) / 10) : '')
  }

  const trimDuration = trimTarget?.duration ?? null
  const trimStartNum = parseFloat(trimStart)
  const trimEndNum = parseFloat(trimEnd)

  // 校验：0 ≤ start < end ≤ duration（duration 未知时不校验上限）
  let trimError = ''
  if (trimTarget) {
    if (Number.isNaN(trimStartNum) || Number.isNaN(trimEndNum)) trimError = '请输入有效的秒数'
    else if (trimStartNum < 0) trimError = '开始时间不能小于 0'
    else if (trimDuration != null && trimEndNum > trimDuration)
      trimError = `结束时间不能超过视频时长（${formatDuration(trimDuration)}）`
    else if (trimStartNum >= trimEndNum) trimError = '开始时间必须小于结束时间'
  }
  const trimValid = !!trimTarget && trimError === ''

  const applyTrimPreset = (seconds: number) => {
    setTrimStart('0')
    setTrimEnd(String(trimDuration != null ? Math.min(seconds, Math.round(trimDuration * 10) / 10) : seconds))
  }

  const submitTrim = async () => {
    if (!trimTarget || !trimValid) return
    setTrimming(true)
    try {
      await trimVideo(trimTarget.id, trimStartNum, trimEndNum)
      toast.success('已生成截取片段')
      setTrimTarget(null)
      await load()
    } catch (e) {
      toast.error(`截取失败：${errorMessage(e)}`)
    } finally {
      setTrimming(false)
    }
  }

  // ================= 派生数据 =================

  const totalSize = useMemo(() => videos.reduce((sum, v) => sum + v.size, 0), [videos])

  // 来源筛选选项：按列表中实际出现的 source_platform 动态生成
  const sourceOptions = useMemo(() => {
    const keys = Array.from(new Set(videos.map((v) => v.source_platform ?? 'local')))
    return [{ value: 'all', label: '全部' }, ...keys.map((k) => ({ value: k, label: platformLabel(k) }))]
  }, [videos])

  const filtered = useMemo(() => {
    const kw = search.trim().toLowerCase()
    const list = videos.filter((v) => {
      if (kw && !v.title.toLowerCase().includes(kw)) return false
      if (sourceFilter !== 'all' && (v.source_platform ?? 'local') !== sourceFilter) return false
      return true
    })
    const sorted = [...list]
    switch (sortBy) {
      case 'oldest':
        sorted.sort((a, b) => a.created_at.localeCompare(b.created_at))
        break
      case 'largest':
        sorted.sort((a, b) => b.size - a.size)
        break
      case 'longest':
        sorted.sort((a, b) => (b.duration ?? 0) - (a.duration ?? 0))
        break
      default:
        sorted.sort((a, b) => b.created_at.localeCompare(a.created_at))
    }
    return sorted
  }, [videos, search, sourceFilter, sortBy])

  const allChecked = filtered.length > 0 && filtered.every((v) => selectedIds.has(v.id))
  const someChecked = filtered.some((v) => selectedIds.has(v.id))

  const toggleSelectAll = () => {
    setSelectedIds(allChecked ? new Set() : new Set(filtered.map((v) => v.id)))
  }

  // ================= 渲染 =================

  return (
    <div>
      <PageHeader
        title="视频库"
        desc={`共 ${videos.length} 个视频 · 总大小 ${formatSize(totalSize)}`}
        extra={
          <Button
            variant="primary"
            icon={<Plus size={15} weight="bold" />}
            loading={uploading}
            onClick={() => fileInputRef.current?.click()}
          >
            上传视频
          </Button>
        }
      />
      <input
        ref={fileInputRef}
        type="file"
        accept="video/*"
        className="hidden"
        onChange={(e: ChangeEvent<HTMLInputElement>) => {
          const f = e.target.files?.[0]
          if (f) handleUpload(f)
          e.target.value = ''
        }}
      />

      {/* 上传区（常驻紧凑） */}
      <div className="mb-6 [&_[role=button]]:py-6">
        <Dropzone
          accept="video/*"
          uploading={uploading}
          progress={uploadProgress}
          tip="支持本地视频文件，上传后可发布到各平台"
          onFile={handleUpload}
        />
      </div>

      {/* 工具栏 */}
      {selecting ? (
        <div className="mb-4 flex flex-wrap items-center gap-3">
          <span className="text-sm text-ink-2">
            已选 <span className="tnum">{selectedIds.size}</span> 项
          </span>
          <Checkbox
            checked={allChecked}
            indeterminate={!allChecked && someChecked}
            onChange={toggleSelectAll}
            label="全选"
          />
          <Button variant="danger" size="sm" disabled={selectedIds.size === 0} onClick={handleBatchDelete}>
            批量删除
          </Button>
          <Button
            size="sm"
            icon={<FilmSlate size={15} />}
            disabled={selectedIds.size < 2}
            onClick={() =>
              navigate('/edit', { state: { videoIds: filtered.filter((v) => selectedIds.has(v.id)).map((v) => v.id) } })
            }
          >
            拼接所选
          </Button>
          <Button size="sm" onClick={exitSelecting}>
            退出
          </Button>
        </div>
      ) : (
        <div className="mb-4 flex flex-wrap items-center gap-3">
          <Input
            allowClear
            prefix={<MagnifyingGlass size={15} />}
            placeholder="搜索标题…"
            value={search}
            onChange={setSearch}
            className="w-[220px]"
          />
          <Select value={sourceFilter} onChange={setSourceFilter} width={120} options={sourceOptions} />
          <Select
            value={sortBy}
            onChange={(v: string) => setSortBy(v as SortKey)}
            width={120}
            options={SORT_OPTIONS}
          />
          <Button className="ml-auto" icon={<Checks size={15} />} onClick={() => setSelecting(true)}>
            多选
          </Button>
        </div>
      )}

      {/* 内容 */}
      <RequestState error={loadError} hasData={hasData} loading={loading} onRetry={load} className="mb-4" />
      {loading && !hasData ? (
        <div className={GRID_CLASS}>
          {Array.from({ length: 12 }).map((_, i) => (
            <div key={i} className="skeleton aspect-[3/4] w-full rounded-2xl" />
          ))}
        </div>
      ) : !hasData ? null : videos.length === 0 ? (
        <Panel pad="none">
          <EmptyState
            icon={<VideoCamera size={26} />}
            title="暂无视频"
            desc="从抖音下载，或上传本地视频"
            action={
              <>
                <Link to="/download">
                  <Button variant="primary" icon={<DownloadSimple size={15} />}>
                    去下载视频
                  </Button>
                </Link>
                <Button icon={<Plus size={15} />} onClick={() => fileInputRef.current?.click()}>
                  上传本地视频
                </Button>
              </>
            }
          />
        </Panel>
      ) : filtered.length === 0 ? (
        <Panel pad="none">
          <EmptyState title="没有匹配的视频" desc="换个关键词或筛选条件试试" />
        </Panel>
      ) : (
        <div className={GRID_CLASS}>
          {filtered.map((v, i) => (
            <div key={v.id} className="enter-item" style={{ animationDelay: `${Math.min(i * 50, 400)}ms` }}>
              <VideoCard
                video={v}
                selected={selectedIds.has(v.id)}
                selecting={selecting}
                onToggleSelect={toggleSelect}
                onPlay={setPlaying}
                onPublish={(video) => navigate('/publish', { state: { videoId: video.id } })}
                onTrim={openTrim}
                onAiEdit={handleAiEdit}
                onRename={openRename}
                onDownload={handleDownload}
                onDelete={handleDelete}
              />
            </div>
          ))}
        </div>
      )}

      {/* 播放弹窗 */}
      <Modal open={!!playing} onClose={() => setPlaying(null)} title={playing?.title} width={860}>
        {playing && (
          <>
            <video
              src={playing.file_url}
              controls
              autoPlay
              className="w-full rounded bg-black"
              style={{ maxHeight: '70vh' }}
            />
            <div className="mt-3 flex justify-end">
              <Button
                icon={<Scissors size={15} />}
                onClick={() => {
                  const v = playing
                  setPlaying(null)
                  if (v) openTrim(v)
                }}
              >
                截取此片段
              </Button>
            </div>
          </>
        )}
      </Modal>

      {/* 截取弹窗 */}
      <Modal
        open={!!trimTarget}
        onClose={() => setTrimTarget(null)}
        title={`截取片段${trimTarget ? ` · ${trimTarget.title}` : ''}`}
        width={640}
        footer={
          <div className="flex w-full flex-col items-end gap-1.5">
            <div className="flex gap-2">
              <Button variant="ghost" onClick={() => setTrimTarget(null)}>
                取消
              </Button>
              <Button
                variant="primary"
                icon={<Scissors size={15} />}
                loading={trimming}
                disabled={!trimValid}
                onClick={submitTrim}
              >
                开始截取
              </Button>
            </div>
            {trimError && <p className="m-0 text-xs text-danger">{trimError}</p>}
          </div>
        }
      >
        {trimTarget && (
          <>
            <video
              src={trimTarget.file_url}
              controls
              className="max-h-[40vh] w-full rounded-lg bg-black"
            />
            <div className="mt-3 text-xs text-ink-3">
              共 <span className="tnum">{formatDuration(trimDuration)}</span>
            </div>
            <div className="mt-3 flex flex-wrap items-end gap-3">
              <div className="w-[140px]">
                <div className="mb-1 text-xs text-ink-3">开始（秒）</div>
                <Input
                  type="number"
                  step={0.5}
                  min={0}
                  value={trimStart}
                  onChange={setTrimStart}
                  placeholder="0"
                />
              </div>
              <div className="w-[140px]">
                <div className="mb-1 text-xs text-ink-3">结束（秒）</div>
                <Input
                  type="number"
                  step={0.5}
                  min={0}
                  value={trimEnd}
                  onChange={setTrimEnd}
                  placeholder={trimDuration != null ? String(trimDuration) : '结束秒数'}
                />
              </div>
              <div className="flex gap-2">
                {TRIM_PRESETS.map((s) => (
                  <Button key={s} size="sm" onClick={() => applyTrimPreset(s)}>
                    前 {s}s
                  </Button>
                ))}
              </div>
            </div>
          </>
        )}
      </Modal>

      {/* 重命名弹窗 */}
      <Modal
        open={!!renaming}
        onClose={() => setRenaming(null)}
        title="重命名视频"
        footer={
          <>
            <Button variant="ghost" onClick={() => setRenaming(null)}>
              取消
            </Button>
            <Button variant="primary" loading={renameSaving} onClick={submitRename}>
              保存
            </Button>
          </>
        }
      >
        <Input
          value={renameTitle}
          onChange={setRenameTitle}
          maxLength={120}
          placeholder="请输入视频标题"
          autoFocus
          suffix={<span className="tnum text-xs">{renameTitle.length}/120</span>}
          onKeyDown={(e: KeyboardEvent<HTMLInputElement>) => {
            if (e.key === 'Enter') submitRename()
          }}
        />
      </Modal>
    </div>
  )
}
