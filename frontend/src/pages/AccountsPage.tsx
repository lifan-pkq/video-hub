import { useCallback, useEffect, useRef, useState, type ChangeEvent } from 'react'
import { CheckCircle, Plus, QrCode, UserCircle, XCircle } from '@phosphor-icons/react'
import {
  cancelLogin,
  checkAccount,
  deleteAccount,
  errorMessage,
  fetchAccounts,
  fetchLoginStatus,
  formatTime,
  PLATFORMS,
  QR_LOGIN_PLATFORMS,
  startLogin,
  uploadCookie,
  type Account,
  type CheckResult,
  type LoginSession,
} from '../api'
import {
  Alert,
  Button,
  confirmDialog,
  EmptyState,
  Input,
  Modal,
  PageHeader,
  Panel,
  PlatformIcon,
  Segment,
  Spinner,
  toast,
} from '../ui'

/** 支持扫码登录的平台（B站走 cookie 上传，不在此列） */
const QR_PLATFORM_META: { key: string; label: string }[] = QR_LOGIN_PLATFORMS.map((key) => ({
  key,
  label: PLATFORMS.find((p) => p.key === key)?.label ?? key,
}))

const ACCOUNT_NAME_RE = /^[A-Za-z0-9_-]+$/

function metaOf(platform: string) {
  return PLATFORMS.find((m) => m.key === platform) ?? { key: platform, label: platform }
}

function accountKey(a: Account): string {
  return `${a.platform}:${a.account}`
}

/** cookie 状态点标：未配置 ink-4 / 已失效 danger / 已配置·有效 success */
function CookieBadge({ tone, text }: { tone: 'gray' | 'danger' | 'success'; text: string }) {
  const color =
    tone === 'gray' ? 'var(--ink-4)' : tone === 'danger' ? 'var(--danger)' : 'var(--success)'
  return (
    <span className="flex items-center gap-1.5 text-xs text-ink-3">
      <span className="h-2 w-2 rounded-full" style={{ background: color }} />
      {text}
    </span>
  )
}

export default function AccountsPage() {
  const [accounts, setAccounts] = useState<Account[]>([])
  const [loading, setLoading] = useState(true)

  const [checking, setChecking] = useState<Record<string, boolean>>({})
  const [checkResults, setCheckResults] = useState<Record<string, CheckResult | { error: string }>>({})

  const [uploading, setUploading] = useState<Record<string, boolean>>({})
  const fileRefs = useRef<Record<string, HTMLInputElement | null>>({})

  // 扫码登录弹窗
  const [loginOpen, setLoginOpen] = useState(false)
  const [loginPlatform, setLoginPlatform] = useState('douyin')
  const [loginAccount, setLoginAccount] = useState('')
  const [loginError, setLoginError] = useState<string | null>(null)
  const [loginStarting, setLoginStarting] = useState(false)
  const [session, setSession] = useState<LoginSession | null>(null)
  const pollRef = useRef<number | null>(null)
  const sessionRef = useRef<LoginSession | null>(null)
  sessionRef.current = session

  const load = useCallback(async () => {
    try {
      setAccounts(await fetchAccounts())
    } catch (e) {
      toast.error(`加载失败：${errorMessage(e)}`)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  // 卸载时停止登录轮询
  useEffect(() => {
    return () => {
      if (pollRef.current) window.clearInterval(pollRef.current)
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
        if (s.done || s.state === 'success' || s.state === 'failed' || s.state === 'timeout') {
          stopPolling()
          if (s.state === 'success') {
            // 稍等片刻让用户看到成功提示，然后自动关闭并刷新列表
            window.setTimeout(() => {
              setLoginOpen(false)
              setSession(null)
              toast.success('登录成功，账号已添加')
              load()
            }, 1200)
          }
        }
      } catch {
        // 轮询失败静默重试
      }
    }, 2000)
  }

  const handleStartLogin = async () => {
    const account = loginAccount.trim()
    if (!ACCOUNT_NAME_RE.test(account)) {
      setLoginError('账号名只能包含字母、数字、下划线和短横线')
      return
    }
    setLoginError(null)
    setLoginStarting(true)
    try {
      const s = await startLogin(loginPlatform, account)
      setSession(s)
      if (s.state === 'failed' || s.state === 'timeout') return
      startPolling(s.session_id)
    } catch (e) {
      setLoginError(errorMessage(e))
    } finally {
      setLoginStarting(false)
    }
  }

  const closeLoginModal = () => {
    stopPolling()
    const s = sessionRef.current
    // 非成功状态关闭时取消登录会话
    if (s && s.state !== 'success') {
      cancelLogin(s.session_id).catch(() => {})
    }
    setLoginOpen(false)
    setSession(null)
    setLoginError(null)
  }

  const openLoginModal = (platform = 'douyin', account = '') => {
    setLoginPlatform(platform)
    setLoginAccount(account)
    setLoginError(null)
    setSession(null)
    setLoginOpen(true)
  }

  const handleCheck = async (acc: Account) => {
    const key = accountKey(acc)
    setChecking((s) => ({ ...s, [key]: true }))
    setCheckResults((s) => {
      const next = { ...s }
      delete next[key]
      return next
    })
    try {
      const r = await checkAccount(acc.platform, acc.account)
      setCheckResults((s) => ({ ...s, [key]: r }))
      if (r.valid) toast.success(`${acc.account} cookie 有效`)
      else toast.warning(`${acc.account} cookie 无效${r.detail ? `：${r.detail}` : ''}`)
    } catch (e) {
      setCheckResults((s) => ({ ...s, [key]: { error: errorMessage(e) } }))
      toast.error(`校验失败：${errorMessage(e)}`)
    } finally {
      setChecking((s) => ({ ...s, [key]: false }))
    }
  }

  const handleUpload = async (acc: Account, file: File) => {
    const key = accountKey(acc)
    setUploading((s) => ({ ...s, [key]: true }))
    try {
      const r = await uploadCookie(acc.platform, acc.account, file)
      if (r.ok) toast.success(r.message)
      else toast.warning(r.message)
      await load()
    } catch (e) {
      toast.error(`上传失败：${errorMessage(e)}`)
    } finally {
      setUploading((s) => ({ ...s, [key]: false }))
      const input = fileRefs.current[key]
      if (input) input.value = ''
    }
  }

  const handleDelete = async (acc: Account) => {
    const ok = await confirmDialog({
      title: '确认删除',
      content: `确定要删除账号「${metaOf(acc.platform).label} / ${acc.account}」吗？其 cookie 文件将一并删除，此操作不可恢复。`,
      okText: '确认删除',
      cancelText: '取消',
      danger: true,
    })
    if (!ok) return
    try {
      await deleteAccount(acc.platform, acc.account)
      toast.success('已删除')
      await load()
    } catch (e) {
      toast.error(`删除失败：${errorMessage(e)}`)
    }
  }

  // 按平台分组
  const grouped = new Map<string, Account[]>()
  for (const acc of accounts) {
    const list = grouped.get(acc.platform) ?? []
    list.push(acc)
    grouped.set(acc.platform, list)
  }
  const platformOrder = [
    ...PLATFORMS.map((m) => m.key).filter((k) => grouped.has(k)),
    ...[...grouped.keys()].filter((k) => !PLATFORMS.some((m) => m.key === k)),
  ]

  return (
    <div>
      <PageHeader
        title="平台账号"
        desc="管理各平台的登录账号，发布功能依赖有效的 cookie"
        extra={
          <Button variant="primary" icon={<Plus size={15} weight="bold" />} onClick={() => openLoginModal()}>
            添加账号
          </Button>
        }
      />

      {loading ? (
        <div className="space-y-3">
          {[0, 1, 2].map((i) => (
            <Panel key={i} pad="sm">
              <div className="space-y-2.5">
                <div className="skeleton h-4 w-2/5" />
                <div className="skeleton h-3.5 w-3/5" />
              </div>
            </Panel>
          ))}
        </div>
      ) : accounts.length === 0 ? (
        <Panel pad="none">
          <EmptyState
            icon={<UserCircle size={28} />}
            title="暂无账号"
            desc="点击右上角添加账号，扫码登录"
          />
        </Panel>
      ) : (
        <div className="max-w-3xl space-y-5">
          {platformOrder.map((platform) => {
            const meta = metaOf(platform)
            const list = grouped.get(platform) ?? []
            return (
              <div key={platform}>
                <div className="mb-2 flex items-center gap-2">
                  <PlatformIcon platform={platform} size={20} />
                  <span className="section-title">{meta.label}</span>
                  <span className="text-xs text-ink-3">{list.length} 个账号</span>
                </div>
                <div className="space-y-3">
                  {list.map((acc, i) => {
                    const key = accountKey(acc)
                    const check = checkResults[key]
                    // 本地校验结果优先于列表返回的 valid 字段
                    const checked = check && !('error' in check) ? check : null
                    const invalid = checked ? !checked.valid : acc.valid === false
                    const valid = checked ? checked.valid : acc.valid === true
                    return (
                      <div
                        key={key}
                        className="vh-card enter-item p-4"
                        style={{ animationDelay: `${Math.min(i * 50, 200)}ms` }}
                      >
                        <div className="flex items-center justify-between">
                          <div>
                            <span className="font-medium text-ink-1" style={{ fontSize: 15 }}>
                              {acc.account}
                            </span>
                            <div className="mt-1 flex items-center gap-2" style={{ fontSize: 12 }}>
                              {!acc.cookie_exists ? (
                                <CookieBadge tone="gray" text="未配置 cookie" />
                              ) : invalid ? (
                                <CookieBadge tone="danger" text="cookie 已失效" />
                              ) : (
                                <CookieBadge
                                  tone="success"
                                  text={valid ? 'cookie 有效' : 'cookie 已配置'}
                                />
                              )}
                              {acc.updated_at && (
                                <span className="tnum text-xs text-ink-3">
                                  更新于 {formatTime(acc.updated_at)}
                                </span>
                              )}
                            </div>
                          </div>
                          <div className="flex items-center gap-2">
                            {acc.cookie_exists && (
                              <Button loading={checking[key]} onClick={() => handleCheck(acc)}>
                                校验
                              </Button>
                            )}
                            {!acc.cookie_exists && QR_LOGIN_PLATFORMS.includes(acc.platform) && (
                              <Button
                                variant="primary"
                                onClick={() => openLoginModal(acc.platform, acc.account)}
                              >
                                扫码登录
                              </Button>
                            )}
                            <Button
                              variant={acc.cookie_exists ? 'primary' : 'secondary'}
                              loading={uploading[key]}
                              onClick={() => fileRefs.current[key]?.click()}
                            >
                              上传 cookie
                            </Button>
                            {acc.cookie_exists && (
                              <Button
                                variant="text"
                                style={{ color: 'var(--danger)' }}
                                onClick={() => handleDelete(acc)}
                              >
                                删除
                              </Button>
                            )}
                            <input
                              ref={(el) => {
                                fileRefs.current[key] = el
                              }}
                              type="file"
                              accept=".json"
                              className="hidden"
                              onChange={(e: ChangeEvent<HTMLInputElement>) => {
                                const f = e.target.files?.[0]
                                if (f) handleUpload(acc, f)
                              }}
                            />
                          </div>
                        </div>

                        {/* 校验结果 */}
                        {check && (
                          <Alert
                            className="mt-3"
                            type={'error' in check ? 'error' : check.valid ? 'success' : 'warning'}
                          >
                            {'error' in check
                              ? `校验失败：${check.error}`
                              : check.valid
                                ? `有效${check.detail ? `：${check.detail}` : ''}`
                                : `无效${check.detail ? `：${check.detail}` : ''}`}
                          </Alert>
                        )}
                      </div>
                    )
                  })}
                </div>
              </div>
            )
          })}
        </div>
      )}

      {/* 扫码登录弹窗 */}
      <Modal open={loginOpen} onClose={closeLoginModal} title="添加账号" width={400}>
        {!session ? (
          <div>
            <div className="text-xs text-ink-3">平台</div>
            <Segment
              className="mt-2"
              value={loginPlatform}
              onChange={setLoginPlatform}
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
            <Input
              className="mt-2"
              value={loginAccount}
              onChange={setLoginAccount}
              placeholder="字母、数字、_、-，如 default"
            />

            {loginError && (
              <Alert type="error" className="mt-3">
                {loginError}
              </Alert>
            )}

            <Button
              variant="primary"
              full
              icon={<QrCode size={15} />}
              loading={loginStarting}
              disabled={!loginAccount.trim()}
              onClick={handleStartLogin}
              className="mt-4"
            >
              开始扫码登录
            </Button>
          </div>
        ) : (
          <div className="text-center">
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
                <div className="mt-3 text-[13px] text-ink-3">正在打开登录页面…</div>
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
                  <div className="py-10">
                    <div className="flex justify-center">
                      <Spinner />
                    </div>
                    <div className="mt-3 text-[13px] text-ink-3">二维码加载中…</div>
                  </div>
                )}
                <div className="mt-3 text-[13px] text-ink-1">请用对应平台的手机 App 扫码登录</div>
              </div>
            )}

            {session.state === 'success' && (
              <div className="py-8">
                <CheckCircle size={40} weight="fill" className="mx-auto text-success" />
                <div className="mt-3 text-[13px] text-success">
                  登录成功{session.message ? `：${session.message}` : ''}
                </div>
              </div>
            )}

            {(session.state === 'failed' || session.state === 'timeout') && (
              <div className="py-8">
                <XCircle size={40} weight="fill" className="mx-auto text-danger" />
                <div className="mt-3 text-[13px] text-danger">
                  {session.state === 'timeout' ? '登录超时' : '登录失败'}
                  {session.message ? `：${session.message}` : ''}
                </div>
                <Button
                  variant="primary"
                  loading={loginStarting}
                  onClick={() => {
                    setSession(null)
                    handleStartLogin()
                  }}
                  className="mt-4"
                >
                  重试
                </Button>
              </div>
            )}
          </div>
        )}
      </Modal>
    </div>
  )
}
