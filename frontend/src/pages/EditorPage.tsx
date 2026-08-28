import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import {
  CaretLeft,
  Export,
  FilmSlate,
  MagicWand,
  Pause,
  Play,
  Plus,
  SkipBack,
  Trash,
} from '@phosphor-icons/react'
import {
  Timeline,
  type TimelineState,
} from '@xzdarcy/react-timeline-editor'
import type { TimelineRow } from '@xzdarcy/timeline-engine'
import '@xzdarcy/react-timeline-editor/dist/react-timeline-editor.css'
import {
  analyzeVideos,
  concatVideos,
  errorMessage,
  fetchTask,
  fetchVideos,
  formatDuration,
  type AiVideoAnalysis,
  type Task,
  type Video,
} from '../api'
import { Button, Checkbox, EmptyState, Input, PageHeader, Panel, toast } from '../ui'

/** 时间线上的一个片段 */
interface EditorClip {
  id: string
  videoId: number
  title: string
  fileUrl: string
  /** 源视频时长（秒） */
  srcDuration: number
  /** 源视频入点 / 出点（秒） */
  trimStart: number
  trimEnd: number
  /** 时间线位置（秒），由 pack() 顺序排布得出 */
  start: number
  end: number
}

/** 片段最短时长（秒） */
const MIN_DUR = 0.3
const EFFECTS = { clip: { id: 'clip', name: '片段' } }

let clipSeq = 0
const nextClipId = () => `clip_${++clipSeq}_${Date.now()}`

/** 磁吸排布：按顺序无缝排列，返回新数组 */
function pack(list: EditorClip[]): EditorClip[] {
  let pos = 0
  return list.map((c) => {
    const dur = c.trimEnd - c.trimStart
    const out = { ...c, start: pos, end: pos + dur }
    pos += dur
    return out
  })
}

function round2(n: number) {
  return Math.round(n * 100) / 100
}

/** 秒 → mm:ss.s */
function formatTimecode(seconds: number): string {
  const s = Math.max(0, seconds)
  const m = Math.floor(s / 60)
  const r = s - m * 60
  return `${String(m).padStart(2, '0')}:${r.toFixed(1).padStart(4, '0')}`
}

export default function EditorPage() {
  const navigate = useNavigate()
  const location = useLocation()
  const [videos, setVideos] = useState<Video[]>([])
  const [libLoading, setLibLoading] = useState(true)

  const [clips, setClips] = useState<EditorClip[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [title, setTitle] = useState('')
  const [exporting, setExporting] = useState(false)

  // 预览播放
  const [playing, setPlaying] = useState(false)
  const [time, setTime] = useState(0)
  const videoRef = useRef<HTMLVideoElement>(null)
  const timelineRef = useRef<TimelineState>(null)
  const timeRef = useRef(0)
  const pendingSeekRef = useRef<number | null>(null)

  // 供事件回调读取最新 clips，避免闭包过期
  const clipsRef = useRef<EditorClip[]>(clips)
  clipsRef.current = clips
  const totalDuration = clips.length > 0 ? clips[clips.length - 1].end : 0
  const totalRef = useRef(totalDuration)
  totalRef.current = totalDuration

  // ================= 素材库 =================

  useEffect(() => {
    fetchVideos()
      .then(setVideos)
      .catch((e) => toast.error(`加载视频库失败：${errorMessage(e)}`))
      .finally(() => setLibLoading(false))
  }, [])

  // 从视频库跳入：预填片段；autoAnalyze 时自动开始 AI 分析（只执行一次）
  const prefillRef = useRef(false)
  useEffect(() => {
    if (prefillRef.current || libLoading) return
    const st = location.state as { videoIds?: number[]; autoAnalyze?: boolean } | null
    // 兼容直链 ?video=ID（多个用逗号分隔），与 location.state 双通道
    const q = new URLSearchParams(location.search).get('video')
    const queryIds = q ? q.split(',').map((s) => Number(s.trim())).filter((n) => Number.isFinite(n) && n > 0) : []
    const ids = st?.videoIds?.length ? st.videoIds : queryIds
    if (!ids.length) return
    prefillRef.current = true
    const picked = ids
      .map((id) => videos.find((v) => v.id === id))
      .filter((v): v is Video => !!v && (v.duration ?? 0) > 0)
    if (picked.length > 0) {
      setClips(pack(picked.map((v) => newClip(v))))
      if (st?.autoAnalyze) startAiAnalysis(picked.map((v) => v.id))
      else toast.success(`已加入 ${picked.length} 个片段`)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [libLoading, videos])

  // ================= AI 智能剪辑 =================

  const AI_KINDS = [
    { value: 'intro', label: '去开头废话' },
    { value: 'outro', label: '去结尾废话' },
    { value: 'ad', label: '去中间广告' },
  ] as const

  const [aiKinds, setAiKinds] = useState<Set<string>>(new Set(['intro', 'outro', 'ad']))
  const [aiPrompt, setAiPrompt] = useState('')
  const [aiTaskId, setAiTaskId] = useState<string | null>(null)
  const [aiTask, setAiTask] = useState<Task | null>(null)
  const [aiAnalyses, setAiAnalyses] = useState<AiVideoAnalysis[] | null>(null)
  const aiAnalyzing = !!aiTaskId && !aiAnalyses && aiTask?.status !== 'failed'

  /** 启动 AI 分析（可传任意视频 id 列表，默认分析时间线上的视频） */
  const startAiAnalysis = async (ids?: number[]) => {
    const targetIds = ids ?? Array.from(new Set(clipsRef.current.map((c) => c.videoId)))
    if (targetIds.length === 0) {
      toast.warning('先把视频加入时间线')
      return
    }
    try {
      const { task_id } = await analyzeVideos(targetIds, Array.from(aiKinds), aiPrompt)
      setAiAnalyses(null)
      setAiTask(null)
      setAiTaskId(task_id)
    } catch (e) {
      toast.error(`启动分析失败：${errorMessage(e)}`)
    }
  }

  // 轮询分析任务
  useEffect(() => {
    if (!aiTaskId) return
    let dead = false
    let timer = 0
    const tick = async () => {
      if (dead) return
      try {
        const t = await fetchTask(aiTaskId)
        if (dead) return
        setAiTask(t)
        if (t.status === 'success') {
          const analyses = (t.result as { analyses?: AiVideoAnalysis[] }).analyses ?? []
          setAiAnalyses(analyses)
          const total = analyses.reduce((s, a) => s + a.waste.length, 0)
          toast.success(`AI 分析完成：共建议剪除 ${total} 段`)
        } else if (t.status === 'failed') {
          toast.error('AI 分析失败，可到任务中心查看日志')
        } else {
          timer = window.setTimeout(tick, 2000)
        }
      } catch {
        if (!dead) timer = window.setTimeout(tick, 3000)
      }
    }
    tick()
    return () => {
      dead = true
      window.clearTimeout(timer)
    }
  }, [aiTaskId])

  /** 应用 AI 建议：时间线上的视频按各自保留区间重新生成片段 */
  const applyAiResults = () => {
    if (!aiAnalyses) return
    const byVideo = new Map(aiAnalyses.map((a) => [a.video_id, a]))
    // 时间线上的视频（按首次出现顺序）；时间线为空则直接铺开所有分析结果
    const order: number[] = []
    for (const c of clips) if (!order.includes(c.videoId)) order.push(c.videoId)
    const targets = order.length > 0 ? order : aiAnalyses.map((a) => a.video_id)

    const next: EditorClip[] = []
    let appliedCount = 0
    for (const id of targets) {
      const v = videos.find((x) => x.id === id)
      const a = byVideo.get(id)
      if (!v || (v.duration ?? 0) <= 0) continue
      if (!a || a.waste.length === 0 || a.keep.length === 0) {
        // 无建议 / 整段都判废：保留该视频原有片段（时间线没有则放整段）
        const orig = clips.filter((c) => c.videoId === id)
        next.push(...(orig.length > 0 ? orig : [newClip(v)]))
        continue
      }
      appliedCount++
      for (const seg of a.keep) {
        next.push({ ...newClip(v), trimStart: seg.start, trimEnd: seg.end })
      }
    }
    if (next.length === 0) {
      toast.warning('没有可保留的内容')
      return
    }
    setClips(pack(next))
    setAiAnalyses(null)
    setAiTask(null)
    setAiTaskId(null)
    setPlayhead(0)
    toast.success(`已应用：${appliedCount} 个视频已按 AI 建议去除废片，可微调后导出`)
  }

  const discardAiResults = () => {
    setAiAnalyses(null)
    setAiTask(null)
    setAiTaskId(null)
  }

  const KIND_LABEL = { intro: '开头', outro: '结尾', ad: '广告' } as const

  function newClip(v: Video): EditorClip {
    const dur = v.duration ?? 0
    return {
      id: nextClipId(),
      videoId: v.id,
      title: v.title,
      fileUrl: v.file_url,
      srcDuration: dur,
      trimStart: 0,
      trimEnd: dur,
      start: 0,
      end: dur,
    }
  }

  const addClip = (v: Video) => {
    if ((v.duration ?? 0) <= 0) {
      toast.warning('该视频时长未知，无法剪辑')
      return
    }
    setClips((prev) => pack([...prev, newClip(v)]))
  }

  const removeClip = useCallback((id: string) => {
    setClips((prev) => pack(prev.filter((c) => c.id !== id)))
    setSelectedId((prev) => (prev === id ? null : prev))
  }, [])

  // Delete / Backspace 删除选中片段（输入框聚焦时不触发）
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Delete' && e.key !== 'Backspace') return
      const tag = (e.target as HTMLElement)?.tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA') return
      if (selectedId) removeClip(selectedId)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [selectedId, removeClip])

  // ================= 时间线数据 =================

  const editorData: TimelineRow[] = useMemo(
    () => [
      {
        id: 'row-1',
        actions: clips.map((c) => ({
          id: c.id,
          start: c.start,
          end: c.end,
          effectId: 'clip',
          selected: c.id === selectedId,
        })),
      },
    ],
    [clips, selectedId],
  )

  /** 拖动结束：按片段中点位置排序后重新磁吸排布（实现拖拽换序） */
  const handleMoveEnd = ({ action }: { action: { id: string; start: number; end: number } }) => {
    setClips((prev) => {
      const moved = prev.find((c) => c.id === action.id)
      if (!moved) return prev
      const dur = moved.trimEnd - moved.trimStart
      const updated = prev.map((c) =>
        c.id === action.id ? { ...c, start: Math.max(0, action.start), end: Math.max(0, action.start) + dur } : c,
      )
      const center = (c: EditorClip) => c.start + (c.end - c.start) / 2
      const sorted = [...updated].sort((a, b) => center(a) - center(b))
      return pack(sorted)
    })
    return false // 阻止库内部 onChange，几何完全由我们接管
  }

  /** 裁剪中：限制最短时长、入点 ≥0、出点 ≤ 源时长 */
  const handleResizing = ({
    action,
    start,
    end,
    dir,
  }: {
    action: { id: string }
    start: number
    end: number
    dir: 'left' | 'right'
  }): boolean => {
    const c = clipsRef.current.find((x) => x.id === action.id)
    if (!c) return false
    if (end - start < MIN_DUR) return false
    if (dir === 'left') {
      if (c.trimStart + (start - c.start) < 0) return false
    } else {
      if (c.trimEnd + (end - c.end) > c.srcDuration) return false
    }
    return true
  }

  /** 裁剪结束：把时间线几何变化写回源视频入/出点 */
  const handleResizeEnd = ({
    action,
    start,
    end,
    dir,
  }: {
    action: { id: string }
    start: number
    end: number
    dir: 'left' | 'right'
  }) => {
    setClips((prev) =>
      pack(
        prev.map((c) => {
          if (c.id !== action.id) return c
          const dur = end - start
          return dir === 'left'
            ? { ...c, trimStart: c.trimEnd - dur }
            : { ...c, trimEnd: c.trimStart + dur }
        }),
      ),
    )
    return false
  }

  // ================= 预览播放 =================

  const findClipAt = (t: number): EditorClip | null => {
    const list = clipsRef.current
    if (list.length === 0) return null
    return list.find((c) => t >= c.start && t < c.end) ?? (t >= totalRef.current ? list[list.length - 1] : null)
  }

  /** 把播放头设置到时间线时间 t（秒），并同步光标与预览视频 */
  const setPlayhead = useCallback((t: number) => {
    const clamped = Math.max(0, Math.min(t, totalRef.current))
    timeRef.current = clamped
    setTime(clamped)
    timelineRef.current?.setTime(clamped)
    const video = videoRef.current
    const clip = findClipAt(clamped)
    if (!video || !clip) return
    const srcTime = Math.min(clip.trimStart + (clamped - clip.start), clip.trimEnd - 0.05)
    const currentSrc = video.getAttribute('src') ?? ''
    if (!currentSrc.endsWith(clip.fileUrl)) {
      pendingSeekRef.current = srcTime
      video.src = clip.fileUrl
      video.load()
    } else if (Math.abs(video.currentTime - srcTime) > 0.35) {
      video.currentTime = srcTime
    }
  }, [])

  // 视频元数据加载完成后执行待定位 seek
  useEffect(() => {
    const video = videoRef.current
    if (!video) return
    const onLoaded = () => {
      if (pendingSeekRef.current != null) {
        video.currentTime = pendingSeekRef.current
        pendingSeekRef.current = null
      }
      if (playing) video.play().catch(() => {})
    }
    video.addEventListener('loadedmetadata', onLoaded)
    return () => video.removeEventListener('loadedmetadata', onLoaded)
  }, [playing])

  // 播放主循环：墙钟驱动播放头，视频元素跟随
  useEffect(() => {
    if (!playing) return
    let raf = 0
    let last = performance.now()
    const tick = (now: number) => {
      const dt = (now - last) / 1000
      last = now
      const t = timeRef.current + dt
      if (t >= totalRef.current) {
        setPlayhead(totalRef.current)
        setPlaying(false)
        return
      }
      setPlayhead(t)
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [playing, setPlayhead])

  // 播放状态 → 视频元素 play/pause
  useEffect(() => {
    const video = videoRef.current
    if (!video) return
    if (playing) video.play().catch(() => {})
    else video.pause()
  }, [playing])

  // 漂移校正：播放中视频时间与播放头偏差过大时回拉
  useEffect(() => {
    const video = videoRef.current
    if (!video) return
    const onTimeUpdate = () => {
      if (!playing) return
      const clip = findClipAt(timeRef.current)
      if (!clip || !video.getAttribute('src')?.endsWith(clip.fileUrl)) return
      const expected = clip.trimStart + (timeRef.current - clip.start)
      if (Math.abs(video.currentTime - expected) > 0.4) video.currentTime = expected
    }
    video.addEventListener('timeupdate', onTimeUpdate)
    return () => video.removeEventListener('timeupdate', onTimeUpdate)
  }, [playing])

  // 片段变化后同步预览（含首次添加时加载首帧）
  useEffect(() => {
    if (clips.length > 0 && !playing) setPlayhead(timeRef.current)
  }, [clips, playing, setPlayhead])

  const currentClip = findClipAt(time)
  const togglePlay = () => {
    if (clipsRef.current.length === 0) return
    if (!playing && timeRef.current >= totalRef.current) setPlayhead(0)
    setPlaying((p) => !p)
  }

  // ================= 导出 =================

  const [splitParts, setSplitParts] = useState('1')

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
        clips.map((c) => ({ video_id: c.videoId, start: round2(c.trimStart), end: round2(c.trimEnd) })),
        parts,
      )
      toast.success(parts > 1 ? `已开始渲染，完成后自动拆成 ${parts} 段入库` : '已开始渲染，完成后自动入库视频库')
      navigate('/tasks')
    } catch (e) {
      toast.error(`导出失败：${errorMessage(e)}`)
      setExporting(false)
    }
  }

  // ================= 渲染 =================

  return (
    <div>
      <PageHeader
        title="视频剪辑"
        desc="多视频时间线拼接：添加片段 → 拖动排序 → 拖动边缘裁剪 → 导出合成"
        extra={
          <Button variant="ghost" icon={<CaretLeft size={15} />} onClick={() => navigate('/videos')}>
            返回视频库
          </Button>
        }
      />

      <div className="flex flex-col gap-4 xl:flex-row">
        {/* 素材库 */}
        <Panel className="shrink-0 xl:w-[280px]" pad="sm">
          <div className="mb-2 px-1 text-xs font-medium text-ink-3">素材库（点击加入时间线）</div>
          <div className="flex max-h-[52vh] flex-col gap-1.5 overflow-y-auto pr-1">
            {libLoading ? (
              Array.from({ length: 4 }).map((_, i) => <div key={i} className="skeleton h-14 w-full rounded-lg" />)
            ) : videos.length === 0 ? (
              <EmptyState title="暂无视频" desc="先去下载或上传视频" />
            ) : (
              videos.map((v) => (
                <button
                  key={v.id}
                  type="button"
                  onClick={() => addClip(v)}
                  className="group flex w-full items-center gap-2.5 rounded-lg border border-transparent p-1.5 text-left transition-colors hover:border-line hover:bg-fill"
                >
                  <div className="h-11 w-8 shrink-0 overflow-hidden rounded-md bg-black/30">
                    {v.cover_url && <img src={v.cover_url} alt="" className="h-full w-full object-cover" />}
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-[13px] text-ink-1">{v.title}</div>
                    <div className="tnum text-xs text-ink-3">{formatDuration(v.duration)}</div>
                  </div>
                  <Plus size={15} className="shrink-0 text-ink-3 transition-colors group-hover:text-accent" />
                </button>
              ))
            )}
          </div>
        </Panel>

        {/* 预览 + 时间线 */}
        <div className="flex min-w-0 flex-1 flex-col gap-4">
          <Panel pad="sm">
            <div className="flex items-center justify-center rounded-lg bg-black" style={{ minHeight: 240 }}>
              {clips.length === 0 && (
                <div className="flex h-[240px] flex-col items-center justify-center gap-2 text-ink-3">
                  <FilmSlate size={30} />
                  <span className="text-sm">从左侧素材库添加片段开始剪辑</span>
                </div>
              )}
              {/* 常挂载：保证 loadedmetadata/timeupdate 监听器总能绑定（无片段时隐藏） */}
              <video
                ref={videoRef}
                playsInline
                hidden={clips.length === 0}
                className="max-h-[46vh] w-full rounded-lg object-contain"
              />
            </div>
            <div className="mt-2.5 flex items-center gap-3">
              <Button size="sm" icon={<SkipBack size={14} />} onClick={() => setPlayhead(0)} disabled={clips.length === 0}>
                回开头
              </Button>
              <Button
                variant="primary"
                size="sm"
                icon={playing ? <Pause size={14} weight="fill" /> : <Play size={14} weight="fill" />}
                onClick={togglePlay}
                disabled={clips.length === 0}
              >
                {playing ? '暂停' : '播放'}
              </Button>
              <span className="tnum text-sm text-ink-2">
                {formatTimecode(time)} <span className="text-ink-4">/ {formatTimecode(totalDuration)}</span>
              </span>
              {currentClip && (
                <span className="ml-auto hidden max-w-[40%] truncate text-xs text-ink-3 sm:block">
                  当前片段：{currentClip.title}
                </span>
              )}
            </div>
          </Panel>

          {/* AI 智能分析（常驻） */}
          <Panel pad="sm">
            <div className="flex flex-wrap items-center gap-x-4 gap-y-2 px-1">
              <span className="flex items-center gap-1.5 text-xs font-medium text-ink-2">
                <MagicWand size={15} className="text-accent" />
                AI 智能分析（语音字幕）
              </span>
              {AI_KINDS.map((k) => (
                <Checkbox
                  key={k.value}
                  checked={aiKinds.has(k.value)}
                  onChange={() =>
                    setAiKinds((prev) => {
                      const next = new Set(prev)
                      if (next.has(k.value)) next.delete(k.value)
                      else next.add(k.value)
                      return next
                    })
                  }
                  label={k.label}
                />
              ))}
              <Input
                value={aiPrompt}
                onChange={setAiPrompt}
                maxLength={200}
                placeholder="自定义要求（可选），如：把停顿超过2秒的片段也删掉"
                className="min-w-[200px] flex-1"
              />
              <Button
                size="sm"
                variant="primary"
                icon={<MagicWand size={14} />}
                loading={aiAnalyzing}
                disabled={clips.length === 0 || aiKinds.size === 0}
                onClick={() => startAiAnalysis()}
              >
                {aiAnalyzing ? '分析中…' : '开始分析'}
              </Button>
            </div>

            {/* 分析中状态 */}
            {aiAnalyzing && (
              <div className="mt-2 border-t border-line-soft px-1 pt-2 text-xs text-ink-3">
                {aiTask?.logs?.[aiTask.logs.length - 1] ?? '排队/语音识别中（首次需下载模型，请耐心等待）…'}
              </div>
            )}

            {/* 分析结果 */}
            {aiAnalyses && (
              <div className="mt-2 border-t border-line-soft px-1 pt-2">
                {aiAnalyses.every((a) => a.waste.length === 0) ? (
                  <div className="text-xs text-ink-3">AI 检查完毕：没有发现需要剪掉的低价值/广告内容。</div>
                ) : (
                  <div className="flex max-h-40 flex-col gap-1.5 overflow-y-auto">
                    {aiAnalyses
                      .filter((a) => a.waste.length > 0)
                      .map((a) => (
                        <div key={a.video_id}>
                          <div className="text-xs font-medium text-ink-2">{a.title}</div>
                          {a.waste.map((w, i) => (
                            <div key={i} className="mt-0.5 flex items-center gap-2 text-xs text-ink-2">
                              <span className="rounded bg-danger-soft px-1.5 py-0.5 text-danger">
                                {KIND_LABEL[w.kind]}
                              </span>
                              <span className="tnum">
                                {formatTimecode(w.start)} - {formatTimecode(w.end)}
                              </span>
                              <span className="truncate text-ink-3">{w.reason}</span>
                            </div>
                          ))}
                        </div>
                      ))}
                  </div>
                )}
                <div className="mt-2.5 flex gap-2">
                  {aiAnalyses.some((a) => a.waste.length > 0) && (
                    <Button variant="primary" size="sm" onClick={applyAiResults}>
                      应用到时间线
                    </Button>
                  )}
                  <Button size="sm" onClick={discardAiResults}>
                    忽略
                  </Button>
                </div>
              </div>
            )}
          </Panel>

          {/* 时间线 */}
          <Panel pad="sm">
            <div className="mb-2 flex flex-wrap items-center gap-3 px-1">
              <span className="text-xs font-medium text-ink-3">
                时间线 · <span className="tnum">{clips.length}</span> 个片段
              </span>
              {selectedId && (
                <Button size="sm" variant="danger" icon={<Trash size={13} />} onClick={() => removeClip(selectedId)}>
                  删除选中（Delete）
                </Button>
              )}
              <div className="ml-auto flex items-center gap-2">
                <Input
                  value={title}
                  onChange={setTitle}
                  maxLength={80}
                  placeholder="导出标题（默认：拼接视频）"
                  className="w-[220px]"
                />
                <div className="flex items-center gap-1.5">
                  <Input
                    type="number"
                    min={1}
                    max={20}
                    value={splitParts}
                    onChange={setSplitParts}
                    className="w-[64px]"
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
            </div>
            <div className="timeline-wrap overflow-hidden rounded-lg border border-line">
              <Timeline
                ref={timelineRef}
                style={{ width: '100%', height: 150 }}
                editorData={editorData}
                effects={EFFECTS}
                scale={2}
                scaleWidth={80}
                scaleSplitCount={10}
                minScaleCount={20}
                rowHeight={44}
                startLeft={12}
                gridSnap
                dragLine
                autoScroll
                onCursorDrag={(t) => setPlayhead(t)}
                onClickTimeArea={(t) => {
                  setPlayhead(t)
                  return false
                }}
                onActionMoveEnd={handleMoveEnd}
                onActionResizing={handleResizing}
                onActionResizeEnd={handleResizeEnd}
                onClickAction={(_e, { action }) => setSelectedId(action.id)}
                getActionRender={(action) => {
                  const c = clipsRef.current.find((x) => x.id === action.id)
                  return (
                    <div className="clip-action">
                      <span className="clip-action-title">{c?.title ?? ''}</span>
                      <span className="clip-action-dur tnum">{formatDuration(action.end - action.start)}</span>
                    </div>
                  )
                }}
              />
            </div>
          </Panel>
        </div>
      </div>
    </div>
  )
}
