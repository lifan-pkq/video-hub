import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { DownloadSimple, ImageSquare, Sparkle, Trash, UploadSimple } from '@phosphor-icons/react'
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
import RequestState from '../ui/RequestState'

export default function ImagesPage() {
  const [images, setImages] = useState<AIImageAsset[]>([])
  const [loading, setLoading] = useState(true)
  const [hasData, setHasData] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [uploading, setUploading] = useState(false)
  const [over, setOver] = useState(false)
  const [search, setSearch] = useState('')
  const [preview, setPreview] = useState<AIImageAsset | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setImages(await fetchAIImages())
      setHasData(true)
      setLoadError(null)
    } catch (error) {
      setLoadError(errorMessage(error))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load() }, [load])

  const upload = async (files: FileList | File[] | null) => {
    const list = files ? Array.from(files) : []
    if (!list.length) return
    setUploading(true)
    try {
      for (const file of list) await uploadAIImage(file, file.name)
      await load()
      toast.success('图片已加入图片素材')
    } catch (error) {
      toast.error('上传失败：' + errorMessage(error))
    } finally {
      setUploading(false)
    }
  }

  const remove = async (image: AIImageAsset) => {
    const ok = await confirmDialog({ title: '删除图片素材？', content: '只删除这张图片，不影响已经生成的视频。', okText: '删除', danger: true })
    if (!ok) return
    try {
      await deleteAIImage(image.id)
      setImages((items) => items.filter((item) => item.id !== image.id))
      setPreview(null)
      toast.success('已删除')
    } catch (error) {
      toast.error('删除失败：' + errorMessage(error))
    }
  }

  const filtered = useMemo(() => {
    const keyword = search.trim().toLowerCase()
    if (!keyword) return images
    return images.filter((image) => `${image.title} ${image.filename} ${image.prompt || ''}`.toLowerCase().includes(keyword))
  }, [images, search])

  return (
    <section aria-label="图片素材库">
      <PageHeader
        title="图片素材"
        desc="生成图和上传的参考图都在这里。选中后可以带到 AI 素材页当参考。"
        extra={
          <>
            <Link to="/ai-media"><Button variant="ghost" icon={<Sparkle size={15} />}>去生成</Button></Link>
            <Button icon={<UploadSimple size={15} />} loading={uploading} onClick={() => inputRef.current?.click()}>上传图片</Button>
            <input ref={inputRef} type="file" accept="image/*" multiple className="hidden" onChange={(event) => { void upload(event.target.files); event.currentTarget.value = '' }} />
          </>
        }
      />
      <RequestState error={loadError} hasData={hasData} loading={loading} onRetry={load} className="mb-4" />
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <Input value={search} onChange={setSearch} placeholder="搜索标题或提示词" allowClear className="max-w-xs" />
        <span className="text-xs text-ink-4">共 {images.length} 张</span>
      </div>
      <div
        className={'mb-5 rounded-2xl border border-dashed px-4 py-6 text-center transition-colors ' + (over ? 'border-accent bg-accent-soft' : 'border-line bg-fill')}
        onDragOver={(event) => { event.preventDefault(); setOver(true) }}
        onDragLeave={() => setOver(false)}
        onDrop={(event) => { event.preventDefault(); setOver(false); void upload(event.dataTransfer.files) }}
      >
        <UploadSimple size={28} className="mx-auto text-accent" />
        <div className="mt-2 text-[12px] text-ink-2">拖拽图片到这里上传</div>
        <div className="mt-1 text-[11px] text-ink-4">PNG / JPG / WebP，单张不超过 20MB</div>
      </div>
      <Panel pad="none">
        {loading && !hasData ? (
          <div className="grid grid-cols-2 gap-3 p-4 sm:grid-cols-4 lg:grid-cols-6">
            {Array.from({ length: 6 }).map((_, index) => <div key={index} className="skeleton aspect-square rounded-xl" />)}
          </div>
        ) : !hasData ? null : filtered.length === 0 ? (
          <EmptyState
            icon={<ImageSquare size={26} />}
            title={images.length === 0 ? '还没有图片素材' : '没有匹配的图片'}
            desc={images.length === 0 ? '先去 AI 素材生成一张并保存，或直接上传参考图。' : '换个关键词，或清空搜索。'}
            action={images.length === 0 ? <Link to="/ai-media"><Button size="sm">去生成图片</Button></Link> : <Button size="sm" variant="ghost" onClick={() => setSearch('')}>清空搜索</Button>}
          />
        ) : (
          <div className="grid grid-cols-2 gap-3 p-4 sm:grid-cols-4 lg:grid-cols-6">
            {filtered.map((image) => (
              <div key={image.id} className="group relative overflow-hidden rounded-xl border border-line-soft bg-surface">
                <button type="button" className="block aspect-square w-full" onClick={() => setPreview(image)}>
                  <img src={image.url} alt={image.title || '图片素材'} className="h-full w-full object-cover transition-transform duration-300 group-hover:scale-[1.03]" />
                </button>
                <div className="px-2.5 py-2">
                  <div className="truncate text-[11px] text-ink-2">{image.title || '图片素材'}</div>
                  <div className="mt-0.5 text-[10px] text-ink-4">{formatSize(image.size)}</div>
                </div>
              </div>
            ))}
          </div>
        )}
      </Panel>
      <Modal
        open={Boolean(preview)}
        onClose={() => setPreview(null)}
        title={preview?.title || '图片素材'}
        width={900}
        footer={preview ? (
          <div className="flex flex-wrap justify-end gap-2">
            <Link to={`/ai-media?image=${preview.id}`}><Button variant="primary" icon={<Sparkle size={14} />}>用作参考</Button></Link>
            <a href={preview.download_url} download><Button icon={<DownloadSimple size={14} />}>下载</Button></a>
            <Button variant="ghost" icon={<Trash size={14} />} onClick={() => void remove(preview)}>删除</Button>
          </div>
        ) : null}
      >
        {preview && (
          <div>
            <img src={preview.url} alt={preview.title || '图片素材'} className="max-h-[72vh] w-full rounded-lg object-contain" />
            {preview.prompt ? <p className="mt-3 text-[12px] leading-5 text-ink-3">{preview.prompt}</p> : null}
          </div>
        )}
      </Modal>
    </section>
  )
}
