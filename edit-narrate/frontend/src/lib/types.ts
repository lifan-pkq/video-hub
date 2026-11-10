export type Video = {
  id: number
  title: string
  filename?: string
  duration?: number | null
  width?: number | null
  height?: number | null
  file_url?: string
  poster_url?: string
  source_platform?: string
}

export type TranscriptSegment = {
  id: string
  start: number
  end: number
  text: string
}

export type Suggestion = {
  id: string
  video_id: number
  start: number
  end: number
  kind: 'silence' | 'black' | 'pause' | 'filler' | 'scene'
  action: 'drop' | 'split'
  score: number
  reason: string
  evidence: string
  recommended: boolean
  llm_action?: 'drop' | 'keep' | null
  text?: string
}

export type JobError = {
  code: string
  message: string
  action: string
}

export type Job = {
  id: string
  kind: string
  status: 'pending' | 'running' | 'success' | 'partial' | 'failed' | 'cancelled'
  progress: number
  stage: string
  label: string
  logs: string[]
  error: JobError | null
  result: Record<string, unknown> | null
}

export type Health = {
  ok: boolean
  ffmpeg: boolean
  llm: boolean
  vision: boolean
  asr: string
  tts: { id: string; available: boolean; detail: string }[]
}

export type FrameNote = {
  time: number
  image: string
  caption: string
  caption_error: string
  brightness: number | null
}

export type Beat = {
  id: string
  start: number
  end: number
  narration: string
  purpose: 'hook' | 'explain' | 'evidence' | 'turn' | 'close'
}

export type VoiceSettings = {
  engine: 'auto' | 'edge' | 'openai' | 'espeak' | 'tone'
  voice: string
  rate: number
  original_volume: number
  subtitles: boolean
  font_size: number
  margin_v: number
}

export type NarrateTask = {
  id: string
  video_id: number
  title: string
  duration: number
  width: number
  height: number
  transcript: {
    source: string
    warning: string
    segments: TranscriptSegment[]
    error?: JobError
  }
  frames: FrameNote[]
  script: {
    beats: Beat[]
    warnings: string[]
    mode?: string
  } | null
  voice: VoiceSettings
  output: {
    file: string
    duration: number
    tts_engine: string
    warnings: string[]
    video_id?: number
  } | null
  warnings?: string[]
  step: string
}

export type EngineInfo = {
  engines: { id: string; available: boolean; detail: string }[]
  voices: { engine: string; id: string; label: string }[]
}
