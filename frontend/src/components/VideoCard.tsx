import type { ReactNode } from 'react'
import {
  DownloadSimple,
  MagicWand,
  NotePencil,
  PaperPlaneTilt,
  Play,
  Scissors,
  Trash,
} from '@phosphor-icons/react'
import { formatDuration, formatSize, platformLabel, type Video } from '../api'
import { Checkbox } from '../ui'

interface Props {
  video: Video
  /** 多选模式下是否选中 */
  selected?: boolean
  /** 是否处于多选模式 */
  selecting?: boolean
  onToggleSelect?: (video: Video) => void
  onPlay: (video: Video) => void
  onPublish: (video: Video) => void
  onTrim: (video: Video) => void
  onAiEdit: (video: Video) => void
  onRename: (video: Video) => void
  onDownload: (video: Video) => void
  onDelete: (video: Video) => void
}

interface CardAction {
  key: string
  tip: string
  icon: ReactNode
  onClick: () => void
  danger?: boolean
}

/** 无封面占位渐变的平台主色 */
const PLATFORM_COLORS: Record<string, string> = {
  douyin: '#fe2c55',
  xiaohongshu: '#ff2442',
  kuaishou: '#ff7e00',
  bilibili: '#00a1d6',
}

function platformColor(platform: string | null): string {
  let key = platform ?? 'local'
  // 直播录制来源沿用原平台主色
  if (key.startsWith('live_')) key = key.slice(5)
  return PLATFORM_COLORS[key] ?? '#536dfe'
}

export default function VideoCard({
  video,
  selected = false,
  selecting = false,
  onToggleSelect,
  onPlay,
  onPublish,
  onTrim,
  onAiEdit,
  onRename,
  onDownload,
  onDelete,
}: Props) {
  const actions: CardAction[] = [
    { key: 'play', tip: '播放', icon: <Play size={15} />, onClick: () => onPlay(video) },
    { key: 'publish', tip: '发布', icon: <PaperPlaneTilt size={15} />, onClick: () => onPublish(video) },
    { key: 'trim', tip: '截取', icon: <Scissors size={15} />, onClick: () => onTrim(video) },
    { key: 'ai', tip: '智能剪辑', icon: <MagicWand size={15} />, onClick: () => onAiEdit(video) },
    { key: 'rename', tip: '重命名', icon: <NotePencil size={15} />, onClick: () => onRename(video) },
    { key: 'download', tip: '下载', icon: <DownloadSimple size={15} />, onClick: () => onDownload(video) },
    { key: 'delete', tip: '删除', icon: <Trash size={15} />, onClick: () => onDelete(video), danger: true },
  ]

  return (
    <div
      role="button"
      tabIndex={0}
      aria-label={selecting ? `选择 ${video.title}` : `播放 ${video.title}`}
      className={`poster-card ${selected ? 'selected' : ''} ${selecting ? 'selecting' : ''}`}
      onClick={selecting ? () => onToggleSelect?.(video) : () => onPlay(video)}
      onKeyDown={(e) => {
        if (e.target !== e.currentTarget || (e.key !== 'Enter' && e.key !== ' ')) return
        e.preventDefault()
        if (selecting) onToggleSelect?.(video)
        else onPlay(video)
      }}
    >
      {/* 封面 / 无封面占位 */}
      {video.cover_url ? (
        <img src={video.cover_url} alt={video.title} loading="lazy" />
      ) : (
        <div
          className="poster-fallback"
          style={{
            background: `linear-gradient(160deg, ${platformColor(video.source_platform)}, #1c2027)`,
            fontSize: 48,
          }}
        >
          {(video.title[0] ?? '影').toUpperCase()}
        </div>
      )}

      {/* 操作列：多选时仅保留勾选框 */}
      <div className="poster-actions" onClick={(e) => e.stopPropagation()}>
        {selecting ? (
          <span className="poster-action-btn">
            <Checkbox checked={selected} onChange={() => onToggleSelect?.(video)} />
          </span>
        ) : (
          actions.map((a) => (
            <button
              key={a.key}
              type="button"
              aria-label={a.tip}
              data-tip={a.tip}
              onClick={a.onClick}
              className={`poster-action-btn ${a.danger ? 'danger' : ''}`}
            >
              {a.icon}
            </button>
          ))
        )}
      </div>

      {/* 底部信息层 */}
      <div className="poster-meta">
        <div className="line-clamp-2 text-sm font-medium leading-5" title={video.title}>
          {video.title}
        </div>
        <div className="tnum mt-1 text-xs text-white/70">
          {platformLabel(video.source_platform)} · {formatSize(video.size)} ·{' '}
          {formatDuration(video.duration)}
        </div>
      </div>
    </div>
  )
}
