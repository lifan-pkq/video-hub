import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import {
  ArrowClockwise,
  ArrowSquareOut,
  CheckCircle,
  Clock,
  DownloadSimple,
  FilmSlate,
  GearSix,
  MagicWand,
  Play,
  Sparkle,
  Subtitles,
  VideoCamera,
  WarningCircle,
  Waveform,
  XCircle,
  type Icon,
} from '@phosphor-icons/react'
import {
  approveNarrationTask,
  cancelNarrationTask,
  fetchNarrationPlan,
  createNarrationTask,
  errorMessage,
  fetchLegacyNarrationTasks,
  fetchNarrationCapabilities,
  fetchNarrationTasks,
  fetchVideos,
  importLegacyNarrationTask,
  formatDuration,
  formatRelative,
  importNarrationTask,
  previewNarrationPlan,
  previewNarrationSubtitle,
  saveNarrationSettings,
  type CreateNarrationPayload,
  type LegacyNarrationTask,
  type NarrationCapabilities,
  type NarrationPlan,
  type NarrationStatus,
  type NarrationTask,
  type Video,
} from '../api'
import {
  Alert,
  Button,
  EmptyState,
  Input,
  Modal,
  PageHeader,
  Panel,
  Progress,
  Select,
  Switch,
  TextArea,
  confirmDialog,
  toast,
} from '../ui'

const cx = (...parts: (string | false | null | undefined)[]) => parts.filter(Boolean).join(' ')

const STATUS_META: Record<NarrationStatus, { label: string; tone: string; icon: Icon }> = {
  queued: { label: '排队中', tone: 'text-ink-3 bg-fill', icon: Clock },
  running: { label: '生成中', tone: 'text-accent bg-accent-soft', icon: MagicWand },
  awaiting_review: { label: '待审片', tone: 'text-warning bg-warning-soft', icon: Clock },
  success: { label: '已完成', tone: 'text-success bg-success-soft', icon: CheckCircle },
  failed: { label: '失败', tone: 'text-danger bg-danger-soft', icon: XCircle },
  cancelled: { label: '已取消', tone: 'text-ink-3 bg-fill', icon: WarningCircle },
}

interface SettingsForm {
  textBase: string
  textModel: string
  textKey: string
  visionBase: string
  visionModel: string
  visionKey: string
  ttsEngine: string
  voice: string
}

const SUBTITLE_POSITIONS = [
  { value: 'bottom', label: '底部' },
  { value: 'center', label: '居中' },
  { value: 'top', label: '顶部' },
  { value: 'custom', label: '自定义位置' },
]

const POSITION_Y: Record<string, number> = { top: 8, center: 50, bottom: 88 }

const EMPTY_SETTINGS: SettingsForm = {
  textBase: '',
  textModel: '',
  textKey: '',
  visionBase: '',
  visionModel: '',
  visionKey: '',
  ttsEngine: 'edge_tts',
  voice: 'zh-CN-XiaoxiaoNeural',
}

function SettingsGroup({ title, subtitle, defaultOpen = true, children }: { title: string; subtitle?: string; defaultOpen?: boolean; children: ReactNode }) {
  return (
    <details open={defaultOpen} className="settings-group">
      <summary>
        <span>{title}</span>
        {subtitle && <small>{subtitle}</small>}
      </summary>
      <div className="mt-3 space-y-4">{children}</div>
    </details>
  )
}

export default function NarratePage() {
  const [caps, setCaps] = useState<NarrationCapabilities | null>(null)
  const [capsLoading, setCapsLoading] = useState(true)
  const [capsError, setCapsError] = useState<string | null>(null)

  const [videos, setVideos] = useState<Video[]>([])
  const [videosLoading, setVideosLoading] = useState(true)
  const [videoSearch, setVideoSearch] = useState('')
  const [selectedId, setSelectedId] = useState<number | null>(null)

  const [tasks, setTasks] = useState<NarrationTask[]>([])
  const [legacyTasks, setLegacyTasks] = useState<LegacyNarrationTask[]>([])
  const [legacyBusy, setLegacyBusy] = useState<string | null>(null)
  const [tasksError, setTasksError] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const [busyTask, setBusyTask] = useState<string | null>(null)
  const [playing, setPlaying] = useState<{ title: string; url: string } | null>(null)
  const [reviewTask, setReviewTask] = useState<NarrationTask | null>(null)
  const [reviewPlan, setReviewPlan] = useState<NarrationPlan | null>(null)
  const [reviewLoading, setReviewLoading] = useState(false)
  const [previewUrl, setPreviewUrl] = useState<string | null>(null)
  const [previewBuilding, setPreviewBuilding] = useState(false)
  const [compareOpen, setCompareOpen] = useState(false)
  const [compareSeed, setCompareSeed] = useState<NarrationTask | null>(null)
  const [compareIds, setCompareIds] = useState<string[]>([])
  const [compareResults, setCompareResults] = useState<Array<{ task: NarrationTask; plan: NarrationPlan | null; preview: string | null }>>([])
  const [compareLoading, setCompareLoading] = useState(false)

  const [contentType, setContentType] = useState('auto')
  const [persona, setPersona] = useState('auto')
  const [personaStrength, setPersonaStrength] = useState('natural')
  const [mode, setMode] = useState('auto')
  const [topic, setTopic] = useState('')
  const [instruction, setInstruction] = useState('')
  const [duration, setDuration] = useState('60')
  const [aspect, setAspect] = useState('auto')
  const [clipMode, setClipMode] = useState<'timeline' | 'narration'>('timeline')
  const [coverage, setCoverage] = useState<'highlights' | 'full'>('highlights')
  const [analysisMode, setAnalysisMode] = useState<'fast' | 'deep'>('deep')
  const [outputMode, setOutputMode] = useState<'narration' | 'edit'>('narration')
  const [pauseBetween, setPauseBetween] = useState(true)
  const [pauseSeconds, setPauseSeconds] = useState(2)
  const [reviewMode, setReviewMode] = useState(true)
  const [ttsPronunciation, setTtsPronunciation] = useState(true)
  const [variants, setVariants] = useState(1)
  const [voice, setVoice] = useState('zh-CN-XiaoxiaoNeural')
  const [ttsEngine, setTtsEngine] = useState('edge_tts')
  const [voiceRate, setVoiceRate] = useState(1)
  const [originalVolume, setOriginalVolume] = useState(0.35)
  const [subtitles, setSubtitles] = useState(true)
  const [fontName, setFontName] = useState('DroidSansFallbackFull.ttf')
  const [fontSize, setFontSize] = useState(54)
  const [subtitleMaxChars, setSubtitleMaxChars] = useState(12)
  const [textColor, setTextColor] = useState('#FFFFFF')
  const [strokeColor, setStrokeColor] = useState('#000000')
  const [strokeWidth, setStrokeWidth] = useState(1.5)
  const [subtitlePosition, setSubtitlePosition] = useState('bottom')
  const [customPosition, setCustomPosition] = useState(70)
  const [subtitleY, setSubtitleY] = useState(88)
  const [maskEnabled, setMaskEnabled] = useState(false)
  const [maskY, setMaskY] = useState(78)
  const [maskHeight, setMaskHeight] = useState(14)
  const [maskBlur, setMaskBlur] = useState(18)
  const [maskOpacity, setMaskOpacity] = useState(82)
  const [subtitlePreviewUrl, setSubtitlePreviewUrl] = useState<string | null>(null)
  const [subtitlePreviewLoading, setSubtitlePreviewLoading] = useState(false)
  const [scriptText, setScriptText] = useState('')
  const [transcriptText, setTranscriptText] = useState('')

  const [settingsOpen, setSettingsOpen] = useState(false)
  const [settingsForm, setSettingsForm] = useState<SettingsForm>(EMPTY_SETTINGS)
  const [settingsSaving, setSettingsSaving] = useState(false)

  const defaultsApplied = useRef(false)
  const [searchParams] = useSearchParams()
  const presetVideoId = Number(searchParams.get('video') || 0) || null

  const loadVideos = useCallback(async () => {
    setVideosLoading(true)
    try {
      const data = await fetchVideos()
      setVideos(data)
      setSelectedId((current) => {
        if (presetVideoId && data.some((item) => item.id === presetVideoId)) return presetVideoId
        if (current && data.some((item) => item.id === current)) return current
        return data[0]?.id ?? null
      })
    } catch (e) {
      toast.error('视频库加载失败：' + errorMessage(e))
    } finally {
      setVideosLoading(false)
    }
  }, [presetVideoId])

  const loadTasks = useCallback(async () => {
    try {
      setTasks(await fetchNarrationTasks())
      setTasksError(null)
    } catch (e) {
      setTasksError(errorMessage(e))
    }
  }, [])

  const loadLegacy = useCallback(async () => {
    try {
      setLegacyTasks(await fetchLegacyNarrationTasks())
    } catch {
      setLegacyTasks([])
    }
  }, [])

  const loadCaps = useCallback(async () => {
    setCapsLoading(true)
    try {
      const data = await fetchNarrationCapabilities()
      setCaps(data)
      setCapsError(null)
      if (!defaultsApplied.current) {
        defaultsApplied.current = true
        setContentType(data.defaults.content_type)
        setPersona(data.defaults.persona ?? 'auto')
        setPersonaStrength(data.defaults.persona_strength ?? 'natural')
        setMode('auto')
        setVoice(data.settings?.tts?.voice_name || data.defaults.voice_name)
        setTtsEngine(data.settings?.tts?.engine || data.defaults.tts_engine)
        setDuration(String(data.defaults.target_duration))
        setAspect(data.defaults.aspect)
        setClipMode(data.defaults.clip_mode === 'narration' ? 'narration' : 'timeline')
        setCoverage(data.defaults.coverage === 'full' ? 'full' : 'highlights')
        setAnalysisMode(data.defaults.analysis_mode === 'fast' ? 'fast' : 'deep')
        setOutputMode(data.defaults.output_mode === 'edit' ? 'edit' : 'narration')
        setPauseBetween(data.defaults.pause_between !== false)
        setPauseSeconds(data.defaults.pause_seconds ?? 2)
        setReviewMode(data.defaults.review_mode !== false)
        setTtsPronunciation(data.defaults.tts_pronunciation !== false)
        setOriginalVolume(data.defaults.original_volume)
        setSubtitles(data.defaults.subtitles)
        setFontName(data.defaults.font_name || 'DroidSansFallbackFull.ttf')
        setFontSize(data.defaults.font_size ?? 54)
        setSubtitleMaxChars(data.defaults.subtitle_max_chars ?? 12)
        setTextColor(data.defaults.text_fore_color || '#FFFFFF')
        setStrokeColor(data.defaults.stroke_color || '#000000')
        setStrokeWidth(data.defaults.stroke_width ?? 1.5)
        setSubtitlePosition(data.defaults.subtitle_position || 'bottom')
        setSubtitleY(data.defaults.subtitle_y_percent ?? 88)
        setMaskEnabled(Boolean(data.defaults.subtitle_mask_enabled))
        setMaskY(data.defaults.subtitle_mask_y ?? 78)
        setMaskHeight(data.defaults.subtitle_mask_height ?? 14)
        setMaskBlur(data.defaults.subtitle_mask_blur ?? 18)
        setMaskOpacity(data.defaults.subtitle_mask_opacity ?? 82)
      }
    } catch (e) {
      setCapsError(errorMessage(e))
    } finally {
      setCapsLoading(false)
    }
  }, [])

  useEffect(() => {
    void loadCaps()
    void loadVideos()
    void loadTasks()
    void loadLegacy()
  }, [loadCaps, loadVideos, loadTasks, loadLegacy])

  const activeCount = tasks.filter((task) => task.status === 'queued' || task.status === 'running').length
  useEffect(() => {
    if (activeCount === 0) return
    const timer = window.setInterval(() => {
      void loadTasks()
    }, 3000)
    return () => window.clearInterval(timer)
  }, [activeCount, loadTasks])

  const filteredVideos = useMemo(() => {
    const keyword = videoSearch.trim().toLowerCase()
    const list = keyword ? videos.filter((video) => video.title.toLowerCase().includes(keyword)) : videos
    return list.slice(0, 30)
  }, [videos, videoSearch])

  const selectedVideo = videos.find((video) => video.id === selectedId) ?? null
  const contentTypes = caps?.content_types ?? []
  const currentContentType = contentTypes.find((item) => item.value === contentType)
  const personas = caps?.personas ?? [{ value: 'auto', label: '通用解说', summary: '不套用主播人格' }]
  const personaStrengthOptions = (caps?.persona_strengths ?? [
    { value: 'natural', label: '自然' },
    { value: 'signature', label: '鲜明' },
    { value: 'strong', label: '强烈' },
  ]).map((item) => ({ value: item.value, label: item.label }))
  const voiceOptions = (caps?.voices ?? []).map((item) => ({ value: item.value, label: item.label }))
  if (voice && !voiceOptions.some((item) => item.value === voice)) {
    voiceOptions.push({ value: voice, label: voice + '（当前配置）' })
  }
  const aspectOptions = (caps?.aspects ?? []).map((item) => ({ value: item.value, label: item.label }))
  const modeOptions = (caps?.script_modes ?? []).map((item) => ({ value: item.value, label: item.label }))
  const fontOptions = (caps?.fonts ?? [{ value: '', label: '系统默认（自动选择）' }]).map((item) => ({ value: item.value, label: item.label }))
  if (fontName && !fontOptions.some((item) => item.value === fontName)) {
    fontOptions.push({ value: fontName, label: fontName })
  }

  const openSettings = () => {
    const source = caps?.settings
    setSettingsForm({
      textBase: source?.text?.base_url ?? '',
      textModel: source?.text?.model ?? '',
      textKey: '',
      visionBase: source?.vision?.base_url ?? '',
      visionModel: source?.vision?.model ?? '',
      visionKey: '',
      ttsEngine: source?.tts?.engine ?? ttsEngine,
      voice: source?.tts?.voice_name ?? voice,
    })
    setSettingsOpen(true)
  }

  const saveSettings = async () => {
    setSettingsSaving(true)
    try {
      const text: Record<string, unknown> = { base_url: settingsForm.textBase, model: settingsForm.textModel }
      const vision: Record<string, unknown> = { base_url: settingsForm.visionBase, model: settingsForm.visionModel }
      if (settingsForm.textKey.trim()) text.api_key = settingsForm.textKey.trim()
      if (settingsForm.visionKey.trim()) vision.api_key = settingsForm.visionKey.trim()
      await saveNarrationSettings({
        text,
        vision,
        tts: { engine: settingsForm.ttsEngine, voice_name: settingsForm.voice },
      })
      setSettingsOpen(false)
      setTtsEngine(settingsForm.ttsEngine)
      setVoice(settingsForm.voice)
      toast.success('AI 模型设置已保存')
      await loadCaps()
    } catch (e) {
      toast.error('保存失败：' + errorMessage(e))
    } finally {
      setSettingsSaving(false)
    }
  }

  const selectContentType = (value: string) => {
    setContentType(value)
    const profile = contentTypes.find((item) => item.value === value)
    if (profile) setOriginalVolume(profile.original_volume)
  }

  useEffect(() => {
    return () => {
      if (subtitlePreviewUrl) URL.revokeObjectURL(subtitlePreviewUrl)
    }
  }, [subtitlePreviewUrl])

  const showSubtitlePreview = async () => {
    setSubtitlePreviewLoading(true)
    try {
      let orientation: 'portrait' | 'landscape' = 'portrait'
      if (aspect === '16:9' || aspect === '4:3') orientation = 'landscape'
      else if (aspect === 'auto' && selectedVideo?.width && selectedVideo?.height) {
        orientation = selectedVideo.width > selectedVideo.height ? 'landscape' : 'portrait'
      }
      const blob = await previewNarrationSubtitle({
        text: '字幕样式预览示例',
        font_name: fontName,
        font_size: fontSize,
        text_color: textColor,
        stroke_color: strokeColor,
        stroke_width: strokeWidth,
        orientation,
        subtitle_position: subtitlePosition,
        subtitle_y_percent: subtitleY,
      })
      setSubtitlePreviewUrl(URL.createObjectURL(blob))
    } catch (e) {
      toast.error('字幕预览失败：' + errorMessage(e))
    } finally {
      setSubtitlePreviewLoading(false)
    }
  }

  const submit = async () => {
    if (!selectedVideo) {
      toast.error('请先选择一条视频')
      return
    }
    const seconds = Math.max(5, Math.min(1800, Number(duration) || 60))
    if (mode === 'script' && !scriptText.trim()) {
      toast.error('已选择“直接使用解说稿”，请填写文稿')
      return
    }
    if (mode === 'transcript' && !transcriptText.trim()) {
      toast.error('已选择“按口播文稿生成”，请填写文稿')
      return
    }
    setCreating(true)
    try {
      const base: CreateNarrationPayload = {
        video_id: selectedVideo.id,
        title: selectedVideo.title,
        content_type: contentType,
        persona,
        persona_strength: personaStrength,
        mode,
        topic,
        instruction,
        target_duration: seconds,
        aspect,
        language: '中文',
        voice_name: voice,
        tts_engine: ttsEngine,
        voice_rate: voiceRate,
        original_volume: originalVolume,
        subtitles,
        font_name: fontName,
        font_size: fontSize,
        text_fore_color: textColor,
        stroke_color: strokeColor,
        stroke_width: strokeWidth,
        subtitle_position: subtitlePosition,
        custom_position: customPosition,
        subtitle_y_percent: subtitleY,
        subtitle_max_chars: subtitleMaxChars,
        subtitle_mask_enabled: maskEnabled,
        subtitle_mask_y: maskY,
        subtitle_mask_height: maskHeight,
        subtitle_mask_blur: maskBlur,
        subtitle_mask_opacity: maskOpacity,
        clip_mode: clipMode,
        coverage,
        analysis_mode: analysisMode,
        output_mode: outputMode,
        pause_between: pauseBetween,
        pause_seconds: pauseSeconds,
        review_mode: reviewMode,
        tts_pronunciation: ttsPronunciation,
        script_text: mode === 'script' ? scriptText : '',
        transcript_text: mode === 'transcript' ? transcriptText : '',
      }
      const hints = [
        '',
        '第二方案：节奏更紧凑，开头更强钩子，减少铺陈，结尾回扣开头。',
        '第三方案：更克制，多留白，突出关键画面，结尾用一句话回扣主题。',
      ]
      const labels = ['A', 'B', 'C']
      const created: NarrationTask[] = []
      for (let index = 0; index < variants; index += 1) {
        const suffix = hints[index] || ''
        const task = await createNarrationTask({
          ...base,
          title: variants > 1 ? selectedVideo.title + ' · 方案 ' + labels[index] : selectedVideo.title,
          instruction: [base.instruction, suffix].filter(Boolean).join('\n'),
        })
        created.push(task)
      }
      setTasks((current) => [...created, ...current.filter((item) => !created.some((task) => task.id === item.id))])
      toast.success(variants > 1 ? `已提交 ${created.length} 个方案，完成规划后分别审片` : '已提交生成，视频完成后自动进入素材库')
    } catch (e) {
      toast.error('提交失败：' + errorMessage(e))
      void loadCaps()
    } finally {
      setCreating(false)
    }
  }

  const openReview = async (task: NarrationTask) => {
    setReviewTask(task)
    setReviewPlan(null)
    setPreviewUrl(null)
    setReviewLoading(true)
    try {
      setReviewPlan(await fetchNarrationPlan(task.id))
    } catch (e) {
      toast.error('读取规划失败：' + errorMessage(e))
    } finally {
      setReviewLoading(false)
    }
  }

  const comparable = (task: NarrationTask) =>
    tasks.filter(
      (item) =>
        item.video_id === task.video_id &&
        (item.status === 'awaiting_review' || item.status === 'success') &&
        item.id !== task.id,
    )

  const openCompare = (task: NarrationTask) => {
    const group = [task, ...comparable(task)].slice(0, 2)
    setCompareSeed(task)
    setCompareIds(group.map((item) => item.id))
    setCompareResults([])
    setCompareOpen(true)
  }

  const buildCompare = async () => {
    const chosen = tasks.filter((task) => compareIds.includes(task.id))
    if (chosen.length < 2) {
      toast.error('至少选择两个方案')
      return
    }
    setCompareLoading(true)
    try {
      const results = await Promise.all(
        chosen.map(async (task) => {
          const [plan, preview] = await Promise.all([
            fetchNarrationPlan(task.id).catch(() => null),
            previewNarrationPlan(task.id).catch(() => null),
          ])
          return { task, plan, preview: preview?.url ? preview.url + '?t=' + Date.now() : null }
        }),
      )
      setCompareResults(results)
    } catch (e) {
      toast.error('对比预览失败：' + errorMessage(e))
    } finally {
      setCompareLoading(false)
    }
  }

  const chooseCompare = async (task: NarrationTask) => {
    if (task.status === 'awaiting_review') {
      setBusyTask(task.id)
      try {
        await approveNarrationTask(task.id)
        toast.success('已选择该方案，开始正式渲染')
        setCompareOpen(false)
        await loadTasks()
      } catch (e) {
        toast.error('选择失败：' + errorMessage(e))
      } finally {
        setBusyTask(null)
      }
      return
    }
    toast.success('已选择「' + task.title + '」，成片已在视频库')
    setCompareOpen(false)
  }

  const buildLowPreview = async () => {
    if (!reviewTask) return
    setPreviewBuilding(true)
    setPreviewUrl(null)
    try {
      const result = await previewNarrationPlan(reviewTask.id)
      setPreviewUrl(result.url + '?t=' + Date.now())
      toast.success('低清预览已生成')
    } catch (e) {
      toast.error('预览失败：' + errorMessage(e))
    } finally {
      setPreviewBuilding(false)
    }
  }

  const approveReview = async () => {
    if (!reviewTask) return
    setBusyTask(reviewTask.id)
    try {
      await approveNarrationTask(reviewTask.id)
      toast.success('已确认，开始渲染')
      setReviewTask(null)
      setReviewPlan(null)
      await loadTasks()
    } catch (e) {
      toast.error('确认失败：' + errorMessage(e))
    } finally {
      setBusyTask(null)
    }
  }

  const downloadOutput = (task: NarrationTask) => {
    const output = task.output_video
    if (!output) return
    window.location.href = '/api/videos/' + output.id + '/download'
  }

  const importLegacy = async (task: LegacyNarrationTask) => {
    setLegacyBusy(task.task_id)
    try {
      await importLegacyNarrationTask(task.task_id)
      toast.success('历史成片已加入视频库')
      await Promise.all([loadVideos(), loadLegacy()])
    } catch (e) {
      toast.error('导入失败：' + errorMessage(e))
    } finally {
      setLegacyBusy(null)
    }
  }

  const cancelTask = async (task: NarrationTask) => {
    const ok = await confirmDialog({
      title: '取消生成任务',
      content: '确定取消「' + task.title + '」吗？已生成的临时文件会保留，但不会入库。',
      okText: '取消任务',
      danger: true,
    })
    if (!ok) return
    setBusyTask(task.id)
    try {
      const updated = await cancelNarrationTask(task.id)
      setTasks((current) => current.map((item) => (item.id === updated.id ? updated : item)))
      toast.success('任务已取消')
    } catch (e) {
      toast.error('取消失败：' + errorMessage(e))
    } finally {
      setBusyTask(null)
    }
  }

  const retryImport = async (task: NarrationTask) => {
    setBusyTask(task.id)
    try {
      const updated = await importNarrationTask(task.id)
      setTasks((current) => current.map((item) => (item.id === updated.id ? updated : item)))
      toast.success('成片已加入视频库')
      void loadVideos()
    } catch (e) {
      toast.error('入库失败：' + errorMessage(e))
    } finally {
      setBusyTask(null)
    }
  }

  const retryAll = () => {
    void loadCaps()
    void loadVideos()
    void loadTasks()
  }

  const needsVision = mode !== 'script' && mode !== 'transcript' && mode !== 'asr'
  const ready = caps?.ready ?? false
  const textReady = Boolean(caps?.settings?.text?.configured && caps.settings.text.model)
  const visionReady = Boolean(caps?.settings?.vision?.configured && caps.settings.vision.model)
  const blockedReason = !caps
    ? 'AI 解说引擎未连接'
    : !textReady
      ? '请先在「AI 模型」中配置文本模型'
      : needsVision && !visionReady
        ? '当前方式需要视觉模型；也可以改用文稿 / 自动识别口播'
        : ''
  const missingDeps = Object.entries(caps?.dependencies ?? {}).filter(([, ok]) => !ok).map(([name]) => name)

  return (
    <section aria-label="AI 解说工作台">
      <PageHeader
        title="AI 解说"
        desc="从素材库选一条视频，组合内容类型与主播人格生成解说、配音、字幕并合成成片；完成后自动入库。"
        extra={
          <>
            <Button variant="ghost" icon={<GearSix size={15} />} onClick={openSettings}>
              AI 模型
            </Button>
            <Button variant="ghost" icon={<ArrowClockwise size={15} />} onClick={retryAll} loading={capsLoading}>
              刷新
            </Button>
          </>
        }
      />

      {capsError && (
        <Alert type="error" className="mb-5">
          <div className="flex flex-wrap items-center gap-3">
            <span className="min-w-0 flex-1">
              AI 解说引擎未连接：{capsError}
              <br />
              请确认 Engine 服务已启动（narrato-engine，默认 127.0.0.1:8601）。
            </span>
            <Button size="sm" onClick={() => void loadCaps()} loading={capsLoading}>重试</Button>
          </div>
        </Alert>
      )}

      {caps && !ready && (
        <Alert type="warning" className="mb-5">
          <div className="flex flex-wrap items-center gap-3">
            <span className="min-w-0 flex-1">
              模型配置还不完整。文本模型用于写解说，视觉模型用于“按画面理解”；如果只用文稿或自动识别口播，可以先配置文本模型和共享 ASR。
            </span>
            <Button size="sm" onClick={openSettings}>去配置</Button>
          </div>
        </Alert>
      )}

      {caps && missingDeps.length > 0 && (
        <Alert type="warning" className="mb-5">
          引擎依赖缺失：{missingDeps.join('、')}。请在 narratoai 虚拟环境中安装 requirements 后重启 Engine。
        </Alert>
      )}


      <div className="narrate-overview">
        <div>
          <span>素材</span>
          <b>{selectedVideo ? selectedVideo.title : '未选择'}</b>
        </div>
        <div>
          <span>输出</span>
          <b>{outputMode === 'edit' ? 'AI 剪辑 · 原声' : 'AI 解说 · 配音+字幕'}</b>
        </div>
        <div>
          <span>目标</span>
          <b>{coverage === 'full' ? '完整原片' : duration + ' 秒精选'}</b>
        </div>
        <div>
          <span>规划</span>
          <b>{analysisMode === 'deep' ? '深度分析' : '快速分析'} · {reviewMode ? '先审片' : '直接渲染'}</b>
        </div>
      </div>
      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_400px]">
        <div className="min-w-0 space-y-5">
          <Panel
            title="1 · 选择视频"
            extra={<Link to="/videos" className="text-xs text-ink-3 hover:text-accent">视频素材库 →</Link>}
          >
            <div className="mb-3 flex flex-wrap items-center gap-2">
              <Input
                value={videoSearch}
                onChange={setVideoSearch}
                placeholder="搜索素材标题"
                allowClear
                className="max-w-xs"
              />
              <span className="text-xs text-ink-4">共 {videos.length} 条素材</span>
            </div>
            {videosLoading ? (
              <div className="grid grid-cols-3 gap-3 sm:grid-cols-4 lg:grid-cols-6">
                {Array.from({ length: 6 }).map((_, index) => (
                  <div key={index} className="skeleton aspect-[3/4] rounded-xl" />
                ))}
              </div>
            ) : filteredVideos.length === 0 ? (
              <EmptyState
                icon={<VideoCamera size={26} />}
                title={videos.length === 0 ? '素材库还没有视频' : '没有匹配的素材'}
                desc={videos.length === 0 ? '先下载、录制或上传一条视频，再回来生成解说。' : '换个关键词试试。'}
                action={<Link to={videos.length === 0 ? '/download' : '/videos'}><Button size="sm">去准备素材</Button></Link>}
              />
            ) : (
              <div className="narrate-strip">
                {filteredVideos.map((video) => (
                  <button
                    key={video.id}
                    type="button"
                    onClick={() => setSelectedId(video.id)}
                    className={cx('narrate-video-card', selectedId === video.id && 'is-selected')}
                    aria-pressed={selectedId === video.id}
                  >
                    <span className="narrate-video-cover">
                      {video.cover_url ? (
                        <img src={video.cover_url} alt="" loading="lazy" />
                      ) : (
                        <FilmSlate size={22} />
                      )}
                      <i>{formatDuration(video.duration)}</i>
                    </span>
                    <strong title={video.title}>{video.title}</strong>
                  </button>
                ))}
              </div>
            )}
            {selectedVideo && (
              <div className="mt-4 flex items-center gap-3 rounded-xl border border-line-soft bg-fill px-3 py-2.5">
                <Sparkle size={16} className="shrink-0 text-accent" />
                <span className="min-w-0 flex-1 truncate text-[13px] text-ink-2">
                  已选择：<b className="font-medium text-ink-1">{selectedVideo.title}</b>
                  <span className="ml-2 text-ink-4">{formatDuration(selectedVideo.duration)}</span>
                </span>
                <Link to={'/videos?q=' + encodeURIComponent(selectedVideo.title)} className="text-xs text-ink-3 hover:text-accent">
                  查看素材
                </Link>
              </div>
            )}
          </Panel>

          <Panel title="2 · 创作设定">
            <div className="space-y-5">
              <div>
                <p className="mb-2 text-xs font-medium text-ink-2">内容类型</p>
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                  {contentTypes.map((item) => (
                    <button
                      key={item.value}
                      type="button"
                      onClick={() => selectContentType(item.value)}
                      className={cx('narrate-type-chip', contentType === item.value && 'is-selected')}
                    >
                      <strong>{item.label}</strong>
                      <span>{item.summary}</span>
                    </button>
                  ))}
                </div>
                {currentContentType && (
                  <p className="mt-2 text-xs text-ink-4">
                    当前风格：{currentContentType.summary}。原声音量建议 {Math.round(currentContentType.original_volume * 100)}%。
                  </p>
                )}
              </div>

              <div className="rounded-2xl border border-line-soft bg-fill/60 p-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div>
                    <p className="text-xs font-medium text-ink-2">主播人格（可选）</p>
                    <p className="mt-1 text-[11px] text-ink-4">内容类型决定讲什么，人格决定怎么讲；风格改写不会改变时间轴。</p>
                  </div>
                  {persona !== 'auto' && (
                    <label className="flex items-center gap-2 text-xs text-ink-3">
                      风格强度
                      <Select value={personaStrength} onChange={setPersonaStrength} options={personaStrengthOptions} className="w-[104px]" />
                    </label>
                  )}
                </div>
                <div className="mt-3 grid gap-2 sm:grid-cols-3">
                  {personas.map((item) => (
                    <button
                      key={item.value}
                      type="button"
                      aria-pressed={persona === item.value}
                      onClick={() => setPersona(item.value)}
                      className={cx('narrate-type-chip', persona === item.value && 'is-selected')}
                    >
                      <strong>{item.label}</strong>
                      <span>{item.summary}</span>
                    </button>
                  ))}
                </div>
              </div>

              <div className="grid gap-3 sm:grid-cols-2">
                <label className="block">
                  <span className="mb-1.5 block text-xs font-medium text-ink-2">主题（可选）</span>
                  <Input value={topic} onChange={setTopic} placeholder="例如：周末露营 / 咖啡萃取原理" />
                  <span className="mt-1 block text-[11px] text-ink-4">告诉模型这条视频讲什么，越具体越稳。</span>
                </label>
                <label className="block">
                  <span className="mb-1.5 block text-xs font-medium text-ink-2">生成方式</span>
                  <Select value={mode} onChange={setMode} options={modeOptions.length ? modeOptions : [{ value: 'auto', label: '自动' }]} />
                  <span className="mt-1 block text-[11px] text-ink-4">
                    {mode === 'asr'
                      ? '先调用共享 ASR 识别原片口播，再改写成解说。'
                      : needsVision
                        ? '会先抽帧理解画面，再写解说。'
                        : '直接按你提供的文字改写，不调用视觉模型。'}
                  </span>
                </label>
              </div>

              <label className="block">
                <span className="mb-1.5 block text-xs font-medium text-ink-2">补充要求（可选）</span>
                <TextArea
                  value={instruction}
                  onChange={setInstruction}
                  rows={3}
                  placeholder="例如：面向完全不懂的观众；不要使用网络流行语；重点解释第二段的动作。"
                />
              </label>

              {mode === 'script' && (
                <label className="block">
                  <span className="mb-1.5 block text-xs font-medium text-ink-2">解说稿</span>
                  <TextArea
                    value={scriptText}
                    onChange={setScriptText}
                    rows={7}
                    placeholder="每行一段。引擎会按字数把文稿分配到整条视频的时间轴。"
                  />
                  <span className="mt-1 block text-[11px] text-ink-4">适合已经有成熟文案、只想让系统配音和合成的情况。</span>
                </label>
              )}

              {mode === 'asr' && (
                <div className="rounded-xl border border-line-soft bg-fill px-3 py-2.5 text-[12px] leading-relaxed text-ink-3">
                  将把原片送到本机共享 ASR（默认 8600 端口）识别口播，再按内容类型改写成解说。
                  视频较长时识别会先花一些时间；识别完成后才开始配音与合成。
                </div>
              )}

              {mode === 'transcript' && (
                <label className="block">
                  <span className="mb-1.5 block text-xs font-medium text-ink-2">口播文稿 / 字幕</span>
                  <TextArea
                    value={transcriptText}
                    onChange={setTranscriptText}
                    rows={7}
                    placeholder="粘贴原视频的口播稿或 SRT 字幕。模型会改写成解说，不会照读。"
                  />
                  <span className="mt-1 block text-[11px] text-ink-4">适合口播、访谈、课程等“内容在声音里”的视频。</span>
                </label>
              )}
            </div>
          </Panel>
        </div>

        <div className="space-y-5">
          <Panel title="3 · 生成设置">
            <div className="space-y-3">
              <SettingsGroup title="基础设置" subtitle="输出、时长与选段" defaultOpen>
              <label className="block">
                <span className="mb-1.5 block text-xs font-medium text-ink-2">输出类型</span>
                <Select
                  value={outputMode}
                  onChange={(value) => setOutputMode(value === 'edit' ? 'edit' : 'narration')}
                  options={[
                    { value: 'narration', label: 'AI 解说 · 配音 + 字幕' },
                    { value: 'edit', label: 'AI 剪辑 · 只选段，保留原声' },
                  ]}
                />
                <span className="mt-1 block text-[11px] text-ink-4">
                  {outputMode === 'edit'
                    ? '不做配音、不烧字幕；只按分析结果选段、排序并拼接原片。'
                    : '生成解说、配音、字幕并合成。'}
                </span>
              </label>
              {outputMode === 'edit' && (
                <Alert type="info">
                  AI 剪辑会复用深度分析和选段规划，但不会生成 TTS/字幕；下方配音、字幕、停顿设置不会生效。
                </Alert>
              )}
              <div className="flex items-center justify-between gap-3 rounded-xl border border-line-soft bg-fill px-3 py-2.5">
                <div>
                  <div className="text-[12px] text-ink-2">渲染前审片</div>
                  <div className="mt-0.5 text-[11px] text-ink-4">规划完成后暂停，确认选段和脚本再开始配音/合成。</div>
                </div>
                <Switch checked={reviewMode} onChange={setReviewMode} label="渲染前审片" />
              </div>
              <label className="block">
                <span className="mb-1.5 block text-xs font-medium text-ink-2">方案数</span>
                <Select
                  value={String(variants)}
                  onChange={(value) => setVariants(Math.max(1, Math.min(3, Number(value) || 1)))}
                  options={[
                    { value: '1', label: '1 个方案' },
                    { value: '2', label: '2 个方案 · 不同叙事节奏' },
                    { value: '3', label: '3 个方案 · 多角度对比' },
                  ]}
                />
                <span className="mt-1 block text-[11px] text-ink-4">多方案会分别分析和审片，可分别低清预览后选一个正式渲染。</span>
              </label>
              <label className="block">
                <span className="mb-1.5 block text-xs font-medium text-ink-2">覆盖范围</span>
                <Select
                  value={coverage}
                  onChange={(value) => setCoverage(value === 'full' ? 'full' : 'highlights')}
                  options={[
                    { value: 'highlights', label: '精选片段 · 分布在全片' },
                    { value: 'full', label: '跟随原片 · 完整时长' },
                  ]}
                />
                <span className="mt-1 block text-[11px] text-ink-4">
                  {coverage === 'full'
                    ? '按整条原片生成，成片时长约等于源视频；长录制会耗时更久。'
                    : '从整条视频中挑选片段，时间戳分散在全片，不会只取开头几十秒。'}
                </span>
              </label>
              <div className="settings-section-title">分析与剪辑</div>
              <label className="block">
                <span className="mb-1.5 block text-xs font-medium text-ink-2">分析深度</span>
                <Select
                  value={analysisMode}
                  onChange={(value) => setAnalysisMode(value === 'fast' ? 'fast' : 'deep')}
                  options={[
                    { value: 'deep', label: '深度 · 分段解析 + 全片规划（慢，质量优先）' },
                    { value: 'fast', label: '快速 · 整片抽帧一次分析' },
                  ]}
                />
                <span className="mt-1 block text-[11px] text-ink-4">
                  {mode === 'auto' || mode === 'visual'
                    ? '深度模式按 30 秒分段、1.5 秒抽帧，逐段解析并落盘，最后统一规划剪辑；长视频更慢。'
                    : '当前方式使用文稿/ASR，不跑画面深度分析。'}
                </span>
              </label>
              <label className="block">
                <span className="mb-1.5 block text-xs font-medium text-ink-2">目标时长（秒）</span>
                <Input value={coverage === 'full' && selectedVideo?.duration ? String(Math.round(selectedVideo.duration)) : duration} onChange={setDuration} type="number" min={5} max={7200} disabled={coverage === 'full'} />
                <span className="mt-1 block text-[11px] text-ink-4">
                  {coverage === 'full'
                    ? '完整覆盖模式忽略该值，按源视频时长生成。'
                    : '精选片段的总原片时长；实际成片由脚本与配音决定。'}
                </span>
              </label>
              <label className="block">
                <span className="mb-1.5 block text-xs font-medium text-ink-2">画幅</span>
                <Select value={aspect} onChange={setAspect} options={aspectOptions.length ? aspectOptions : [{ value: 'auto', label: '跟随原片' }]} />
              </label>
              <label className="block">
                <span className="mb-1.5 block text-xs font-medium text-ink-2">剪辑方式</span>
                <Select
                  value={clipMode}
                  onChange={(value) => setClipMode(value === 'narration' ? 'narration' : 'timeline')}
                  options={[
                    { value: 'timeline', label: '按原片时间轴 · 逐段保留原片时长' },
                    { value: 'narration', label: '按解说长度 · 成片更短更紧凑' },
                  ]}
                />
                <span className="mt-1 block text-[11px] text-ink-4">
                  时间轴模式会按脚本每一段的时间区间分别剪切，不会只取开头几十秒；解说比原片段短时保留原声画面。
                </span>
              </label>
              </SettingsGroup>
              <SettingsGroup title="配音与字幕" subtitle={voice.split('-').slice(2).join(' ').replace('Neural', '') + (subtitles ? ' · 字幕开' : ' · 字幕关')} defaultOpen={false}>
              <label className="block">
                <span className="mb-1.5 block text-xs font-medium text-ink-2">配音音色</span>
                <Select value={voice} onChange={setVoice} options={voiceOptions.length ? voiceOptions : [{ value: voice, label: voice }]} />
              </label>
              <label className="block">
                <span className="mb-1.5 flex items-center justify-between text-xs font-medium text-ink-2">
                  <span>语速</span><b className="tnum font-normal text-ink-3">{voiceRate.toFixed(1)}x</b>
                </span>
                <input
                  type="range"
                  min={0.6}
                  max={1.6}
                  step={0.05}
                  value={voiceRate}
                  onChange={(event) => setVoiceRate(Number(event.target.value))}
                  className="w-full accent-[var(--accent)]"
                />
              </label>
              <label className="block">
                <span className="mb-1.5 flex items-center justify-between text-xs font-medium text-ink-2">
                  <span>原声音量</span><b className="tnum font-normal text-ink-3">{Math.round(originalVolume * 100)}%</b>
                </span>
                <input
                  type="range"
                  min={0}
                  max={1.2}
                  step={0.05}
                  value={originalVolume}
                  onChange={(event) => setOriginalVolume(Number(event.target.value))}
                  className="w-full accent-[var(--accent)]"
                />
                <span className="mt-1 block text-[11px] text-ink-4">访谈/口播建议调高；纯画面建议调低。</span>
              </label>
              {outputMode === 'narration' && (
                <div className="rounded-xl border border-line-soft bg-fill px-3 py-2.5">
                  <div className="flex items-center justify-between gap-3">
                    <span className="text-[12px] text-ink-2">段间保留原声停顿</span>
                    <Switch checked={pauseBetween} onChange={setPauseBetween} label="段间保留原声停顿" />
                  </div>
                  {pauseBetween && (
                    <label className="mt-2 block">
                      <span className="mb-1 flex items-center justify-between text-[11px] text-ink-2">
                        <span>停顿时长</span><b className="tnum font-normal text-ink-3">{pauseSeconds.toFixed(1)}s</b>
                      </span>
                      <input
                        type="range"
                        min={0.5}
                        max={6}
                        step={0.5}
                        value={pauseSeconds}
                        onChange={(event) => setPauseSeconds(Number(event.target.value))}
                        className="w-full accent-[var(--accent)]"
                      />
                      <span className="mt-1 block text-[11px] text-ink-4">在相邻解说段之间插入原声片段，让解说有呼吸，不是全程连读。</span>
                    </label>
                  )}
                </div>
              )}
              {outputMode === 'narration' && (
                <div className="flex items-center justify-between gap-3 rounded-xl border border-line-soft bg-fill px-3 py-2.5">
                  <div>
                    <div className="text-[12px] text-ink-2">多音字读音校正</div>
                    <div className="mt-0.5 text-[11px] text-ink-4">只改 TTS 朗读文本，字幕仍显示原词；内置重庆/银行/重新等常见词。</div>
                  </div>
                  <Switch checked={ttsPronunciation} onChange={setTtsPronunciation} label="多音字读音校正" />
                </div>
              )}
              <div className="flex items-center justify-between gap-3 rounded-xl border border-line-soft bg-fill px-3 py-2.5">
                <span className="flex items-center gap-2 text-[13px] text-ink-2"><Subtitles size={16} className="text-ink-3" />烧录字幕</span>
                <Switch checked={subtitles} onChange={setSubtitles} label="烧录字幕" />
              </div>
              {subtitles && (
                <div className="space-y-3 rounded-xl border border-line-soft bg-fill px-3 py-3">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-[12px] font-medium text-ink-2">字幕大小与位置</span>
                    <Button size="sm" variant="text" loading={subtitlePreviewLoading} onClick={() => void showSubtitlePreview()}>
                      预览
                    </Button>
                  </div>
                  <label className="block">
                    <span className="mb-1.5 flex items-center justify-between text-[11px] font-medium text-ink-2">
                      <span>字幕大小</span><b className="tnum font-normal text-ink-3">{fontSize}px</b>
                    </span>
                    <div className="flex items-center gap-2">
                      <input type="range" min={20} max={120} step={1} value={fontSize} onChange={(event) => setFontSize(Number(event.target.value))} className="min-w-0 flex-1 accent-[var(--accent)]" />
                      <input type="number" min={20} max={120} value={fontSize} onChange={(event) => setFontSize(Math.max(20, Math.min(120, Number(event.target.value) || 20)))} className="h-8 w-16 rounded-lg border border-line bg-surface px-2 text-center text-xs text-ink-1 outline-none focus:border-accent" aria-label="字幕字号" />
                    </div>
                  </label>
                  <label className="block">
                    <span className="mb-1.5 flex items-center justify-between text-[11px] font-medium text-ink-2">
                      <span>字幕每行字数</span><b className="tnum font-normal text-ink-3">{subtitleMaxChars} 字</b>
                    </span>
                    <div className="flex items-center gap-2">
                      <input type="range" min={6} max={24} step={1} value={subtitleMaxChars} onChange={(event) => setSubtitleMaxChars(Number(event.target.value))} className="min-w-0 flex-1 accent-[var(--accent)]" />
                      <input type="number" min={6} max={24} value={subtitleMaxChars} onChange={(event) => setSubtitleMaxChars(Math.max(6, Math.min(24, Number(event.target.value) || 12)))} className="h-8 w-16 rounded-lg border border-line bg-surface px-2 text-center text-xs text-ink-1 outline-none focus:border-accent" aria-label="字幕每行字数" />
                    </div>
                    <span className="mt-1 block text-[11px] text-ink-4">按标点和语义断句，再限制每行长度；不是按固定字数硬切。</span>
                  </label>
                  <label className="block">
                    <span className="mb-1.5 block text-[11px] font-medium text-ink-2">字幕位置</span>
                    <Select
                      value={subtitlePosition}
                      onChange={(value) => {
                        setSubtitlePosition(value)
                        setSubtitleY(value === 'custom' ? customPosition : (POSITION_Y[value] ?? 88))
                      }}
                      options={SUBTITLE_POSITIONS.map((item) => ({ value: item.value, label: item.value === 'custom' ? item.label : item.label + ' · ' + (POSITION_Y[item.value] ?? 88) + '%' }))}
                    />
                  </label>
                  {subtitlePosition === 'custom' && (
                    <label className="block">
                      <span className="mb-1.5 flex items-center justify-between text-[11px] font-medium text-ink-2">
                        <span>距顶部</span><b className="tnum font-normal text-ink-3">{Math.round(customPosition)}%</b>
                      </span>
                      <div className="flex items-center gap-2">
                        <input type="range" min={0} max={99} step={1} value={customPosition} onChange={(event) => { const value = Number(event.target.value); setCustomPosition(value); setSubtitleY(value) }} className="min-w-0 flex-1 accent-[var(--accent)]" />
                        <input type="number" min={0} max={99} value={Math.round(customPosition)} onChange={(event) => { const value = Math.max(0, Math.min(99, Number(event.target.value) || 0)); setCustomPosition(value); setSubtitleY(value) }} className="h-8 w-16 rounded-lg border border-line bg-surface px-2 text-center text-xs text-ink-1 outline-none focus:border-accent" aria-label="字幕距顶部百分比" />
                      </div>
                    </label>
                  )}
                  <p className="text-[11px] text-ink-4">
                    当前：{fontSize}px · {SUBTITLE_POSITIONS.find((item) => item.value === subtitlePosition)?.label ?? '底部'}
                    {subtitlePosition === 'custom' ? '（距顶部 ' + Math.round(customPosition) + '%）' : '（' + Math.round(subtitleY) + '%）'}
                  </p>
                  <details className="rounded-lg border border-line-soft bg-surface px-3 py-2.5">
                    <summary className="cursor-pointer select-none text-[11px] font-medium text-ink-3 hover:text-ink-1">字体、颜色、描边与遮罩</summary>
                    <div className="mt-3 space-y-3">
                      <label className="block">
                        <span className="mb-1 block text-[11px] font-medium text-ink-2">字体</span>
                        <Select value={fontName} onChange={setFontName} options={fontOptions} />
                      </label>
                      <div className="grid grid-cols-2 gap-2">
                        <label className="block">
                          <span className="mb-1 block text-[11px] font-medium text-ink-2">字色</span>
                          <input type="color" value={textColor} onChange={(event) => setTextColor(event.target.value)} className="h-8 w-full cursor-pointer rounded-lg border border-line bg-transparent p-0.5" />
                        </label>
                        <label className="block">
                          <span className="mb-1 block text-[11px] font-medium text-ink-2">描边色</span>
                          <input type="color" value={strokeColor} onChange={(event) => setStrokeColor(event.target.value)} className="h-8 w-full cursor-pointer rounded-lg border border-line bg-transparent p-0.5" />
                        </label>
                      </div>
                      <label className="block">
                        <span className="mb-1 flex items-center justify-between text-[11px] font-medium text-ink-2">
                          <span>描边宽度</span><b className="tnum font-normal text-ink-3">{strokeWidth.toFixed(1)}</b>
                        </span>
                        <input type="range" min={0} max={5} step={0.1} value={strokeWidth} onChange={(event) => setStrokeWidth(Number(event.target.value))} className="w-full accent-[var(--accent)]" />
                      </label>
                      <div className="flex items-center justify-between gap-3 rounded-lg border border-line-soft bg-fill px-3 py-2">
                        <span className="text-[12px] text-ink-2">遮盖原视频字幕</span>
                        <Switch checked={maskEnabled} onChange={setMaskEnabled} label="遮盖原视频字幕" />
                      </div>
                      {maskEnabled && (
                        <div className="space-y-2.5 rounded-lg border border-line-soft bg-fill px-3 py-2.5">
                          <label className="block">
                            <span className="mb-1 flex items-center justify-between text-[11px] text-ink-2"><span>遮罩顶部</span><b className="tnum font-normal text-ink-3">{Math.round(maskY)}%</b></span>
                            <input type="range" min={0} max={99} step={1} value={maskY} onChange={(event) => setMaskY(Number(event.target.value))} className="w-full accent-[var(--accent)]" />
                          </label>
                          <label className="block">
                            <span className="mb-1 flex items-center justify-between text-[11px] text-ink-2"><span>遮罩高度</span><b className="tnum font-normal text-ink-3">{Math.round(maskHeight)}%</b></span>
                            <input type="range" min={2} max={40} step={1} value={maskHeight} onChange={(event) => setMaskHeight(Number(event.target.value))} className="w-full accent-[var(--accent)]" />
                          </label>
                          <label className="block">
                            <span className="mb-1 flex items-center justify-between text-[11px] text-ink-2"><span>模糊半径</span><b className="tnum font-normal text-ink-3">{maskBlur}px</b></span>
                            <input type="range" min={0} max={50} step={1} value={maskBlur} onChange={(event) => setMaskBlur(Number(event.target.value))} className="w-full accent-[var(--accent)]" />
                          </label>
                          <label className="block">
                            <span className="mb-1 flex items-center justify-between text-[11px] text-ink-2"><span>不透明度</span><b className="tnum font-normal text-ink-3">{maskOpacity}%</b></span>
                            <input type="range" min={0} max={100} step={1} value={maskOpacity} onChange={(event) => setMaskOpacity(Number(event.target.value))} className="w-full accent-[var(--accent)]" />
                          </label>
                        </div>
                      )}
                    </div>
                  </details>
                </div>
              )}
              </SettingsGroup>
              <Button
                variant="primary"
                size="md"
                full
                loading={creating}
                disabled={!!blockedReason || !selectedVideo}
                title={blockedReason || (selectedVideo ? '' : '请先选择视频')}
                icon={<MagicWand size={16} />}
                onClick={() => void submit()}
              >
                {outputMode === 'edit' ? '生成 AI 剪辑' : '生成 AI 解说'}
              </Button>
              <p className="text-center text-[11px] text-ink-4">
                {blockedReason || (selectedVideo ? '生成期间可离开页面，任务在后台继续；完成后自动入库。' : '请先选择一条视频。')}
              </p>
            </div>
          </Panel>

          <Panel title="引擎状态">
            <div className="space-y-2 text-[12px] text-ink-3">
              <div className="flex items-center justify-between"><span>引擎</span><b className={cx('font-medium', caps ? 'text-success' : 'text-danger')}>{caps ? '已连接' : '未连接'}</b></div>
              <div className="flex items-center justify-between"><span>版本</span><b className="font-medium text-ink-2">{caps?.version ?? '-'}</b></div>
              <div className="flex items-center justify-between"><span>文本模型</span><b className="font-medium text-ink-2">{caps?.settings?.text?.model || '未配置'}</b></div>
              <div className="flex items-center justify-between"><span>视觉模型</span><b className="font-medium text-ink-2">{caps?.settings?.vision?.model || '未配置'}</b></div>
              <div className="flex items-center justify-between"><span>并发任务</span><b className="font-medium text-ink-2">{activeCount}</b></div>
            </div>
          </Panel>
        </div>
      </div>

      <Panel className="mt-5" title="解说任务" extra={<span className="text-xs text-ink-4">最近 {tasks.length} 条</span>}>
        {tasksError && <Alert type="warning" className="mb-3">任务列表刷新失败：{tasksError}</Alert>}
        {tasks.length === 0 ? (
          <EmptyState
            icon={<Waveform size={26} />}
            title="还没有解说任务"
            desc="选好视频和内容类型，点“生成 AI 解说”，这里会显示配音、剪辑和合成进度。"
          />
        ) : (
          <div className="space-y-2.5">
            {tasks.map((task) => {
              const meta = STATUS_META[task.status] ?? STATUS_META.queued
              const Icon = meta.icon
              const running = task.status === 'queued' || task.status === 'running'
              const awaiting = task.status === 'awaiting_review'
              const canCompare = (awaiting || task.status === 'success') && comparable(task).length > 0
              const output = task.output_video
              return (
                <div key={task.id} className="rounded-xl border border-line-soft bg-fill px-3.5 py-3">
                  <div className="flex flex-wrap items-center gap-3">
                    <span className={cx('inline-flex items-center gap-1 rounded-md px-2 py-1 text-[11px] font-medium', meta.tone)}>
                      <Icon size={12} />{meta.label}
                    </span>
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-[13px] font-medium text-ink-1">{task.title}</p>
                      <p className="mt-0.5 truncate text-[11px] text-ink-4">
                        {task.message || '等待引擎处理'}
                        {' · '}{formatRelative(task.created_at)}
                      </p>
                    </div>
                    <div className="flex flex-wrap items-center gap-1.5">
                      {running && (
                        <Button size="sm" variant="ghost" loading={busyTask === task.id} onClick={() => void cancelTask(task)}>取消</Button>
                      )}
                      {awaiting && (
                        <>
                          <Button size="sm" variant="primary" onClick={() => void openReview(task)}>审片</Button>
                          <Button size="sm" variant="ghost" loading={busyTask === task.id} onClick={() => void cancelTask(task)}>取消</Button>
                        </>
                      )}
                      {task.status === 'success' && output && (
                        <>
                          <Button size="sm" variant="ghost" icon={<Play size={13} />} onClick={() => setPlaying({ title: output.title, url: output.file_url })}>播放</Button>
                          <Link to={'/publish?video=' + output.id}><Button size="sm" variant="ghost" icon={<ArrowSquareOut size={13} />}>发布</Button></Link>
                        </>
                      )}
                      {task.status === 'success' && !output && (
                        <Button size="sm" variant="ghost" loading={busyTask === task.id} onClick={() => void retryImport(task)}>重试入库</Button>
                      )}
                      {task.status === 'success' && output && (
                        <Button size="sm" variant="ghost" icon={<DownloadSimple size={13} />} onClick={() => downloadOutput(task)}>下载</Button>
                      )}
                      {canCompare && (
                        <Button size="sm" variant="ghost" onClick={() => openCompare(task)}>对比</Button>
                      )}
                    </div>
                  </div>
                  <div className="mt-2.5 flex items-center gap-3">
                    <Progress
                      percent={(task.progress ?? 0) / 100}
                      status={task.status === 'failed' ? 'error' : task.status === 'success' ? 'success' : 'normal'}
                      className="flex-1"
                    />
                    <span className="w-9 shrink-0 text-right text-[11px] tabular-nums text-ink-4">{Math.round(task.progress ?? 0)}%</span>
                  </div>
                  {task.error && task.status !== 'running' && (
                    <details className="mt-2 text-[11px] text-ink-4">
                      <summary className="cursor-pointer hover:text-ink-2">查看错误详情</summary>
                      <pre className="mt-1 max-h-32 overflow-auto whitespace-pre-wrap break-all rounded-lg bg-bg p-2">{task.error}</pre>
                    </details>
                  )}
                </div>
              )
            })}
          </div>
        )}
        {legacyTasks.length > 0 && (
          <div className="mt-4 border-t border-line-soft pt-3">
            <div className="mb-2 flex items-center justify-between">
              <span className="text-[12px] font-medium text-ink-2">旧版历史任务</span>
              <span className="text-[11px] text-ink-4">只读扫描 · 可一键入库</span>
            </div>
            <div className="space-y-2">
              {legacyTasks.map((item) => (
                <div key={item.task_id} className="flex flex-wrap items-center gap-2 rounded-xl border border-line-soft bg-fill px-3 py-2">
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-[12px] text-ink-1">{item.title}</p>
                    <p className="mt-0.5 text-[11px] text-ink-4">
                      {formatDuration(item.duration)} · {formatRelative(item.created_at)}
                    </p>
                  </div>
                  {item.imported_video_id ? (
                    <span className="text-[11px] text-success">已入库 #{item.imported_video_id}</span>
                  ) : (
                    <Button size="sm" variant="ghost" loading={legacyBusy === item.task_id} onClick={() => void importLegacy(item)}>导入</Button>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}
      </Panel>

      <Modal
        open={compareOpen}
        onClose={() => setCompareOpen(false)}
        title="方案对比 · 选段与低清预览"
        width={980}
        footer={
          <>
            <Button variant="ghost" onClick={() => setCompareOpen(false)}>关闭</Button>
            <Button variant="primary" loading={compareLoading} disabled={compareIds.length < 2} onClick={() => void buildCompare()}>
              生成对比预览
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <div className="space-y-2">
            {compareSeed && [compareSeed, ...comparable(compareSeed)].map((item) => {
              const checked = compareIds.includes(item.id)
              return (
                <label key={item.id} className="flex cursor-pointer items-center gap-3 rounded-xl border border-line-soft bg-fill px-3 py-2">
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() => {
                      setCompareIds((current) => {
                        if (current.includes(item.id)) return current.filter((id) => id !== item.id)
                        if (current.length >= 2) return current
                        return [...current, item.id]
                      })
                    }}
                    className="accent-[var(--accent)]"
                  />
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-[12px] text-ink-1">{item.title}</div>
                    <div className="text-[11px] text-ink-4">
                      {item.status === 'awaiting_review' ? '待审片' : '已完成'} · {Math.round(item.progress ?? 0)}%
                    </div>
                  </div>
                  {checked && <span className="text-[11px] text-accent">已选</span>}
                </label>
              )
            })}
          </div>
          {compareResults.length > 0 && (
            <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
              {compareResults.map(({ task, plan, preview }) => (
                <div key={task.id} className="rounded-xl border border-line-soft bg-fill p-3">
                  <div className="mb-2 flex items-center justify-between gap-2">
                    <div className="truncate text-[12px] font-medium text-ink-1">{task.title}</div>
                    <Button size="sm" variant="primary" loading={busyTask === task.id} onClick={() => void chooseCompare(task)}>
                      {task.status === 'awaiting_review' ? '选这个' : '已选'}
                    </Button>
                  </div>
                  {preview ? (
                    <video src={preview} controls className="max-h-[28vh] w-full rounded-lg bg-black" />
                  ) : (
                    <div className="flex h-32 items-center justify-center rounded-lg bg-surface text-[11px] text-ink-4">预览生成失败</div>
                  )}
                  <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-ink-4">
                    <span>{plan?.coverage === 'full' ? '完整原片' : '精选片段'}</span>
                    <span>{plan?.output_mode === 'edit' ? 'AI 剪辑' : 'AI 解说'}</span>
                    <span>{plan?.items.length ?? 0} 段</span>
                  </div>
                  <div className="mt-2 max-h-32 space-y-1 overflow-y-auto pr-1">
                    {plan?.items.slice(0, 6).map((item, index) => (
                      <div key={index} className="text-[11px] leading-5 text-ink-2">
                        <span className="tnum text-ink-4">{item.timestamp}</span>
                        <span className="ml-1">{item.OST === 1 ? '· 原声停顿' : '· ' + (item.narration || '解说')}</span>
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}
          {compareResults.length === 0 && (
            <Alert type="info">勾选两个方案后生成低清对比。对比只拼原片，不带配音和字幕，用来判断选段和节奏。</Alert>
          )}
        </div>
      </Modal>

      <Modal
        open={!!reviewTask}
        onClose={() => { setReviewTask(null); setReviewPlan(null); setPreviewUrl(null) }}
        title="审片 · 选段与解说规划"
        width={760}
        footer={
          <>
            <Button variant="ghost" onClick={() => { setReviewTask(null); setReviewPlan(null); setPreviewUrl(null) }}>稍后</Button>
            <Button variant="ghost" loading={previewBuilding} disabled={!reviewPlan} onClick={() => void buildLowPreview()}>低清预览</Button>
            <Button variant="primary" loading={busyTask === reviewTask?.id} disabled={!reviewPlan} onClick={() => void approveReview()}>
              确认并开始渲染
            </Button>
          </>
        }
      >
        {reviewLoading ? (
          <p className="py-8 text-center text-[13px] text-ink-3">正在读取规划…</p>
        ) : !reviewPlan || reviewPlan.items.length === 0 ? (
          <p className="py-8 text-center text-[13px] text-ink-3">规划尚未生成</p>
        ) : (
          <div className="space-y-3">
            <Alert type="info">
              这里是正式渲染前的剪辑决定。确认后才会开始 TTS、裁剪、字幕和合成；先关闭窗口不会丢任务。
            </Alert>
            {previewUrl && (
              <div className="rounded-xl border border-line-soft bg-black/90 p-1">
                <video src={previewUrl} controls className="max-h-[42vh] w-full rounded-lg" />
                <p className="px-2 py-1 text-[11px] text-white/60">低清选段预览：无配音、无字幕，只检查选段和原声节奏。</p>
              </div>
            )}
            {previewUrl && (
              <div className="rounded-xl border border-line-soft bg-black/90 p-1">
                <video src={previewUrl} controls className="max-h-[42vh] w-full rounded-lg" />
                <p className="px-2 py-1 text-[11px] text-white/60">低清选段预览：无配音、无字幕，只检查选段和原声节奏。</p>
              </div>
            )}
            <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-ink-4">
              <span>覆盖：{reviewPlan.coverage === 'full' ? '完整原片' : '精选片段'}</span>
              <span>剪辑：{reviewPlan.clip_mode === 'narration' ? '按解说长度' : '按原片时间轴'}</span>
              <span>输出：{reviewPlan.output_mode === 'edit' ? 'AI 剪辑' : 'AI 解说'}</span>
              <span>共 {reviewPlan.items.length} 段</span>
            </div>
            <div className="max-h-[52vh] space-y-2 overflow-y-auto pr-1">
              {reviewPlan.items.map((item, index) => (
                <div key={index} className="rounded-xl border border-line-soft bg-fill px-3 py-2.5">
                  <div className="flex flex-wrap items-center gap-2 text-[11px]">
                    <span className="rounded bg-surface px-1.5 py-0.5 font-medium text-ink-2">#{index + 1}</span>
                    <span className="tnum text-ink-3">{item.timestamp}</span>
                    {item.OST === 1 ? (
                      <span className="rounded bg-warning-soft px-1.5 py-0.5 text-warning">原声停顿</span>
                    ) : (
                      <span className="rounded bg-accent-soft px-1.5 py-0.5 text-accent">解说</span>
                    )}
                  </div>
                  <p className="mt-1.5 text-[13px] leading-6 text-ink-1">{item.narration || '（保留原声，不解说）'}</p>
                </div>
              ))}
            </div>
          </div>
        )}
      </Modal>

      <Modal open={!!subtitlePreviewUrl} onClose={() => setSubtitlePreviewUrl(null)} title="字幕样式预览" width={560}>
        {subtitlePreviewUrl && (
          <img src={subtitlePreviewUrl} alt="字幕样式预览" className="w-full rounded-xl border border-line-soft" />
        )}
        <p className="mt-3 text-[11px] text-ink-4">预览使用独立字幕预览服务渲染，实际视频帧以成片为准。</p>
      </Modal>

      <Modal open={!!playing} onClose={() => setPlaying(null)} title={playing?.title} width={900}>
        {playing && <video src={playing.url} controls autoPlay className="max-h-[70vh] w-full rounded-xl bg-black" />}
      </Modal>

      <Modal
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        title="AI 模型与配音设置"
        width={640}
        footer={
          <>
            <Button variant="ghost" onClick={() => setSettingsOpen(false)}>取消</Button>
            <Button variant="primary" loading={settingsSaving} onClick={() => void saveSettings()}>保存设置</Button>
          </>
        }
      >
        <div className="space-y-5">
          <Alert type="info">
            配置写入引擎的 config.toml，本机保存。API Key 留空表示保持不变；环境变量 VIDEO_HUB_AI_* 会自动兜底。
          </Alert>
          <div>
            <h4 className="mb-2 text-[13px] font-medium text-ink-1">文本模型（写解说）</h4>
            <div className="space-y-2.5">
              <Input value={settingsForm.textBase} onChange={(value) => setSettingsForm((form) => ({ ...form, textBase: value }))} placeholder="Base URL，例如 https://api.deepseek.com/v1" />
              <Input value={settingsForm.textModel} onChange={(value) => setSettingsForm((form) => ({ ...form, textModel: value }))} placeholder="模型名，例如 deepseek-chat" />
              <Input value={settingsForm.textKey} onChange={(value) => setSettingsForm((form) => ({ ...form, textKey: value }))} type="password" placeholder={caps?.settings?.text?.configured ? '已配置 ' + caps.settings.text.api_key_hint + '，留空保持不变' : 'API Key'} />
            </div>
          </div>
          <div>
            <h4 className="mb-2 text-[13px] font-medium text-ink-1">视觉模型（看懂画面）</h4>
            <div className="space-y-2.5">
              <Input value={settingsForm.visionBase} onChange={(value) => setSettingsForm((form) => ({ ...form, visionBase: value }))} placeholder="Base URL，可与文本模型相同" />
              <Input value={settingsForm.visionModel} onChange={(value) => setSettingsForm((form) => ({ ...form, visionModel: value }))} placeholder="视觉模型名，例如 gpt-4o-mini / Qwen2.5-VL" />
              <Input value={settingsForm.visionKey} onChange={(value) => setSettingsForm((form) => ({ ...form, visionKey: value }))} type="password" placeholder={caps?.settings?.vision?.configured ? '已配置 ' + caps.settings.vision.api_key_hint + '，留空保持不变' : 'API Key'} />
            </div>
          </div>
          <div>
            <h4 className="mb-2 text-[13px] font-medium text-ink-1">配音</h4>
            <div className="grid gap-2.5 sm:grid-cols-2">
              <Select
                value={settingsForm.ttsEngine}
                onChange={(value) => setSettingsForm((form) => ({ ...form, ttsEngine: value }))}
                options={(caps?.tts_engines ?? [{ value: 'edge_tts', label: 'Edge TTS' }]).map((item) => ({ value: item.value, label: item.label }))}
              />
              <Select
                value={settingsForm.voice}
                onChange={(value) => setSettingsForm((form) => ({ ...form, voice: value }))}
                options={(caps?.voices ?? []).map((item) => ({ value: item.value, label: item.label }))}
              />
            </div>
            <p className="mt-2 text-[11px] text-ink-4">
              非 Edge 引擎（Azure / 腾讯云）需要先在 config.toml 配置对应密钥；本页音色预设以 Edge 为主。
            </p>
          </div>
          <p className="text-[11px] text-ink-4">配置文件：{caps?.settings?.config_file ?? '-'}</p>
        </div>
      </Modal>
    </section>
  )
}
