import { Link } from 'react-router-dom'
import { Button } from '../ui'

export default function NotFoundPage() {
  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center text-center">
      <div className="font-display text-[72px] font-semibold leading-none tracking-tight text-accent">
        404
      </div>
      <h1 className="mt-4 text-[17px] font-medium text-ink-1">页面不存在</h1>
      <p className="mt-1.5 text-[13px] text-ink-3">你访问的页面不存在或已被移动</p>
      <Link to="/" className="mt-6">
        <Button variant="primary">回到仪表盘</Button>
      </Link>
    </div>
  )
}
