import { useCallback, useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { DownloadSimple, FilmSlate, MagicWand, Package, VideoCamera } from '@phosphor-icons/react'
import { errorMessage, fetchVideos, formatDuration, formatRelative, type Video } from '../api'
import { Button, EmptyState, PageHeader, Panel } from '../ui'
import RequestState from '../ui/RequestState'

export default function WorkbenchPage() {
  const navigate = useNavigate()
  const [videos, setVideos] = useState<Video[]>([])
  const [loading, setLoading] = useState(true)
  const [hasData, setHasData] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setVideos(await fetchVideos())
      setHasData(true)
      setError(null)
    } catch (cause) {
      setError(errorMessage(cause))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load() }, [load])
  const recent = videos.slice(0, 12)

  return (
    <div>
      <PageHeader
        title="创作工作台"
        desc="从最近的素材直接进入剪辑、解说或导出。这里不再重复概览页。"
        extra={<Link to="/videos"><Button variant="ghost">全部素材</Button></Link>}
      />
      <RequestState error={error} hasData={hasData} loading={loading} onRetry={load} className="mb-4" />
      <div className="mb-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Link to="/edit" className="vh-card flex items-center gap-3 p-4 hover:border-accent"><FilmSlate size={20} /><span><strong className="block text-sm text-ink-1">智能剪辑</strong><small className="text-ink-3">一键生成抖音竖屏短视频</small></span></Link>
        <Link to="/narrate" className="vh-card flex items-center gap-3 p-4 hover:border-accent"><MagicWand size={20} /><span><strong className="block text-sm text-ink-1">AI 解说</strong><small className="text-ink-3">理解、文稿、审阅、成片</small></span></Link>
        <Link to="/export" className="vh-card flex items-center gap-3 p-4 hover:border-accent"><Package size={20} /><span><strong className="block text-sm text-ink-1">封面与导出</strong><small className="text-ink-3">给成片做封面和套装</small></span></Link>
        <Link to="/download" className="vh-card flex items-center gap-3 p-4 hover:border-accent"><DownloadSimple size={20} /><span><strong className="block text-sm text-ink-1">下载素材</strong><small className="text-ink-3">链接解析后进入素材库</small></span></Link>
      </div>
      <Panel title="最近素材">
        {recent.length === 0 ? (
          <EmptyState icon={<VideoCamera size={24} />} title="还没有视频" desc="下载、录制或上传一条原片，就可以从这里进剪辑或解说。" action={<Link to="/videos"><Button size="sm">去素材库</Button></Link>} />
        ) : (
          <ul className="divide-y divide-line-soft">
            {recent.map((video) => (
              <li key={video.id} className="flex flex-col gap-3 py-3 sm:flex-row sm:items-center">
                <div className="min-w-0 flex-1">
                  <p className="truncate text-[14px] text-ink-1">{video.title}</p>
                  <p className="text-[11px] text-ink-4">{formatDuration(video.duration)} · {formatRelative(video.created_at)}</p>
                </div>
                <div className="flex flex-wrap gap-2">
                  <Button size="sm" variant="secondary" onClick={() => navigate('/edit', { state: { videoIds: [video.id] } })}>智能剪辑</Button>
                  <Button size="sm" variant="secondary" onClick={() => navigate(`/narrate?video=${video.id}`)}>AI 解说</Button>
                  <Link to={`/export?video=${video.id}`}><Button size="sm" variant="ghost">导出</Button></Link>
                </div>
              </li>
            ))}
          </ul>
        )}
      </Panel>
    </div>
  )
}
