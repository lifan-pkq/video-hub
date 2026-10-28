import { useEffect, useState } from 'react'
import { ArrowSquareOut, MagicWand } from '@phosphor-icons/react'
import { Button } from '../ui'

type ServiceStatus = 'loading' | 'ready' | 'error'

export default function NarratePage() {
  const [status, setStatus] = useState<ServiceStatus>('loading')
  const [frameLoaded, setFrameLoaded] = useState(false)
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    let active = true
    const controller = new AbortController()
    const timeout = window.setTimeout(() => controller.abort(), 10000)
    setStatus('loading')
    setFrameLoaded(false)
    fetch('/narrato/_stcore/health', { signal: controller.signal })
      .then(async (response) => {
        const body = (await response.text()).trim()
        if (!response.ok || body !== 'ok') throw new Error(`HTTP ${response.status}`)
        if (active) setStatus('ready')
      })
      .catch(() => {
        if (active) setStatus('error')
      })
      .finally(() => window.clearTimeout(timeout))
    return () => {
      active = false
      window.clearTimeout(timeout)
      controller.abort()
    }
  }, [attempt])

  useEffect(() => {
    if (status !== 'ready' || frameLoaded) return
    const timeout = window.setTimeout(() => setStatus('error'), 30000)
    return () => window.clearTimeout(timeout)
  }, [frameLoaded, status])

  return (
    <section className="narrate-page" aria-label="AI 解说工作台">
      <header className="narrate-toolbar">
        <div>
          <p className="eyebrow">AI NARRATION</p>
          <h1><MagicWand size={20} />AI 解说</h1>
          <p>生成解说视频后，成片会自动进入视频素材库。</p>
        </div>
        <a className="narrate-open" href="/narrato/" target="_blank" rel="noreferrer">
          <ArrowSquareOut size={16} />新标签页打开
        </a>
      </header>

      {status === 'loading' && (
        <div className="narrate-state" role="status">
          <span className="skeleton" />
          <p>正在连接 AI 解说服务…</p>
        </div>
      )}
      {status === 'error' && (
        <div className="narrate-state" role="alert">
          <h2>AI 解说服务暂不可用</h2>
          <p>请稍后重试，或检查 NarratoAI 服务是否已启动。</p>
          <Button onClick={() => setAttempt((value) => value + 1)}>重新连接</Button>
        </div>
      )}
      {status === 'ready' && (
        <div className="narrate-frame-shell">
          {!frameLoaded && <div className="narrate-state narrate-frame-loading" role="status"><p>正在加载 NarratoAI 工作台…</p></div>}
          <iframe className="narrate-frame" src="/narrato/" title="NarratoAI AI 解说工作台" onLoad={() => setFrameLoaded(true)} />
        </div>
      )}
    </section>
  )
}
