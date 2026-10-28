import { useCallback, useEffect, useRef, useState, type SetStateAction } from 'react'
import { clipsEqual, pack, type EditorClip, type EditorDocument } from './model'

const HISTORY_LIMIT = 100

interface HistoryState {
  past: EditorDocument[]
  present: EditorDocument
  future: EditorDocument[]
}

function pushPast(current: HistoryState, present: EditorDocument): HistoryState {
  return {
    past: [...current.past, current.present].slice(-HISTORY_LIMIT),
    present,
    future: [],
  }
}

export function useEditorHistory(initial: EditorDocument) {
  const [history, setHistory] = useState<HistoryState>(() => ({
    past: [],
    present: { clips: pack(initial.clips), title: initial.title },
    future: [],
  }))

  const setClips = useCallback((action: SetStateAction<EditorClip[]>) => {
    setHistory((current) => {
      const candidate = typeof action === 'function' ? action(current.present.clips) : action
      const clips = pack(candidate)
      if (clipsEqual(clips, current.present.clips)) return current
      return pushPast(current, { ...current.present, clips })
    })
  }, [])

  const setTitle = useCallback((action: SetStateAction<string>) => {
    setHistory((current) => {
      const title = typeof action === 'function' ? action(current.present.title) : action
      if (title === current.present.title) return current
      return pushPast(current, { ...current.present, title })
    })
  }, [])

  const reset = useCallback((document: EditorDocument) => {
    setHistory({
      past: [],
      present: { clips: pack(document.clips), title: document.title },
      future: [],
    })
  }, [])

  const undo = useCallback(() => {
    setHistory((current) => {
      if (current.past.length === 0) return current
      const previous = current.past[current.past.length - 1]
      return {
        past: current.past.slice(0, -1),
        present: previous,
        future: [current.present, ...current.future],
      }
    })
  }, [])

  const redo = useCallback(() => {
    setHistory((current) => {
      if (current.future.length === 0) return current
      const next = current.future[0]
      return {
        past: [...current.past, current.present].slice(-HISTORY_LIMIT),
        present: next,
        future: current.future.slice(1),
      }
    })
  }, [])

  return {
    clips: history.present.clips,
    title: history.present.title,
    setClips,
    setTitle,
    reset,
    undo,
    redo,
    canUndo: history.past.length > 0,
    canRedo: history.future.length > 0,
  }
}

interface EditorShortcuts {
  enabled: boolean
  onSplit: () => void
  onDelete: () => void
  onTogglePlay: () => void
  onSeek: (delta: number) => void
  onUndo: () => void
  onRedo: () => void
}

export function useEditorShortcuts(shortcuts: EditorShortcuts) {
  const latest = useRef(shortcuts)
  latest.current = shortcuts

  useEffect(() => {
    if (!shortcuts.enabled) return
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.defaultPrevented) return
      const target = event.target
      if (
        target instanceof HTMLElement &&
        (target.isContentEditable ||
          target.tagName === 'INPUT' ||
          target.tagName === 'TEXTAREA' ||
          target.tagName === 'SELECT' ||
          target.closest('[contenteditable="true"]'))
      ) {
        return
      }

      const key = event.key.toLowerCase()
      const actions = latest.current
      if (event.ctrlKey || event.metaKey) {
        if (key === 'z') {
          event.preventDefault()
          if (event.shiftKey) actions.onRedo()
          else actions.onUndo()
        } else if (key === 'y') {
          event.preventDefault()
          actions.onRedo()
        }
        return
      }
      if (event.altKey) return

      if (key === 's') {
        event.preventDefault()
        actions.onSplit()
      } else if (event.key === 'Delete' || event.key === 'Backspace') {
        event.preventDefault()
        actions.onDelete()
      } else if (event.key === ' ') {
        event.preventDefault()
        actions.onTogglePlay()
      } else if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
        event.preventDefault()
        const direction = event.key === 'ArrowLeft' ? -1 : 1
        actions.onSeek(direction * (event.shiftKey ? 5 : 1))
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [shortcuts.enabled])
}
