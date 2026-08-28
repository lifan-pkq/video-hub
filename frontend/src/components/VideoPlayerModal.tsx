import { useState } from 'react'
import { WarningCircle } from '@phosphor-icons/react'
import { Modal } from '../ui'

interface Props {
  /** 为 null 时弹窗关闭 */
  videoId: number | null
  title?: string
  onClose: () => void
}

/**
 * 视频播放器弹窗：后端 /api/videos/:id/file 支持 Range 流式，直接 <video> 播放。
 * 加载失败显示错误态。
 */
export default function VideoPlayerModal({ videoId, title, onClose }: Props) {
  const [failedId, setFailedId] = useState<number | null>(null)
  const open = videoId !== null
  const failed = open && failedId === videoId

  return (
    <Modal open={open} onClose={onClose} title={title ?? '视频预览'} width={880}>
      {open &&
        (failed ? (
          <div className="flex h-[240px] flex-col items-center justify-center gap-2 text-center">
            <WarningCircle size={28} className="text-danger" />
            <div className="text-[13px] font-medium text-ink-1">视频加载失败</div>
            <div className="text-[12px] text-ink-3">文件可能已被移动或删除，请稍后重试</div>
          </div>
        ) : (
          <video
            key={videoId}
            src={`/api/videos/${videoId}/file`}
            controls
            autoPlay
            playsInline
            onError={() => setFailedId(videoId)}
            className="max-h-[70vh] w-full rounded-xl bg-black"
          />
        ))}
    </Modal>
  )
}
