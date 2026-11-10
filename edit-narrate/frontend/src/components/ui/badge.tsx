import type { HTMLAttributes } from 'react'
import { cn } from '../../lib/utils.ts'

export function Badge({ className, ...props }: HTMLAttributes<HTMLSpanElement>) {
  return (
    <span
      className={cn('inline-flex items-center rounded-full border border-line px-2 py-0.5 text-[11px] text-muted', className)}
      {...props}
    />
  )
}
