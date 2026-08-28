import { useState } from 'react'
import { MagnifyingGlass, Play, VideoCamera } from '@phosphor-icons/react'
import { EmptyState, Input, PlatformIcon } from '../ui'
import { formatDuration, type Video } from '../api'
import VideoPlayerModal from './VideoPlayerModal'

interface Props {
  videos: Video[]
  selectedId: number | null
  onSelect: (video: Video) => void
}

/** 第①步：选择视频。网格单选高亮 + 关键词搜索 + 在线预览弹窗 */
export default function PublishVideoStep({ videos, selectedId, onSelect }: Props) {
  const [query, setQuery] = useState('')
  const [playId, setPlayId] = useState<number | null>(null)

  const keyword = query.trim().toLowerCase()
  const filtered = keyword ? videos.filter((v) => v.title.toLowerCase().includes(keyword)) : videos
  const playVideo = videos.find((v) => v.id === playId) ?? null

  return (
    <div>
      <div className="mb-3 flex items-center gap-2">
        <Input
          allowClear
          prefix={<MagnifyingGlass size={14} />}
          placeholder="搜索视频标题"
          value={query}
          onChange={setQuery}
          className="w-full sm:w-[240px]"
        />
        <span className="tnum shrink-0 text-xs text-ink-3">
          {selectedId ? '已选 1 个' : `共 ${videos.length} 个`}
        </span>
      </div>

      {videos.length === 0 ? (
        <EmptyState
          icon={<VideoCamera size={28} />}
          title="暂无视频"
          desc="请先到素材库上传或导入视频，再回来选择发布"
        />
      ) : filtered.length === 0 ? (
        <EmptyState icon={<MagnifyingGlass size={28} />} title="没有匹配的视频" desc="换个关键词试试" />
      ) : (
        <div className="grid max-h-[560px] grid-cols-2 gap-3 overflow-y-auto pr-1 sm:grid-cols-3 lg:grid-cols-4">
          {filtered.map((v, i) => {
            const active = selectedId === v.id
            return (
              <div
                key={v.id}
                onClick={() => onSelect(v)}
                className={`enter-item cursor-pointer overflow-hidden rounded-lg ring-2 transition-all ${
                  active ? 'ring-accent' : 'ring-transparent hover:ring-line'
                }`}
                style={{ animationDelay: `${Math.min(i * 40, 400)}ms` }}
              >
                <div className="relative aspect-video bg-fill">
                  {v.cover_url ? (
                    <img src={v.cover_url} alt={v.title} loading="lazy" className="h-full w-full object-cover" />
                  ) : (
                    <div className="flex h-full items-center justify-center text-ink-4">
                      <PlatformIcon platform={v.source_platform} size={32} />
                    </div>
                  )}
                  <span className="tnum absolute bottom-1 right-1 rounded bg-black/70 px-1 text-[10px] text-white">
                    {formatDuration(v.duration)}
                  </span>
                  {/* 预览入口：移动端常驻（触屏无 hover） */}
                  <button
                    type="button"
                    aria-label={`预览 ${v.title}`}
                    onClick={(e) => {
                      e.stopPropagation()
                      setPlayId(v.id)
                    }}
                    className="absolute left-1 top-1 flex h-7 items-center gap-1 rounded-md bg-black/70 px-2 text-[11px] text-white transition-opacity hover:bg-black/85"
                  >
                    <Play size={11} weight="fill" />
                    预览
                  </button>
                  {active && (
                    <span className="absolute inset-x-0 bottom-0 h-0.5 bg-accent" aria-hidden />
                  )}
                </div>
                <span className={`block truncate px-1.5 py-1 text-xs ${active ? 'text-ink-1' : 'text-ink-3'}`} title={v.title}>
                  {v.title}
                </span>
              </div>
            )
          })}
        </div>
      )}

      <VideoPlayerModal videoId={playId} title={playVideo?.title ?? '视频预览'} onClose={() => setPlayId(null)} />
    </div>
  )
}
