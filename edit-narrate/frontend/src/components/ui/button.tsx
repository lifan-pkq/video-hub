import { Slot } from '@radix-ui/react-slot'
import { cva, type VariantProps } from 'class-variance-authority'
import type { ButtonHTMLAttributes } from 'react'
import { cn } from '../../lib/utils.ts'

const buttonVariants = cva(
  'inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded-md text-sm font-medium transition disabled:pointer-events-none disabled:opacity-40 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-amber',
  {
    variants: {
      variant: {
        default: 'bg-amber text-bg hover:bg-[#f0b56e]',
        secondary: 'bg-panel-2 text-ink border border-line hover:border-muted',
        ghost: 'text-muted hover:bg-panel-2 hover:text-ink',
        destructive: 'bg-drop/15 text-[#ffd0c8] border border-drop/40 hover:bg-drop/25',
      },
      size: {
        default: 'h-9 px-3',
        sm: 'h-8 px-2.5 text-xs',
        icon: 'h-8 w-8',
      },
    },
    defaultVariants: { variant: 'default', size: 'default' },
  },
)

export function Button({
  className,
  variant,
  size,
  asChild = false,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & VariantProps<typeof buttonVariants> & { asChild?: boolean }) {
  const Comp = asChild ? Slot : 'button'
  return <Comp className={cn(buttonVariants({ variant, size }), className)} {...props} />
}
