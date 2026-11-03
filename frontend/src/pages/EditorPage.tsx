import { useCallback, useEffect, useRef, useState } from 'react'
import { useLocation, useNavigate, useParams } from 'react-router-dom'
import { CaretLeft, Export, Plus } from '@phosphor-icons/react'
import {
  concatVideos,
  errorMessage,
  fetchTask,
  fetchVideos,
  formatDuration,
  type Task,
  type Video,
} from '../api'
import {
  Button,
  EmptyState,
  Input,
  PageHeader,
  Panel,
  Progress,
  confirmDialog,
  toast,
} from '../ui'
import RequestState from '../ui/RequestState'
import StatusBadge from '../components/StatusBadge'
import AiAnalysisPanel, {
  type AiSuggestionToApply,
} from '../features/editor/AiAnalysisPanel'
import AiPlanPanel from '../features/editor/AiPlanPanel'
import type { AiEditPlan } from '../api'
import EditorPreview, {
  type EditorPreviewHandle,
} from '../features/editor/EditorPreview'
import EditorTimeline from '../features/editor/EditorTimeline'
import {
  editorDraftStorageKey,
  restoreTaskDocument,
} from '../features/editor/taskRestore'
import {
  MIN_DUR,
  clipsEqual,
  duplicateClip,
  findClipAt,
  moveClip,
  newClip,
  pack,
  removeAtPlayhead,
  removeClip,
  restoreDraft,
  serializeDraft,
  splitClip,
  subtractWaste,
  timelineDuration,
  trimClip,
  type DeleteSide,
  type SourceRange,
} from '../features/editor/model'
import {
  useEditorHistory,
  useEditorShortcuts,
} from '../features/editor/useEditorHistory'

function round2(value: number): number {
  return Math.round(value * 100) / 100
}

export default function EditorPage() {
  const navigate = useNavigate()
  const location = useLocation()
  const { taskId } = useParams<{ taskId: string }>()
  const [videos, setVideos] = useState<Video[]>([])
  const [task, setTask] = useState<Task | null>(null)
  const [taskLoading, setTaskLoading] = useState(!!taskId)
  const [taskError, setTaskError] = useState<string | null>(null)
  const [libLoading, setLibLoading] = useState(true)
  const [libHasData, setLibHasData] = useState(false)
  const [libError, setLibError] = useState<string | null>(null)
  const [draftReady, setDraftReady] = useState(false)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [time, setTime] = useState(0)
  const [exporting, setExporting] = useState(false)
  const [splitParts, setSplitParts] = useState('1')
  const [libraryOpen, setLibraryOpen] = useState(false)
  const previewRef = useRef<EditorPreviewHandle>(null)
  const storageErrorShownRef = useRef(false)

  const {
    clips,
    title,
    setClips,
    setTitle,
    reset,
    undo,
    redo,
    canUndo,
    canRedo,
  } = useEditorHistory({ clips: [], title: '' })

  const loadVideos = useCallback(async () => {
    setLibLoading(true)
    try {
      setVideos(await fetchVideos())
      setLibHasData(true)
      setLibError(null)
    } catch (error) {
      setLibError(errorMessage(error))
    } finally {
      setLibLoading(false)
    }
  }, [])

  useEffect(() => {
    void loadVideos()
  }, [loadVideos])

  useEffect(() => {
    if (!taskId) {
      setTask(null)
      setTaskLoading(false)
      setTaskError(null)
      return
    }
    let disposed = false
    let timer = 0
    setTaskLoading(true)
    setTaskError(null)

    const tick = async () => {
      try {
        const value = await fetchTask(taskId)
        if (disposed) return
        if (value.type !== 'edit') throw new Error('该任务不是剪辑任务')
        setTask(value)
        setTaskLoading(false)
        if (value.status === 'pending' || value.status === 'running') {
          timer = window.setTimeout(tick, 3000)
        }
      } catch (error) {
        if (disposed) return
        setTaskError(errorMessage(error))
        setTaskLoading(false)
      }
    }
    void tick()
    return () => {
      disposed = true
      window.clearTimeout(timer)
    }
  }, [taskId])

  const storageKey = editorDraftStorageKey(taskId)

  useEffect(() => {
    if (draftReady || libLoading || libError || (taskId && !task)) return
    const state = location.state as { videoIds?: number[] } | null
    const queryVideoId = Number(new URLSearchParams(location.search).get('video'))
    const requestedIds = taskId ? [] : state?.videoIds?.length
      ? state.videoIds
      : Number.isInteger(queryVideoId) && queryVideoId > 0 ? [queryVideoId] : []

    if (requestedIds.length > 0) {
      const picked = requestedIds
        .map((id) => videos.find((video) => video.id === id))
        .filter((video): video is Video => !!video && (video.duration ?? 0) >= MIN_DUR)
      if (picked.length > 0) {
        reset({ clips: pack(picked.map(newClip)), title: '' })
        toast.success(`已加入 ${picked.length} 个片段`)
      } else {
        toast.warning('所选视频不存在或时长无效，未加入时间线')
      }
      setDraftReady(true)
      navigate(`${location.pathname}${location.search}`, { replace: true, state: null })
      return
    }

    let restored = false
    let raw: string | null = null
    try {
      raw = localStorage.getItem(storageKey)
    } catch {
      toast.warning('浏览器禁止读取本地草稿')
    }
    if (raw) {
      const draft = restoreDraft(raw, videos)
      if (draft) {
        reset(draft)
        restored = true
        toast.success(`已恢复本地草稿（${draft.clips.length} 个片段）`)
      } else {
        toast.warning('本地草稿已损坏或引用的视频/区间失效，已忽略')
        try {
          localStorage.removeItem(storageKey)
        } catch {
          storageErrorShownRef.current = true
        }
      }
    }

    if (!restored && task) {
      const document = restoreTaskDocument(task, videos)
      if (document) {
        reset(document)
        toast.success(`已恢复任务素材（${document.clips.length} 个片段）`)
      } else {
        toast.warning('该历史任务缺少原始剪辑信息，无法恢复时间线')
      }
    }
    if (task?.input?.split_parts) setSplitParts(String(task.input.split_parts))
    setDraftReady(true)
  }, [draftReady, libError, libLoading, location.pathname, location.search, location.state, navigate, reset, storageKey, task, taskId, videos])

  useEffect(() => {
    if (!draftReady) return
    try {
      localStorage.setItem(storageKey, serializeDraft({ clips, title }))
      storageErrorShownRef.current = false
    } catch {
      if (!storageErrorShownRef.current) {
        storageErrorShownRef.current = true
        toast.warning('本地草稿保存失败，请检查浏览器存储权限')
      }
    }
  }, [clips, draftReady, storageKey, title])

  useEffect(() => {
    if (selectedId && !clips.some((clip) => clip.id === selectedId)) setSelectedId(null)
  }, [clips, selectedId])

  const addClip = useCallback((video: Video) => {
    if ((video.duration ?? 0) < MIN_DUR) {
      toast.warning('该视频时长未知或过短，无法剪辑')
      return
    }
    const clip = newClip(video)
    setClips((current) => pack([...current, clip]))
    setSelectedId(clip.id)
  }, [setClips])

  const splitAtPlayhead = useCallback(() => {
    const clip = findClipAt(clips, time)
    if (!clip) return
    const index = clips.findIndex((item) => item.id === clip.id)
    const next = splitClip(clips, clip.id, time)
    if (next === clips) {
      toast.warning('播放头需位于片段内部，且截断后两侧均不少于 0.3 秒')
      return
    }
    previewRef.current?.pause()
    setClips(next)
    setSelectedId(next[index + 1]?.id ?? clip.id)
  }, [clips, setClips, time])

  const deleteSide = useCallback((side: DeleteSide) => {
    const next = removeAtPlayhead(clips, time, side)
    if (next === clips) {
      toast.warning('播放头边缘没有可删除的内容，或保留片段会短于 0.3 秒')
      return
    }
    previewRef.current?.pause()
    setClips(next)
    previewRef.current?.seek(side === 'left' ? 0 : Math.min(time, timelineDuration(next)))
  }, [clips, setClips, time])

  const deleteSelected = useCallback(() => {
    if (!selectedId) return
    const next = removeClip(clips, selectedId)
    if (next === clips) return
    previewRef.current?.pause()
    setClips(next)
    setSelectedId(null)
    previewRef.current?.seek(Math.min(time, timelineDuration(next)))
  }, [clips, selectedId, setClips, time])

  const duplicateSelected = useCallback(() => {
    if (!selectedId) return
    const index = clips.findIndex((clip) => clip.id === selectedId)
    const next = duplicateClip(clips, selectedId)
    if (next === clips) return
    setClips(next)
    setSelectedId(next[index + 1]?.id ?? selectedId)
  }, [clips, selectedId, setClips])

  const updateTrim = useCallback((id: string, start: number, end: number) => {
    const next = trimClip(clips, id, start, end)
    if (next === clips) return
    previewRef.current?.pause()
    setClips(next)
    previewRef.current?.seek(Math.min(time, timelineDuration(next)))
  }, [clips, setClips, time])

  const clearTimeline = useCallback(async () => {
    if (clips.length === 0) return
    const confirmed = await confirmDialog({
      title: '清空时间线？',
      content: '只会移除时间线片段，不会删除视频库中的原视频。此操作可通过撤销恢复。',
      okText: '清空',
      danger: true,
    })
    if (!confirmed) return
    previewRef.current?.pause()
    previewRef.current?.seek(0)
    setClips([])
    setSelectedId(null)
  }, [clips.length, setClips])

  const seek = useCallback((value: number) => {
    previewRef.current?.seek(value)
  }, [])

  useEditorShortcuts({
    enabled: draftReady,
    onSplit: splitAtPlayhead,
    onDelete: deleteSelected,
    onTogglePlay: () => previewRef.current?.togglePlay(),
    onSeek: (delta) => previewRef.current?.seekBy(delta),
    onUndo: undo,
    onRedo: redo,
  })

  const previewRange = useCallback((range: { videoId: number; start: number; end: number }) => {
    const video = videos.find((item) => item.id === range.videoId)
    if (!video) {
      toast.warning('区间对应的视频已不存在，无法预览')
      return
    }
    previewRef.current?.previewRange({
      videoId: range.videoId,
      fileUrl: video.file_url,
      title: video.title,
      start: range.start,
      end: range.end,
    })
  }, [videos])

  const applyPlan = useCallback((plan: AiEditPlan): boolean => {
    const source = videos.find((video) => video.id === plan.video_id)
    if (!source) {
      toast.error('找不到该视频，可能已从素材库删除')
      return false
    }
    try {
      const next = pack(plan.clips.map((item) => {
        const clip = newClip(source)
        clip.trimStart = Math.max(0, item.start)
        clip.trimEnd = Math.min(item.end, source.duration ?? item.end)
        return clip
      }))
      if (next.length === 0) return false
      setClips(next)
      setSelectedId(next[0]?.id ?? null)
      setTitle('AI 成片 · ' + source.title)
      previewRef.current?.seek(0)
      return true
    } catch {
      toast.error('选段超出源视频范围，未应用')
      return false
    }
  }, [setClips, setTitle, videos])

  const applySuggestions = useCallback((suggestions: AiSuggestionToApply[]): boolean => {
    const byVideo = new Map<number, SourceRange[]>()
    suggestions.forEach((suggestion) => {
      const ranges = byVideo.get(suggestion.videoId) ?? []
      ranges.push({ start: suggestion.start, end: suggestion.end })
      byVideo.set(suggestion.videoId, ranges)
    })

    let next = clips
    let touched = false
    byVideo.forEach((ranges, videoId) => {
      const updated = subtractWaste(next, videoId, ranges)
      if (updated !== next) touched = true
      next = updated
    })

    if (!touched || clipsEqual(next, clips)) {
      toast.warning('所选建议没有作用于当前时间线，可能区间已被裁剪')
      return false
    }

    previewRef.current?.pause()
    setClips(next)
    setSelectedId(null)
    previewRef.current?.seek(Math.min(time, timelineDuration(next)))
    const removed = Math.max(0, timelineDuration(clips) - timelineDuration(next))
    toast.success(`已应用 ${suggestions.length} 条建议，时间线缩短 ${removed.toFixed(1)} 秒，可撤销`)
    return true
  }, [clips, setClips, time])

  const handleTaskCreated = useCallback((createdTaskId: string) => {
    if (createdTaskId === taskId) return
    if (clips.length > 0) {
      try {
        localStorage.setItem(
          editorDraftStorageKey(createdTaskId),
          serializeDraft({ clips, title }),
        )
      } catch {
        toast.warning('任务已创建，但当前时间线草稿保存失败')
      }
    }
    navigate(`/edit/${createdTaskId}`, { replace: true })
  }, [clips, navigate, taskId, title])

  const submitExport = async () => {
    if (clips.length === 0) {
      toast.warning('时间线上还没有片段')
      return
    }
    const parts = Math.max(1, Math.min(20, parseInt(splitParts, 10) || 1))
    setExporting(true)
    try {
      await concatVideos(
        title.trim(),
        clips.map((clip) => ({
          video_id: clip.videoId,
          start: round2(clip.trimStart),
          end: round2(clip.trimEnd),
        })),
        parts,
      )
      toast.success(parts > 1 ? `已开始渲染，完成后自动拆成 ${parts} 段入库` : '已开始渲染，完成后自动入库视频库')
      navigate('/tasks')
    } catch (error) {
      toast.error(`导出失败：${errorMessage(error)}`)
      setExporting(false)
    }
  }

  if (taskId && !task) {
    return (
      <div>
        <PageHeader
          title="智能剪辑详情"
          desc="正在读取任务和剪辑数据"
          extra={<Button variant="ghost" icon={<CaretLeft size={15} />} onClick={() => navigate('/edit')}>返回智能剪辑</Button>}
        />
        <Panel>
          {taskLoading ? (
            <div className="space-y-3"><div className="skeleton h-5 w-1/2" /><div className="skeleton h-5 w-3/4" /></div>
          ) : (
            <EmptyState
              title="任务无法打开"
              desc={taskError ?? '任务不存在或已被清理'}
              action={<Button variant="primary" onClick={() => navigate('/edit')}>返回智能剪辑</Button>}
            />
          )}
        </Panel>
      </div>
    )
  }

  const hasAiResults = !!task && (
    task.input?.mode === 'plan' ||
    task.input?.mode === 'analyze' ||
    (!task.input?.mode && ('plan' in task.result || 'analyses' in task.result))
  )

  return (
    <div>
      <PageHeader
        title={task ? '智能剪辑结果' : '视频剪辑'}
        desc={task?.title ?? '精确截断、区间删除、复制与撤销；草稿自动保存在当前浏览器'}
        extra={
          <>
            <Button variant="secondary" icon={<Plus size={15} />} onClick={() => setLibraryOpen((open) => !open)}>
              {libraryOpen ? '收起素材库' : '添加素材'}
            </Button>
            <Button variant="ghost" icon={<CaretLeft size={15} />} onClick={() => navigate('/edit')}>
              返回智能剪辑
            </Button>
          </>
        }
      />

      {task && (
        <Panel pad="sm" className="mb-4">
          <div className="flex flex-wrap items-center gap-3">
            <StatusBadge status={task.status} />
            {(task.status === 'pending' || task.status === 'running') && (
              <>
                <Progress percent={task.progress / 100} className="min-w-[160px] flex-1" />
                <span className="tnum text-xs text-ink-3">{Math.round(task.progress)}%</span>
              </>
            )}
            <span className="min-w-0 flex-1 truncate text-xs text-ink-4">{task.logs?.[task.logs.length - 1]}</span>
          </div>
        </Panel>
      )}

      {libraryOpen && (
        <Panel className="mb-4" pad="sm">
          <div className="mb-2 px-1 text-xs font-medium text-ink-3">素材库（点击加入时间线）</div>
          <RequestState error={libError} hasData={libHasData} loading={libLoading} onRetry={loadVideos} className="mb-2" />
          <div className="grid max-h-[42vh] gap-2 overflow-y-auto pr-1 sm:grid-cols-2 lg:grid-cols-3">
            {libLoading && !libHasData ? (
              Array.from({ length: 3 }).map((_, index) => <div key={index} className="skeleton h-14 w-full rounded-lg" />)
            ) : !libHasData ? null : videos.length === 0 ? (
              <EmptyState title="暂无视频" desc="先去下载或上传视频" />
            ) : (
              videos.map((video) => (
                <button
                  key={video.id}
                  type="button"
                  onClick={() => addClip(video)}
                  className="group flex w-full items-center gap-2.5 rounded-lg border border-transparent p-1.5 text-left transition-colors hover:border-line hover:bg-fill"
                >
                  <div className="h-11 w-8 shrink-0 overflow-hidden rounded-md bg-black/30">
                    {video.cover_url && <img src={video.cover_url} alt="" className="h-full w-full object-cover" />}
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-[13px] text-ink-1">{video.title}</div>
                    <div className="tnum text-xs text-ink-3">{formatDuration(video.duration)}</div>
                  </div>
                  <Plus size={15} className="shrink-0 text-ink-3 transition-colors group-hover:text-accent" />
                </button>
              ))
            )}
          </div>
        </Panel>
      )}

      <div className="flex min-w-0 flex-col gap-4">
        <div className={`grid min-w-0 items-start gap-4 ${hasAiResults ? 'xl:grid-cols-[minmax(0,1fr)_minmax(360px,0.72fr)]' : ''}`}>
          <div className="min-w-0">
            <EditorPreview ref={previewRef} clips={clips} onTimeChange={setTime} />
          </div>
          {hasAiResults && (
            <div className="flex min-w-0 flex-col gap-4">
              <AiPlanPanel
                initialTask={task}
                onTaskCreated={handleTaskCreated}
                onPreview={previewRange}
                onApply={applyPlan}
              />
              <AiAnalysisPanel
                initialTask={task}
                onTaskCreated={handleTaskCreated}
                onPreview={previewRange}
                onApply={applySuggestions}
              />
            </div>
          )}
        </div>

        <EditorTimeline
          clips={clips}
          selectedId={selectedId}
          time={time}
          canUndo={canUndo}
          canRedo={canRedo}
          onSelect={setSelectedId}
          onSeek={seek}
          onSplit={splitAtPlayhead}
          onDeleteSide={deleteSide}
          onDelete={deleteSelected}
          onDuplicate={duplicateSelected}
          onTrim={updateTrim}
          onMove={(id, start) => setClips((current) => moveClip(current, id, start))}
          onUndo={undo}
          onRedo={redo}
          onClear={() => { void clearTimeline() }}
        />

        <Panel pad="sm">
          <div className="editor-export flex flex-wrap items-center gap-2 px-1">
            <span className="mr-1 text-xs font-medium text-ink-3">导出设置</span>
            <Input
              value={title}
              onChange={setTitle}
              maxLength={80}
              placeholder="导出标题（默认：拼接视频）"
              aria-label="导出标题"
              className="min-w-0 flex-1 sm:max-w-[280px]"
            />
            <div className="flex items-center gap-1.5">
              <Input
                aria-label="导出拆分段数"
                type="number"
                min={1}
                max={20}
                value={splitParts}
                onChange={setSplitParts}
                className="w-[68px]"
              />
              <span className="whitespace-nowrap text-xs text-ink-3">拆分</span>
            </div>
            <Button
              variant="primary"
              icon={<Export size={15} />}
              loading={exporting}
              disabled={clips.length === 0}
              onClick={submitExport}
            >
              导出合成
            </Button>
          </div>
        </Panel>
      </div>
    </div>
  )
}
