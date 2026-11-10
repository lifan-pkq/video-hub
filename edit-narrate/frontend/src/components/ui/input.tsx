import type { InputHTMLAttributes } from 'react'
import { cn } from '../../lib/utils.ts'

export function Input({ className, ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      className={cn(
        'h-9 w-full rounded-md border border-line bg-bg px-2.5 text-sm text-ink outline-none placeholder:text-faint focus:border-amber',
        className,
      )}
      {...props}
    />
  )
}
