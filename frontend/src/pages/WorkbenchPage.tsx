import { Hammer } from '@phosphor-icons/react'
import { EmptyState, PageHeader } from '../ui'

/** 工作台首页（占位，Wave agent 重写） */
export default function WorkbenchPage() {
  return (
    <div>
      <PageHeader title="工作台" desc="素材、任务与发布的一站式概览" />
      <div className="vh-card">
        <EmptyState
          icon={<Hammer size={26} />}
          title="工作台建设中"
          desc="这里将汇聚素材库概览、进行中任务、快捷操作与发布动态。"
        />
      </div>
    </div>
  )
}
