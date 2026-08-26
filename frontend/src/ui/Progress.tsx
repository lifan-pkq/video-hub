interface Props {
  /** 0~1 */
  percent: number
  status?: 'normal' | 'success' | 'error'
  className?: string
}

const BAR = {
  normal: 'var(--accent)',
  success: 'var(--success)',
  error: 'var(--danger)',
}

export default function Progress({ percent, status = 'normal', className = '' }: Props) {
  const p = Math.max(0, Math.min(1, percent))
  return (
    <div className={`h-1.5 w-full overflow-hidden rounded-full bg-fill-deep ${className}`}>
      <div
        className="h-full rounded-full transition-all duration-300"
        style={{ width: `${p * 100}%`, backgroundColor: BAR[status] }}
      />
    </div>
  )
}
