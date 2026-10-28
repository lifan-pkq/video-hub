export const MIN_DUR = 0.3
export const DRAFT_STORAGE_KEY = 'video-hub:editor-draft:v1'

const EPS = 1e-6
let clipSeq = 0

export interface EditorClip {
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

export interface EditorSource {
  id: number
  title: string
  file_url: string
  duration: number | null
}

export interface SourceRange {
  start: number
  end: number
}

export interface EditorDocument {
  clips: EditorClip[]
  title: string
}

export type DeleteSide = 'left' | 'right'

export function nextClipId(): string {
  clipSeq += 1
  return `clip_${clipSeq}_${Date.now()}`
}

export function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(value, max))
}

export function clipDuration(clip: EditorClip): number {
  return clip.trimEnd - clip.trimStart
}

export function timelineDuration(clips: readonly EditorClip[]): number {
  return clips.length > 0 ? clips[clips.length - 1].end : 0
}

export function isValidRange(start: number, end: number, srcDuration: number): boolean {
  return (
    Number.isFinite(start) &&
    Number.isFinite(end) &&
    Number.isFinite(srcDuration) &&
    srcDuration >= MIN_DUR &&
    start >= 0 &&
    end <= srcDuration + EPS &&
    end - start >= MIN_DUR - EPS
  )
}

export function pack(list: readonly EditorClip[]): EditorClip[] {
  let position = 0
  return list.map((clip) => {
    if (!isValidRange(clip.trimStart, clip.trimEnd, clip.srcDuration)) {
      throw new Error(`片段 ${clip.id} 的源区间无效`)
    }
    const trimStart = Math.max(0, clip.trimStart)
    const trimEnd = Math.min(clip.srcDuration, clip.trimEnd)
    const start = position
    const end = start + trimEnd - trimStart
    position = end
    return { ...clip, trimStart, trimEnd, start, end }
  })
}

export function newClip(source: EditorSource): EditorClip {
  const duration = source.duration ?? 0
  return {
    id: nextClipId(),
    videoId: source.id,
    title: source.title,
    fileUrl: source.file_url,
    srcDuration: duration,
    trimStart: 0,
    trimEnd: duration,
    start: 0,
    end: duration,
  }
}

export function clipsEqual(a: readonly EditorClip[], b: readonly EditorClip[]): boolean {
  if (a === b) return true
  if (a.length !== b.length) return false
  return a.every((clip, index) => {
    const other = b[index]
    return (
      clip.id === other.id &&
      clip.videoId === other.videoId &&
      clip.title === other.title &&
      clip.fileUrl === other.fileUrl &&
      Math.abs(clip.srcDuration - other.srcDuration) < EPS &&
      Math.abs(clip.trimStart - other.trimStart) < EPS &&
      Math.abs(clip.trimEnd - other.trimEnd) < EPS &&
      Math.abs(clip.start - other.start) < EPS &&
      Math.abs(clip.end - other.end) < EPS
    )
  })
}

export function findClipAt(clips: readonly EditorClip[], time: number): EditorClip | null {
  if (clips.length === 0 || !Number.isFinite(time)) return null
  return (
    clips.find((clip) => time >= clip.start && time < clip.end) ??
    (time >= timelineDuration(clips) ? clips[clips.length - 1] : null)
  )
}

export function splitClip(clips: EditorClip[], clipId: string, timelineTime: number): EditorClip[] {
  const index = clips.findIndex((clip) => clip.id === clipId)
  if (index < 0 || !Number.isFinite(timelineTime)) return clips
  const clip = clips[index]
  if (timelineTime <= clip.start + EPS || timelineTime >= clip.end - EPS) return clips
  const sourceCut = clip.trimStart + timelineTime - clip.start
  if (
    !isValidRange(clip.trimStart, sourceCut, clip.srcDuration) ||
    !isValidRange(sourceCut, clip.trimEnd, clip.srcDuration)
  ) {
    return clips
  }
  const next = [...clips]
  next.splice(
    index,
    1,
    { ...clip, trimEnd: sourceCut },
    { ...clip, id: nextClipId(), trimStart: sourceCut },
  )
  return pack(next)
}

export function removeAtPlayhead(
  clips: EditorClip[],
  timelineTime: number,
  side: DeleteSide,
): EditorClip[] {
  if (clips.length === 0 || !Number.isFinite(timelineTime)) return clips
  const total = timelineDuration(clips)
  if (timelineTime < -EPS || timelineTime > total + EPS) return clips

  let index = clips.findIndex((clip) => timelineTime >= clip.start && timelineTime < clip.end)
  let atTimelineEnd = false
  if (index < 0) {
    if (Math.abs(timelineTime - total) > EPS) return clips
    index = clips.length - 1
    atTimelineEnd = true
  }

  const clip = clips[index]
  const atStart = !atTimelineEnd && Math.abs(timelineTime - clip.start) <= EPS
  const atEnd = atTimelineEnd || Math.abs(timelineTime - clip.end) <= EPS
  const sourceCut = clip.trimStart + timelineTime - clip.start
  let next: EditorClip[]

  if (side === 'left') {
    if (atStart) {
      next = clips.slice(index)
    } else if (atEnd) {
      next = clips.slice(index + 1)
    } else {
      if (!isValidRange(sourceCut, clip.trimEnd, clip.srcDuration)) return clips
      next = [{ ...clip, trimStart: sourceCut }, ...clips.slice(index + 1)]
    }
  } else {
    if (atStart) {
      next = clips.slice(0, index)
    } else if (atEnd) {
      next = clips.slice(0, index + 1)
    } else {
      if (!isValidRange(clip.trimStart, sourceCut, clip.srcDuration)) return clips
      next = [...clips.slice(0, index), { ...clip, trimEnd: sourceCut }]
    }
  }

  if (
    next.length === clips.length &&
    ((atStart && side === 'left') || (atEnd && side === 'right'))
  ) {
    return clips
  }
  return pack(next)
}

export function removeClip(clips: EditorClip[], clipId: string): EditorClip[] {
  const next = clips.filter((clip) => clip.id !== clipId)
  return next.length === clips.length ? clips : pack(next)
}

export function duplicateClip(clips: EditorClip[], clipId: string): EditorClip[] {
  const index = clips.findIndex((clip) => clip.id === clipId)
  if (index < 0) return clips
  const next = [...clips]
  next.splice(index + 1, 0, { ...clips[index], id: nextClipId() })
  return pack(next)
}

export function trimClip(
  clips: EditorClip[],
  clipId: string,
  trimStart: number,
  trimEnd: number,
): EditorClip[] {
  const clip = clips.find((item) => item.id === clipId)
  if (!clip || !isValidRange(trimStart, trimEnd, clip.srcDuration)) return clips
  if (Math.abs(clip.trimStart - trimStart) < EPS && Math.abs(clip.trimEnd - trimEnd) < EPS) {
    return clips
  }
  return pack(clips.map((item) => (item.id === clipId ? { ...item, trimStart, trimEnd } : item)))
}

export function moveClip(clips: EditorClip[], clipId: string, timelineStart: number): EditorClip[] {
  const moved = clips.find((clip) => clip.id === clipId)
  if (!moved || !Number.isFinite(timelineStart)) return clips
  const start = Math.max(0, timelineStart)
  const duration = clipDuration(moved)
  const updated = clips.map((clip) =>
    clip.id === clipId ? { ...clip, start, end: start + duration } : clip,
  )
  const center = (clip: EditorClip) => clip.start + clipDuration(clip) / 2
  const sorted = [...updated].sort((a, b) => center(a) - center(b))
  if (sorted.every((clip, index) => clip.id === clips[index].id)) return clips
  return pack(sorted)
}

export function subtractWaste(
  clips: EditorClip[],
  videoId: number,
  waste: readonly SourceRange[],
): EditorClip[] {
  if (waste.length === 0) return clips
  let changed = false
  const next: EditorClip[] = []

  for (const clip of clips) {
    if (clip.videoId !== videoId) {
      next.push(clip)
      continue
    }
    const ranges = waste
      .filter(
        (range) =>
          Number.isFinite(range.start) &&
          Number.isFinite(range.end) &&
          range.end > clip.trimStart + EPS &&
          range.start < clip.trimEnd - EPS,
      )
      .map((range) => ({
        start: clamp(range.start, clip.trimStart, clip.trimEnd),
        end: clamp(range.end, clip.trimStart, clip.trimEnd),
      }))
      .filter((range) => range.end - range.start > EPS)
      .sort((a, b) => a.start - b.start)

    if (ranges.length === 0) {
      next.push(clip)
      continue
    }

    const pieces: SourceRange[] = []
    let cursor = clip.trimStart
    for (const range of ranges) {
      if (range.start - cursor >= MIN_DUR - EPS) {
        pieces.push({ start: cursor, end: Math.min(range.start, clip.trimEnd) })
      }
      cursor = Math.max(cursor, range.end)
    }
    if (clip.trimEnd - cursor >= MIN_DUR - EPS) {
      pieces.push({ start: cursor, end: clip.trimEnd })
    }

    const unchanged =
      pieces.length === 1 &&
      Math.abs(pieces[0].start - clip.trimStart) < EPS &&
      Math.abs(pieces[0].end - clip.trimEnd) < EPS
    if (unchanged) {
      next.push(clip)
      continue
    }

    changed = true
    pieces.forEach((piece, index) => {
      next.push({
        ...clip,
        id: index === 0 ? clip.id : nextClipId(),
        trimStart: piece.start,
        trimEnd: piece.end,
      })
    })
  }

  return changed ? pack(next) : clips
}

export function serializeDraft({ clips, title }: EditorDocument): string {
  return JSON.stringify({
    version: 1,
    savedAt: Date.now(),
    title,
    clips: clips.map((clip) => ({
      id: clip.id,
      videoId: clip.videoId,
      trimStart: clip.trimStart,
      trimEnd: clip.trimEnd,
    })),
  })
}

export function restoreDraft(raw: string, sources: readonly EditorSource[]): EditorDocument | null {
  try {
    const value: unknown = JSON.parse(raw)
    if (!value || typeof value !== 'object') return null
    const draft = value as {
      version?: unknown
      title?: unknown
      clips?: unknown
    }
    if (draft.version !== 1 || typeof draft.title !== 'string' || !Array.isArray(draft.clips)) {
      return null
    }
    if (draft.title.length > 80) return null

    const byId = new Map(sources.map((source) => [source.id, source]))
    const ids = new Set<string>()
    const clips = draft.clips.map((value: unknown) => {
      if (!value || typeof value !== 'object') throw new Error('invalid draft clip')
      const item = value as {
        id?: unknown
        videoId?: unknown
        trimStart?: unknown
        trimEnd?: unknown
      }
      if (
        typeof item.id !== 'string' ||
        !item.id ||
        ids.has(item.id) ||
        typeof item.videoId !== 'number' ||
        !Number.isInteger(item.videoId) ||
        typeof item.trimStart !== 'number' ||
        typeof item.trimEnd !== 'number'
      ) {
        throw new Error('invalid draft clip')
      }
      const source = byId.get(item.videoId)
      const duration = source?.duration ?? 0
      if (!source || !isValidRange(item.trimStart, item.trimEnd, duration)) {
        throw new Error('stale draft clip')
      }
      ids.add(item.id)
      return {
        ...newClip(source),
        id: item.id,
        trimStart: item.trimStart,
        trimEnd: item.trimEnd,
      }
    })
    return { clips: pack(clips), title: draft.title }
  } catch {
    return null
  }
}

export function formatTimecode(seconds: number): string {
  const safe = Number.isFinite(seconds) ? Math.max(0, seconds) : 0
  const minutes = Math.floor(safe / 60)
  const remainder = safe - minutes * 60
  return `${String(minutes).padStart(2, '0')}:${remainder.toFixed(1).padStart(4, '0')}`
}
