import { useCallback, useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { ArrowLeft, ArrowClockwise, DownloadSimple, Play, Stop, Trash } from '@phosphor-icons/react'
import {
  cancelDramaProject,
  deleteDramaProject,
  errorMessage,
  fetchDramaProject,
  formatDuration,
  generateDramaStoryboard,
  generateShotStoryboard,
  resumeDramaProject,
  retryDramaShot,
  startDramaProject,
  type DramaProject,
  type DramaShot,
  type DramaShotBoard,
  type DramaShotRetryStage,
} from '../api'
import { Alert, Button, Panel, Progress, toast } from '../ui'
import { StatusTag } from './AIDramaPage'

/**
 * AI 短剧结果页：全屏展示，不带全局左侧导航。
 * 路由 /ai-drama/:projectId，左上角「返回」回到 /ai-drama。
 * 每个镜头都完整展示：图片提示词 + 首帧图、视频提示词 + 视频，
 * 视频失败可单镜重试，所有镜头、每集、整个项目都可以重试。
 */
export default function AIDramaResultPage() {
  const { projectId = '' } = useParams()
  const navigate = useNavigate()
  const [detail, setDetail] = useState<DramaProject | null>(null)
  const [loadError, setLoadError] = useState('')
  const [loading, setLoading] = useState(true)
  const [retrying, setRetrying] = useState<Record<string, boolean>>({})

  const goBack = useCallback(() => navigate('/ai-drama'), [navigate])

  const loadDetail = useCallback(async () => {
    if (!projectId) return
    try {
      setDetail(await fetchDramaProject(projectId))
      setLoadError('')
    } catch (error) {
      setLoadError(errorMessage(error))
    } finally {
      setLoading(false)
    }
  }, [projectId])

  useEffect(() => {
    setLoading(true)
    setDetail(null)
    setLoadError('')
    loadDetail()
    const timer = window.setInterval(() => {
      loadDetail()
    }, 4000)
    return () => window.clearInterval(timer)
  }, [projectId, loadDetail])

  const action = async (kind: 'resume' | 'cancel' | 'delete' | 'start') => {
    if (!detail) return
    try {
      if (kind === 'start') {
        await startDramaProject(detail.id)
        toast.success('已开始生成')
      } else if (kind === 'resume') {
        await resumeDramaProject(detail.id)
        toast.success('已从断点继续生成')
      } else if (kind === 'cancel') {
        await cancelDramaProject(detail.id)
        toast.success('已请求取消，产物保留可续跑')
      } else {
        if (!window.confirm('确定删除该项目？项目目录与成片都会被删除。')) return
        await deleteDramaProject(detail.id)
        toast.success('项目已删除')
        goBack()
        return
      }
      loadDetail()
    } catch (error) {
      toast.error(errorMessage(error))
    }
  }

  const retryShot = async (shot: DramaShot, stage: DramaShotRetryStage) => {
    if (!detail) return
    const key = `${shot.id}:${stage}`
    setRetrying((prev) => ({ ...prev, [key]: true }))
    try {
      await retryDramaShot(detail.id, shot.id, stage)
      toast.success(`镜头 ${shot.index} 已开始${stage === 'image' ? '重做图片' : stage === 'video' ? '重试视频' : '全部重做'}`)
      loadDetail()
    } catch (error) {
      toast.error(errorMessage(error))
    } finally {
      setRetrying((prev) => ({ ...prev, [key]: false }))
    }
  }

  // 给整个项目补生成缺失的连环画分镜图（后台跑，页面轮询会陆续刷出来）
  const generateBoards = async () => {
    if (!detail) return
    try {
      const result = await generateDramaStoryboard(detail.id)
      toast.success(`已开始生成 ${result.started} 个镜头的分镜图${result.skipped.length ? `，${result.skipped.length} 镜正忙已跳过` : ''}`)
      loadDetail()
    } catch (error) {
      toast.error(errorMessage(error))
    }
  }

  // 给单个镜头补生成缺失的分镜图
  const generateShotBoards = async (shot: DramaShot) => {
    if (!detail) return
    const key = `${shot.id}:board`
    setRetrying((prev) => ({ ...prev, [key]: true }))
    try {
      await generateShotStoryboard(detail.id, shot.id)
      toast.success(`镜头 ${shot.index} 分镜图已开始生成`)
      loadDetail()
    } catch (error) {
      toast.error(errorMessage(error))
    } finally {
      setRetrying((prev) => ({ ...prev, [key]: false }))
    }
  }

  // 重试一批失败镜头（视频阶段）：每集 / 整个项目共用
  const retryFailedShots = async (shots: DramaShot[], label: string) => {
    if (!detail) return
    const failed = shots.filter((shot) => !shot.video_url)
    if (failed.length === 0) {
      toast.success('没有需要重试的视频镜头')
      return
    }
    let ok = 0
    for (const shot of failed) {
      try {
        await retryDramaShot(detail.id, shot.id, 'video')
        ok += 1
      } catch (error) {
        toast.error(`镜头 ${shot.index} 重试失败：${errorMessage(error)}`)
      }
    }
    toast.success(`${label}：已发起 ${ok}/${failed.length} 个视频镜头重试`)
    loadDetail()
  }

  if (loading && !detail) {
    return (
      <div>
        <Button variant="ghost" icon={<ArrowLeft size={16} />} onClick={goBack}>返回 AI 短剧</Button>
        <p className="mt-6 text-sm text-ink-3">结果加载中…</p>
      </div>
    )
  }

  if (!detail) {
    return (
      <div>
        <Button variant="ghost" icon={<ArrowLeft size={16} />} onClick={goBack}>返回 AI 短剧</Button>
        <div className="mt-6">
          <Alert type="error">{loadError || '找不到这个短剧项目，可能已被删除。'}</Alert>
        </div>
      </div>
    )
  }

  const running = detail.status === 'running' || detail.status === 'pending'
  const allShots = detail.episodes.flatMap((episode) => episode.shots)
  const failedShots = allShots.filter((shot) => !shot.video_url)
  const boardCount = detail.options.storyboard_frames || 3
  const missingBoards = allShots.filter((shot) => readyBoardCount(shot) < boardCount)
  const totalReadyBoards = allShots.reduce((sum, shot) => sum + readyBoardCount(shot), 0)

  return (
    <div>
      {/* 左上角返回：回到 AI 短剧页面 */}
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <Button variant="ghost" icon={<ArrowLeft size={16} />} onClick={goBack}>返回 AI 短剧</Button>
        <StatusTag status={detail.status} />
        <span className="text-xs text-ink-3">{detail.stage}</span>
      </div>

      {loadError && <Alert type="error" className="mb-4">{loadError}</Alert>}

      <div className="flex flex-col gap-5">
        <Panel
          title={detail.title || '未命名短剧'}
          extra={
            <div className="flex flex-wrap items-center gap-1.5">
              {detail.status === 'planned' && (
                <Button size="sm" variant="primary" icon={<Play size={14} />} onClick={() => action('start')}>开始生成</Button>
              )}
              {missingBoards.length > 0 && (
                <Button size="sm" icon={<ArrowClockwise size={14} />} onClick={generateBoards}>生成全部镜头的分镜图（缺 {missingBoards.length} 镜）</Button>
              )}
              {failedShots.length > 0 && (
                <Button size="sm" icon={<ArrowClockwise size={14} />} onClick={() => retryFailedShots(allShots, '全部失败镜头')}>重试全部失败视频（{failedShots.length} 镜）</Button>
              )}
              {(detail.status === 'failed' || detail.status === 'cancelled') && (
                <Button size="sm" icon={<ArrowClockwise size={14} />} onClick={() => action('resume')}>继续</Button>
              )}
              {(detail.status === 'running' || detail.status === 'pending') && (
                <Button size="sm" variant="ghost" icon={<Stop size={14} />} onClick={() => action('cancel')}>取消</Button>
              )}
              <Button size="sm" variant="text" icon={<Trash size={14} />} onClick={() => action('delete')}>删除</Button>
            </div>
          }
        >
          <div className="mb-2 flex items-center gap-3">
            <div className="min-w-0 flex-1">
              <Progress percent={(detail.progress || 0) / 100} status={detail.status === 'failed' ? 'error' : detail.status === 'success' ? 'success' : 'normal'} />
            </div>
            <span className="shrink-0 text-xs text-ink-3">{Math.round(detail.progress || 0)}%</span>
          </div>
          <p className="mb-3 truncate text-xs text-ink-3">{detail.created_at} · {detail.stage} · 视频 {allShots.filter((s) => s.video_url).length}/{allShots.length} 镜 · 首帧图 {allShots.filter((s) => s.frame_url || s.image?.url).length}/{allShots.length} 张 · 分镜图 {totalReadyBoards}/{allShots.length * boardCount} 张</p>
          {detail.status === 'planned' && (
            <p className="mb-3 rounded-lg bg-accent/10 p-2.5 text-sm text-ink-2">
              AI 已规划 {detail.episodes.length} 集、{allShots.length} 个镜头（集数、镜头数、每镜时长均为 AI 根据小说决定），全部镜头如下。确认无误后点右上角「开始生成」。
            </p>
          )}
          <p className="mb-3 text-sm text-ink-3">{detail.logline || detail.stage}{running && ` · ${detail.stage}`}</p>
          {detail.error && <p className="mb-3 line-clamp-2 text-xs leading-relaxed text-danger" title={detail.error}>{detail.error}</p>}
          {detail.characters.length > 0 && (
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {detail.characters.map((character) => (
                <div key={character.id} className="overflow-hidden rounded-[10px] border border-line">
                  {character.anchor_url
                    ? <img src={character.anchor_url} alt={character.name} className="aspect-[3/4] w-full object-cover" />
                    : <div className="flex aspect-[3/4] w-full items-center justify-center bg-fill px-3 text-center text-xs text-ink-4">{detail.status === 'planned' ? '点「开始生成」后生成定妆照' : '定妆照生成中…'}</div>}
                  <div className="p-2.5">
                    <strong className="text-sm">{character.name}</strong>
                    <span className="ml-1.5 text-xs text-ink-3">{character.age}{character.gender}</span>
                    <p className="mt-1 line-clamp-3 text-xs text-ink-3">{character.appearance}</p>
                    <p className="mt-0.5 line-clamp-2 text-xs text-ink-4">{character.clothing}</p>
                  </div>
                </div>
              ))}
            </div>
          )}
        </Panel>

        {detail.episodes.map((episode) => {
          const episodeFailed = episode.shots.filter((shot) => !shot.video_url)
          return (
            <Panel
              key={episode.index}
              title={`第 ${episode.index} 集 · ${episode.title}`}
              extra={
                <div className="flex items-center gap-2">
                  <span className="text-xs text-ink-3">{episode.shots.filter((s) => s.video_url).length}/{episode.shots.length} 镜视频完成{episode.duration ? ` · ${formatDuration(episode.duration)}` : ''}</span>
                  {episodeFailed.length > 0 && (
                    <Button size="sm" icon={<ArrowClockwise size={13} />} onClick={() => retryFailedShots(episode.shots, `第 ${episode.index} 集`)}>重试本集失败视频（{episodeFailed.length} 镜）</Button>
                  )}
                </div>
              }
            >
              {episode.summary && <p className="mb-3 text-xs text-ink-3">{episode.summary}</p>}
              {episode.video_url
                ? (
                  <div className="mb-4 flex flex-wrap items-center gap-3">
                    <video src={episode.video_url} controls className="max-h-80 rounded-[10px] border border-line" />
                    <div className="flex flex-col gap-2">
                      <a href={episode.video_url} download className="inline-flex items-center gap-1.5 text-sm text-accent"><DownloadSimple size={15} />下载第 {episode.index} 集成片</a>
                      {episode.srt_url && <a href={episode.srt_url} download className="inline-flex items-center gap-1.5 text-sm text-accent"><DownloadSimple size={15} />下载台词字幕 SRT</a>}
                    </div>
                  </div>
                )
                : (
                  <div className="mb-4 flex items-center justify-center rounded-[10px] border border-dashed border-line bg-fill px-4 py-6 text-xs text-ink-4">
                    第 {episode.index} 集成片未生成：还有 {episodeFailed.length} 镜视频未完成，全部视频完成后自动合成。
                  </div>
                )}
              {episode.shots.length === 0 && <p className="text-xs text-ink-4">本集暂无镜头。</p>}
              <div className="flex flex-col gap-4">
                {episode.shots.map((shot) => (
                  <ShotCard key={shot.id || shot.index} shot={shot} projectTitle={detail.title} boardCount={boardCount} retrying={retrying} onRetry={retryShot} onGenerateBoard={generateShotBoards} />
                ))}
              </div>
            </Panel>
          )
        })}

        <Panel title="生成日志" extra={<Button size="sm" variant="text" icon={<Play size={13} />} onClick={() => loadDetail()}>刷新</Button>}>
          <pre className="max-h-64 overflow-auto whitespace-pre-wrap text-xs leading-relaxed text-ink-3">
            {(detail.logs || []).slice().reverse().join('\n') || '暂无日志'}
          </pre>
        </Panel>
      </div>
    </div>
  )
}

const BOARD_LABELS: Record<number, string> = { 1: '开场', 2: '进行', 3: '高潮', 4: '收尾' }

// 只看当前版本号的分镜图（改提示词会升版本号，旧图不展示）
function boardPanels(shot: DramaShot): DramaShotBoard[] {
  return (shot.storyboard || [])
    .filter((panel) => panel.revision === undefined || panel.revision === shot.revision)
    .sort((a, b) => a.beat - b.beat)
}

function readyBoardCount(shot: DramaShot): number {
  return boardPanels(shot).filter((panel) => panel.status === 'ready' && panel.url).length
}

function latestPrompt(shot: DramaShot, stage: 'image' | 'video'): string | undefined {
  const history = (shot.quality_history || []).filter((item) => item.stage === stage && item.effective_prompt)
  return history[history.length - 1]?.effective_prompt
}

function imagePrompt(shot: DramaShot, projectTitle: string): string {
  return latestPrompt(shot, 'image')
    || `漫剧《${projectTitle || '未命名短剧'}》镜头首帧。${shot.prompt || `场景：${shot.scene}。动作：${shot.action}。表情：${shot.expression}。运镜：${shot.camera}。`}\n人物与参考图保持一致，画面清晰，构图完整，严格符合场景、动作和表情。`
}

function videoPrompt(shot: DramaShot): string {
  return latestPrompt(shot, 'video')
    || `从首帧开始，严格遵循镜头提示词：${shot.prompt || `${shot.action}。运镜：${shot.camera}。表情变化：${shot.expression}`}。运镜缓慢稳定。人物外貌、服装、场景光线与首帧完全一致，不变脸、不换人、不切换场景。${shot.dialogue ? `角色说台词时口型自然：「${shot.dialogue}」。` : ''}`
}

function ShotCard({ shot, projectTitle, boardCount, retrying, onRetry, onGenerateBoard }: {
  shot: DramaShot
  projectTitle: string
  boardCount: number
  retrying: Record<string, boolean>
  onRetry: (shot: DramaShot, stage: DramaShotRetryStage) => void
  onGenerateBoard: (shot: DramaShot) => void
}) {
  const imageUrl = shot.frame_url || shot.image?.url || ''
  const videoUrl = shot.video_url || (shot.video && typeof shot.video === 'object' ? shot.video.url : '') || ''
  const imageScore = shot.image?.quality?.score
  const videoScore = shot.video && typeof shot.video === 'object' ? shot.video.quality?.score : undefined
  const videoDone = Boolean(videoUrl)
  const imageDone = Boolean(imageUrl)
  const statusText = videoDone ? '视频完成' : imageDone ? '首帧就绪 · 视频未完成' : '待生成'
  const statusColor = videoDone ? 'text-success' : shot.status === 'failed' ? 'text-danger' : 'text-ink-4'
  const panels = boardPanels(shot)
  const readyBoards = readyBoardCount(shot)

  return (
    <div className="overflow-hidden rounded-[10px] border border-line">
      {/* 镜头头：编号 / 时长 / 运镜 / 状态，始终显示 */}
      <div className="flex flex-wrap items-center gap-2 border-b border-line bg-fill px-3 py-2 text-xs">
        <strong className="text-sm">镜头 {shot.index} · {shot.seconds}s</strong>
        <span className="text-ink-3">{shot.camera}</span>
        <span className={`ml-auto font-medium ${statusColor}`}>{statusText}{imageScore !== undefined ? ` · 图 ${imageScore} 分` : ''}{videoScore !== undefined ? ` · 视频 ${videoScore} 分` : ''}</span>
        <Button size="sm" variant="ghost" icon={<ArrowClockwise size={13} />} loading={retrying[`${shot.id}:all`]} onClick={() => onRetry(shot, 'all')}>全部重做</Button>
      </div>

      <div className="grid gap-4 p-3 md:grid-cols-2">
        {/* 左：图片提示词 + 首帧图 */}
        <section>
          <h4 className="mb-2 text-xs font-medium text-ink-1">首帧图{imageScore !== undefined ? ` · ${imageScore} 分` : ''}</h4>
          <p className="mb-1 text-[11px] text-ink-4">图片提示词</p>
          <p className="mb-2 whitespace-pre-wrap break-words rounded-lg bg-fill p-2 text-xs leading-relaxed text-ink-2">{imagePrompt(shot, projectTitle)}</p>
          {imageDone
            ? <img src={imageUrl} alt={`镜头 ${shot.index} 首帧`} className="aspect-video w-full rounded-[8px] border border-line object-cover" />
            : <div className="flex aspect-video w-full flex-col items-center justify-center gap-1 rounded-[8px] border border-dashed border-line bg-fill text-xs text-ink-4"><span>首帧图未生成</span>{shot.error && <span className="max-w-full truncate px-2 text-danger" title={shot.error}>{shot.error}</span>}</div>}
          <div className="mt-2 flex items-center gap-1.5">
            <Button size="sm" variant="ghost" icon={<ArrowClockwise size={13} />} loading={retrying[`${shot.id}:image`]} onClick={() => onRetry(shot, 'image')}>{imageDone ? '重做图片' : '生成图片'}</Button>
          </div>
        </section>

        {/* 右：视频提示词 + 视频 */}
        <section>
          <h4 className="mb-2 text-xs font-medium text-ink-1">视频{videoScore !== undefined ? ` · ${videoScore} 分` : ''}</h4>
          <p className="mb-1 text-[11px] text-ink-4">视频提示词</p>
          <p className="mb-2 whitespace-pre-wrap break-words rounded-lg bg-fill p-2 text-xs leading-relaxed text-ink-2">{videoPrompt(shot)}</p>
          {videoDone
            ? <video src={videoUrl} controls preload="metadata" className="aspect-video w-full rounded-[8px] border border-line bg-black object-contain" />
            : (
              <div className="flex aspect-video w-full flex-col items-center justify-center gap-1.5 rounded-[8px] border border-dashed border-line bg-fill px-3 text-center text-xs text-ink-4">
                <span>视频未生成{shot.status === 'failed' ? ' · 生成失败' : ''}</span>
                {shot.error && <span className="line-clamp-3 break-words text-danger" title={shot.error}>{shot.error}</span>}
                {!shot.error && <span>首帧图就绪后即可生成视频</span>}
              </div>
            )}
          <div className="mt-2 flex items-center gap-1.5">
            <Button
              size="sm"
              variant={videoDone ? 'ghost' : 'primary'}
              icon={<ArrowClockwise size={13} />}
              loading={retrying[`${shot.id}:video`]}
              onClick={() => onRetry(shot, 'video')}
            >
              {videoDone ? '重做视频' : '重试视频'}
            </Button>
          </div>
        </section>
      </div>

      {/* 连环画分镜图：第 1 张就是首帧图，第 2 张起按剧情节拍出图 */}
      <div className="border-t border-line px-3 py-2.5">
        <div className="mb-2 flex items-center gap-2">
          <h4 className="text-xs font-medium text-ink-1">分镜图 · 连环画（{readyBoards}/{boardCount} 张）</h4>
          {readyBoards < boardCount && (
            <Button size="sm" variant="ghost" icon={<ArrowClockwise size={13} />} loading={retrying[`${shot.id}:board`]} onClick={() => onGenerateBoard(shot)}>生成分镜图</Button>
          )}
        </div>
        <div className="grid gap-3 sm:grid-cols-3">
          {Array.from({ length: boardCount }, (_, index) => index + 1).map((beat) => {
            const panel = panels.find((item) => item.beat === beat)
            const label = panel?.label || BOARD_LABELS[beat] || `第 ${beat} 张`
            const url = panel?.url || (beat === 1 ? imageUrl : '')
            const prompt = panel?.prompt || (beat === 1 ? imagePrompt(shot, projectTitle) : '')
            const score = panel?.quality?.score
            return (
              <div key={beat} className="overflow-hidden rounded-[8px] border border-line">
                {url
                  ? <img src={url} alt={`镜头 ${shot.index} 分镜 ${beat}`} className="aspect-video w-full object-cover" />
                  : <div className="flex aspect-video w-full items-center justify-center bg-fill text-xs text-ink-4">{panel?.status === 'generating' ? '生成中…' : '待生成'}</div>}
                <div className="p-2 text-xs">
                  <div className="flex items-center justify-between gap-2">
                    <strong>{label}</strong>
                    {score !== undefined && <span className="shrink-0 text-ink-3">{score} 分</span>}
                  </div>
                  {prompt && <p className="mt-1 whitespace-pre-wrap break-words leading-relaxed text-ink-3">{prompt}</p>}
                </div>
              </div>
            )
          })}
        </div>
      </div>

      {/* 镜头文案：场景 / 动作 / 台词，始终显示 */}
      <div className="border-t border-line px-3 py-2.5 text-xs">
        <p className="text-ink-3">{shot.scene}｜{shot.action}</p>
        {shot.dialogue && <p className="mt-1 text-ink-2">「{shot.dialogue}」</p>}
      </div>
    </div>
  )
}
