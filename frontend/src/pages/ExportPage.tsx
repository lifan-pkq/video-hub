import { useEffect, useState } from 'react'
import { Link, useLocation, useSearchParams } from 'react-router-dom'
import { DownloadSimple, Package, VideoCamera } from '@phosphor-icons/react'
import { errorMessage, fetchVideos, formatSize, type Video } from '../api'
import { cancelExport, createExport, exportFileUrl, getExport, selectExport, type ExportManifest } from '../exports'
import { Alert, Button, EmptyState, Input, PageHeader, Panel, Progress, Select, toast } from '../ui'

const EXPORT_POLL_INTERVAL = 3000

export default function ExportPage() {
  const [searchParams] = useSearchParams()
  const location = useLocation()
  const [videos, setVideos] = useState<Video[]>([])
  const [videosReady, setVideosReady] = useState(false)
  const [videoId, setVideoId] = useState('')
  const [manifest, setManifest] = useState<ExportManifest | null>(null)
  const [task, setTask] = useState<ExportManifest | null>(null)
  const [exportId, setExportId] = useState('')
  const [selectedTitle, setSelectedTitle] = useState('')

  useEffect(() => {
    const stateId = (location.state as { videoId?: number } | null)?.videoId
    void fetchVideos().then((rows) => {
      setVideos(rows)
      const requested = searchParams.get('video') || (stateId ? String(stateId) : '')
      const matched = requested && rows.some((item) => item.id === Number(requested))
      setVideoId(matched ? requested : '')
      setVideosReady(true)
    }).catch((error) => {
      setVideosReady(true)
      toast.error('视频库加载失败：' + errorMessage(error))
    })
  }, [searchParams, location.state])

  // 导出在服务端后台进行：拿到 export_id 后轮询到终态，成功才展示下载 UI。
  useEffect(() => {
    if (!exportId) return
    let disposed = false
    let timer = 0
    const tick = async () => {
      try {
        const result = await getExport(exportId)
        if (disposed) return
        setTask(result)
        if (result.status === 'success') {
          setManifest(result)
          if (result.selected_title) setSelectedTitle(result.selected_title)
          toast.success('整套成片已生成')
        } else if (result.status === 'failed') {
          toast.error('导出失败：' + (result.error || '未知错误'))
        } else if (result.status === 'cancelled') {
          toast.success('导出已取消')
        } else {
          timer = window.setTimeout(tick, EXPORT_POLL_INTERVAL)
        }
      } catch {
        if (disposed) return
        timer = window.setTimeout(tick, EXPORT_POLL_INTERVAL * 2)
      }
    }
    void tick()
    return () => {
      disposed = true
      window.clearTimeout(timer)
    }
  }, [exportId])

  const create = async () => {
    if (!videoId) return
    setManifest(null)
    setTask(null)
    setExportId('')
    try {
      const result = await createExport(Number(videoId))
      setTask(result)
      setExportId(result.export_id)
    } catch (error) {
      toast.error('导出失败：' + errorMessage(error))
    }
  }

  const choose = async (cover: string, title = selectedTitle) => {
    if (!manifest) return
    try {
      setManifest(await selectExport(manifest.export_id, cover, title))
    } catch (error) {
      toast.error('保存选择失败：' + errorMessage(error))
    }
  }

  const running = task?.status === 'running'
  const fileEntries = manifest ? Object.entries({
    landscape: '横屏 16:9',
    portrait: '主体跟拍竖屏 9:16',
    square: '方形 1:1',
    without_subtitles: '无字幕母版',
    srt: 'SRT 字幕',
    titles: '标题与简介 JSON',
  }).filter(([key]) => Boolean(manifest.files[key as keyof ExportManifest['files']])) : []

  return (
    <section>
      <PageHeader title="封面与导出" desc="选定一条已入库视频，生成横屏、竖屏、方形、字幕和封面，再下载整套 ZIP。" />
      {videosReady && videos.length === 0 ? (
        <Panel>
          <EmptyState
            icon={<VideoCamera size={26} />}
            title="还没有可导出的视频"
            desc="先把成片放进素材库。下载或剪辑入库之后，再回到这里打包。"
            action={<Link to="/videos"><Button size="sm">去素材库</Button></Link>}
          />
        </Panel>
      ) : (
      <Panel>
        <div className="flex flex-wrap items-end gap-3">
          <label className="min-w-0 flex-1">
            <span className="mb-1.5 block text-xs text-ink-3">选择成片</span>
            <Select value={videoId} onChange={setVideoId} options={[{ value: '', label: videos.length ? '请选择一条视频' : '正在读取素材库…' }, ...videos.map((video) => ({ value: String(video.id), label: video.title }))]} />
          </label>
          <Button variant="primary" icon={<Package size={16} />} loading={running} disabled={!videoId} onClick={() => void create()}>生成整套成片</Button>
        </div>
        {!videoId && videos.length > 0 && <p className="mt-3 text-[12px] text-ink-4">还没有选中视频。从上面的列表选一条，或从素材库点「导出」进来。</p>}
      </Panel>
      )}
      {running ? (
        <Panel className="mt-5">
          <Alert type="info">导出已在后台进行{task?.label ? `：${task.label}` : ''}。可以离开本页，回来时仍能查到结果。</Alert>
          <div className="mt-4"><Progress percent={Math.max(0.04, Math.min(0.98, (task?.progress || 8) / 100))} /></div>
          <div className="mt-3">
            <Button size="sm" variant="ghost" onClick={() => { if (exportId) void cancelExport(exportId).then(setTask).catch((error) => toast.error(errorMessage(error))) }}>取消导出</Button>
          </div>
        </Panel>
      ) : task?.status === 'cancelled' ? (
        <Panel className="mt-5">
          <Alert type="warning">导出已取消。</Alert>
          <div className="mt-3"><Button size="sm" onClick={() => void create()}>重新生成</Button></div>
        </Panel>
      ) : task?.status === 'failed' ? (
        <Panel className="mt-5">
          <Alert type="error">导出失败：{task.error || '未知错误'}</Alert>
          <div className="mt-3"><Button size="sm" onClick={() => void create()}>再试一次</Button></div>
        </Panel>
      ) : !manifest ? (
        videosReady && videos.length === 0 ? null : (
          <Panel className="mt-5"><EmptyState icon={<VideoCamera size={26} />} title="还没有导出套装" desc={videoId ? '点「生成整套成片」。完成后可以在这里选封面，并下载 ZIP。' : '先选择一条视频。生成完成后可以选封面并下载 ZIP。'} /></Panel>
        )
      ) : (
        <div className="mt-5 space-y-5">
          <Alert type="success">导出完成 · ZIP {formatSize(manifest.archive_size || 0)}</Alert>
          <Panel title="选择封面" extra={<a href={manifest.download_url} className="inline-flex items-center gap-1 text-sm text-accent"><DownloadSimple size={15} />下载整套 ZIP</a>}>
            <div className="grid gap-4 md:grid-cols-3">
              {(manifest.files.covers || []).map((cover) => (
                <button key={cover} type="button" onClick={() => void choose(cover)} className={`rounded-xl border p-2 text-left ${manifest.selected_cover === cover ? 'border-accent' : 'border-line-soft'}`}>
                  <img src={exportFileUrl(manifest.export_id, cover)} alt="候选封面" className="aspect-video w-full rounded-lg object-cover" />
                  <span className="mt-2 block text-xs text-ink-3">{manifest.selected_cover === cover ? '当前封面' : '使用这张'}</span>
                </button>
              ))}
            </div>
          </Panel>
          <Panel title="标题与简介">
            <div className="grid gap-3 md:grid-cols-3">
              {(manifest.title_options || []).map((item) => (
                <button key={item.kind} type="button" onClick={() => { setSelectedTitle(item.title); void choose(manifest.selected_cover, item.title) }} className={`rounded-xl border p-3 text-left ${selectedTitle === item.title ? 'border-accent bg-accent-soft' : 'border-line-soft bg-fill'}`}>
                  <strong className="text-[13px] text-ink-1">{item.title}</strong>
                  <p className="mt-2 text-[11px] leading-5 text-ink-4">{item.description}</p>
                </button>
              ))}
            </div>
            <label className="mt-4 block"><span className="mb-1.5 block text-xs text-ink-3">最终标题</span><Input value={selectedTitle} onChange={setSelectedTitle} onBlur={() => void choose(manifest.selected_cover)} /></label>
          </Panel>
          <Panel title="套装内容">
            <div className="grid gap-2 text-[12px] text-ink-3 sm:grid-cols-2 lg:grid-cols-3">
              {fileEntries.map(([key, label]) => (
                <a key={key} href={exportFileUrl(manifest.export_id, manifest.files[key as keyof ExportManifest['files']] as string)} className="rounded-lg border border-line-soft bg-fill px-3 py-2.5 hover:border-accent">{label} · 下载</a>
              ))}
            </div>
            {!manifest.files.without_subtitles && manifest.without_subtitles_note ? (
              <p className="mt-3 text-[12px] text-ink-4">{manifest.without_subtitles_note}</p>
            ) : null}
          </Panel>
        </div>
      )}
    </section>
  )
}
