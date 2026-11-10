import { useEffect, useRef, useState } from 'react'
import {
  ApiError,
  errorMessage,
  fetchAIImages,
  fetchTask,
  fetchVideos,
  generateAIImage,
  generateAIVideo,
  uploadAIImage,
  type AIImageAsset,
  type Video,
} from '../../api'
import { Alert, Button, Modal, TextArea } from '../../ui'

export type PickedImage = {
  imageId: number
  imageName: string
  imageUrl: string
  title: string
  overlay: boolean
}

export type PickedVideo = {
  videoId: number
  videoName: string
  title: string
  fileUrl: string
  srcDuration: number
}

export function InsertPicker({
  open,
  onClose,
  videos,
  onPickImage,
  onPickVideo,
  initialTab = 'image',
}: {
  open: boolean
  onClose: () => void
  videos: Video[]
  onPickImage: (picked: PickedImage) => void
  onPickVideo: (picked: PickedVideo) => void
  initialTab?: 'image' | 'video'
}) {
  const [tab, setTab] = useState<'image' | 'video'>(initialTab)
  const [overlay, setOverlay] = useState(true)
  const [images, setImages] = useState<AIImageAsset[]>([])
  const [libraryError, setLibraryError] = useState('')
  const [prompt, setPrompt] = useState('')
  const [seconds, setSeconds] = useState(5)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [progress, setProgress] = useState('')
  const fileRef = useRef<HTMLInputElement>(null)
  const pollRef = useRef(0)

  useEffect(() => {
    if (!open) return
    setTab(initialTab)
    setError('')
    setProgress('')
    fetchAIImages()
      .then((list) => {
        setImages(list)
        setLibraryError('')
      })
      .catch((err: unknown) => setLibraryError(errorMessage(err)))
    return () => window.clearTimeout(pollRef.current)
  }, [open, initialTab])

  function fail(err: unknown) {
    if (err instanceof ApiError) setError(err.action ? `${err.message} ${err.action}` : err.message)
    else setError(errorMessage(err))
  }

  async function pickGeneratedImage(name: string, url: string, title: string) {
    onPickImage({
      imageId: 0,
      imageName: name,
      imageUrl: url,
      title: title.slice(0, 40) || 'AI 图片',
      overlay,
    })
    onClose()
  }

  async function onUpload(file?: File) {
    if (!file) return
    setBusy(true)
    setError('')
    try {
      const asset = await uploadAIImage(file, file.name)
      setImages((list) => [asset, ...list])
      onPickImage({
        imageId: asset.id,
        imageName: '',
        imageUrl: asset.url,
        title: asset.title || '图片',
        overlay,
      })
      onClose()
    } catch (err) {
      fail(err)
    } finally {
      setBusy(false)
    }
  }

  async function generateImage() {
    const text = prompt.trim()
    if (!text) {
      setError('先写图片描述。')
      return
    }
    setBusy(true)
    setError('')
    try {
      const result = await generateAIImage({ prompt: text, size: '2K', ratio: '16:9' })
      if (!result?.name || !result.url) {
        setError('图像接口没有返回图片。检查 AI Key 后再试。')
        return
      }
      await pickGeneratedImage(result.name, result.url, text)
    } catch (err) {
      fail(err)
    } finally {
      setBusy(false)
    }
  }

  async function settleVideoTask(taskId: string) {
    const tick = async () => {
      try {
        const task = await fetchTask(taskId)
        const status = task.status
        setProgress(`${task.title || 'AI 视频'} · ${Math.round(task.progress || 0)}%`)
        if (status === 'pending' || status === 'running') {
          pollRef.current = window.setTimeout(() => void tick(), 2000)
          return
        }
        setBusy(false)
        if (status === 'failed' || status === 'cancelled') {
          const message = task.error?.message || task.logs[task.logs.length - 1] || '视频没有生成'
          const action = task.error?.action || ''
          setError(action ? `${message} ${action}` : message)
          return
        }
        await finishVideo(task)
      } catch (err) {
        setBusy(false)
        fail(err)
      }
    }
    void tick()
  }

  async function finishVideo(task: Awaited<ReturnType<typeof fetchTask>>) {
    const result = (task.result || {}) as {
      video?: Video
      video_url?: string
      title?: string
      duration?: number
    }
    const videoId = Number(result.video?.id || 0)
    if (videoId) {
      const list = await fetchVideos()
      const found = list.find((item) => item.id === videoId)
      if (!found) {
        setError('视频已生成，但素材库里还找不到它。到素材库刷新后再插入。')
        return
      }
      onPickVideo({
        videoId: found.id,
        videoName: '',
        title: found.title,
        fileUrl: found.file_url,
        srcDuration: Number(found.duration || 0),
      })
      onClose()
      return
    }
    const videoUrl = result.video_url || ''
    const name = videoUrl.split('/').pop() || ''
    if (!name.startsWith('ai_')) {
      setError('视频生成结束了，但没有可插入的文件。')
      return
    }
    onPickVideo({
      videoId: 0,
      videoName: name,
      title: result.title || 'AI 视频',
      fileUrl: videoUrl,
      srcDuration: Number(result.duration || 5),
    })
    onClose()
  }

  async function generateVideo() {
    const text = prompt.trim()
    if (!text) {
      setError('先写视频描述。')
      return
    }
    setBusy(true)
    setError('')
    setProgress('正在提交…')
    try {
      const created = await generateAIVideo({
        prompt: text,
        seconds,
        aspect_ratio: '16:9',
        save_to_library: true,
      })
      if (!created.task_id) {
        setBusy(false)
        setError('视频接口没有返回任务编号。')
        return
      }
      await settleVideoTask(created.task_id)
    } catch (err) {
      setBusy(false)
      fail(err)
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      width={560}
      title="插入画面"
      footer={
        <Button variant="secondary" onClick={onClose}>
          关闭
        </Button>
      }
    >
      <div className="space-y-3">
        <div className="flex gap-2">
          <Button size="sm" variant={tab === 'image' ? 'primary' : 'secondary'} onClick={() => setTab('image')}>
            加图片
          </Button>
          <Button size="sm" variant={tab === 'video' ? 'primary' : 'secondary'} onClick={() => setTab('video')}>
            加视频
          </Button>
        </div>
        {error ? <Alert type="error">{error}</Alert> : null}
        {libraryError ? <Alert type="warning">{libraryError}</Alert> : null}

        {tab === 'image' ? (
          <>
            <div className="flex flex-wrap gap-2">
              <Button size="sm" variant={overlay ? 'primary' : 'secondary'} onClick={() => setOverlay(true)}>
                叠在画面上
              </Button>
              <Button size="sm" variant={overlay ? 'secondary' : 'primary'} onClick={() => setOverlay(false)}>
                全屏静帧
              </Button>
            </div>
            <p className="text-xs text-ink-3">叠图盖在上一段画面角落。全屏静帧单独占一段时间线。选已有图片，或上传，或 AI 生成。</p>
            <div className="grid max-h-40 grid-cols-2 gap-2 overflow-auto sm:grid-cols-3">
              {images.length === 0 ? <p className="col-span-full text-xs text-ink-4">图片库是空的。</p> : null}
              {images.map((image) => (
                <button
                  key={image.id}
                  type="button"
                  className="truncate rounded-lg border border-line px-2 py-2 text-left text-xs text-ink-1 hover:bg-fill"
                  onClick={() => {
                    onPickImage({
                      imageId: image.id,
                      imageName: '',
                      imageUrl: image.url,
                      title: image.title || '图片',
                      overlay,
                    })
                    onClose()
                  }}
                >
                  {image.title || `图片 ${image.id}`}
                </button>
              ))}
            </div>
            <div className="flex flex-wrap gap-2">
              <Button size="sm" variant="secondary" disabled={busy} onClick={() => fileRef.current?.click()}>
                上传图片
              </Button>
              <input
                ref={fileRef}
                type="file"
                accept="image/png,image/jpeg,image/webp"
                className="hidden"
                onChange={(event) => void onUpload(event.target.files?.[0])}
              />
            </div>
          </>
        ) : (
          <>
            <p className="text-xs text-ink-3">插入一段素材库视频，或让已配置的视频接口生成 B-roll。生成失败时时间线保持原样，并显示原因。</p>
            <div className="max-h-36 space-y-1 overflow-auto">
              {videos.length === 0 ? <p className="text-xs text-ink-4">素材库没有视频。</p> : null}
              {videos.map((video) => (
                <button
                  key={video.id}
                  type="button"
                  className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-xs hover:bg-fill"
                  onClick={() => {
                    onPickVideo({
                      videoId: video.id,
                      videoName: '',
                      title: video.title,
                      fileUrl: video.file_url,
                      srcDuration: Number(video.duration || 0),
                    })
                    onClose()
                  }}
                >
                  <span className="min-w-0 flex-1 truncate text-ink-1">{video.title}</span>
                </button>
              ))}
            </div>
            <label className="block text-xs text-ink-3">
              时长（秒）
              <input
                className="mt-1 h-9 w-24 rounded-[10px] border border-line bg-fill px-2 text-[13px] text-ink-1"
                type="number"
                min={4}
                max={12}
                value={seconds}
                onChange={(event) => setSeconds(Math.min(12, Math.max(4, Number(event.target.value) || 5)))}
              />
            </label>
          </>
        )}

        <label className="block text-xs text-ink-3">
          AI 生成描述
          <TextArea
            className="mt-1"
            rows={3}
            value={prompt}
            onChange={setPrompt}
            placeholder={tab === 'image' ? '一张横屏静帧，主体和光线写清楚' : '一段 5 秒空镜，主体和运动写清楚'}
          />
        </label>
        <Button variant="primary" disabled={busy} loading={busy} onClick={() => void (tab === 'image' ? generateImage() : generateVideo())}>
          AI 生成
        </Button>
        {progress ? <p className="text-xs text-ink-3">{progress}</p> : null}
      </div>
    </Modal>
  )
}
