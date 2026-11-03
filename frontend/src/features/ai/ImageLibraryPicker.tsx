import { useEffect, useRef, useState } from 'react'
import { Check, Images, UploadSimple } from '@phosphor-icons/react'
import { fetchAIImages, uploadAIImage, type AIImageAsset, errorMessage } from '../../api'
import { Button, EmptyState, Modal, toast } from '../../ui'

interface Props {
  open: boolean
  onClose: () => void
  selectedUrls: string[]
  max: number
  onPick: (asset: AIImageAsset) => void
}

export default function ImageLibraryPicker({ open, onClose, selectedUrls, max, onPick }: Props) {
  const [images, setImages] = useState<AIImageAsset[]>([])
  const [loading, setLoading] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [over, setOver] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  const load = async () => {
    setLoading(true)
    try {
      setImages(await fetchAIImages())
    } catch (error) {
      toast.error('读取图片素材失败：' + errorMessage(error))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (open) void load()
  }, [open])

  const upload = async (files: FileList | null) => {
    if (!files?.length) return
    setUploading(true)
    try {
      for (const file of Array.from(files)) {
        await uploadAIImage(file, file.name)
      }
      await load()
      toast.success('图片已加入图片素材')
    } catch (error) {
      toast.error('上传失败：' + errorMessage(error))
    } finally {
      setUploading(false)
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="从图片素材选择"
      width={820}
      footer={
        <div className="flex items-center justify-between gap-3">
          <span className="text-[11px] text-ink-4">已选 {selectedUrls.length} / {max} 张</span>
          <Button onClick={onClose}>完成</Button>
        </div>
      }
    >
      <div
        className="rounded-xl border border-dashed border-line p-3"
        onDragOver={(event) => { event.preventDefault(); setOver(true) }}
        onDragLeave={() => setOver(false)}
        onDrop={(event) => { event.preventDefault(); setOver(false); void upload(event.dataTransfer.files) }}
      >
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2 text-[11px] text-ink-3">
            <UploadSimple size={16} className="text-accent" />
            {over ? '松开上传到图片素材' : '拖拽图片到这里上传，或点击选择'}
          </div>
          <Button size="sm" variant="ghost" loading={uploading} onClick={() => inputRef.current?.click()}>上传图片</Button>
          <input ref={inputRef} type="file" accept="image/*" multiple className="hidden" onChange={(event) => { void upload(event.target.files); event.currentTarget.value = '' }} />
        </div>
      </div>

      <div className="mt-4 max-h-[55vh] overflow-y-auto pr-1">
        {loading ? (
          <p className="py-8 text-center text-[12px] text-ink-4">加载中…</p>
        ) : images.length === 0 ? (
          <EmptyState icon={<Images size={26} />} title="还没有图片素材" desc="先生成图片并保存到图片素材，或直接上传图片。" />
        ) : (
          <div className="grid grid-cols-3 gap-3 sm:grid-cols-4">
            {images.map((image) => {
              const selected = selectedUrls.includes(image.public_url)
              return (
                <button
                  key={image.id}
                  type="button"
                  className={'group relative overflow-hidden rounded-xl border text-left transition-colors ' + (selected ? 'border-accent ring-2 ring-accent' : 'border-line-soft hover:border-accent/60')}
                  onClick={() => onPick(image)}
                  title={image.title}
                >
                  <img src={image.url} alt={image.title} className="aspect-square w-full object-cover" />
                  {selected && <span className="absolute right-2 top-2 flex h-6 w-6 items-center justify-center rounded-full bg-accent text-white"><Check size={14} weight="bold" /></span>}
                  <div className="truncate bg-surface px-2 py-1.5 text-[10px] text-ink-3">{image.title || '图片素材'}</div>
                </button>
              )
            })}
          </div>
        )}
      </div>
    </Modal>
  )
}
