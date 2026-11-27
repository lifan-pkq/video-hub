import { useEffect, useState } from 'react'
import { analysisDocumentDownloadUrl, errorMessage, fetchAnalysisDocument } from '../api'
import { Button, Modal } from '../ui'

interface Props {
  videoId: number | null
  title?: string
  onClose: () => void
}

/** 整段内容分析文档：阅读和导出，不把 JSON 片段当成正文。 */
export default function AnalysisDocument({ videoId, title, onClose }: Props) {
  const [text, setText] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!videoId) return
    let cancelled = false
    setLoading(true)
    setError('')
    setText('')
    void fetchAnalysisDocument(videoId)
      .then((body) => {
        if (!cancelled) setText(body)
      })
      .catch((reason) => {
        if (!cancelled) setError(errorMessage(reason))
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [videoId])

  return (
    <Modal open={!!videoId} onClose={onClose} title={title || '内容分析文档'} width={860}>
      <div className="mb-3 flex justify-end">
        {videoId ? (
          <a href={analysisDocumentDownloadUrl(videoId)}>
            <Button size="sm" variant="secondary">导出文档</Button>
          </a>
        ) : null}
      </div>
      {loading && <p className="text-[13px] text-ink-3">正在读取内容分析文档…</p>}
      {error && <p className="text-[13px] text-danger">{error}</p>}
      {text && (
        <pre className="max-h-[65vh] overflow-auto whitespace-pre-wrap rounded-lg bg-fill p-4 text-[13px] leading-6 text-ink-1">
          {text}
        </pre>
      )}
    </Modal>
  )
}
