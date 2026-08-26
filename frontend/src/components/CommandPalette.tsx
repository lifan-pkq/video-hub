import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { DownloadSimple, MagnifyingGlass, VideoCamera, type Icon } from '@phosphor-icons/react'
import { fetchVideos, platformLabel, type Video } from '../api'
import { NAV_ITEMS } from '../nav'

interface CmdItem {
  id: string
  icon: Icon
  title: string
  subtitle?: string
  keywords: string
  action: () => void
}

interface Props {
  open: boolean
  onClose: () => void
}

/** Cmd/Ctrl+K 命令面板：页面跳转 + 视频搜索 + 快捷动作 */
export default function CommandPalette({ open, onClose }: Props) {
  const navigate = useNavigate()
  const [query, setQuery] = useState('')
  const [videos, setVideos] = useState<Video[]>([])
  const [active, setActive] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)
  const listRef = useRef<HTMLDivElement>(null)

  // 打开时：聚焦 + 拉取视频列表（用于搜索）
  useEffect(() => {
    if (!open) return
    setQuery('')
    setActive(0)
    window.setTimeout(() => inputRef.current?.focus(), 30)
    fetchVideos()
      .then(setVideos)
      .catch(() => {})
  }, [open])

  const items = useMemo<CmdItem[]>(() => {
    const go = (to: string) => () => {
      navigate(to)
      onClose()
    }
    const navItems: CmdItem[] = NAV_ITEMS.map((n) => ({
      id: `nav:${n.to}`,
      icon: n.icon,
      title: n.label,
      subtitle: '页面',
      keywords: `${n.label} ${n.keywords}`,
      action: go(n.to),
    }))
    const videoItems: CmdItem[] = videos.map((v) => ({
      id: `video:${v.id}`,
      icon: VideoCamera,
      title: v.title,
      subtitle: `视频 · ${platformLabel(v.source_platform)}`,
      keywords: v.title,
      action: () => {
        navigate(`/videos?q=${encodeURIComponent(v.title)}`)
        onClose()
      },
    }))
    const q = query.trim().toLowerCase()
    if (!q) return navItems
    return [...navItems, ...videoItems]
      .filter((i) => i.title.toLowerCase().includes(q) || i.keywords.toLowerCase().includes(q))
      .slice(0, 12)
  }, [query, videos, navigate, onClose])

  useEffect(() => setActive(0), [query])

  // 键盘导航
  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault()
      setActive((i) => (items.length === 0 ? 0 : (i + (e.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      items[active]?.action()
    }
  }

  // active 项滚进可视区
  useEffect(() => {
    listRef.current
      ?.querySelector(`[data-idx="${active}"]`)
      ?.scrollIntoView({ block: 'nearest' })
  }, [active])

  if (!open) return null

  return (
    <div className="fixed inset-0 z-[100] flex items-start justify-center p-4 pt-[14vh]" onClick={onClose}>
      <div className="fade-in absolute inset-0" style={{ backgroundColor: 'var(--mask)' }} />
      <div
        className="modal-in relative w-full max-w-xl overflow-hidden rounded-2xl border border-line bg-card shadow-pop"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={onKeyDown}
      >
        <div className="flex items-center gap-2.5 border-b border-line-soft px-4">
          <MagnifyingGlass size={16} className="shrink-0 text-ink-3" />
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="搜索页面、视频，或输入操作…"
            className="h-12 w-full bg-transparent text-sm text-ink-1 outline-none placeholder:text-ink-4"
          />
          <kbd className="shrink-0 rounded-md border border-line bg-fill px-1.5 py-0.5 text-[10px] text-ink-4">ESC</kbd>
        </div>
        <div ref={listRef} className="max-h-[46vh] overflow-y-auto p-2">
          {items.length === 0 ? (
            <div className="px-3 py-10 text-center text-[13px] text-ink-4">没有匹配的结果</div>
          ) : (
            items.map((item, i) => {
              const ItemIcon = item.icon
              return (
                <button
                  key={item.id}
                  type="button"
                  data-idx={i}
                  onMouseEnter={() => setActive(i)}
                  onClick={item.action}
                  className={`cmdk-item ${i === active ? 'active' : ''}`}
                >
                  <ItemIcon size={16} className="cmdk-icon shrink-0 text-ink-4" />
                  <span className="min-w-0 flex-1 truncate">{item.title}</span>
                  {item.subtitle && <span className="shrink-0 text-[11px] text-ink-4">{item.subtitle}</span>}
                </button>
              )
            })
          )}
        </div>
        <div className="flex items-center gap-4 border-t border-line-soft px-4 py-2 text-[11px] text-ink-4">
          <span>↑↓ 移动</span>
          <span>↵ 打开</span>
          <span className="ml-auto flex items-center gap-1">
            <DownloadSimple size={11} /> 输入视频标题可直接跳到视频库
          </span>
        </div>
      </div>
    </div>
  )
}
