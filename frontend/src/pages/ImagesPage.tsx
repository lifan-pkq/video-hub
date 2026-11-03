import { useEffect, useMemo, useRef, useState } from 'react'
import { DownloadSimple, Images, Sparkle, Trash, UploadSimple } from '@phosphor-icons/react'
import { Link } from 'react-router-dom'
import {
  deleteAIImage,
  errorMessage,
  fetchAIImages,
  formatSize,
  uploadAIImage,
  type AIImageAsset,
} from '../api'
import { Button, EmptyState, Input, Modal, PageHeader, Panel, confirmDialog, toast } from '../ui'

export default function ImagesPage() {
  const [images, setImages] = useState<AIImageAsset[]>([])
  const [loading, setLoading] = useState(true)
  const [uploading, setUploading] = useState(false)
  const [over, setOver] = useState(false)
  const [search, setSearch] = useState('')
  const [preview, setPreview] = useState<AIImageAsset | null>(null)
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

  useEffect(() => { void load() }, [])

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

  const remove = async (image: AIImageAsset) => {
    const ok = await confirmDialog({ title: '删除图片素材？', content: '只删除图片文件，不影响已生成的视频。', okText: '删除' })
    if (!ok) return
    try {
      await deleteAIImage(image.id)
      setImages((items) => items.filter((item) => item.id !== image.id))
      setPreview(null)
    } catch (error) {
      toast.error('删除失败：' + errorMessage(error))
    }
  }

  const filtered = useMemo(() => {
    const keyword = search.trim().toLowerCase()
    return keyword ? images.filter((image) => (image.title + ' ' + image.filename).toLowerCase().includes(keyword)) : images
  }, [images, search])

  return (
    <section aria-label="图片素材库">
      <PageHeader
        title="图片素材"
        desc="AI 生成图片、手动上传的参考图。这里保存的图片可以被 AI 生成视频时直接引用。"
        extra={
          <>
            <Link to="/ai-media"><Button variant="ghost" icon={<Sparkle size={15} />}>去生成</Button></Link>
            <Button icon={<UploadSimple size={15} />} loading={uploading} onClick={() => inputRef.current?.click()}>上传图片</Button>
            <input ref={inputRef} type="file" accept="image/*" multiple className="hidden" onChange={(event) => { void upload(event.target.files); event.currentTarget.value = '' }} />
          </>
        }
      />

      <div className="mb-4 flex flex-wrap items-center gap-3">
        <Input value={search} onChange={setSearch} placeholder="搜索图片标题" allowClear className="max-w-xs" />
        <span className="text-xs text-ink-4">共 {images.length} 张图片</span>
      </div>

      <div
        className={'mb-5 rounded-2xl border border-dashed px-4 py-6 text-center transition-colors ' + (over ? 'border-accent bg-accent-soft' : 'border-line bg-fill')}
        onDragOver={(event) => { event.preventDefault(); setOver(true) }}
        onDragLeave={() => setOver(false)}
        onDrop={(event) => { event.preventDefault(); setOver(false); void upload(event.dataTransfer.files) }}
      >
        <UploadSimple size={28} className="mx-auto text-accent" />
        <div className="mt-2 text-[12px] text-ink-2">拖拽图片到这里上传</div>
        <div className="mt-1 text-[11px] text-ink-4">支持 PNG / JPG / WebP，上传后可被视频生成引用</div>
      </div>

      <Panel pad="none">
        {loading ? (
          <div className="grid grid-cols-2 gap-3 p-4 sm:grid-cols-4 lg:grid-cols-6">
            {Array.from({ length: 6 }).map((_, index) => <div key={index} className="skeleton aspect-square rounded-xl" />)}
          </div>
        ) : filtered.length === 0 ? (
          <EmptyState
            icon={<Images size={26} />}
            title={images.length === 0 ? '还没有图片素材' : '没有匹配的图片'}
            desc={images.length === 0 ? '在 AI 素材生成图片后点击保存，或直接上传图片。' : '换个关键词试试。'}
            action={<Link to="/ai-media"><Button size="sm">去生成图片</Button></Link>}
          />
        ) : (
          <div className="grid grid-cols-2 gap-3 p-4 sm:grid-cols-4 lg:grid-cols-6">
            {filtered.map((image) => (
              <div key={image.id} className="group relative overflow-hidden rounded-xl border border-line-soft bg-surface">
                <button type="button" className="block aspect-square w-full" onClick={() => setPreview(image)}>
                  <img src={image.url} alt={image.title} className="h-full w-full object-cover transition-transform duration-300 group-hover:scale-[1.03]" />
                </button>
                <div className="px-2.5 py-2">
                  <div className="truncate text-[11px] text-ink-2">{image.title || '图片素材'}</div>
                  <div className="mt-0.5 text-[10px] text-ink-4">{formatSize(image.size)}</div>
                </div>
                <div className="absolute right-2 top-2 flex gap-1 opacity-0 transition-opacity group-hover:opacity-100">
                  <a href={image.download_url} download className="flex h-7 w-7 items-center justify-center rounded-lg bg-black/60 text-white" title="下载"><DownloadSimple size={14} /></a>
                  <button type="button" className="flex h-7 w-7 items-center justify-center rounded-lg bg-black/60 text-white" title="删除" onClick={() => void remove(image)}><Trash size={14} /></button>
                </div>
              </div>
            ))}
          </div>
        )}
      </Panel>

      <Modal open={Boolean(preview)} onClose={() => setPreview(null)} title={preview?.title || '图片素材'} width={900}
        footer={preview ? <div className="flex justify-end gap-2"><a href={preview.download_url} download><Button>下载</Button></a><Button variant="ghost" onClick={() => void remove(preview)}>删除</Button></div> : null}>
        {preview && <img src={preview.url} alt={preview.title} className="max-h-[72vh] w-full rounded-lg object-contain" />}
      </Modal>
    </section>
  )
}
