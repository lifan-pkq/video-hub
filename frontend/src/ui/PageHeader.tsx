import type { ReactNode } from 'react'

interface Props {
  title: string
  desc?: string
  /** 右上角操作区 */
  extra?: ReactNode
}

/** 页面头部：大标题 + 副标题 + 操作区 */
export default function PageHeader({ title, desc, extra }: Props) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="page-title">{title}</h1>
        {desc && <p className="page-desc">{desc}</p>}
      </div>
      {extra && <div className="flex items-center gap-2">{extra}</div>}
    </div>
  )
}
