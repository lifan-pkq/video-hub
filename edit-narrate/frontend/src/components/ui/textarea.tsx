import type { TextareaHTMLAttributes } from 'react'
import { cn } from '../../lib/utils.ts'

export function Textarea({ className, ...props }: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <textarea
      className={cn(
        'min-h-20 w-full rounded-md border border-line bg-bg px-2.5 py-2 text-sm text-ink outline-none placeholder:text-faint focus:border-amber',
        className,
      )}
      {...props}
    />
  )
}
