import type { KeyboardEvent } from 'react'
import { Alert, Button, Input, Modal } from '../ui'

interface Props {
  open: boolean
  /** input = 等待用户输入；waiting = 已提交等待引擎验证 */
  phase: 'input' | 'waiting'
  /** 上次验证码未通过/已过期 */
  rejected: boolean
  busy: boolean
  code: string
  onCodeChange: (v: string) => void
  onSubmit: () => void
  onClose: () => void
}

/** 抖音短信验证弹窗（遮罩不可点关闭，轮询期间由页面状态机驱动） */
export default function PublishVerifyModal({
  open,
  phase,
  rejected,
  busy,
  code,
  onCodeChange,
  onSubmit,
  onClose,
}: Props) {
  return (
    <Modal
      title="抖音需要短信验证"
      open={open}
      maskClosable={false}
      onClose={onClose}
      width={420}
      footer={
        phase === 'input' ? (
          <Button variant="primary" full loading={busy} disabled={!code.trim()} onClick={onSubmit}>
            提交验证码
          </Button>
        ) : null
      }
    >
      <p className="text-[13px] leading-relaxed text-ink-3">
        验证码已发送到你的手机，请输入短信中的 6 位数字。提交后引擎会自动填入并继续发布。
      </p>
      {rejected && (
        <Alert type="warning" className="mt-3">
          上次提交的验证码未通过或已过期，请重新获取后再输入
        </Alert>
      )}
      {phase === 'waiting' ? (
        <Alert type="info" className="mt-3">
          已提交，等待引擎验证…
        </Alert>
      ) : (
        <Input
          value={code}
          onChange={(v: string) => onCodeChange(v.replace(/\D/g, '').slice(0, 8))}
          maxLength={8}
          placeholder="输入验证码"
          inputMode="numeric"
          onKeyDown={(e: KeyboardEvent<HTMLInputElement>) => {
            if (e.key === 'Enter') onSubmit()
          }}
          className="tnum mt-3"
          autoFocus
        />
      )}
    </Modal>
  )
}
