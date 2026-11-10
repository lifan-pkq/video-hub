import type { Suggestion, Video } from '../../lib/types.ts'
import { fileUrl } from '../../lib/api.ts'

export const MIN_DUR = 0.3

export type Clip = {
  id: string
  videoId: number
  title: string
  fileUrl: string
  srcDuration: number
  trimStart: number
  trimEnd: number
  start: number
  end: number
}

export type Decision = 'accepted' | 'rejected'

export type EditDoc = {
  title: string
  clips: Clip[]
  decisions: Record<string, Decision>
}

let sequence = 0

export function nextClipId(): string {
  sequence += 1
  return `c${sequence}_${Date.now().toString(36)}`
}

export function round2(value: number): number {
  return Math.round(value * 100) / 100
}

export function clipDuration(clip: Clip): number {
  return Math.max(0, clip.trimEnd - clip.trimStart)
}

export function timelineDuration(clips: readonly Clip[]): number {
  return clips.length ? clips[clips.length - 1].end : 0
}

export function pack(list: readonly Clip[]): Clip[] {
  let cursor = 0
  return list.map((clip) => {
    const trimStart = Math.max(0, clip.trimStart)
    const trimEnd = Math.min(clip.srcDuration, Math.max(trimStart + MIN_DUR, clip.trimEnd))
    const start = cursor
    const end = start + (trimEnd - trimStart)
    cursor = end
    return { ...clip, trimStart, trimEnd, start, end }
  })
}

export function newClip(video: Video): Clip | null {
  const duration = Number(video.duration || 0)
  if (duration < MIN_DUR) return null
  return {
    id: nextClipId(),
    videoId: video.id,
    title: video.title || `视频 ${video.id}`,
    fileUrl: fileUrl(video),
    srcDuration: duration,
    trimStart: 0,
    trimEnd: duration,
    start: 0,
    end: duration,
  }
}

export function clipAt(clips: readonly Clip[], time: number): Clip | null {
  if (!clips.length) return null
  return clips.find((clip) => time >= clip.start && time < clip.end - 1e-4) ?? clips[clips.length - 1]
}

export function splitClip(clips: readonly Clip[], clipId: string, sourceTime: number): Clip[] {
  const index = clips.findIndex((clip) => clip.id === clipId)
  if (index < 0) return [...clips]
  const clip = clips[index]
  if (sourceTime <= clip.trimStart + MIN_DUR || sourceTime >= clip.trimEnd - MIN_DUR) return [...clips]
  const next = [...clips]
  next.splice(
    index,
    1,
    { ...clip, trimEnd: sourceTime },
    { ...clip, id: nextClipId(), trimStart: sourceTime },
  )
  return pack(next)
}

export function removeClip(clips: readonly Clip[], clipId: string): Clip[] {
  return pack(clips.filter((clip) => clip.id !== clipId))
}

export function trimClip(clips: readonly Clip[], clipId: string, trimStart: number, trimEnd: number): Clip[] {
  return pack(
    clips.map((clip) => {
      if (clip.id !== clipId) return clip
      const start = Math.max(0, Math.min(trimStart, clip.srcDuration - MIN_DUR))
      const end = Math.min(clip.srcDuration, Math.max(trimEnd, start + MIN_DUR))
      return { ...clip, trimStart: start, trimEnd: end }
    }),
  )
}

export function moveClip(clips: readonly Clip[], clipId: string, direction: -1 | 1): Clip[] {
  const index = clips.findIndex((clip) => clip.id === clipId)
  const target = index + direction
  if (index < 0 || target < 0 || target >= clips.length) return [...clips]
  const next = [...clips]
  const [item] = next.splice(index, 1)
  next.splice(target, 0, item)
  return pack(next)
}

export function exportRanges(clips: readonly Clip[], suggestions: readonly Suggestion[], decisions: Record<string, Decision>) {
  const ranges: { video_id: number; start: number; end: number }[] = []
  for (const clip of clips) {
    const drops = suggestions
      .filter((item) => item.video_id === clip.videoId && item.action === 'drop' && decisions[item.id] === 'accepted')
      .map((item) => ({
        start: Math.max(item.start, clip.trimStart),
        end: Math.min(item.end, clip.trimEnd),
      }))
      .filter((item) => item.end - item.start > 0.05)
      .sort((a, b) => a.start - b.start)
    let cursor = clip.trimStart
    for (const drop of drops) {
      if (drop.start - cursor >= MIN_DUR) {
        ranges.push({ video_id: clip.videoId, start: round2(cursor), end: round2(Math.min(drop.start, clip.trimEnd)) })
      }
      cursor = Math.max(cursor, drop.end)
    }
    if (clip.trimEnd - cursor >= MIN_DUR) {
      ranges.push({ video_id: clip.videoId, start: round2(cursor), end: round2(clip.trimEnd) })
    }
  }
  return ranges
}

export function keptDuration(clips: readonly Clip[], suggestions: readonly Suggestion[], decisions: Record<string, Decision>): number {
  return exportRanges(clips, suggestions, decisions).reduce((sum, item) => sum + item.end - item.start, 0)
}

export function sourceToTimeline(clips: readonly Clip[], videoId: number, source: number): number | null {
  for (const clip of clips) {
    if (clip.videoId !== videoId) continue
    if (source < clip.trimStart - 0.02 || source > clip.trimEnd + 0.02) continue
    const clamped = Math.min(clip.trimEnd, Math.max(clip.trimStart, source))
    return clip.start + (clamped - clip.trimStart)
  }
  return null
}
