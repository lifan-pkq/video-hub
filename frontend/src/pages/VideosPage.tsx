import { useCallback, useEffect, useMemo, useRef, useState, type ChangeEvent, type KeyboardEvent } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { Checks, DownloadSimple, MagnifyingGlass, Plus, VideoCamera } from '@phosphor-icons/react'
import {
  deleteVideo,
  errorMessage,
  fetchVideos,
  formatSize,
  platformLabel,
  renameVideo,
  uploadVideo,
  type Video,
} from '../api'
import LibraryVideoCard from '../components/LibraryVideoCard'
import VideoPlayerModal from '../components/VideoPlayerModal'
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

type SortKey = 'newest' | 'oldest' | 'longest'

const SORT_OPTIONS: { value: SortKey; label: string }[] = [
  { value: 'newest', label: '最新优先' },
  { value: 'oldest', label: '最旧优先' },
  { value: 'longest', label: '时长最长' },
]

/** 海报墙网格：移动端 2 列 */
const GRID_CLASS = 'grid grid-cols-2 gap-4 sm:grid-cols-3 md:grid-cols-4 xl:grid-cols-5 2xl:grid-cols-6'

/** 触屏设备（无 hover）操作条需点击展开 */
const isCoarsePointer = () =>
  typeof window !== 'undefined' && window.matchMedia('(hover: none)').matches

export default function VideosPage() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const [videos, setVideos] = useState<Video[]>([])
  const [loading, setLoading] = useState(true)

  // 在线播放
  const [playId, setPlayId] = useState<number | null>(null)

  // 上传
  const [uploading, setUploading] = useState(false)
  const [uploadProgress, setUploadProgress] = useState(0)
  const fileInputRef = useRef<HTMLInputElement>(null)

  // 搜索 / 筛选 / 排序（命令面板可带 ?q= 预填搜索）
  const [search, setSearch] = useState(() => searchParams.get('q') ?? '')
  const [sourceFilter, setSourceFilter] = useState('all')
  const [sortBy, setSortBy] = useState<SortKey>('newest')

  // SPA 内从命令面板跳到 /library?q= 时组件不重挂载，需监听参数变化
  useEffect(() => {
    const q = searchParams.get('q')
    if (q !== null) setSearch(q)
  }, [searchParams])

  // 多选
  const [selecting, setSelecting] = useState(false)
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set())

  // 移动端：点击展开操作条的卡片
  const [openActionsId, setOpenActionsId] = useState<number | null>(null)

  // 重命名
  const [renaming, setRenaming] = useState<Video | null>(null)
  const [renameTitle, setRenameTitle] = useState('')
  const [renameSaving, setRenameSaving] = useState(false)

  const load = useCallback(async () => {
    try {
      setVideos(await fetchVideos())
    } catch (e) {
      toast.error(`加载失败：${errorMessage(e)}`)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  // 点击卡片以外的空白处收起移动端操作条（卡片自身点击由 handleCardClick 处理）
  useEffect(() => {
    if (openActionsId === null) return
    const close = (e: MouseEvent) => {
      if (!(e.target as HTMLElement | null)?.closest?.('.poster-card')) setOpenActionsId(null)
    }
    document.addEventListener('click', close)
    return () => document.removeEventListener('click', close)
  }, [openActionsId])

  // ================= 上传 =================

  const handleUpload = async (file: File) => {
    setUploading(true)
    setUploadProgress(0)
    try {
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

  // ================= 卡片交互 =================

  /** 桌面端点击直接播放；触屏首次点击展开操作条，再次点击播放 */
  const handleCardClick = (video: Video) => {
    if (isCoarsePointer() && openActionsId !== video.id) {
      setOpenActionsId(video.id)
      return
    }
    setOpenActionsId(null)
    setPlayId(video.id)
  }

  const handleEdit = (video: Video) => {
    // 跳剪辑页并预选该视频（query 供刷新/直链场景，state 供当前剪辑页 SPA 内识别）
    navigate(`/edit?video=${video.id}`, { state: { videoIds: [video.id] } })
  }

  const handlePublish = (video: Video) => {
    navigate(`/publish?video=${video.id}`, { state: { videoId: video.id } })
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
      setVideos((prev) => prev.filter((v) => v.id !== video.id))
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

  // ================= 重命名 =================

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
      setVideos((prev) => prev.map((v) => (v.id === updated.id ? { ...v, title: updated.title } : v)))
      toast.success('已重命名')
      setRenaming(null)
    } catch (e) {
      toast.error(`重命名失败：${errorMessage(e)}`)
    } finally {
      setRenameSaving(false)
    }
  }

  // ================= 派生数据 =================

  const totalSize = useMemo(() => videos.reduce((sum, v) => sum + v.size, 0), [videos])

  // 来源筛选选项：按列表中实际出现的 source_platform 动态生成
  const sourceOptions = useMemo(() => {
    const keys = Array.from(new Set(videos.map((v) => v.source_platform ?? 'local')))
    return [{ value: 'all', label: '全部来源' }, ...keys.map((k) => ({ value: k, label: platformLabel(k) }))]
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

  const playTitle = playId !== null ? videos.find((v) => v.id === playId)?.title : undefined

  // ================= 渲染 =================

  return (
    <div>
      <PageHeader
        title="素材库"
        desc={`共 ${videos.length} 个视频 · 总大小 ${formatSize(totalSize)}`}
        extra={
          <>
            <Link to="/library/import">
              <Button icon={<DownloadSimple size={15} />}>去导入</Button>
            </Link>
            <Button
              variant="primary"
              icon={<Plus size={15} weight="bold" />}
              loading={uploading}
              onClick={() => fileInputRef.current?.click()}
            >
              上传视频
            </Button>
          </>
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

      {/* 拖拽上传区（常驻紧凑，保留旧页拖拽能力） */}
      <div className="mb-6 [&_[role=button]]:py-6">
        <Dropzone
          accept="video/*"
          uploading={uploading}
          progress={uploadProgress}
          tip="拖拽视频到此处上传，或在下方素材库中在线播放、剪辑、发布"
          onFile={handleUpload}
        />
      </div>

      {/* 工具条 */}
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
            className="w-full sm:w-[220px]"
          />
          <Select value={sourceFilter} onChange={setSourceFilter} width={120} options={sourceOptions} />
          <Select
            value={sortBy}
            onChange={(v: string) => setSortBy(v as SortKey)}
            width={120}
            options={SORT_OPTIONS}
          />
          <Button className="sm:ml-auto" icon={<Checks size={15} />} onClick={() => setSelecting(true)}>
            多选
          </Button>
        </div>
      )}

      {/* 内容 */}
      {loading ? (
        <div className={GRID_CLASS}>
          {Array.from({ length: 12 }).map((_, i) => (
            <div key={i} className="skeleton aspect-[3/4] w-full rounded-2xl" />
          ))}
        </div>
      ) : videos.length === 0 ? (
        <Panel pad="none">
          <EmptyState
            icon={<VideoCamera size={26} />}
            title="素材库还是空的"
            desc="从导入向导下载平台视频，或上传本地视频，之后即可在线播放、剪辑与发布"
            action={
              <>
                <Link to="/library/import">
                  <Button variant="primary" icon={<DownloadSimple size={15} />}>
                    去导入视频
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
              <LibraryVideoCard
                video={v}
                selected={selectedIds.has(v.id)}
                selecting={selecting}
                actionsOpen={openActionsId === v.id}
                onToggleSelect={toggleSelect}
                onCardClick={handleCardClick}
                onEdit={handleEdit}
                onPublish={handlePublish}
                onRename={openRename}
                onDelete={handleDelete}
              />
            </div>
          ))}
        </div>
      )}

      {/* 在线播放弹窗（骨架共享组件，Range 流式） */}
      <VideoPlayerModal videoId={playId} title={playTitle} onClose={() => setPlayId(null)} />

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
