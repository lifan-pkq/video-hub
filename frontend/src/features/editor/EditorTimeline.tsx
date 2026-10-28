import { useEffect, useMemo, useRef, useState } from 'react'
import {
  ArrowClockwise,
  ArrowCounterClockwise,
  CaretLeft,
  CaretRight,
  Copy,
  Minus,
  Plus,
  Scissors,
  Trash,
} from '@phosphor-icons/react'
import { Timeline, type TimelineState } from '@xzdarcy/react-timeline-editor'
import type { TimelineRow } from '@xzdarcy/timeline-engine'
import '@xzdarcy/react-timeline-editor/dist/react-timeline-editor.css'
import { formatDuration } from '../../api'
import { Button, Input, Panel } from '../../ui'
import {
  formatTimecode,
  isValidRange,
  type DeleteSide,
  type EditorClip,
} from './model'

const EFFECTS = { clip: { id: 'clip', name: '片段' } }

interface Props {
  clips: EditorClip[]
  selectedId: string | null
  time: number
  canUndo: boolean
  canRedo: boolean
  onSelect: (id: string | null) => void
  onSeek: (time: number) => void
  onSplit: () => void
  onDeleteSide: (side: DeleteSide) => void
  onDelete: () => void
  onDuplicate: () => void
  onTrim: (id: string, start: number, end: number) => void
  onMove: (id: string, start: number) => void
  onUndo: () => void
  onRedo: () => void
  onClear: () => void
}

export default function EditorTimeline({
  clips,
  selectedId,
  time,
  canUndo,
  canRedo,
  onSelect,
  onSeek,
  onSplit,
  onDeleteSide,
  onDelete,
  onDuplicate,
  onTrim,
  onMove,
  onUndo,
  onRedo,
  onClear,
}: Props) {
  const timelineRef = useRef<TimelineState>(null)
  const clipsRef = useRef(clips)
  clipsRef.current = clips
  const selected = clips.find((clip) => clip.id === selectedId) ?? null
  const [zoom, setZoom] = useState(1)
  const [trimStart, setTrimStart] = useState('0.00')
  const [trimEnd, setTrimEnd] = useState('0.00')
  const [trimError, setTrimError] = useState<string | null>(null)
  const [moveRevision, setMoveRevision] = useState(0)

  useEffect(() => {
    timelineRef.current?.setTime(time)
  }, [time])

  useEffect(() => {
    setTrimStart(selected?.trimStart.toFixed(2) ?? '0.00')
    setTrimEnd(selected?.trimEnd.toFixed(2) ?? '0.00')
    setTrimError(null)
  }, [selected?.id, selected?.trimEnd, selected?.trimStart])

  const editorData: TimelineRow[] = useMemo(() => [{
    id: 'row-1',
    actions: clips.map((clip) => ({
      id: clip.id,
      start: clip.start,
      end: clip.end,
      effectId: 'clip',
      selected: clip.id === selectedId,
    })),
  }], [clips, moveRevision, selectedId])

  const applyTrim = () => {
    if (!selected) return
    const start = Number(trimStart)
    const end = Number(trimEnd)
    if (!isValidRange(start, end, selected.srcDuration)) {
      setTrimError(`需满足 0 ≤ 入点 < 出点 ≤ ${selected.srcDuration.toFixed(2)}，且时长至少 0.3 秒`)
      return
    }
    setTrimError(null)
    onTrim(selected.id, start, end)
  }

  return (
    <Panel pad="sm">
      <div className="editor-toolbar mb-3 px-1">
        <span className="mr-1 text-xs font-medium text-ink-3">
          时间线 · <span className="tnum">{clips.length}</span> 个片段
        </span>
        <div className="editor-tool-group" aria-label="历史操作">
          <Button size="sm" variant="ghost" icon={<ArrowCounterClockwise size={14} />} onClick={onUndo} disabled={!canUndo}>
            撤销
          </Button>
          <Button size="sm" variant="ghost" icon={<ArrowClockwise size={14} />} onClick={onRedo} disabled={!canRedo}>
            重做
          </Button>
        </div>
        <div className="editor-tool-group" aria-label="片段操作">
          <Button size="sm" icon={<Scissors size={14} />} onClick={onSplit} disabled={clips.length === 0}>
            截断 <kbd>S</kbd>
          </Button>
          <Button size="sm" icon={<CaretLeft size={13} />} onClick={() => onDeleteSide('left')} disabled={clips.length === 0}>
            删除左侧
          </Button>
          <Button size="sm" icon={<CaretRight size={13} />} onClick={() => onDeleteSide('right')} disabled={clips.length === 0}>
            删除右侧
          </Button>
          <Button size="sm" icon={<Copy size={14} />} onClick={onDuplicate} disabled={!selected}>
            复制
          </Button>
          <Button size="sm" variant="danger" icon={<Trash size={13} />} onClick={onDelete} disabled={!selected}>
            删除 <kbd>Del</kbd>
          </Button>
        </div>
        <div className="editor-tool-group" aria-label="时间线显示">
          <Button size="sm" variant="ghost" aria-label="缩小时间线" icon={<Minus size={13} />} onClick={() => setZoom((value) => Math.max(0.5, value - 0.25))} disabled={zoom <= 0.5} />
          <input
            aria-label="时间线缩放"
            type="range"
            min="0.5"
            max="4"
            step="0.25"
            value={zoom}
            onChange={(event) => setZoom(Number(event.target.value))}
            className="h-1.5 w-20 cursor-pointer accent-[var(--accent)] sm:w-24"
          />
          <Button size="sm" variant="ghost" aria-label="放大时间线" icon={<Plus size={13} />} onClick={() => setZoom((value) => Math.min(4, value + 0.25))} disabled={zoom >= 4} />
          <span className="tnum w-9 text-[11px] text-ink-3">{Math.round(zoom * 100)}%</span>
        </div>
        <Button size="sm" variant="text" onClick={onClear} disabled={clips.length === 0}>
          清空时间线
        </Button>
      </div>

      {selected && (
        <div className="mb-3 rounded-lg border border-line-soft bg-fill px-3 py-2.5">
          <div className="mb-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
            <span className="max-w-full truncate font-medium text-ink-2">{selected.title}</span>
            <span className="tnum text-ink-3">
              时间线 {formatTimecode(selected.start)}–{formatTimecode(selected.end)} · 源时长 {formatDuration(selected.srcDuration)}
            </span>
          </div>
          <div className="flex flex-wrap items-end gap-2">
            <label className="text-[11px] text-ink-3">
              <span className="mb-1 block">源入点（秒）</span>
              <Input
                aria-label="源入点（秒）"
                type="number"
                min={0}
                max={selected.srcDuration}
                step={0.01}
                value={trimStart}
                onChange={setTrimStart}
                className="w-[112px]"
              />
            </label>
            <label className="text-[11px] text-ink-3">
              <span className="mb-1 block">源出点（秒）</span>
              <Input
                aria-label="源出点（秒）"
                type="number"
                min={0}
                max={selected.srcDuration}
                step={0.01}
                value={trimEnd}
                onChange={setTrimEnd}
                className="w-[112px]"
              />
            </label>
            <Button size="sm" variant="primary" onClick={applyTrim} className="h-9">
              应用入出点
            </Button>
          </div>
          {trimError && <p className="mt-2 text-xs text-danger">{trimError}</p>}
        </div>
      )}

      <div className="timeline-wrap max-w-full overflow-hidden rounded-lg border border-line">
        <Timeline
          ref={timelineRef}
          style={{ width: '100%', height: 150 }}
          editorData={editorData}
          effects={EFFECTS}
          scale={2}
          scaleWidth={80 * zoom}
          scaleSplitCount={10}
          minScaleCount={20}
          rowHeight={44}
          startLeft={12}
          gridSnap
          dragLine
          autoScroll
          onCursorDrag={onSeek}
          onClickTimeArea={(value) => {
            onSeek(value)
            return false
          }}
          onActionMoveEnd={({ action, start }) => {
            onMove(action.id, start)
            setMoveRevision((value) => value + 1)
            return false
          }}
          onActionResizing={({ action, start, end, dir }) => {
            const clip = clipsRef.current.find((item) => item.id === action.id)
            if (!clip) return false
            const nextStart = dir === 'left' ? clip.trimStart + start - clip.start : clip.trimStart
            const nextEnd = dir === 'right' ? clip.trimEnd + end - clip.end : clip.trimEnd
            return isValidRange(nextStart, nextEnd, clip.srcDuration)
          }}
          onActionResizeEnd={({ action, start, end, dir }) => {
            const clip = clipsRef.current.find((item) => item.id === action.id)
            if (clip) {
              const nextStart = dir === 'left' ? clip.trimStart + start - clip.start : clip.trimStart
              const nextEnd = dir === 'right' ? clip.trimEnd + end - clip.end : clip.trimEnd
              onTrim(clip.id, nextStart, nextEnd)
            }
            return false
          }}
          onClickAction={(_event, { action }) => onSelect(action.id)}
          getActionRender={(action) => {
            const clip = clipsRef.current.find((item) => item.id === action.id)
            return (
              <div className="clip-action">
                <span className="clip-action-title">{clip?.title ?? ''}</span>
                <span className="clip-action-dur tnum">{formatDuration(action.end - action.start)}</span>
              </div>
            )
          }}
        />
      </div>
      <p className="mt-2 px-1 text-[11px] leading-relaxed text-ink-4">
        空格播放/暂停 · ←/→ 1 秒 · Shift+←/→ 5 秒 · Ctrl/Cmd+Z 撤销 · Ctrl/Cmd+Shift+Z 或 Ctrl/Cmd+Y 重做
      </p>
    </Panel>
  )
}
