import { useEffect, useState } from 'react'
import { CheckCircle, Info, Warning, XCircle } from '@phosphor-icons/react'

export type ToastType = 'success' | 'error' | 'warning' | 'info'

interface ToastItem {
  id: number
  type: ToastType
  text: string
}

let push: ((t: ToastType, text: string) => void) | null = null

/** 全局轻提示：`toast.success('已保存')` */
export const toast = {
  success: (text: string) => push?.('success', text),
  error: (text: string) => push?.('error', text),
  warning: (text: string) => push?.('warning', text),
  info: (text: string) => push?.('info', text),
}

const META: Record<ToastType, { icon: typeof CheckCircle; cls: string }> = {
  success: { icon: CheckCircle, cls: 'text-success' },
  error: { icon: XCircle, cls: 'text-danger' },
  warning: { icon: Warning, cls: 'text-warning' },
  info: { icon: Info, cls: 'text-ink-3' },
}

/** 挂在 App 根部 */
export function ToastHost() {
  const [items, setItems] = useState<ToastItem[]>([])

  useEffect(() => {
    let id = 0
    push = (type, text) => {
      const item = { id: ++id, type, text }
      setItems((prev) => [...prev.slice(-4), item])
      window.setTimeout(() => {
        setItems((prev) => prev.filter((t) => t.id !== item.id))
      }, 3000)
    }
    return () => {
      push = null
    }
  }, [])

  return (
    <div className="pointer-events-none fixed left-1/2 top-5 z-[200] flex -translate-x-1/2 flex-col items-center gap-2">
      {items.map((t) => {
        const Icon = META[t.type].icon
        return (
          <div
            key={t.id}
            className="toast-in pointer-events-auto flex max-w-[80vw] items-center gap-2 rounded-[10px] border border-line bg-raise px-4 py-2.5 text-[13px] text-ink-1 shadow-pop"
          >
            <Icon size={16} weight="fill" className={META[t.type].cls} />
            <span className="break-all">{t.text}</span>
          </div>
        )
      })}
    </div>
  )
}
