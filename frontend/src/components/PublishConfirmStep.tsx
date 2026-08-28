import { Clock, PaperPlaneTilt } from '@phosphor-icons/react'
import { Button, DateTimeInput, PlatformIcon, Segment, Tag } from '../ui'
import { formatDuration, platformLabel, type Video } from '../api'

export type PublishMode = 'now' | 'schedule'

interface Props {
  video: Video
  title: string
  desc: string
  tags: string[]
  /** 已选目标，元素格式 "platform:account" */
  targets: string[]
  mode: PublishMode
  schedule: string
  onModeChange: (m: PublishMode) => void
  onScheduleChange: (v: string) => void
  publishing: boolean
  onSubmit: () => void
}

/** "douyin:default" → "抖音 / default" */
function targetLabel(key: string): string {
  const i = key.indexOf(':')
  if (i < 0) return key
  return `${platformLabel(key.slice(0, i))} / ${key.slice(i + 1)}`
}

function targetPlatform(key: string): string {
  const i = key.indexOf(':')
  return i < 0 ? key : key.slice(0, i)
}

/** 第④步：发布设置（立即/定时）+ 信息汇总 + 确认提交 */
export default function PublishConfirmStep({
  video,
  title,
  desc,
  tags,
  targets,
  mode,
  schedule,
  onModeChange,
  onScheduleChange,
  publishing,
  onSubmit,
}: Props) {
  return (
    <div className="space-y-4">
      <div>
        <label className="mb-1.5 block text-[13px] text-ink-2">发布方式</label>
        <Segment
          value={mode}
          onChange={(v) => onModeChange(v as PublishMode)}
          options={[
            { value: 'now', label: '立即发布' },
            { value: 'schedule', label: '定时发布' },
          ]}
          className="max-w-[320px]"
        />
        {mode === 'schedule' && (
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <Clock size={15} className="shrink-0 text-ink-3" />
            <DateTimeInput value={schedule} onChange={onScheduleChange} className="w-full sm:w-[220px]" />
            {schedule && (
              <Button size="sm" variant="text" onClick={() => onScheduleChange('')}>
                清空
              </Button>
            )}
          </div>
        )}
      </div>

      {/* 汇总卡片 */}
      <div className="space-y-3 rounded-[10px] border border-line-soft bg-fill p-4">
        <div className="flex items-center gap-3">
          <div className="relative aspect-video w-28 shrink-0 overflow-hidden rounded-lg bg-fill-deep">
            {video.cover_url ? (
              <img src={video.cover_url} alt={video.title} className="h-full w-full object-cover" />
            ) : (
              <div className="flex h-full items-center justify-center text-ink-4">
                <PlatformIcon platform={video.source_platform} size={24} />
              </div>
            )}
            <span className="tnum absolute bottom-1 right-1 rounded bg-black/70 px-1 text-[10px] text-white">
              {formatDuration(video.duration)}
            </span>
          </div>
          <div className="min-w-0">
            <div className="truncate text-[13px] font-medium text-ink-1" title={video.title}>
              {video.title}
            </div>
            <div className="mt-1 text-xs text-ink-3">发布时间：{mode === 'now' ? '立即发布' : schedule || '未设置'}</div>
          </div>
        </div>

        <div className="border-t border-line-soft pt-3">
          <div className="mb-1 text-xs text-ink-3">发布文案</div>
          <div className="truncate text-[13px] text-ink-1" title={title}>
            {title}
          </div>
          {desc && <div className="mt-0.5 line-clamp-2 break-all text-xs text-ink-3">{desc}</div>}
          {tags.length > 0 && (
            <div className="mt-1.5 flex flex-wrap gap-1.5">
              {tags.map((t) => (
                <Tag key={t} color="blue">
                  #{t}
                </Tag>
              ))}
            </div>
          )}
        </div>

        <div className="border-t border-line-soft pt-3">
          <div className="mb-1.5 text-xs text-ink-3">发布账号（{targets.length}）</div>
          <div className="flex flex-wrap gap-x-4 gap-y-1.5">
            {targets.map((key) => (
              <span key={key} className="flex items-center gap-1.5 text-[13px] text-ink-2">
                <PlatformIcon platform={targetPlatform(key)} size={14} />
                <span className="break-all">{targetLabel(key)}</span>
              </span>
            ))}
          </div>
        </div>
      </div>

      <Button
        variant="primary"
        full
        icon={<PaperPlaneTilt size={15} />}
        loading={publishing}
        disabled={mode === 'schedule' && !schedule}
        onClick={onSubmit}
      >
        {publishing ? '创建任务中…' : mode === 'now' ? '确认发布' : '确认定时发布'}
      </Button>
    </div>
  )
}
