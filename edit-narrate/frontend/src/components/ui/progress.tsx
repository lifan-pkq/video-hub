import * as ProgressPrimitive from '@radix-ui/react-progress'
import { cn } from '../../lib/utils.ts'

export function Progress({ value, className }: { value: number; className?: string }) {
  return (
    <ProgressPrimitive.Root className={cn('relative h-1.5 overflow-hidden rounded-full bg-line', className)}>
      <ProgressPrimitive.Indicator
        className="h-full bg-amber transition-transform"
        style={{ transform: `translateX(-${100 - Math.max(0, Math.min(100, value))}%)` }}
      />
    </ProgressPrimitive.Root>
  )
}
