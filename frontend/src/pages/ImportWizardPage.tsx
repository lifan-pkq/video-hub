import { CloudArrowDown } from '@phosphor-icons/react'
import { EmptyState, PageHeader } from '../ui'
import WizardSteps from '../components/WizardSteps'

const STEPS = [
  { key: 'source', label: '粘贴链接' },
  { key: 'parse', label: '解析预览' },
  { key: 'confirm', label: '确认导入' },
]

/** 素材导入向导（占位，Wave agent 重写） */
export default function ImportWizardPage() {
  return (
    <div>
      <PageHeader title="导入素材" desc="粘贴分享链接，解析后一键入库" />
      <div className="vh-card p-5">
        <WizardSteps steps={STEPS} current={0} />
        <EmptyState
          icon={<CloudArrowDown size={26} />}
          title="导入向导建设中"
          desc="将支持单条 / 批量链接解析、作者主页批量导入与下载进度跟踪。"
        />
      </div>
    </div>
  )
}
