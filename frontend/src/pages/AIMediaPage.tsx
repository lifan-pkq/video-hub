import { useCallback, useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { DownloadSimple, ImageSquare, Sparkle, VideoCamera } from '@phosphor-icons/react'
import {
  errorMessage,
  fetchAIPool,
  fetchTask,
  formatSize,
  generateAIImage,
  generateAIVideo,
  saveGeneratedAIImage,
  uploadAIReference,
  type AIImageAsset,
  type AIImageResult,
  type AIReference,
  type AIPoolStatus,
  type Task,
  type Video,
} from '../api'
import { Alert, Button, PageHeader, Panel, Progress, Select, Switch, TextArea, toast } from '../ui'
import ImageLibraryPicker from '../features/ai/ImageLibraryPicker'

const IMAGE_SIZES = [
  { value: '1K', label: '1K · 快速草稿' },
  { value: '2K', label: '2K · 推荐' },
  { value: '3K', label: '3K · 高精度' },
  { value: '4K', label: '4K · 最慢' },
]
const RATIOS = [
  { value: '16:9', label: '16:9 横屏' },
  { value: '9:16', label: '9:16 竖屏' },
  { value: '1:1', label: '1:1 方形' },
  { value: '4:3', label: '4:3' },
  { value: '3:4', label: '3:4' },
  { value: '21:9', label: '21:9 宽银幕' },
]
const VIDEO_SECONDS = ['4', '5', '6', '8', '10', '12'].map((value) => ({ value, label: value + ' 秒' }))

export default function AIMediaPage() {
  const [pool, setPool] = useState<AIPoolStatus | null>(null)
  const [tab, setTab] = useState<'image' | 'video'>('image')
  const [imagePrompt, setImagePrompt] = useState('')
  const [imageSize, setImageSize] = useState('2K')
  const [imageRatio, setImageRatio] = useState('16:9')
  const [image, setImage] = useState<AIImageResult | null>(null)
  const [imageLoading, setImageLoading] = useState(false)
  const [imageRefs, setImageRefs] = useState<AIReference[]>([])
  const [videoMode, setVideoMode] = useState<'text' | 'keyframe' | 'reference'>('text')
  const [videoRefs, setVideoRefs] = useState<AIReference[]>([])
  const [firstFrame, setFirstFrame] = useState<AIReference | null>(null)
  const [lastFrame, setLastFrame] = useState<AIReference | null>(null)
  const [saveToLibrary, setSaveToLibrary] = useState(true)
  const [savingImage, setSavingImage] = useState(false)
  const [pickerOpen, setPickerOpen] = useState(false)
  const [pickerTarget, setPickerTarget] = useState<'image' | 'video'>('image')
  const [searchParams] = useSearchParams()
  const focusTaskId = searchParams.get('task')
  const [videoPrompt, setVideoPrompt] = useState('')
  const [videoSeconds, setVideoSeconds] = useState('5')
  const [videoAspect, setVideoAspect] = useState('16:9')
  const [videoTaskId, setVideoTaskId] = useState<string | null>(null)
  const [videoTask, setVideoTask] = useState<Task | null>(null)
  const [videoLoading, setVideoLoading] = useState(false)
  const timerRef = useRef(0)

  const loadPool = useCallback(async () => {
    try {
      setPool(await fetchAIPool())
    } catch {
      setPool(null)
    }
  }, [])

  const uploadRefs = async (
    files: FileList | null,
    setter: (items: AIReference[]) => void,
    current: AIReference[],
    max: number,
  ) => {
    if (!files || files.length === 0) return
    const next = [...current]
    for (const file of Array.from(files)) {
      if (next.length >= max) break
      try {
        next.push(await uploadAIReference(file))
      } catch (e) {
        toast.error('参考图上传失败：' + errorMessage(e))
      }
    }
    setter(next.slice(0, max))
  }

  const removeRef = (setter: (items: AIReference[]) => void, current: AIReference[], index: number) => {
    setter(current.filter((_, itemIndex) => itemIndex !== index))
  }

  const refFromAsset = (asset: AIImageAsset): AIReference => ({
    url: asset.public_url,
    data_uri: '',
    name: asset.filename,
    size: asset.size,
  })

  const openPicker = (target: 'image' | 'video') => {
    setPickerTarget(target)
    setPickerOpen(true)
  }

  const pickerSelectedUrls = pickerTarget === 'image'
    ? imageRefs.map((item) => item.url)
    : [firstFrame?.url, lastFrame?.url, ...videoRefs.map((item) => item.url)].filter(Boolean) as string[]

  const pickAsset = (asset: AIImageAsset) => {
    const ref = refFromAsset(asset)
    if (pickerTarget === 'image') {
      if (imageRefs.some((item) => item.url === ref.url)) return
      if (imageRefs.length >= 5) { toast.warning('参考图最多 5 张'); return }
      setImageRefs([...imageRefs, ref])
      return
    }
    if (videoMode === 'keyframe') {
      if (!firstFrame) setFirstFrame(ref)
      else if (!lastFrame) setLastFrame(ref)
      else toast.warning('首帧和尾帧都已有图片')
      return
    }
    if (videoMode === 'text') setVideoMode('reference')
    if (videoRefs.some((item) => item.url === ref.url)) return
    if (videoRefs.length >= 5) { toast.warning('参考图最多 5 张'); return }
    setVideoRefs([...videoRefs, ref])
  }

  const saveImageToLibrary = async () => {
    if (!image) return
    setSavingImage(true)
    try {
      await saveGeneratedAIImage({ name: image.name, title: imagePrompt.slice(0, 80), prompt: imagePrompt })
      toast.success('已保存到图片素材')
    } catch (e) {
      toast.error('保存失败：' + errorMessage(e))
    } finally {
      setSavingImage(false)
    }
  }

  useEffect(() => {
    if (!focusTaskId) return
    setTab('video')
    setVideoTaskId(focusTaskId)
  }, [focusTaskId])

  useEffect(() => { void loadPool() }, [loadPool])

  useEffect(() => {
    if (!videoTaskId) return
    let disposed = false
    const tick = async () => {
      try {
        const task = await fetchTask(videoTaskId)
        if (disposed) return
        setVideoTask(task)
        if (task.status === 'success' || task.status === 'failed' || task.status === 'partial') {
          setVideoLoading(false)
          if (task.status === 'success') toast.success('AI 视频已生成并加入素材库')
          else toast.error('AI 视频生成失败：' + (task.logs?.[task.logs.length - 1] || '未知错误'))
          void loadPool()
          return
        }
        timerRef.current = window.setTimeout(tick, 2500)
      } catch {
        timerRef.current = window.setTimeout(tick, 4000)
      }
    }
    void tick()
    return () => { disposed = true; window.clearTimeout(timerRef.current) }
  }, [videoTaskId, loadPool])

  const makeImage = async () => {
    if (!imagePrompt.trim()) { toast.warning('先写图片描述'); return }
    setImageLoading(true)
    setImage(null)
    try {
      const result = await generateAIImage({
        prompt: imagePrompt.trim(),
        size: imageSize,
        ratio: imageRatio,
        images: imageRefs.map((item) => item.url),
      })
      setImage(result)
      toast.success('图片已生成')
      void loadPool()
    } catch (e) {
      toast.error('图片生成失败：' + errorMessage(e))
    } finally {
      setImageLoading(false)
    }
  }

  const makeVideo = async () => {
    if (!videoPrompt.trim()) { toast.warning('先写视频描述'); return }
    setVideoLoading(true)
    setVideoTask(null)
    setVideoTaskId(null)
    try {
      const { task_id } = await generateAIVideo({
        prompt: videoPrompt.trim(),
        seconds: Number(videoSeconds),
        aspect_ratio: videoAspect,
        first_frame: videoMode === 'keyframe' ? firstFrame?.url : '',
        last_frame: videoMode === 'keyframe' ? lastFrame?.url : '',
        images: videoMode === 'reference' ? videoRefs.map((item) => item.url) : [],
        save_to_library: saveToLibrary,
      })
      setVideoTaskId(task_id)
      toast.success('视频任务已提交，生成需要几分钟')
    } catch (e) {
      setVideoLoading(false)
      toast.error('视频提交失败：' + errorMessage(e))
    }
  }

  const videoResult = (videoTask?.result ?? {}) as { video?: Video; video_url?: string; saved_to_library?: boolean; title?: string; duration?: number; size?: number }
  const generatedVideo = videoResult.video ?? null
  const generatedVideoUrl = generatedVideo?.file_url || (videoResult.saved_to_library === false ? videoResult.video_url : '')
  const imageClass = 'flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-[12px] ' + (tab === 'image' ? 'bg-surface text-ink-1 shadow-sm' : 'text-ink-3')
  const videoClass = 'flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-[12px] ' + (tab === 'video' ? 'bg-surface text-ink-1 shadow-sm' : 'text-ink-3')

  return (
    <div>
      <PageHeader
        title="AI 素材"
        desc="使用 Agnes 生成图片和视频，结果直接保存到本地素材库。"
        extra={pool ? <span className="rounded-full border border-line bg-fill px-3 py-1 text-[11px] text-ink-3">Key 池 {pool.available}/{pool.total} 可用</span> : null}
      />

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
        <Panel>
          <div className="mb-4 inline-flex rounded-xl border border-line-soft bg-fill p-1">
            <button type="button" className={imageClass} onClick={() => setTab('image')}><ImageSquare size={15} />图片</button>
            <button type="button" className={videoClass} onClick={() => setTab('video')}><VideoCamera size={15} />视频</button>
          </div>

          {tab === 'image' ? (
            <div className="space-y-4">
              <label className="block">
                <span className="mb-1.5 block text-xs text-ink-3">图片描述</span>
                <TextArea value={imagePrompt} onChange={setImagePrompt} rows={5} placeholder="例如：雨后霓虹街道，电影感，低机位，青橙色调，高细节" />
              </label>
              <div
                className="rounded-xl border border-line-soft bg-fill p-3"
                onDragOver={(event) => event.preventDefault()}
                onDrop={(event) => { event.preventDefault(); void uploadRefs(event.dataTransfer.files, setImageRefs, imageRefs, 5) }}
              >
                <div className="mb-2 flex items-center justify-between gap-2">
                  <span className="text-[11px] font-medium text-ink-2">参考图（可拖拽，最多 5 张）</span>
                  <Button size="sm" variant="ghost" onClick={() => openPicker('image')}>从图片素材选择</Button>
                </div>
                <input
                  type="file"
                  accept="image/*"
                  multiple
                  className="block w-full text-[11px] text-ink-3 file:mr-3 file:rounded-lg file:border-0 file:bg-accent-soft file:px-3 file:py-1.5 file:text-[11px] file:text-accent"
                  onChange={(event) => { void uploadRefs(event.target.files, setImageRefs, imageRefs, 5); event.currentTarget.value = '' }}
                />
                {imageRefs.length > 0 && (
                  <div className="mt-2 flex flex-wrap gap-2">
                    {imageRefs.map((item, index) => (
                      <div key={item.name} className="group relative h-14 w-14 overflow-hidden rounded-lg border border-line-soft">
                        <img src={item.url} alt="参考图" className="h-full w-full object-cover" />
                        <button type="button" onClick={() => removeRef(setImageRefs, imageRefs, index)} className="absolute right-0 top-0 hidden h-4 w-4 items-center justify-center rounded-bl bg-black/70 text-[10px] text-white group-hover:flex">×</button>
                      </div>
                    ))}
                  </div>
                )}
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                <label className="block">
                  <span className="mb-1.5 block text-xs text-ink-3">分辨率</span>
                  <Select value={imageSize} onChange={setImageSize} options={IMAGE_SIZES} />
                </label>
                <label className="block">
                  <span className="mb-1.5 block text-xs text-ink-3">画幅</span>
                  <Select value={imageRatio} onChange={setImageRatio} options={RATIOS} />
                </label>
              </div>
              <Button variant="primary" icon={<Sparkle size={15} />} loading={imageLoading} onClick={() => void makeImage()}>生成图片</Button>
              {image && (
                <div className="rounded-xl border border-line-soft bg-fill p-3">
                  <img src={image.url} alt="AI 生成图片" className="max-h-[52vh] w-full rounded-lg object-contain" />
                  <div className="mt-2 flex flex-wrap items-center justify-between gap-2 text-[11px] text-ink-4">
                    <span>{image.model} · {formatSize(image.size)}</span>
                    <div className="flex items-center gap-2">
                      <Button size="sm" variant="ghost" loading={savingImage} onClick={() => void saveImageToLibrary()}>保存到图片素材</Button>
                      <a href={image.url} download className="inline-flex items-center gap-1 text-accent"><DownloadSimple size={14} />下载图片</a>
                    </div>
                  </div>
                </div>
              )}
            </div>
          ) : (
            <div className="space-y-4">
              <label className="block">
                <span className="mb-1.5 block text-xs text-ink-3">视频描述</span>
                <TextArea value={videoPrompt} onChange={setVideoPrompt} rows={5} placeholder="例如：镜头缓慢推进，红色苹果在木桌上旋转，柔和电影光，背景虚化" />
              </label>
              <div className="grid gap-3 sm:grid-cols-2">
                <label className="block">
                  <span className="mb-1.5 block text-xs text-ink-3">生成模式</span>
                  <Select
                    value={videoMode}
                    onChange={(value) => setVideoMode(value === 'keyframe' ? 'keyframe' : value === 'reference' ? 'reference' : 'text')}
                    options={[
                      { value: 'text', label: '文生视频' },
                      { value: 'keyframe', label: '首帧/尾帧控制' },
                      { value: 'reference', label: '图片参考（最多 5 张）' },
                    ]}
                  />
                </label>
                <label className="block">
                  <span className="mb-1.5 block text-xs text-ink-3">时长</span>
                  <Select value={videoSeconds} onChange={setVideoSeconds} options={VIDEO_SECONDS} />
                </label>
              </div>
              {videoMode === 'keyframe' && (
                <div className="grid gap-3 sm:grid-cols-2">
                  {[{ label: '首帧', value: firstFrame, set: setFirstFrame, key: 'first' }, { label: '尾帧', value: lastFrame, set: setLastFrame, key: 'last' }].map((item) => (
                    <div key={item.key} className="rounded-xl border border-line-soft bg-fill p-3">
                      <div className="mb-1.5 text-[11px] text-ink-2">{item.label}（可选）</div>
                      <input type="file" accept="image/*" className="block w-full text-[11px] text-ink-3 file:mr-2 file:rounded-lg file:border-0 file:bg-accent-soft file:px-2 file:py-1 file:text-[10px] file:text-accent" onChange={(event) => { void uploadRefs(event.target.files, (items) => item.set(items[0] ?? null), item.value ? [item.value] : [], 1); event.currentTarget.value = '' }} />
                      {item.value && <div className="mt-2 flex items-center gap-2"><img src={item.value.url} alt={item.label} className="h-12 w-12 rounded object-cover" /><button type="button" className="text-[10px] text-danger" onClick={() => item.set(null)}>移除</button></div>}
                    </div>
                  ))}
                  <div className="sm:col-span-2">
                    <Button size="sm" variant="ghost" onClick={() => openPicker('video')}>从图片素材选择首帧/尾帧</Button>
                  </div>
                </div>
              )}
              {videoMode === 'reference' && (
                <div
                  className="rounded-xl border border-line-soft bg-fill p-3"
                  onDragOver={(event) => event.preventDefault()}
                  onDrop={(event) => { event.preventDefault(); void uploadRefs(event.dataTransfer.files, setVideoRefs, videoRefs, 5) }}
                >
                  <div className="mb-2 flex items-center justify-between gap-2">
                    <span className="text-[11px] text-ink-2">参考图（可拖拽，最多 5 张）</span>
                    <Button size="sm" variant="ghost" onClick={() => openPicker('video')}>从图片素材选择</Button>
                  </div>
                  <input type="file" accept="image/*" multiple className="block w-full text-[11px] text-ink-3 file:mr-3 file:rounded-lg file:border-0 file:bg-accent-soft file:px-3 file:py-1.5 file:text-[11px] file:text-accent" onChange={(event) => { void uploadRefs(event.target.files, setVideoRefs, videoRefs, 5); event.currentTarget.value = '' }} />
                  {videoRefs.length > 0 && <div className="mt-2 flex flex-wrap gap-2">{videoRefs.map((item, index) => <div key={item.name} className="group relative h-14 w-14 overflow-hidden rounded-lg border border-line-soft"><img src={item.url} alt="参考图" className="h-full w-full object-cover" /><button type="button" onClick={() => removeRef(setVideoRefs, videoRefs, index)} className="absolute right-0 top-0 hidden h-4 w-4 items-center justify-center rounded-bl bg-black/70 text-[10px] text-white group-hover:flex">×</button></div>)}</div>}
                </div>
              )}
              <div className="grid gap-3 sm:grid-cols-2">
                <label className="block">
                  <span className="mb-1.5 block text-xs text-ink-3">画幅</span>
                  <Select value={videoAspect} onChange={setVideoAspect} options={RATIOS.filter((item) => item.value !== '21:9')} />
                </label>
                <div className="flex items-center justify-between gap-2 rounded-xl border border-line-soft bg-fill px-3 py-2">
                  <div><div className="text-[11px] text-ink-2">加入视频素材库</div><div className="text-[10px] text-ink-4">关闭则只生成下载链接</div></div>
                  <Switch checked={saveToLibrary} onChange={setSaveToLibrary} label="加入视频素材库" />
                </div>
              </div>

              <Alert type="info">视频生成是异步任务，提交后可以离开页面，完成时会自动进入素材库。</Alert>
              <Button variant="primary" icon={<Sparkle size={15} />} loading={videoLoading} onClick={() => void makeVideo()}>生成视频</Button>
              {videoTask && (
                <div className="rounded-xl border border-line-soft bg-fill p-3">
                  <div className="flex items-center gap-3">
                    <Progress percent={(videoTask.progress ?? 0) / 100} className="flex-1" status={videoTask.status === 'failed' ? 'error' : 'normal'} />
                    <span className="tnum text-[11px] text-ink-4">{Math.round(videoTask.progress ?? 0)}%</span>
                  </div>
                  <p className="mt-1 text-[11px] text-ink-4">
                    {videoTask.status === 'success'
                      ? (videoResult.saved_to_library === false ? '生成完成 · 未加入素材库' : '生成完成 · 已加入素材库')
                      : videoTask.status === 'failed'
                        ? '生成失败'
                        : (videoTask.logs?.[videoTask.logs.length - 1] || '排队中')}
                  </p>
                  {generatedVideoUrl && (
                    <div className="mt-3">
                      <video src={generatedVideoUrl} controls className="max-h-[52vh] w-full rounded-lg bg-black" />
                      <div className="mt-2 flex items-center justify-between gap-2 text-[11px] text-ink-4">
                        <span>{generatedVideo?.title || videoResult.title || 'AI 生成视频'}{videoResult.saved_to_library === false ? ' · 未加入素材库' : ' · 已加入素材库'}</span>
                        <a href={generatedVideo ? '/api/videos/' + generatedVideo.id + '/download' : generatedVideoUrl} download className="inline-flex items-center gap-1 text-accent"><DownloadSimple size={14} />下载视频</a>
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
        </Panel>

        <Panel title="AI Key 池" extra={<span className="text-[11px] text-ink-4">自动轮换</span>}>
          {!pool ? <p className="text-[12px] text-ink-4">读取失败</p> : (
            <div className="space-y-2">
              {pool.keys.map((key) => (
                <div key={key.hint} className="flex items-center gap-2 rounded-lg border border-line-soft bg-fill px-2.5 py-2">
                  <span className={key.available ? 'status-dot' : 'status-dot is-off'} />
                  <div className="min-w-0 flex-1">
                    <div className="tnum truncate text-[11px] text-ink-2">{key.hint}</div>
                    <div className="text-[10px] text-ink-4">
                      {key.available ? '可用' : '冷却 ' + key.cooldown_seconds + 's'} · 成功 {key.successes} / 失败 {key.failures}
                    </div>
                  </div>
                </div>
              ))}
              <p className="pt-1 text-[10px] leading-4 text-ink-4">遇到 429/401 会自动切换下一个 Key；失效 Key 会进入冷却。</p>
            </div>
          )}
        </Panel>
      </div>
      <ImageLibraryPicker
        open={pickerOpen}
        onClose={() => setPickerOpen(false)}
        selectedUrls={pickerSelectedUrls}
        max={5}
        onPick={pickAsset}
      />
    </div>
  )
}
