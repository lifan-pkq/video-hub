import type { ReactNode } from 'react'
import {
  DownloadSimple,
  NotePencil,
  PaperPlaneTilt,
  Play,
  Scissors,
  Trash,
} from '@phosphor-icons/react'
import { formatDuration, formatRelative, formatSize, platformLabel, type Video } from '../api'
import { Checkbox } from '../ui'

interface Props {
  video: Video
  /** 多选模式下是否选中 */
  selected?: boolean
  /** 是否处于多选模式 */
  selecting?: boolean
  /** 移动端「点击展开」操作条是否展开（触屏无 hover，由父级状态驱动） */
  actionsOpen?: boolean
  onToggleSelect?: (video: Video) => void
  /** 点击卡片主体：桌面端直接播放，移动端展开操作条后再次点击播放 */
  onCardClick: (video: Video) => void
  onEdit: (video: Video) => void
  onPublish: (video: Video) => void
  onRename: (video: Video) => void
  onDelete: (video: Video) => void
}

interface CardAction {
  key: string
  tip: string
  icon: ReactNode
  onClick?: () => void
  /** 直接链（如下载原片） */
  href?: string
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
  if (key.startsWith('live_')) key = key.slice(5)
  return PLATFORM_COLORS[key] ?? '#536dfe'
}

const ACTION_BTN_CLASS = 'poster-action-btn'

/**
 * 素材库视频卡片：3:4 海报卡（沿用 .poster-card 体系）。
 * 点击卡片播放在线预览；悬浮（桌面）/点击展开（移动）显示快捷操作条。
 */
export default function LibraryVideoCard({
  video,
  selected = false,
  selecting = false,
  actionsOpen = false,
  onToggleSelect,
  onCardClick,
  onEdit,
  onPublish,
  onRename,
  onDelete,
}: Props) {
  const actions: CardAction[] = [
    { key: 'play', tip: '播放', icon: <Play size={15} />, onClick: () => onCardClick(video) },
    { key: 'edit', tip: '去剪辑', icon: <Scissors size={15} />, onClick: () => onEdit(video) },
    { key: 'publish', tip: '去发布', icon: <PaperPlaneTilt size={15} />, onClick: () => onPublish(video) },
    {
      key: 'download',
      tip: '下载原片',
      icon: <DownloadSimple size={15} />,
      href: `/api/videos/${video.id}/file`,
    },
    { key: 'rename', tip: '重命名', icon: <NotePencil size={15} />, onClick: () => onRename(video) },
    { key: 'delete', tip: '删除', icon: <Trash size={15} />, onClick: () => onDelete(video), danger: true },
  ]

  return (
    <div
      className={`poster-card ${selected ? 'selected' : ''} ${selecting ? 'selecting' : ''}`}
      onClick={() => (selecting ? onToggleSelect?.(video) : onCardClick(video))}
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

      {/* 快捷操作条：桌面 hover 显现；移动端由 actionsOpen 强制展开；多选时仅勾选框 */}
      <div
        className="poster-actions"
        style={actionsOpen && !selecting ? { opacity: 1, transform: 'translateX(0)' } : undefined}
        onClick={(e) => e.stopPropagation()}
      >
        {selecting ? (
          <span className={ACTION_BTN_CLASS}>
            <Checkbox checked={selected} onChange={() => onToggleSelect?.(video)} />
          </span>
        ) : (
          actions.map((a) =>
            a.href ? (
              <a
                key={a.key}
                href={a.href}
                download={`${video.title}.mp4`}
                aria-label={a.tip}
                data-tip={a.tip}
                className={ACTION_BTN_CLASS}
              >
                {a.icon}
              </a>
            ) : (
              <button
                key={a.key}
                type="button"
                aria-label={a.tip}
                data-tip={a.tip}
                onClick={a.onClick}
                className={`${ACTION_BTN_CLASS} ${a.danger ? 'danger' : ''}`}
              >
                {a.icon}
              </button>
            ),
          )
        )}
      </div>

      {/* 底部信息层 */}
      <div className="poster-meta">
        <div className="line-clamp-2 text-sm font-medium leading-5" title={video.title}>
          {video.title}
        </div>
        <div className="tnum mt-1 text-xs text-white/70">
          {formatDuration(video.duration)} · {formatSize(video.size)}
        </div>
        <div className="tnum mt-0.5 text-[11px] text-white/50">
          {platformLabel(video.source_platform)} · {formatRelative(video.created_at)}
        </div>
      </div>
    </div>
  )
}
