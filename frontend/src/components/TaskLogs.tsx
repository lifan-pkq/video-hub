import { useEffect, useRef } from 'react'

/** 滚动日志区：日志追加时自动滚到底部 */
export default function TaskLogs({ logs }: { logs: string[] }) {
  const boxRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const el = boxRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [logs])

  if (logs.length === 0) return null

  return (
    <div ref={boxRef} className="log-box">
      {logs.map((line, i) => (
        <div key={i}>{line}</div>
      ))}
    </div>
  )
}
