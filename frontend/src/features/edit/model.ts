import type { Suggestion, Video } from '../../api'

export const MIN_DUR = 0.3

export type ClipKind = 'video' | 'image'

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
  kind: ClipKind
  speed: number
  freeze: number
  hold?: number
  overlay?: boolean
  imageId?: number
  imageName?: string
  imageUrl?: string
  videoName?: string
}

export type Decision = 'accepted' | 'rejected'

export type EditDoc = {
  title: string
  clips: Clip[]
  decisions: Record<string, Decision>
}

export type ExportClipPayload = {
  video_id: number
  start: number
  end: number
  speed: number
  freeze: number
  kind: ClipKind
  image_id: number
  image_name: string
  video_name: string
  overlay: boolean
  title: string
}

let sequence = 0

export function nextClipId(): string {
  sequence += 1
  return `c${sequence}_${Date.now().toString(36)}`
}

export function round2(value: number): number {
  return Math.round(value * 100) / 100
}

export function clipSpeed(clip: Pick<Clip, 'speed'>): number {
  const value = Number(clip.speed ?? 1)
  return Number.isFinite(value) ? Math.min(2, Math.max(0.5, value)) : 1
}

export function clipFreeze(clip: Pick<Clip, 'freeze'>): number {
  const value = Number(clip.freeze ?? 0)
  if (!Number.isFinite(value) || value < 0) return 0
  return Math.min(30, value)
}

export function isImageClip(clip: Pick<Clip, 'kind'>): boolean {
  return clip.kind === 'image'
}

export function imageHold(clip: Pick<Clip, 'hold'>): number {
  const value = Number(clip.hold ?? 2.5)
  return Number.isFinite(value) ? Math.min(30, Math.max(MIN_DUR, value)) : 2.5
}

export function clipDuration(clip: Clip): number {
  if (isImageClip(clip) && clip.overlay) return 0
  if (isImageClip(clip)) return imageHold(clip)
  return Math.max(0, (clip.trimEnd - clip.trimStart) / clipSpeed(clip)) + clipFreeze(clip)
}

export function timelineDuration(clips: readonly Clip[]): number {
  return clips.length ? clips[clips.length - 1].end : 0
}

function normalizeClip(clip: Clip): Clip {
  if (isImageClip(clip)) {
    const hold = imageHold(clip)
    return {
      ...clip,
      kind: 'image',
      speed: 1,
      freeze: 0,
      hold,
      overlay: !!clip.overlay,
      trimStart: 0,
      trimEnd: hold,
      srcDuration: Math.max(hold, clip.srcDuration || hold),
      videoId: clip.videoId || 0,
    }
  }
  const speed = clipSpeed(clip)
  const freeze = clipFreeze(clip)
  const trimStart = Math.max(0, clip.trimStart)
  const srcDuration = clip.srcDuration > 0 ? clip.srcDuration : trimStart + MIN_DUR
  const trimEnd = Math.min(srcDuration, Math.max(trimStart + MIN_DUR, clip.trimEnd))
  return {
    ...clip,
    kind: 'video',
    speed,
    freeze,
    overlay: false,
    trimStart,
    trimEnd,
  }
}

export function pack(list: readonly Clip[]): Clip[] {
  let cursor = 0
  return list.map((item) => {
    const clip = normalizeClip(item)
    if (clip.kind === 'image' && clip.overlay) {
      return { ...clip, start: cursor, end: cursor }
    }
    const start = cursor
    const end = start + clipDuration(clip)
    cursor = end
    return { ...clip, start, end }
  })
}

export function newClip(video: Video): Clip | null {
  const duration = Number(video.duration || 0)
  if (duration < MIN_DUR) return null
  return {
    id: nextClipId(),
    videoId: video.id,
    title: video.title || `视频 ${video.id}`,
    fileUrl: video.file_url || `/api/videos/${video.id}/file`,
    srcDuration: duration,
    trimStart: 0,
    trimEnd: duration,
    start: 0,
    end: duration,
    kind: 'video',
    speed: 1,
    freeze: 0,
  }
}

export function newImageClip(input: {
  imageId?: number
  imageName?: string
  imageUrl: string
  title?: string
  overlay?: boolean
  hold?: number
}): Clip {
  const hold = Math.min(30, Math.max(MIN_DUR, Number(input.hold ?? 2.5) || 2.5))
  return {
    id: nextClipId(),
    kind: 'image',
    videoId: 0,
    title: input.title || '静帧',
    fileUrl: input.imageUrl,
    imageUrl: input.imageUrl,
    imageId: input.imageId || 0,
    imageName: input.imageName || '',
    srcDuration: hold,
    trimStart: 0,
    trimEnd: hold,
    hold,
    overlay: !!input.overlay,
    speed: 1,
    freeze: 0,
    start: 0,
    end: hold,
  }
}

export function newInsertedVideoClip(input: {
  videoId: number
  videoName?: string
  title?: string
  fileUrl: string
  srcDuration: number
}): Clip | null {
  const duration = Number(input.srcDuration || 0)
  if (duration < MIN_DUR && !input.videoName) return null
  const length = Math.max(MIN_DUR, duration || 4)
  return {
    id: nextClipId(),
    kind: 'video',
    videoId: input.videoId,
    title: input.title || '插入视频',
    fileUrl: input.fileUrl,
    videoName: input.videoName || '',
    srcDuration: length,
    trimStart: 0,
    trimEnd: length,
    start: 0,
    end: length,
    speed: 1,
    freeze: 0,
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
  if (isImageClip(clip)) return [...clips]
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
      if (clip.id !== clipId || isImageClip(clip)) return clip
      const start = Math.max(0, Math.min(trimStart, clip.srcDuration - MIN_DUR))
      const end = Math.min(clip.srcDuration, Math.max(trimEnd, start + MIN_DUR))
      return { ...clip, trimStart: start, trimEnd: end }
    }),
  )
}

export function updateClip(clips: readonly Clip[], clipId: string, patch: Partial<Clip>): Clip[] {
  return pack(clips.map((clip) => (clip.id === clipId ? { ...clip, ...patch } : clip)))
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

export function insertAfter(clips: readonly Clip[], afterId: string | null, clip: Clip): Clip[] {
  const next = [...clips]
  const index = afterId ? next.findIndex((item) => item.id === afterId) : -1
  if (index < 0) next.push(clip)
  else next.splice(index + 1, 0, clip)
  return pack(next)
}

export function exportRanges(
  clips: readonly Clip[],
  suggestions: readonly Suggestion[],
  decisions: Record<string, Decision>,
): ExportClipPayload[] {
  const ranges: ExportClipPayload[] = []
  for (const clip of clips) {
    if (isImageClip(clip)) {
      ranges.push({
        video_id: 0,
        start: 0,
        end: round2(imageHold(clip)),
        speed: 1,
        freeze: 0,
        kind: 'image',
        image_id: clip.imageId || 0,
        image_name: clip.imageName || '',
        video_name: '',
        overlay: !!clip.overlay,
        title: clip.title,
      })
      continue
    }
    const drops = suggestions
      .filter((item) => item.video_id === clip.videoId && item.action === 'drop' && decisions[item.id] === 'accepted')
      .map((item) => ({
        start: Math.max(item.start, clip.trimStart),
        end: Math.min(item.end, clip.trimEnd),
      }))
      .filter((item) => item.end - item.start > 0.05)
      .sort((a, b) => a.start - b.start)
    const pieces: { start: number; end: number }[] = []
    let cursor = clip.trimStart
    for (const drop of drops) {
      if (drop.start - cursor >= MIN_DUR) pieces.push({ start: cursor, end: Math.min(drop.start, clip.trimEnd) })
      cursor = Math.max(cursor, drop.end)
    }
    if (clip.trimEnd - cursor >= MIN_DUR) pieces.push({ start: cursor, end: clip.trimEnd })
    pieces.forEach((piece, index) => {
      ranges.push({
        video_id: clip.videoId,
        start: round2(piece.start),
        end: round2(piece.end),
        speed: clipSpeed(clip),
        freeze: index === pieces.length - 1 ? clipFreeze(clip) : 0,
        kind: 'video',
        image_id: 0,
        image_name: '',
        video_name: clip.videoName || '',
        overlay: false,
        title: clip.title,
      })
    })
  }
  return ranges
}

function payloadDuration(item: ExportClipPayload): number {
  if (item.kind === 'image' && item.overlay) return 0
  if (item.kind === 'image') return Math.max(0, item.end - item.start)
  const speed = Math.min(2, Math.max(0.5, item.speed || 1))
  return Math.max(0, (item.end - item.start) / speed) + Math.max(0, item.freeze || 0)
}

export function keptDuration(
  clips: readonly Clip[],
  suggestions: readonly Suggestion[],
  decisions: Record<string, Decision>,
): number {
  return exportRanges(clips, suggestions, decisions).reduce((sum, item) => sum + payloadDuration(item), 0)
}

export function sourceToTimeline(clips: readonly Clip[], videoId: number, source: number): number | null {
  for (const clip of clips) {
    if (isImageClip(clip) || clip.videoId !== videoId) continue
    if (source < clip.trimStart - 0.02 || source > clip.trimEnd + 0.02) continue
    const clamped = Math.min(clip.trimEnd, Math.max(clip.trimStart, source))
    return clip.start + (clamped - clip.trimStart) / clipSpeed(clip)
  }
  return null
}

export function timelineToSource(clip: Clip, timelineTime: number): number {
  if (isImageClip(clip)) return 0
  const local = Math.max(0, timelineTime - clip.start)
  const playable = Math.max(0, (clip.trimEnd - clip.trimStart) / clipSpeed(clip))
  if (local >= playable) return Math.max(clip.trimStart, clip.trimEnd - 0.04)
  return clip.trimStart + local * clipSpeed(clip)
}
