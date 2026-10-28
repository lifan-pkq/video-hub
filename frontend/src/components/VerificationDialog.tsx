import { useEffect, useState, type KeyboardEvent } from 'react'
import { useTaskCenter } from '../context/TaskCenter'
import { Alert, Button, Input, Modal, toast } from '../ui'

export default function VerificationDialog() {
  const {
    verificationTask,
    verificationPhase,
    verificationError,
    dismissVerification,
    submitVerification,
  } = useTaskCenter()
  const [code, setCode] = useState('')

  useEffect(() => {
    setCode('')
  }, [verificationTask?.id, verificationPhase])

  const handleSubmit = async () => {
    if (!verificationTask || verificationPhase === 'submitting') return
    const value = code.trim()
    if (!/^\d{4,8}$/.test(value)) {
      toast.warning('请输入 4-8 位数字验证码')
      return
    }
    await submitVerification(verificationTask.id, value)
  }

  const inputPhase = verificationPhase !== 'waiting'

  return (
    <Modal
      title="抖音需要短信验证"
      open={!!verificationTask}
      maskClosable={false}
      onClose={dismissVerification}
      width={420}
      footer={
        verificationTask ? (
          <>
            <Button variant="ghost" onClick={dismissVerification}>稍后处理</Button>
            {inputPhase && (
              <Button
                variant="primary"
                loading={verificationPhase === 'submitting'}
                disabled={!code.trim()}
                onClick={handleSubmit}
              >
                提交验证码
              </Button>
            )}
          </>
        ) : null
      }
    >
      <p className="text-[13px] leading-relaxed text-ink-3">
        验证码已发送到你的手机，请输入短信中的 4-8 位数字。提交后引擎会自动填入并继续发布。
      </p>
      {verificationTask && (
        <p className="mt-2 truncate text-xs text-ink-4" title={verificationTask.title}>
          任务：{verificationTask.title}
        </p>
      )}
      {verificationPhase === 'rejected' && (
        <Alert type="warning" className="mt-3">
          上次提交的验证码未通过或已过期，请重新获取后再输入
        </Alert>
      )}
      {verificationError && <Alert type="error" className="mt-3">提交失败：{verificationError}</Alert>}
      {verificationPhase === 'waiting' ? (
        <Alert type="info" className="mt-3">已提交，等待引擎验证…</Alert>
      ) : (
        <Input
          value={code}
          onChange={(value: string) => setCode(value.replace(/\D/g, '').slice(0, 8))}
          maxLength={8}
          placeholder={verificationPhase === 'submitting' ? '正在提交…' : '输入验证码'}
          inputMode="numeric"
          disabled={verificationPhase === 'submitting'}
          onKeyDown={(event: KeyboardEvent<HTMLInputElement>) => {
            if (event.key === 'Enter') void handleSubmit()
          }}
          className="tnum mt-3"
          autoFocus
        />
      )}
    </Modal>
  )
}
