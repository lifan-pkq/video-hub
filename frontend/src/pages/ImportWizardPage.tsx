import { useState } from 'react'
import { Link } from 'react-router-dom'
import { DownloadSimple, UploadSimple } from '@phosphor-icons/react'
import { createBatchDownload, errorDetail, uploadVideo, type Video } from '../api'
import { Button, Dropzone, PageHeader, Panel, ProblemAlert, StepBar, TextArea, toast } from '../ui'

type Source = 'upload' | 'link'

export default function ImportWizardPage() {
  const [step, setStep] = useState(0)
  const [source, setSource] = useState<Source | null>(null)
  const [text, setText] = useState('')
  const [uploading, setUploading] = useState(false)
  const [progress, setProgress] = useState(0)
  const [submitting, setSubmitting] = useState(false)
  const [problem, setProblem] = useState<{ message: string; action: string } | null>(null)
  const [uploaded, setUploaded] = useState<Video | null>(null)
  const [taskCount, setTaskCount] = useState(0)

  const choose = (next: Source) => {
    setSource(next)
    setProblem(null)
    setStep(1)
  }

  const onFile = async (file: File) => {
    setUploading(true)
    setProgress(0)
    setProblem(null)
    try {
      const video = await uploadVideo(file, file.name.replace(/\.[^.]+$/, ''), setProgress)
      setUploaded(video)
      setStep(2)
      toast.success(`已入库「${video.title}」`)
    } catch (error) {
      setProblem(errorDetail(error))
    } finally {
      setUploading(false)
    }
  }

  const submitLinks = async () => {
    const links = text.split('\n').map((line) => line.trim()).filter(Boolean)
    if (!links.length) {
      setProblem({ message: '还没有可导入的链接。', action: '每行粘贴一条以 http 开头的视频链接。' })
      return
    }
    setSubmitting(true)
    setProblem(null)
    try {
      const result = await createBatchDownload(links)
      setTaskCount(result.count)
      setStep(2)
      toast.success(`已提交 ${result.count} 个下载，可到任务中心看进度`)
    } catch (error) {
      setProblem(errorDetail(error))
    } finally {
      setSubmitting(false)
    }
  }

  const reset = () => {
    setStep(0)
    setSource(null)
    setText('')
    setUploaded(null)
    setTaskCount(0)
    setProblem(null)
  }

  return (
    <section>
      <PageHeader
        title="导入素材"
        desc="本地原片直接入库；分享链接交给下载队列。这里不保存别人的灵感笔记。"
        extra={<Link to="/videos"><Button variant="ghost">返回素材库</Button></Link>}
      />
      <div className="mb-5">
        <StepBar steps={[
          { label: '选择来源', desc: '上传或链接', state: step === 0 ? 'current' : 'done' },
          { label: '导入', desc: source === 'link' ? '提交下载' : '上传文件', state: step === 1 ? 'current' : step > 1 ? 'done' : 'todo' },
          { label: '下一步', desc: '去剪辑或看任务', state: step === 2 ? 'current' : 'todo' },
        ]} />
      </div>
      {problem && <ProblemAlert message={problem.message} action={problem.action} className="mb-4" />}
      {step === 0 && (
        <div className="grid gap-4 md:grid-cols-2">
          <button type="button" className="vh-card p-5 text-left hover:border-accent" onClick={() => choose('upload')}>
            <UploadSimple size={22} className="text-accent" />
            <strong className="mt-3 block text-[15px] text-ink-1">上传本地视频</strong>
            <p className="mt-1 text-[12px] leading-5 text-ink-3">适合已经下载好的原片。上传完成后立刻出现在素材库。</p>
          </button>
          <button type="button" className="vh-card p-5 text-left hover:border-accent" onClick={() => choose('link')}>
            <DownloadSimple size={22} className="text-accent" />
            <strong className="mt-3 block text-[15px] text-ink-1">从链接导入</strong>
            <p className="mt-1 text-[12px] leading-5 text-ink-3">每行一条链接即可下载，抖音无需 Cookie。失败时会告诉你怎么处理。</p>
          </button>
        </div>
      )}
      {step === 1 && source === 'upload' && (
        <Panel title="上传本地视频" extra={<Button size="sm" variant="ghost" onClick={reset}>重选来源</Button>}>
          <Dropzone accept="video/*" uploading={uploading} progress={progress} tip="支持常见视频格式，上传后自动生成封面" onFile={(file) => void onFile(file)} />
        </Panel>
      )}
      {step === 1 && source === 'link' && (
        <Panel title="从链接导入" extra={<Button size="sm" variant="ghost" onClick={reset}>重选来源</Button>}>
          <TextArea value={text} onChange={setText} rows={6} placeholder="每行一个视频链接" />
          <div className="mt-3 flex gap-2">
            <Button variant="primary" icon={<DownloadSimple size={15} />} loading={submitting} onClick={() => void submitLinks()}>提交下载</Button>
            <Link to="/download"><Button variant="ghost">去下载页解析画质</Button></Link>
          </div>
        </Panel>
      )}
      {step === 2 && (
        <Panel title="导入已提交">
          {uploaded ? (
            <p className="text-[13px] text-ink-2">「{uploaded.title}」已进入视频素材库，可以直接剪辑。</p>
          ) : (
            <p className="text-[13px] text-ink-2">已排队 {taskCount} 个下载。可以离开本页，失败原因会写在任务上。</p>
          )}
          <div className="mt-4 flex flex-wrap gap-2">
            {uploaded && <Link to="/edit" state={{ videoIds: [uploaded.id] }}><Button variant="primary">去智能剪辑</Button></Link>}
            {!uploaded && <Link to="/tasks"><Button variant="primary">查看任务</Button></Link>}
            <Link to="/videos"><Button variant="ghost">打开素材库</Button></Link>
            <Button variant="ghost" onClick={reset}>再导入一条</Button>
          </div>
        </Panel>
      )}
    </section>
  )
}
