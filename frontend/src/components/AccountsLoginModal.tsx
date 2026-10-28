import { useEffect, useRef, useState } from 'react'
import { Check, CheckCircle, CaretDown, QrCode, XCircle } from '@phosphor-icons/react'
import {
  cancelLogin,
  errorMessage,
  fetchLoginStatus,
  PLATFORMS,
  QR_LOGIN_PLATFORMS,
  startLogin,
  type LoginSession,
} from '../api'
import { Alert, Button, Input, Modal, PlatformIcon, Segment, Spinner, toast } from '../ui'
import { metaOf } from './AccountsShared'

const ACCOUNT_NAME_RE = /^[A-Za-z0-9_-]+$/

/** 支持扫码登录的平台 */
const QR_PLATFORM_META = QR_LOGIN_PLATFORMS.map((key) => ({
  key,
  label: PLATFORMS.find((p) => p.key === key)?.label ?? key,
}))

const STEPS = [
  { key: 'qr', label: '生成二维码' },
  { key: 'scan', label: '手机扫码' },
  { key: 'confirm', label: '确认登录' },
  { key: 'done', label: '完成' },
]

interface Props {
  open: boolean
  /** 初始平台 */
  platform: string
  /** 初始账号名；非空且 lockTarget 时锁定（账号卡「重新登录」场景） */
  account: string
  lockTarget?: boolean
  onClose: () => void
  /** 登录成功（自动关闭前）回调，用于刷新列表 */
  onSuccess: () => void
}

/** 顶部 4 步指示：始终保持一排（圆圈在上、文字在下，连接线弹性填充，窄屏不溢出） */
function StepIndicator({ current }: { current: number }) {
  return (
    <ol className="flex items-start" aria-label="扫码登录步骤">
      {STEPS.map((step, i) => {
        const done = i < current
        const active = i === current
        return (
          <li key={step.key} className="contents">
            <div className="flex flex-col items-center gap-1">
              <span
                aria-current={active ? 'step' : undefined}
                className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-full border text-[10px] font-medium transition-colors ${
                  done
                    ? 'border-accent bg-accent text-white'
                    : active
                      ? 'border-accent bg-accent-soft text-accent'
                      : 'border-line bg-fill text-ink-4'
                }`}
              >
                {done ? <Check size={10} weight="bold" /> : i + 1}
              </span>
              <span
                className={`whitespace-nowrap text-[10px] leading-tight transition-colors ${
                  active ? 'font-medium text-ink-1' : done ? 'text-ink-2' : 'text-ink-4'
                }`}
              >
                {step.label}
              </span>
            </div>
            {i < STEPS.length - 1 && (
              <span
                aria-hidden
                className={`mx-1.5 mt-2.5 h-px min-w-2 flex-1 ${done ? 'bg-accent' : 'bg-line'}`}
              />
            )}
          </li>
        )
      })}
    </ol>
  )
}

/** 扫码登录向导弹窗：生成二维码 → 手机扫码 → 手机确认 → 完成 */
export default function AccountsLoginModal({
  open,
  platform: initPlatform,
  account: initAccount,
  lockTarget = false,
  onClose,
  onSuccess,
}: Props) {
  const [platform, setPlatform] = useState(initPlatform)
  const [account, setAccount] = useState(initAccount)
  const [error, setError] = useState<string | null>(null)
  const [starting, setStarting] = useState(false)
  const [session, setSession] = useState<LoginSession | null>(null)
  const [shotTick, setShotTick] = useState(0)
  const [liveOpen, setLiveOpen] = useState(false)

  const pollRef = useRef<number | null>(null)
  const successTimerRef = useRef<number | null>(null)
  const sessionRef = useRef<LoginSession | null>(null)
  sessionRef.current = session

  // 打开时重置到初始状态
  useEffect(() => {
    if (open) {
      setPlatform(initPlatform)
      setAccount(initAccount)
      setError(null)
      setStarting(false)
      setSession(null)
      setLiveOpen(false)
    }
  }, [open, initPlatform, initAccount])

  // 卸载时停止轮询与延迟关闭定时器
  useEffect(() => {
    return () => {
      if (pollRef.current) window.clearInterval(pollRef.current)
      if (successTimerRef.current) window.clearTimeout(successTimerRef.current)
    }
  }, [])

  const stopPolling = () => {
    if (pollRef.current) {
      window.clearInterval(pollRef.current)
      pollRef.current = null
    }
  }

  const startPolling = (sessionId: string) => {
    stopPolling()
    pollRef.current = window.setInterval(async () => {
      try {
        const s = await fetchLoginStatus(sessionId)
        setSession(s)
        setShotTick(Date.now())
        if (s.done || s.state === 'success' || s.state === 'failed' || s.state === 'timeout') {
          stopPolling()
          if (s.state === 'success') {
            // 稍等片刻让用户看到成功提示，然后自动关闭并刷新列表
            if (successTimerRef.current) window.clearTimeout(successTimerRef.current)
            successTimerRef.current = window.setTimeout(() => {
              successTimerRef.current = null
              toast.success('登录成功，账号已添加')
              onSuccess()
              onClose()
            }, 1200)
          }
        }
      } catch {
        // 轮询失败静默重试
      }
    }, 2000)
  }

  const handleStart = async () => {
    const name = account.trim()
    if (!ACCOUNT_NAME_RE.test(name)) {
      setError('账号名只能包含字母、数字、下划线和短横线')
      return
    }
    setError(null)
    setStarting(true)
    try {
      const s = await startLogin(platform, name)
      setSession(s)
      if (s.state === 'failed' || s.state === 'timeout') return
      startPolling(s.session_id)
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      setStarting(false)
    }
  }

  /** 重试：取消旧会话后重新开始 */
  const handleRetry = () => {
    const s = sessionRef.current
    if (s) cancelLogin(s.session_id).catch(() => {})
    setSession(null)
    setLiveOpen(false)
    void handleStart()
  }

  const requestClose = () => {
    stopPolling()
    const hadSuccessTimer = !!successTimerRef.current
    if (successTimerRef.current) {
      window.clearTimeout(successTimerRef.current)
      successTimerRef.current = null
    }
    const s = sessionRef.current
    if (s && s.state !== 'success') {
      // 非成功状态关闭时取消登录会话
      cancelLogin(s.session_id).catch(() => {})
    } else if (s && hadSuccessTimer) {
      // 成功倒计时期间手动关闭：仍刷新列表
      onSuccess()
    }
    onClose()
  }

  // 步骤指示当前下标：生成中=0，二维码就绪=1（扫码+确认提示），成功=3
  const currentStep = !session
    ? 0
    : session.state === 'starting'
      ? 0
      : session.state === 'success'
        ? 3
        : 1

  const footer = (
    <Button onClick={requestClose} disabled={starting}>
      取消
    </Button>
  )

  return (
    <Modal open={open} onClose={requestClose} title="扫码登录" width={440} footer={footer} maskClosable={false}>
      <StepIndicator current={currentStep} />

      {!session ? (
        <div className="mt-5">
          {!lockTarget && (
            <>
              <div className="text-xs text-ink-3">平台</div>
              <Segment
                className="mt-2"
                value={platform}
                onChange={setPlatform}
                options={QR_PLATFORM_META.map((m) => ({
                  value: m.key,
                  label: (
                    <span className="inline-flex items-center gap-1.5">
                      <PlatformIcon platform={m.key} size={14} />
                      {m.label}
                    </span>
                  ),
                }))}
              />
              <div className="mt-4 text-xs text-ink-3">账号名</div>
            </>
          )}
          {lockTarget && (
            <div className="mb-1 inline-flex items-center gap-1.5 text-[13px] text-ink-1">
              <PlatformIcon platform={platform} size={16} />
              {metaOf(platform).label}
              <span className="text-ink-3"> / {account}</span>
            </div>
          )}
          {!lockTarget && (
            <Input
              className="mt-2"
              value={account}
              onChange={setAccount}
              placeholder="字母、数字、_、-，如 default"
            />
          )}

          {error && (
            <Alert type="error" className="mt-3">
              {error}
            </Alert>
          )}

          <Button
            variant="primary"
            full
            icon={<QrCode size={15} />}
            loading={starting}
            disabled={!account.trim()}
            onClick={handleStart}
            className="mt-4"
          >
            生成二维码
          </Button>
        </div>
      ) : (
        <div className="mt-5 text-center">
          <div className="inline-flex items-center gap-1.5 text-[13px] text-ink-1">
            <PlatformIcon platform={session.platform} size={14} />
            {metaOf(session.platform).label}
            <span className="text-ink-3"> / {session.account}</span>
          </div>

          {session.state === 'starting' && (
            <div className="py-10">
              <div className="flex justify-center">
                <Spinner />
              </div>
              <div className="mt-3 text-[13px] text-ink-3">正在生成二维码，请稍候…</div>
            </div>
          )}

          {session.state === 'qrcode_ready' && (
            <div className="mt-3">
              {session.has_qr ? (
                <img
                  key={session.qr_version}
                  src={`/api/accounts/login/${session.session_id}/qr.png?v=${session.qr_version}`}
                  alt="登录二维码"
                  className="mx-auto h-52 w-52 rounded-lg bg-white p-2"
                />
              ) : (
                <div className="flex h-52 items-center justify-center">
                  <div>
                    <div className="flex justify-center">
                      <Spinner />
                    </div>
                    <div className="mt-3 text-[13px] text-ink-3">二维码加载中…</div>
                  </div>
                </div>
              )}
              <div className="mt-3 text-[13px] text-ink-1">
                用手机 {metaOf(session.platform).label} App 扫码
              </div>
              <Alert type="warning" className="mt-3 text-left">
                扫码后请在手机上点击【确认登录】，完成前请勿关闭本窗口。
              </Alert>

              {/* B站走 TV API 扫码，无浏览器实况 */}
              {session.platform !== 'bilibili' && (
                <div className="mt-4 text-left">
                  <button
                    type="button"
                    aria-expanded={liveOpen}
                    onClick={() => setLiveOpen((v) => !v)}
                    className="flex w-full items-center gap-1.5 rounded-[10px] border border-line bg-fill px-3 py-2.5 text-left text-[13px] text-ink-2 transition-colors hover:bg-fill-deep"
                  >
                    <CaretDown
                      size={13}
                      className={`shrink-0 transition-transform ${liveOpen ? '' : '-rotate-90'}`}
                    />
                    看不到二维码？查看浏览器实时画面
                  </button>
                  {liveOpen && (
                    <div className="mt-2">
                      <div className="mb-2 text-[12px] leading-relaxed text-ink-3">
                        浏览器实况（2 秒刷新）：手机上确认后这里应跳转到登录后页面；若出现滑块/安全验证请在手机或联系管理员处理。
                      </div>
                      <img
                        src={`/api/accounts/login/${session.session_id}/screenshot.png?v=${shotTick}`}
                        alt="浏览器实况"
                        className="mx-auto w-full rounded-lg border border-line"
                        onError={(e) => {
                          ;(e.target as HTMLImageElement).style.visibility = 'hidden'
                        }}
                        onLoad={(e) => {
                          ;(e.target as HTMLImageElement).style.visibility = 'visible'
                        }}
                      />
                    </div>
                  )}
                </div>
              )}
            </div>
          )}

          {session.state === 'success' && (
            <div className="py-8">
              <CheckCircle size={44} weight="fill" className="mx-auto text-success" />
              <div className="mt-3 text-[14px] font-medium text-success">
                登录成功{session.message ? `：${session.message}` : ''}
              </div>
              <div className="mt-1.5 text-[12px] text-ink-3">即将自动关闭并刷新账号列表…</div>
            </div>
          )}

          {(session.state === 'failed' || session.state === 'timeout') && (
            <div className="py-8">
              <XCircle size={44} weight="fill" className="mx-auto text-danger" />
              <div className="mt-3 text-[13px] text-danger">
                {session.state === 'timeout' ? '登录超时，二维码已过期' : '登录失败'}
                {session.message ? `：${session.message}` : ''}
              </div>
              <Button variant="primary" loading={starting} onClick={handleRetry} className="mt-4">
                重试
              </Button>
            </div>
          )}
        </div>
      )}
    </Modal>
  )
}
