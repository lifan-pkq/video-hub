import * as DialogPrimitive from '@radix-ui/react-dialog'
import type { ReactNode } from 'react'
import { cn } from '../../lib/utils.ts'

export function Dialog({
  open,
  onOpenChange,
  title,
  children,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  children: ReactNode
}) {
  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-40 bg-black/60" />
        <DialogPrimitive.Content
          className={cn(
            'fixed left-1/2 top-1/2 z-50 w-[min(420px,calc(100%-2rem))] -translate-x-1/2 -translate-y-1/2 rounded-lg border border-line bg-panel p-4 shadow-2xl',
          )}
        >
          <DialogPrimitive.Title className="text-base font-medium">{title}</DialogPrimitive.Title>
          <div className="mt-3 text-sm text-muted">{children}</div>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  )
}
