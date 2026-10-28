import { useEffect, useId, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { X } from '@phosphor-icons/react'
import Button from './Button'

interface Props {
  open: boolean
  onClose: () => void
  title?: ReactNode
  children: ReactNode
  footer?: ReactNode
  width?: number
  /** 点遮罩关闭，默认 true */
  maskClosable?: boolean
}

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'

/**
 * 弹窗：通过 createPortal 挂载到 document.body。
 * 不能渲染在页面内部——页面的进场动画（.page-in 等）会留下 transform，
 * 形成包含块，把 fixed 弹窗囚禁在页面容器里，滚动后位置错乱。
 * Esc 关闭、焦点圈定与恢复、遮罩、进出场动画。
 */
export default function Modal({ open, onClose, title, children, footer, width = 480, maskClosable = true }: Props) {
  const dialogRef = useRef<HTMLDivElement>(null)
  const onCloseRef = useRef(onClose)
  const titleId = useId()
  onCloseRef.current = onClose

  useEffect(() => {
    if (!open) return
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const previousOverflow = document.body.style.overflow
    const dialog = dialogRef.current
    const focusable = () =>
      Array.from(dialog?.querySelectorAll<HTMLElement>(FOCUSABLE) ?? []).filter(
        (element) => element.getClientRects().length > 0,
      )
    ;(focusable()[0] ?? dialog)?.focus()

    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        onCloseRef.current()
        return
      }
      if (e.key !== 'Tab') return
      const items = focusable()
      if (items.length === 0) {
        e.preventDefault()
        dialog?.focus()
        return
      }
      const first = items[0]
      const last = items[items.length - 1]
      const active = document.activeElement
      if (e.shiftKey && (active === first || !dialog?.contains(active))) {
        e.preventDefault()
        last.focus()
      } else if (!e.shiftKey && (active === last || !dialog?.contains(active))) {
        e.preventDefault()
        first.focus()
      }
    }

    document.addEventListener('keydown', onKey)
    document.body.style.overflow = 'hidden'
    return () => {
      document.removeEventListener('keydown', onKey)
      document.body.style.overflow = previousOverflow
      if (previousFocus?.isConnected) previousFocus.focus()
    }
  }, [open])

  if (!open) return null

  return createPortal(
    <div className="fixed inset-0 z-[100] overflow-y-auto">
      <div
        className="fade-in fixed inset-0"
        style={{ backgroundColor: 'var(--mask)' }}
        onClick={maskClosable ? onClose : undefined}
      />
      <div className="flex min-h-full items-start justify-center p-4 pt-[12vh]">
        <div
          ref={dialogRef}
          role="dialog"
          aria-modal="true"
          aria-labelledby={title != null ? titleId : undefined}
          tabIndex={-1}
          className="modal-in relative w-full rounded-2xl border border-line bg-card shadow-pop"
          style={{ maxWidth: width }}
        >
          {title != null && (
            <div className="flex items-center justify-between border-b border-line-soft px-5 py-4">
              <h3 id={titleId} className="section-title">{title}</h3>
              <button
                type="button"
                aria-label="关闭"
                onClick={onClose}
                className="flex h-7 w-7 items-center justify-center rounded-lg text-ink-3 transition-colors hover:bg-fill hover:text-ink-1"
              >
                <X size={15} />
              </button>
            </div>
          )}
          <div className="px-5 py-4">{children}</div>
          {footer != null && (
            <div className="flex items-center justify-end gap-2 border-t border-line-soft px-5 py-3.5">{footer}</div>
          )}
        </div>
      </div>
    </div>,
    document.body,
  )
}

/* ================= 命令式确认弹窗 ================= */

export interface ConfirmOptions {
  title: string
  content: ReactNode
  okText?: string
  cancelText?: string
  danger?: boolean
}

interface ConfirmState extends ConfirmOptions {
  resolve: (ok: boolean) => void
}

let showConfirm: ((s: ConfirmState) => void) | null = null

/** 命令式确认弹窗：`if (await confirmDialog({...})) { ... }` */
export function confirmDialog(opts: ConfirmOptions): Promise<boolean> {
  return new Promise((resolve) => {
    if (showConfirm) showConfirm({ ...opts, resolve })
    else resolve(false)
  })
}

/** 挂在 App 根部，承接 confirmDialog 调用 */
export function ConfirmHost() {
  const [state, setState] = useState<ConfirmState | null>(null)

  useEffect(() => {
    showConfirm = setState
    return () => {
      showConfirm = null
    }
  }, [])

  const close = (ok: boolean) => {
    state?.resolve(ok)
    setState(null)
  }

  return (
    <Modal
      open={!!state}
      onClose={() => close(false)}
      title={state?.title}
      width={420}
      footer={
        <>
          <Button variant="ghost" onClick={() => close(false)}>
            {state?.cancelText ?? '取消'}
          </Button>
          <Button variant={state?.danger ? 'danger' : 'primary'} onClick={() => close(true)}>
            {state?.okText ?? '确定'}
          </Button>
        </>
      }
    >
      <div className="text-[13px] leading-relaxed text-ink-2">{state?.content}</div>
    </Modal>
  )
}
