import { useCallback, useRef, useState } from 'react'
import type { EditDoc } from './model'

const EMPTY: EditDoc = { title: '未命名成片', clips: [], decisions: {} }

export function useHistory(initial: EditDoc = EMPTY) {
  const [doc, setDoc] = useState<EditDoc>(initial)
  const [counts, setCounts] = useState({ undo: 0, redo: 0 })
  const past = useRef<EditDoc[]>([])
  const future = useRef<EditDoc[]>([])
  const present = useRef(doc)
  present.current = doc

  const commit = useCallback((next: EditDoc) => {
    past.current.push(structuredClone(present.current))
    if (past.current.length > 50) past.current.shift()
    future.current = []
    setDoc(next)
    setCounts({ undo: past.current.length, redo: 0 })
  }, [])

  const undo = useCallback(() => {
    const previous = past.current.pop()
    if (!previous) return
    future.current.push(structuredClone(present.current))
    setDoc(previous)
    setCounts({ undo: past.current.length, redo: future.current.length })
  }, [])

  const redo = useCallback(() => {
    const next = future.current.pop()
    if (!next) return
    past.current.push(structuredClone(present.current))
    setDoc(next)
    setCounts({ undo: past.current.length, redo: future.current.length })
  }, [])

  const reset = useCallback((next: EditDoc) => {
    past.current = []
    future.current = []
    setDoc(next)
    setCounts({ undo: 0, redo: 0 })
  }, [])

  const replace = useCallback((next: EditDoc) => {
    setDoc(next)
  }, [])

  return { doc, commit, undo, redo, reset, replace, canUndo: counts.undo > 0, canRedo: counts.redo > 0 }
}
