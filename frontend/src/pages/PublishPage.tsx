import { useCallback, useEffect, useRef, useState } from 'react'
import { useLocation, useSearchParams } from 'react-router-dom'
import { ArrowLeft, ArrowRight } from '@phosphor-icons/react'
import { Button, PageHeader, Panel, toast } from '../ui'
import {
  errorMessage,
  fetchAccounts,
  fetchTask,
  fetchVideos,
  publishVideo,
  submitVerifyCode,
  type Account,
  type Task,
  type Video,
} from '../api'
import WizardSteps from '../components/WizardSteps'
import PublishVideoStep from '../components/PublishVideoStep'
import PublishCopyStep, { TITLE_MAX } from '../components/PublishCopyStep'
import PublishAccountsStep from '../components/PublishAccountsStep'
import PublishConfirmStep, { type PublishMode } from '../components/PublishConfirmStep'
import PublishResult from '../components/PublishResult'
import PublishVerifyModal from '../components/PublishVerifyModal'
import PublishRecords from '../components/PublishRecords'

const FINISHED = ['success', 'failed', 'partial']

const STEPS = [
  { key: 'video', label: '选择视频' },
  { key: 'copy', label: '填写文案' },
  { key: 'accounts', label: '选择平台账号' },
  { key: 'confirm', label: '发布设置与确认' },
]

export default function PublishPage() {
  const location = useLocation()
  const [searchParams] = useSearchParams()

  const [step, setStep] = useState(0)

  const [videos, setVideos] = useState<Video[]>([])
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [accounts, setAccounts] = useState<Account[]>([])

  const [title, setTitle] = useState('')
  const [desc, setDesc] = useState('')
  const [tags, setTags] = useState<string[]>([])
  const [tagInput, setTagInput] = useState('')
  // 选中的发布目标，元素格式 "platform:account"
  const [targets, setTargets] = useState<string[]>([])
  const [mode, setMode] = useState<PublishMode>('now')
  const [schedule, setSchedule] = useState('')

  const [publishing, setPublishing] = useState(false)
  const [task, setTask] = useState<Task | null>(null)
  const timerRef = useRef<number | null>(null)
  /** 发布记录面板的刷新信号（任务终态时递增） */
  const [recordsKey, setRecordsKey] = useState(0)

  // 短信验证状态机（迁移自旧发布页，逻辑保持一致）
  const [verifyOpen, setVerifyOpen] = useState(false)
  const [verifyCode, setVerifyCode] = useState('')
  const [verifyBusy, setVerifyBusy] = useState(false)
  const [verifyPhase, setVerifyPhase] = useState<'input' | 'waiting'>('input')
  const [verifyRejected, setVerifyRejected] = useState(false)
  /** 用户主动关闭过本次验证弹窗（needs_verify 持续为 true 时不再自动弹出） */
  const verifyDismissedRef = useRef(false)
  /** 已提交验证码，等待引擎消费 */
  const verifySubmittedRef = useRef(false)
  /** 提交后观察到 needs_verify 变过 false（引擎已取走验证码） */
  const verifyConsumedRef = useRef(false)
  const verifySubmittedAtRef = useRef(0)

  const selectedVideo = videos.find((v) => v.id === selectedId) ?? null

  // 加载视频列表并处理预选（?video=ID / ?videoId=ID / location.state.videoId）
  useEffect(() => {
    fetchVideos()
      .then((list) => {
        setVideos(list)
        const stateId = (location.state as { videoId?: number } | null)?.videoId
        const queryId = searchParams.get('video') ?? searchParams.get('videoId')
        const preselect = stateId ?? (queryId ? Number(queryId) : null)
        if (preselect && list.some((v) => v.id === preselect)) {
          setSelectedId(preselect)
          const v = list.find((x) => x.id === preselect)
          if (v) setTitle((t) => t || v.title.slice(0, TITLE_MAX))
        }
      })
      .catch(() => {})

    // 先快速加载账号（cookie 状态），再后台校准 valid/detail（check 较慢，不阻塞）
    fetchAccounts()
      .then(setAccounts)
      .catch(() => {})
    fetchAccounts(true)
      .then(setAccounts)
      .catch(() => {})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    return () => {
      if (timerRef.current) window.clearInterval(timerRef.current)
    }
  }, [])

  // 重置短信验证状态机（新任务 / 任务结束时调用）
  const resetVerify = useCallback(() => {
    verifyDismissedRef.current = false
    verifySubmittedRef.current = false
    verifyConsumedRef.current = false
    setVerifyOpen(false)
    setVerifyCode('')
    setVerifyPhase('input')
    setVerifyRejected(false)
  }, [])

  // 每次拿到最新任务对象时同步验证弹窗状态（轮询期间持续生效）
  const syncVerify = useCallback(
    (t: Task) => {
      if (FINISHED.includes(t.status)) {
        resetVerify()
        return
      }
      if (t.needs_verify) {
        if (verifySubmittedRef.current) {
          if (verifyConsumedRef.current) {
            // 提交后 false → true：上次验证码未通过或已过期
            verifySubmittedRef.current = false
            verifyConsumedRef.current = false
            setVerifyPhase('input')
            setVerifyRejected(true)
            setVerifyCode('')
          } else if (Date.now() - verifySubmittedAtRef.current > 30000) {
            // 兜底：30 秒仍未被消费（可能漏采 false 窗口），允许重新输入
            verifySubmittedRef.current = false
            setVerifyPhase('input')
            setVerifyRejected(true)
          }
          // 否则保持「已提交，等待引擎验证…」
        }
        if (!verifyDismissedRef.current) setVerifyOpen(true)
      } else {
        if (verifySubmittedRef.current) {
          // 引擎已取走验证码（false 窗口）
          verifySubmittedRef.current = false
          verifyConsumedRef.current = true
        } else if (!verifyConsumedRef.current) {
          // 无验证需求：重置忽略标记，确保弹窗关闭
          verifyDismissedRef.current = false
          setVerifyOpen(false)
          return
        }
        // needs_verify 变 false：本次验证已通过，关闭弹窗
        verifyConsumedRef.current = false
        verifyDismissedRef.current = false
        setVerifyOpen(false)
        setVerifyPhase('input')
        setVerifyRejected(false)
      }
    },
    [resetVerify],
  )

  const applyTask = useCallback(
    (t: Task) => {
      setTask(t)
      syncVerify(t)
    },
    [syncVerify],
  )

  const handleVerifySubmit = async () => {
    const code = verifyCode.trim()
    if (!/^\d{4,8}$/.test(code)) {
      toast.warning('请输入 4-8 位数字验证码')
      return
    }
    if (!task || verifyBusy) return
    setVerifyBusy(true)
    try {
      await submitVerifyCode(task.id, code)
      verifySubmittedRef.current = true
      verifyConsumedRef.current = false
      verifySubmittedAtRef.current = Date.now()
      setVerifyPhase('waiting')
      setVerifyRejected(false)
      setVerifyCode('')
    } catch (e) {
      toast.error(`提交失败：${errorMessage(e)}`)
    } finally {
      setVerifyBusy(false)
    }
  }

  const startPolling = (taskId: string) => {
    if (timerRef.current) window.clearInterval(timerRef.current)
    timerRef.current = window.setInterval(async () => {
      try {
        const t = await fetchTask(taskId)
        applyTask(t)
        if (FINISHED.includes(t.status)) {
          if (timerRef.current) window.clearInterval(timerRef.current)
          timerRef.current = null
          setRecordsKey((k) => k + 1)
          if (t.status === 'success') toast.success('全部账号发布成功')
          else if (t.status === 'partial') toast.warning('部分账号发布失败，请查看结果')
          else toast.error('发布失败，请查看结果与日志')
        }
      } catch {
        // 轮询失败静默重试
      }
    }, 1500)
  }

  const handleSelectVideo = (v: Video) => {
    setSelectedId(v.id)
    if (!title) setTitle(v.title.slice(0, TITLE_MAX))
  }

  const validateTitle = (): boolean => {
    if (!title.trim()) {
      toast.warning('标题必填')
      return false
    }
    if (title.trim().length > TITLE_MAX) {
      toast.warning(`标题最长 ${TITLE_MAX} 字`)
      return false
    }
    return true
  }

  const goNext = () => {
    if (step === 0 && !selectedId) {
      toast.warning('请先选择一个视频')
      return
    }
    if (step === 1 && !validateTitle()) return
    if (step === 2 && targets.length === 0) {
      toast.warning('请至少选择一个账号')
      return
    }
    setStep((s) => Math.min(s + 1, STEPS.length - 1))
  }

  const handlePublish = async () => {
    if (!selectedVideo || publishing) return
    if (!validateTitle()) return
    if (targets.length === 0) {
      toast.warning('请至少选择一个账号')
      return
    }
    if (mode === 'schedule' && !schedule) {
      toast.warning('请选择定时发布时间')
      return
    }

    setPublishing(true)
    setTask(null)
    resetVerify()
    try {
      const { task_id } = await publishVideo({
        video_id: selectedVideo.id,
        targets: targets.map((key) => {
          const i = key.indexOf(':')
          return { platform: key.slice(0, i), account: key.slice(i + 1) }
        }),
        title: title.trim(),
        desc: desc.trim(),
        tags,
        schedule: mode === 'schedule' && schedule ? schedule : null,
      })
      const t = await fetchTask(task_id)
      applyTask(t)
      if (!FINISHED.includes(t.status)) startPolling(task_id)
      else setRecordsKey((k) => k + 1)
    } catch (e) {
      toast.error(`创建发布任务失败：${errorMessage(e)}`)
    } finally {
      setPublishing(false)
    }
  }

  const busy = task !== null && ['pending', 'running'].includes(task.status)

  // 再发一条：回到向导第一步，保留表单状态，清空当前任务
  const handleRestart = () => {
    if (busy) return
    setTask(null)
    resetVerify()
    setStep(0)
  }

  return (
    <div>
      <PageHeader title="发布视频" desc="四步完成多平台分发：选视频 → 写文案 → 选账号 → 确认发布" />

      {task ? (
        <PublishResult task={task} targets={targets} busy={busy} onRestart={handleRestart} />
      ) : (
        <Panel>
          <WizardSteps steps={STEPS} current={step} />

          <div className="mt-5">
            {step === 0 && (
              <PublishVideoStep videos={videos} selectedId={selectedId} onSelect={handleSelectVideo} />
            )}
            {step === 1 && (
              <PublishCopyStep
                title={title}
                desc={desc}
                tags={tags}
                tagInput={tagInput}
                onTitleChange={setTitle}
                onDescChange={setDesc}
                onTagsChange={setTags}
                onTagInputChange={setTagInput}
              />
            )}
            {step === 2 && <PublishAccountsStep accounts={accounts} targets={targets} onChange={setTargets} />}
            {step === 3 && selectedVideo && (
              <PublishConfirmStep
                video={selectedVideo}
                title={title.trim()}
                desc={desc.trim()}
                tags={tags}
                targets={targets}
                mode={mode}
                schedule={schedule}
                onModeChange={setMode}
                onScheduleChange={setSchedule}
                publishing={publishing}
                onSubmit={handlePublish}
              />
            )}
          </div>

          {/* 步骤导航 */}
          <div className="mt-5 flex items-center justify-between gap-2 border-t border-line-soft pt-4">
            <Button
              variant="ghost"
              icon={<ArrowLeft size={14} />}
              disabled={step === 0 || publishing}
              onClick={() => setStep((s) => Math.max(s - 1, 0))}
            >
              上一步
            </Button>
            {step < STEPS.length - 1 && (
              <Button variant="primary" onClick={goNext}>
                下一步
                <ArrowRight size={14} />
              </Button>
            )}
          </div>
        </Panel>
      )}

      {/* 发布记录（保留旧页能力：记录/排期双视角 + 筛选 + 日志） */}
      <PublishRecords refreshKey={recordsKey} />

      {/* 抖音短信验证弹窗 */}
      <PublishVerifyModal
        open={verifyOpen}
        phase={verifyPhase}
        rejected={verifyRejected}
        busy={verifyBusy}
        code={verifyCode}
        onCodeChange={setVerifyCode}
        onSubmit={handleVerifySubmit}
        onClose={() => {
          // 用户暂不操作：本轮 needs_verify 持续期间不再自动弹出
          verifyDismissedRef.current = true
          setVerifyOpen(false)
        }}
      />
    </div>
  )
}
