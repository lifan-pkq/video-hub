import Alert from './Alert'
import Button from './Button'

interface Props {
  error: string | null
  hasData: boolean
  loading?: boolean
  onRetry: () => void
  className?: string
}

export default function RequestState({ error, hasData, loading, onRetry, className = '' }: Props) {
  if (!error) return null
  return (
    <Alert type={hasData ? 'warning' : 'error'} className={className}>
      <div className="flex flex-wrap items-center gap-3">
        <span className="min-w-0 flex-1">
          {hasData ? `刷新失败，已保留上次数据：${error}` : `加载失败：${error}`}
        </span>
        <Button size="sm" loading={loading} onClick={onRetry}>
          重试
        </Button>
      </div>
    </Alert>
  )
}
