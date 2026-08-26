import { useRef, useState, type DragEvent } from 'react'
import { UploadSimple } from '@phosphor-icons/react'
import Progress from './Progress'

interface Props {
  accept?: string
  disabled?: boolean
  uploading?: boolean
  /** 0~100 */
  progress?: number
  tip?: string
  onFile: (file: File) => void
}

/** 拖拽上传区 */
export default function Dropzone({ accept, disabled, uploading, progress, tip, onFile }: Props) {
  const [over, setOver] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  const onDrop = (e: DragEvent) => {
    e.preventDefault()
    setOver(false)
    if (disabled || uploading) return
    const f = e.dataTransfer.files?.[0]
    if (f) onFile(f)
  }

  return (
    <div>
      <div
        role="button"
        tabIndex={0}
        aria-label="上传文件"
        onClick={() => !disabled && !uploading && inputRef.current?.click()}
        onKeyDown={(e) => e.key === 'Enter' && inputRef.current?.click()}
        onDragOver={(e) => {
          e.preventDefault()
          if (!disabled && !uploading) setOver(true)
        }}
        onDragLeave={() => setOver(false)}
        onDrop={onDrop}
        className={`flex cursor-pointer flex-col items-center justify-center rounded-[10px] border border-dashed px-4 py-8 text-center transition-colors ${
          over ? 'border-accent bg-accent-soft' : 'border-line bg-fill hover:border-accent/50'
        } ${disabled || uploading ? 'cursor-not-allowed opacity-60' : ''}`}
      >
        <UploadSimple size={30} className="text-accent" />
        <div className="mt-2 text-[13px] text-ink-2">{uploading ? '上传中…' : '点击或拖拽视频文件到此区域上传'}</div>
        {tip && <div className="mt-1 text-xs text-ink-4">{tip}</div>}
        <input
          ref={inputRef}
          type="file"
          accept={accept}
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0]
            if (f) onFile(f)
            e.target.value = ''
          }}
        />
      </div>
      {uploading && typeof progress === 'number' && (
        <div className="mt-3 flex items-center gap-3">
          <Progress percent={progress / 100} className="flex-1" />
          <span className="tnum text-xs text-ink-3">{Math.round(progress)}%</span>
        </div>
      )}
    </div>
  )
}
