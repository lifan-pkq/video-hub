import { useCallback, useEffect, useState } from 'react'
import { CalendarBlank, MagnifyingGlass } from '@phosphor-icons/react'
import { Button, EmptyState, Input, Modal, Panel, PlatformIcon, Segment, Select } from '../ui'
import {
  fetchPublishRecords,
  formatTime,
  PLATFORMS,
  platformLabel,
  type PublishRecord,
} from '../api'
import StatusBadge from './StatusBadge'

const WEEKDAYS = ['日', '一', '二', '三', '四', '五', '六']

/** 排期分组的日期标题："2026-08-22" → "2026/8/22 星期六" */
function scheduleDayLabel(day: string): string {
  const [y, m, d] = day.split('-').map(Number)
  return `${y}/${m}/${d} 星期${WEEKDAYS[new Date(y, m - 1, d).getDay()]}`
}

/** 定时时间是否已过（schedule 为本地时间 "YYYY-MM-DD HH:mm"） */
function scheduleExpired(schedule: string): boolean {
  return new Date(schedule.replace(' ', 'T')).getTime() < Date.now()
}

interface Props {
  /** 递增触发重新加载（发布任务终态时由页面 bump） */
  refreshKey: number
}

/** 发布记录面板：记录表格 / 排期时间线 双视角 + 筛选 + 日志弹窗 */
export default function PublishRecords({ refreshKey }: Props) {
  const [records, setRecords] = useState<PublishRecord[]>([])
  const [logRecord, setLogRecord] = useState<PublishRecord | null>(null)
  const [filterPlatform, setFilterPlatform] = useState('')
  const [filterStatus, setFilterStatus] = useState('')
  const [filterKeyword, setFilterKeyword] = useState('')
  const [view, setView] = useState<'records' | 'schedule'>('records')

  const load = useCallback(async () => {
    try {
      setRecords(await fetchPublishRecords())
    } catch {
      // 记录加载失败不阻塞主流程
    }
  }, [])

  useEffect(() => {
    load()
  }, [load, refreshKey])

  const recordKeyword = filterKeyword.trim().toLowerCase()
  const filteredRecords = records.filter((r) => {
    if (filterPlatform && r.platform !== filterPlatform) return false
    if (filterStatus && r.status !== filterStatus) return false
    if (
      recordKeyword &&
      !r.video_title.toLowerCase().includes(recordKeyword) &&
      !r.title.toLowerCase().includes(recordKeyword)
    ) {
      return false
    }
    return true
  })

  // 排期视图：筛选后取有定时时间的记录，按时间正序，再按日期分组
  const scheduledRecords = filteredRecords
    .filter((r) => r.schedule !== null)
    .sort((a, b) => a.schedule!.localeCompare(b.schedule!))
  const scheduleGroups = new Map<string, PublishRecord[]>()
  for (const r of scheduledRecords) {
    const day = r.schedule!.slice(0, 10)
    const list = scheduleGroups.get(day) ?? []
    list.push(r)
    scheduleGroups.set(day, list)
  }

  return (
    <Panel
      title="发布记录"
      className="mt-4"
      extra={
        <Segment
          value={view}
          onChange={(v) => setView(v as 'records' | 'schedule')}
          options={[
            { value: 'records', label: '记录' },
            { value: 'schedule', label: '排期' },
          ]}
        />
      }
    >
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Select
          width={120}
          value={filterPlatform}
          onChange={setFilterPlatform}
          options={[
            { label: '全部平台', value: '' },
            ...PLATFORMS.map((p) => ({ label: p.label, value: p.key })),
          ]}
        />
        <Select
          width={120}
          value={filterStatus}
          onChange={setFilterStatus}
          options={[
            { label: '全部状态', value: '' },
            { label: '成功', value: 'success' },
            { label: '失败', value: 'failed' },
            { label: '运行中', value: 'running' },
          ]}
        />
        <Input
          allowClear
          prefix={<MagnifyingGlass size={14} />}
          placeholder="搜索视频或发布标题"
          value={filterKeyword}
          onChange={setFilterKeyword}
          className="w-full sm:w-[200px]"
        />
      </div>

      {view === 'records' ? (
        <div className="overflow-x-auto">
          <table className="vh-table">
            <thead>
              <tr>
                <th className="w-[160px]">时间</th>
                <th className="w-[180px]">视频</th>
                <th className="w-[96px]">平台</th>
                <th className="w-[100px]">账号</th>
                <th className="w-[100px]">状态</th>
                <th>标题</th>
                <th className="w-[90px]">操作</th>
              </tr>
            </thead>
            <tbody>
              {filteredRecords.map((r) => (
                <tr key={r.id}>
                  <td>
                    <span className="tnum text-xs text-ink-3">{formatTime(r.created_at)}</span>
                    {r.schedule && <span className="tnum block text-xs text-accent">定时 {r.schedule}</span>}
                  </td>
                  <td>
                    <span className="block max-w-[170px] truncate text-xs" title={r.video_title}>
                      {r.video_title}
                    </span>
                  </td>
                  <td>
                    <span className="flex items-center gap-1.5">
                      <PlatformIcon platform={r.platform} size={14} />
                      <span>{platformLabel(r.platform)}</span>
                    </span>
                  </td>
                  <td>{r.account || '-'}</td>
                  <td>
                    <StatusBadge status={r.status} />
                  </td>
                  <td>
                    <span className="block max-w-[220px] truncate" title={r.title}>
                      {r.title}
                    </span>
                  </td>
                  <td>
                    <Button size="sm" variant="text" onClick={() => setLogRecord(r)}>
                      查看日志
                    </Button>
                  </td>
                </tr>
              ))}
              {filteredRecords.length === 0 && (
                <tr>
                  <td colSpan={7}>
                    {records.length === 0 ? (
                      <EmptyState title="暂无发布记录" desc="发布任务完成后会在这里展示" />
                    ) : (
                      <EmptyState title="没有匹配的记录" desc="调整筛选条件试试" />
                    )}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      ) : scheduledRecords.length === 0 ? (
        <EmptyState
          icon={<CalendarBlank size={28} />}
          title="暂无排期"
          desc="发布视频时选择定时发布时间，就会出现在这里"
        />
      ) : (
        <div className="space-y-4">
          {[...scheduleGroups.entries()].map(([day, list]) => (
            <div key={day} className="flex flex-col gap-2 sm:flex-row sm:gap-4">
              <h3 className="section-title shrink-0 pt-0 sm:w-[132px] sm:pt-2">{scheduleDayLabel(day)}</h3>
              <div className="min-w-0 flex-1 space-y-3 border-l border-line pl-4">
                {list.map((r) => (
                  <div
                    key={r.id}
                    className={`vh-card flex items-center gap-3 p-3 ${scheduleExpired(r.schedule!) ? 'opacity-60' : ''}`}
                  >
                    <span className="tnum font-display w-14 shrink-0 text-lg">{r.schedule!.slice(11)}</span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[13px]" title={r.video_title}>
                        {r.video_title}
                      </span>
                      <span className="mt-1 flex items-center gap-1.5 text-xs text-ink-3">
                        <PlatformIcon platform={r.platform} size={14} />
                        <span className="truncate">{r.account || '-'}</span>
                      </span>
                    </span>
                    <StatusBadge status={r.status} />
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* 日志弹窗 */}
      <Modal
        title={`发布日志 - ${logRecord ? platformLabel(logRecord.platform) : ''}${logRecord ? ` / ${logRecord.account}` : ''}`}
        open={!!logRecord}
        onClose={() => setLogRecord(null)}
        footer={null}
        width={720}
      >
        {logRecord && <div className="log-box" style={{ maxHeight: 400 }}>{logRecord.log || '（无日志）'}</div>}
      </Modal>
    </Panel>
  )
}
