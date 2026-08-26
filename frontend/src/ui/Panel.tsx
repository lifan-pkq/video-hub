import type { ReactNode } from 'react'

interface Props {
  title?: ReactNode
  /** 标题右侧操作区 */
  extra?: ReactNode
  children: ReactNode
  className?: string
  /** 内边距档位 */
  pad?: 'none' | 'sm' | 'md'
}

/** 统一面板：圆角、细边、微阴影，替代 Arco Card 的默认视觉 */
export default function Panel({ title, extra, children, className = '', pad = 'md' }: Props) {
  const padding = pad === 'none' ? '' : pad === 'sm' ? 'p-4' : 'p-5'
  return (
    <section className={`vh-card ${padding} ${className}`}>
      {(title || extra) && (
        <header className={`flex items-center justify-between gap-3 ${pad === 'none' ? 'p-4 pb-0' : 'mb-4'}`}>
          <h2 className="section-title">{title}</h2>
          {extra}
        </header>
      )}
      {children}
    </section>
  )
}
