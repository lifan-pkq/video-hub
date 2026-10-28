import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
} from 'react'
import {
  CircleNotch,
  FilmSlate,
  Pause,
  Play,
  SkipBack,
  X,
} from '@phosphor-icons/react'
import { errorMessage, formatDuration } from '../../api'
import { Button, Panel } from '../../ui'
import {
  clamp,
  findClipAt,
  formatTimecode,
  timelineDuration,
  type EditorClip,
} from './model'

export interface SuggestionPreviewRange {
  videoId: number
  fileUrl: string
  title: string
  start: number
  end: number
}

export interface EditorPreviewHandle {
  seek: (time: number) => void
  seekBy: (delta: number) => void
  togglePlay: () => void
  pause: () => void
  previewRange: (range: SuggestionPreviewRange) => void
}

interface Props {
  clips: EditorClip[]
  onTimeChange: (time: number) => void
}

interface RestoreState {
  time: number
  playing: boolean
}

function sameSource(video: HTMLVideoElement, fileUrl: string): boolean {
  const source = video.getAttribute('src') ?? ''
  return source === fileUrl || source.endsWith(fileUrl)
}

const EditorPreview = forwardRef<EditorPreviewHandle, Props>(function EditorPreview(
  { clips, onTimeChange },
  ref,
) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const clipsRef = useRef(clips)
  const totalRef = useRef(timelineDuration(clips))
  const timeRef = useRef(0)
  const pendingSeekRef = useRef<number | null>(null)
  const playingRef = useRef(false)
  const waitingRef = useRef(false)
  const suggestionRef = useRef<SuggestionPreviewRange | null>(null)
  const restoreRef = useRef<RestoreState | null>(null)
  const onTimeChangeRef = useRef(onTimeChange)
  clipsRef.current = clips
  totalRef.current = timelineDuration(clips)
  onTimeChangeRef.current = onTimeChange

  const [time, setTime] = useState(0)
  const [playing, setPlayingState] = useState(false)
  const [waiting, setWaitingState] = useState(false)
  const [playbackError, setPlaybackError] = useState<string | null>(null)
  const [suggestion, setSuggestion] = useState<SuggestionPreviewRange | null>(null)
  const [suggestionTime, setSuggestionTime] = useState(0)

  const setPlaying = useCallback((value: boolean) => {
    playingRef.current = value
    setPlayingState(value)
  }, [])

  const setWaiting = useCallback((value: boolean) => {
    waitingRef.current = value
    setWaitingState(value)
  }, [])

  const setPlayhead = useCallback((value: number, force = false) => {
    const next = clamp(Number.isFinite(value) ? value : 0, 0, totalRef.current)
    timeRef.current = next
    setTime(next)
    onTimeChangeRef.current(next)

    const video = videoRef.current
    const clip = findClipAt(clipsRef.current, next)
    if (!video || !clip) return
    const sourceTime = Math.min(
      clip.trimStart + next - clip.start,
      Math.max(clip.trimStart, clip.trimEnd - 0.05),
    )
    if (!sameSource(video, clip.fileUrl)) {
      pendingSeekRef.current = sourceTime
      if (playingRef.current) setWaiting(true)
      video.src = clip.fileUrl
      video.load()
    } else if (video.readyState === 0) {
      pendingSeekRef.current = sourceTime
      video.load()
    } else if (force || Math.abs(video.currentTime - sourceTime) > 0.35) {
      video.currentTime = sourceTime
    }
  }, [setWaiting])

  const playVideo = useCallback((video: HTMLVideoElement) => {
    video.play().catch((error: unknown) => {
      if (error instanceof DOMException && error.name === 'AbortError') return
      video.pause()
      setPlaying(false)
      setWaiting(false)
      setPlaybackError(`播放失败：${errorMessage(error)}`)
    })
  }, [setPlaying, setWaiting])

  const exitSuggestion = useCallback((restore = true) => {
    if (!suggestionRef.current) return
    const video = videoRef.current
    const snapshot = restoreRef.current
    suggestionRef.current = null
    restoreRef.current = null
    setSuggestion(null)
    setPlaying(false)
    video?.pause()
    setWaiting(false)
    setPlayhead(restore && snapshot ? snapshot.time : timeRef.current, true)
    if (restore && snapshot?.playing && clipsRef.current.length > 0) {
      setWaiting(true)
      setPlaying(true)
      if (video && video.readyState > 0) playVideo(video)
    }
  }, [playVideo, setPlayhead, setPlaying, setWaiting])

  const previewRange = useCallback((range: SuggestionPreviewRange) => {
    const video = videoRef.current
    if (!video || !range.fileUrl || !(range.end > range.start)) return
    if (!restoreRef.current) {
      restoreRef.current = { time: timeRef.current, playing: playingRef.current }
    }
    setPlaying(false)
    video.pause()
    suggestionRef.current = range
    setSuggestion(range)
    setSuggestionTime(range.start)
    setPlaybackError(null)
    pendingSeekRef.current = range.start

    if (!sameSource(video, range.fileUrl)) {
      video.src = range.fileUrl
      video.load()
    } else if (video.readyState > 0) {
      video.currentTime = range.start
      pendingSeekRef.current = null
    } else {
      video.load()
    }
    setWaiting(true)
    setPlaying(true)
    if (video.readyState > 0) playVideo(video)
  }, [playVideo, setPlaying, setWaiting])

  const seek = useCallback((value: number) => {
    if (suggestionRef.current) exitSuggestion(false)
    setPlayhead(value, true)
  }, [exitSuggestion, setPlayhead])

  const togglePlay = useCallback(() => {
    if (suggestionRef.current) {
      if (playingRef.current) {
        setPlaying(false)
        setWaiting(false)
      } else {
        setPlaybackError(null)
        setPlaying(true)
      }
      return
    }
    if (clipsRef.current.length === 0) return
    if (!playingRef.current && timeRef.current >= totalRef.current) {
      setPlayhead(0, true)
    }
    setPlaybackError(null)
    setWaiting(!playingRef.current)
    setPlaying(!playingRef.current)
  }, [setPlayhead, setPlaying, setWaiting])

  const pause = useCallback(() => {
    setPlaying(false)
    setWaiting(false)
  }, [setPlaying, setWaiting])

  useImperativeHandle(ref, () => ({
    seek,
    seekBy: (delta) => seek(timeRef.current + delta),
    togglePlay,
    pause,
    previewRange,
  }), [pause, previewRange, seek, togglePlay])

  useEffect(() => {
    const video = videoRef.current
    if (!video) return

    const onLoadedMetadata = () => {
      if (pendingSeekRef.current != null) {
        const pending = pendingSeekRef.current
        pendingSeekRef.current = null
        try {
          video.currentTime = pending
        } catch (error) {
          setPlaybackError(`定位失败：${errorMessage(error)}`)
          setPlaying(false)
          setWaiting(false)
          return
        }
      }
      if (playingRef.current) playVideo(video)
    }
    const onWaiting = () => {
      if (playingRef.current) setWaiting(true)
    }
    const onPlaying = () => {
      setWaiting(false)
      setPlaybackError(null)
    }
    const onError = () => {
      if (!video.error) return
      setPlaying(false)
      setWaiting(false)
      setPlaybackError('播放失败：视频加载或解码失败，请重试')
    }
    const onTimeUpdate = () => {
      const active = suggestionRef.current
      if (active) {
        setSuggestionTime(video.currentTime)
        if (playingRef.current && (video.currentTime >= active.end - 0.03 || video.ended)) {
          exitSuggestion(true)
        }
        return
      }
      if (!playingRef.current || waitingRef.current) return
      const clip = findClipAt(clipsRef.current, timeRef.current)
      if (!clip || !sameSource(video, clip.fileUrl)) return
      const expected = clip.trimStart + timeRef.current - clip.start
      if (Math.abs(video.currentTime - expected) > 0.4) {
        video.currentTime = Math.min(expected, Math.max(clip.trimStart, clip.trimEnd - 0.05))
      }
    }
    const onEnded = () => {
      if (suggestionRef.current) {
        exitSuggestion(true)
      } else if (playingRef.current && timeRef.current >= totalRef.current - 0.1) {
        setPlayhead(totalRef.current, true)
        setPlaying(false)
        setWaiting(false)
      }
    }

    video.addEventListener('loadedmetadata', onLoadedMetadata)
    video.addEventListener('waiting', onWaiting)
    video.addEventListener('playing', onPlaying)
    video.addEventListener('error', onError)
    video.addEventListener('timeupdate', onTimeUpdate)
    video.addEventListener('ended', onEnded)
    return () => {
      video.removeEventListener('loadedmetadata', onLoadedMetadata)
      video.removeEventListener('waiting', onWaiting)
      video.removeEventListener('playing', onPlaying)
      video.removeEventListener('error', onError)
      video.removeEventListener('timeupdate', onTimeUpdate)
      video.removeEventListener('ended', onEnded)
    }
  }, [exitSuggestion, playVideo, setPlayhead, setPlaying, setWaiting])

  useEffect(() => {
    if (!playing || waiting) return
    let frame = 0
    let last = performance.now()
    const tick = (now: number) => {
      const active = suggestionRef.current
      if (active) {
        const video = videoRef.current
        const current = video?.currentTime ?? active.start
        setSuggestionTime(current)
        if (current >= active.end - 0.03 || video?.ended) {
          exitSuggestion(true)
          return
        }
        frame = requestAnimationFrame(tick)
        return
      }

      const delta = (now - last) / 1000
      last = now
      const next = timeRef.current + delta
      if (next >= totalRef.current) {
        setPlayhead(totalRef.current, true)
        setPlaying(false)
        setWaiting(false)
        return
      }
      setPlayhead(next)
      frame = requestAnimationFrame(tick)
    }
    frame = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(frame)
  }, [exitSuggestion, playing, setPlayhead, setPlaying, setWaiting, suggestion, waiting])

  useEffect(() => {
    const video = videoRef.current
    if (!video) return
    if (playing) {
      if (video.getAttribute('src')) playVideo(video)
    } else {
      video.pause()
    }
  }, [playVideo, playing])

  useEffect(() => {
    if (suggestionRef.current) exitSuggestion(false)
    setPlaying(false)
    setWaiting(false)
    setPlaybackError(null)
    setPlayhead(Math.min(timeRef.current, timelineDuration(clips)), true)
  }, [clips, exitSuggestion, setPlayhead, setPlaying, setWaiting])

  const currentClip = findClipAt(clips, time)
  const hasVideo = clips.length > 0 || suggestion != null
  const shownTime = suggestion ? suggestionTime - suggestion.start : time
  const shownDuration = suggestion ? suggestion.end - suggestion.start : timelineDuration(clips)

  return (
    <Panel pad="sm">
      <div className="relative flex min-h-[240px] items-center justify-center overflow-hidden rounded-lg bg-black">
        {!hasVideo && (
          <div className="flex h-[240px] flex-col items-center justify-center gap-2 px-4 text-center text-ink-3">
            <FilmSlate size={30} />
            <span className="text-sm">从素材库添加片段开始剪辑</span>
          </div>
        )}
        <video
          ref={videoRef}
          playsInline
          preload="metadata"
          aria-label="剪辑预览"
          className={hasVideo ? 'max-h-[46vh] w-full rounded-lg object-contain' : 'hidden'}
        />
        {waiting && hasVideo && (
          <div className="pointer-events-none absolute inset-0 flex items-center justify-center bg-black/25 text-white">
            <span className="flex items-center gap-2 rounded-full bg-black/55 px-3 py-1.5 text-xs">
              <CircleNotch size={15} className="spin" /> 缓冲中
            </span>
          </div>
        )}
      </div>

      {suggestion && (
        <div className="mt-2 flex flex-wrap items-center gap-2 rounded-lg bg-accent-soft px-3 py-2 text-xs text-ink-2">
          <span className="min-w-0 flex-1 truncate">
            建议预览：{suggestion.title} · {formatTimecode(suggestion.start)}–{formatTimecode(suggestion.end)}
          </span>
          <Button size="sm" variant="ghost" icon={<X size={13} />} onClick={() => exitSuggestion(true)}>
            退出建议预览
          </Button>
        </div>
      )}

      <div className="mt-2.5 flex flex-wrap items-center gap-2 sm:gap-3">
        <Button
          size="sm"
          icon={<SkipBack size={14} />}
          onClick={() => seek(0)}
          disabled={clips.length === 0}
        >
          回开头
        </Button>
        <Button
          variant="primary"
          size="sm"
          icon={playing ? <Pause size={14} weight="fill" /> : <Play size={14} weight="fill" />}
          onClick={togglePlay}
          disabled={!hasVideo}
        >
          {playing ? '暂停' : '播放'}
        </Button>
        <span className="tnum text-sm text-ink-2">
          {formatTimecode(shownTime)} <span className="text-ink-4">/ {formatTimecode(shownDuration)}</span>
        </span>
        {suggestion ? (
          <span className="ml-auto hidden text-xs text-accent sm:block">源区间预览，不修改时间线</span>
        ) : currentClip ? (
          <span className="ml-auto hidden max-w-[40%] truncate text-xs text-ink-3 sm:block">
            当前片段：{currentClip.title} · {formatDuration(currentClip.trimEnd - currentClip.trimStart)}
          </span>
        ) : null}
      </div>

      {playbackError && (
        <div className="mt-2 flex flex-wrap items-center gap-2 text-[13px] text-danger">
          <span>{playbackError}</span>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => {
              setPlaybackError(null)
              if (suggestionRef.current) previewRange(suggestionRef.current)
              else {
                setPlayhead(timeRef.current, true)
                setPlaying(true)
              }
            }}
          >
            重试
          </Button>
        </div>
      )}
    </Panel>
  )
})

export default EditorPreview
