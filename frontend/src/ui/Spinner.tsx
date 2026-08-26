import { CircleNotch } from '@phosphor-icons/react'

export default function Spinner({ size = 18, className = '' }: { size?: number; className?: string }) {
  return <CircleNotch size={size} weight="bold" className={`spin text-ink-3 ${className}`} />
}
