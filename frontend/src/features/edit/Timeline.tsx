import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react'
import type { Suggestion } from '../../api'
import { clipDuration, clipFreeze, clipSpeed, isImageClip, sourceToTimeline, timelineDuration, type Clip, type Decision } from './model'

function formatTime(seconds: number): string {
  const safe = Number.isFinite(seconds) ? Math.max(0, seconds) : 0
  const minutes = Math.floor(safe / 60)
  const rest = safe - minutes * 60
  return `${String(minutes).padStart(2, '0')}:${rest.toFixed(1).padStart(4, '0')}`
}

const KIND_COLOR: Record<Suggestion['kind'], string> = {
  silence: 'rgba(225, 91, 74, 0.45)',
  black: 'rgba(225, 91, 74, 0.7)',
  pause: 'rgba(232, 161, 90, 0.45)',
  filler: 'rgba(232, 161, 90, 0.28)',
  scene: '#8eb6e0',
}

export function Timeline({
  clips,
  suggestions,
  decisions,
  time,
  selectedClipId,
  selectedSuggestionId,
  onSeek,
  onSelectClip,
  onSelectSuggestion,
  onTrim,
}: {
  clips: Clip[]
  suggestions: Suggestion[]
  decisions: Record<string, Decision>
  time: number
  selectedClipId: string | null
  selectedSuggestionId: string | null
  onSeek: (time: number) => void
  onSelectClip: (id: string) => void
  onSelectSuggestion: (id: string) => void
  onTrim: (clipId: string, trimStart: number, trimEnd: number, phase: 'move' | 'up') => void
}) {
  const ref = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(640)
  const total = Math.max(timelineDuration(clips), 0.1)
  const px = Math.max(48, (width - 24) / total)

  useEffect(() => {
    const node = ref.current
    if (!node) return
    const observer = new ResizeObserver(() => setWidth(node.clientWidth))
    observer.observe(node)
    setWidth(node.clientWidth)
    return () => observer.disconnect()
  }, [])

  function seekFromEvent(event: ReactPointerEvent<HTMLDivElement>) {
    const bounds = event.currentTarget.getBoundingClientRect()
    const next = Math.max(0, Math.min(total, (event.clientX - bounds.left - 12) / px))
    onSeek(next)
  }

  return (
    <div ref={ref} className="relative h-44 overflow-hidden rounded-xl border border-line bg-fill-deep">
      <div className="absolute inset-x-0 top-0 h-6 border-b border-line-soft" onPointerDown={seekFromEvent}>
        {Array.from({ length: Math.floor(total / (px > 70 ? 1 : px > 28 ? 5 : 15)) + 1 }, (_, index) => {
          const second = index * (px > 70 ? 1 : px > 28 ? 5 : 15)
          return (
            <span key={second} className="tnum absolute top-1 text-[10px] text-ink-4" style={{ left: 12 + second * px }}>
              {formatTime(second)}
            </span>
          )
        })}
      </div>
      <div className="absolute inset-x-3 bottom-3 top-8" onPointerDown={seekFromEvent}>
        {clips.map((clip) => {
          if (isImageClip(clip) && clip.overlay) {
            return (
              <button
                key={clip.id}
                type="button"
                title={clip.title}
                className={`absolute top-2 z-10 h-6 max-w-[7rem] truncate rounded px-1.5 text-[10px] ${clip.id === selectedClipId ? 'bg-accent text-white' : 'bg-amber-200 text-ink-1'}`}
                style={{ left: clip.start * px }}
                onPointerDown={(event) => {
                  event.stopPropagation()
                  onSelectClip(clip.id)
                }}
              >
                叠图 · {clip.title}
              </button>
            )
          }
          const left = clip.start * px
          const clipWidth = Math.max(8, clipDuration(clip) * px)
          const selected = clip.id === selectedClipId
          const speed = clipSpeed(clip)
          const freeze = clipFreeze(clip)
          return (
            <div
              key={clip.id}
              className={`absolute top-2 h-24 overflow-hidden rounded-md border ${selected ? 'border-accent' : 'border-line'}`}
              style={{
                left,
                width: clipWidth,
                background: isImageClip(clip)
                  ? 'linear-gradient(180deg, #efe6cf, #e7d7b0)'
                  : 'linear-gradient(180deg, var(--raise), var(--fill))',
              }}
              onPointerDown={(event) => {
                event.stopPropagation()
                onSelectClip(clip.id)
                onSeek(clip.start + Math.max(0, (event.nativeEvent.offsetX / clipWidth) * clipDuration(clip)))
              }}
            >
              <div className="truncate px-2 pt-1 text-xs text-ink-1">{clip.title}</div>
              <div className="tnum truncate px-2 text-[10px] text-ink-3">
                {formatTime(clipDuration(clip))}
                {isImageClip(clip) ? ' · 静帧' : ''}
                {Math.abs(speed - 1) >= 0.05 ? ` · ${speed.toFixed(2)}x` : ''}
                {freeze >= 0.05 ? ` · 静止 ${freeze.toFixed(1)}s` : ''}
              </div>
              {suggestions
                .filter((item) => item.video_id === clip.videoId && item.action === 'drop' && decisions[item.id] !== 'rejected')
                .map((item) => {
                  const start = sourceToTimeline([clip], clip.videoId, item.start)
                  const end = sourceToTimeline([clip], clip.videoId, item.end)
                  if (start == null || end == null || end <= start) return null
                  const bandLeft = (start - clip.start) * px
                  const bandWidth = Math.max(3, (end - start) * px)
                  const accepted = decisions[item.id] === 'accepted'
                  return (
                    <button
                      key={item.id}
                      type="button"
                      title={item.reason}
                      className={`absolute bottom-1 top-8 rounded-sm ${selectedSuggestionId === item.id ? 'ring-1 ring-ink-1' : ''}`}
                      style={{
                        left: bandLeft,
                        width: bandWidth,
                        background: accepted ? 'rgba(225, 91, 74, 0.72)' : KIND_COLOR[item.kind],
                      }}
                      onPointerDown={(event) => {
                        event.stopPropagation()
                        onSelectSuggestion(item.id)
                        onSelectClip(clip.id)
                      }}
                    />
                  )
                })}
              {suggestions
                .filter((item) => item.video_id === clip.videoId && item.action === 'split' && decisions[item.id] !== 'rejected')
                .map((item) => {
                  const at = sourceToTimeline([clip], clip.videoId, item.start)
                  if (at == null) return null
                  return (
                    <button
                      key={item.id}
                      type="button"
                      className="absolute bottom-1 top-8 w-0 border-l-2 border-accent"
                      style={{ left: (at - clip.start) * px }}
                      title="镜头切点"
                      onPointerDown={(event) => {
                        event.stopPropagation()
                        onSelectSuggestion(item.id)
                        onSelectClip(clip.id)
                      }}
                    />
                  )
                })}
              {!isImageClip(clip) ? (
                <>
                  <TrimHandle edge="start" clip={clip} px={px} onTrim={onTrim} />
                  <TrimHandle edge="end" clip={clip} px={px} onTrim={onTrim} />
                </>
              ) : null}
            </div>
          )
        })}
        <div className="absolute bottom-0 top-0 w-px bg-accent" style={{ left: time * px }} />
      </div>
      {clips.length === 0 ? (
        <p className="absolute inset-x-0 top-16 text-center text-sm text-ink-3">时间线是空的。从左侧把素材加进来。</p>
      ) : null}
    </div>
  )
}

function TrimHandle({
  edge,
  clip,
  px,
  onTrim,
}: {
  edge: 'start' | 'end'
  clip: Clip
  px: number
  onTrim: (clipId: string, trimStart: number, trimEnd: number, phase: 'move' | 'up') => void
}) {
  return (
    <span
      className={`absolute bottom-0 top-0 w-2 cursor-ew-resize bg-ink-1/15 ${edge === 'start' ? 'left-0' : 'right-0'}`}
      onPointerDown={(event) => {
        event.stopPropagation()
        const originX = event.clientX
        const originStart = clip.trimStart
        const originEnd = clip.trimEnd
        const move = (ev: PointerEvent) => {
          const delta = (ev.clientX - originX) / px
          if (edge === 'start') onTrim(clip.id, originStart + delta, originEnd, 'move')
          else onTrim(clip.id, originStart, originEnd + delta, 'move')
        }
        const up = (ev: PointerEvent) => {
          const delta = (ev.clientX - originX) / px
          if (edge === 'start') onTrim(clip.id, originStart + delta, originEnd, 'up')
          else onTrim(clip.id, originStart, originEnd + delta, 'up')
          window.removeEventListener('pointermove', move)
          window.removeEventListener('pointerup', up)
        }
        window.addEventListener('pointermove', move)
        window.addEventListener('pointerup', up)
      }}
    />
  )
}
